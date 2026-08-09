"""Off-main-thread GL texture uploader.

``gl_canvas.py`` used to call ``glTexImage2D`` synchronously inside the
GLArea's render signal, which stalled the main thread for tens of
milliseconds per visible page during page flips (a ~9.4 MB copy at 2.5x
texture zoom, more at higher zooms). ``QuadRenderer.upload_surface`` owned
that upload plus the eviction machinery, so texture lifetime was tied to the
render pass.

``TextureUploader`` moves uploads and deletions to a dedicated daemon thread
that owns its own *shared* ``Gdk.GLContext``:

  * ``Gdk.Display.create_gl_context()`` (GDK >= 4.6) creates a worker
    context that shares textures with the GLArea's context on the same
    display.
  * The worker runs ``glTexImage2D``, then inserts a fence and waits on it
    with ``glClientWaitSync`` *inside the worker context*. Waiting there
    (instead of on the main thread) guarantees the pixel copy has completed
    before the texture is published, without blocking the UI thread.
  * Evicted textures are queued and deleted on the worker thread too, so
    ``glDeleteTextures`` never appears in the render pass.

If a shared worker context cannot be created (headless or old GDK), the
uploader transparently falls back to the old synchronous main-thread path.
"""

import queue
import threading

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib

from ..core.texture import PageTexture


def _default_gl():
    from OpenGL import GL as gl

    return gl


class TextureUploader:
    """Uploads :class:`PageTexture` objects to GL on a worker thread.

    The object is not thread-safe through its public API; only the internal
    worker thread may run GL commands. All state shared with the main thread
    is guarded by ``_lock``.
    """

    def __init__(self, gl=None):
        self._gl = gl if gl is not None else _default_gl()
        self._ctx: "Gdk.GLContext | None" = None
        self._thread = None
        self._jobs: queue.Queue = queue.Queue()
        self._deletes: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._tex_ids: dict[PageTexture, int] = {}
        self._pending: set[PageTexture] = set()
        self._deferred_evict: set[PageTexture] = set()
        self._stop = threading.Event()
        self._notify = None

    @property
    def _using_worker(self) -> bool:
        return self._ctx is not None and self._thread is not None

    def initialize(self, area) -> None:
        """Create a shared worker GL context and start the upload thread.

        Falls back to synchronous main-thread uploads if the worker context
        cannot be created or does not share resources with the GLArea's
        context. ``area`` must already be realized.
        """
        try:
            main_ctx = area.get_context()
            if main_ctx is None:
                raise RuntimeError("GLArea has no GL context")
            display = Gdk.Display.get_default()
            if display is None:
                raise RuntimeError("no default Gdk display")
            worker = display.create_gl_context()
            worker.set_required_version(3, 3)
            worker.realize()
            if not worker.is_shared(main_ctx):
                print(
                    "[TextureUploader] worker GL context is not shared with the "
                    "main context; falling back to main-thread uploads"
                )
                return
        except Exception as e:
            print(
                f"[TextureUploader] shared-context init failed ({e}); "
                "falling back to main-thread uploads"
            )
            return
        self._start_worker(worker)

    def _start_worker(self, ctx) -> None:
        """Bind the upload thread to an already-realized (possibly fake) context.

        Tests use this to exercise the async path without a real display.
        """
        self._ctx = ctx
        self._thread = threading.Thread(target=self._run, name="texture-uploader", daemon=True)
        self._thread.start()

    def set_redraw_notify(self, notify) -> None:
        """Install a callback invoked (on the main thread) when a texture
        finishes uploading so the view can redraw."""
        self._notify = notify

    # --- Main-thread API ---

    def tex_id_for(self, texture: PageTexture) -> int | None:
        """Return the uploaded GL texture id, or None if not yet uploaded."""
        with self._lock:
            return self._tex_ids.get(texture)

    def textures(self) -> list[PageTexture]:
        """Snapshot of the textures that have finished uploading."""
        with self._lock:
            return list(self._tex_ids.keys())

    def request_upload(self, texture: PageTexture) -> None:
        """Ensure ``texture`` is uploaded.

        In worker mode the upload is queued and happens off the main thread;
        in fallback mode it happens synchronously on the caller's thread (the
        render pass, whose GL context is current).
        """
        if not self._using_worker:
            with self._lock:
                if texture in self._tex_ids:
                    return
            tex_id = self._upload_now(texture, wait_fence=False)
            if tex_id is not None:
                with self._lock:
                    self._tex_ids[texture] = tex_id
            return
        with self._lock:
            if texture in self._tex_ids or texture in self._pending:
                return
            self._pending.add(texture)
        self._jobs.put(texture)

    def on_evicted(self, texture: PageTexture) -> None:
        """Release a texture that is no longer referenced by the render pass.

        If the upload is still pending/in-flight, the texture is marked for
        deletion right after it lands instead of being published.
        """
        with self._lock:
            if texture in self._pending:
                self._deferred_evict.add(texture)
                return
            tex_id = self._tex_ids.pop(texture, None)
        if tex_id is None:
            return
        if self._using_worker:
            self._deletes.put(tex_id)
        else:
            self._gl.glDeleteTextures([tex_id])

    def evict_not_active(self, keep: set[PageTexture]) -> None:
        """Evict every uploaded texture not referenced by ``keep``.

        ``keep`` should be the set of textures drawn this frame (or still
        awaiting upload), so pages that scrolled out of the viewport free
        their GPU memory on the worker thread.
        """
        with self._lock:
            gone = []
            for tex in list(self._tex_ids):
                if tex not in keep:
                    gone.append(self._tex_ids.pop(tex))
            for tex in list(self._pending):
                if tex not in keep:
                    self._deferred_evict.add(tex)
        for tex_id in gone:
            if self._using_worker:
                self._deletes.put(tex_id)
            else:
                self._gl.glDeleteTextures([tex_id])

    def shutdown(self) -> None:
        """Stop the worker thread, draining queued deletes on its context."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._ctx = None

    # --- Worker thread ---

    def _run(self) -> None:
        ctx = self._ctx
        assert ctx is not None, "uploader worker started without a GL context"
        ctx.make_current()
        try:
            while not self._stop.is_set():
                try:
                    texture = self._jobs.get(timeout=0.1)
                except queue.Empty:
                    self._drain_deletes()
                    continue
                self._drain_deletes()
                self._handle_job(texture)
        finally:
            self._drain_deletes()
            ctx.clear_current()

    def _handle_job(self, texture: PageTexture) -> None:
        with self._lock:
            if texture in self._deferred_evict:
                self._deferred_evict.discard(texture)
                self._pending.discard(texture)
                return
        tex_id = self._upload_now(texture, wait_fence=True)
        if tex_id is None:
            with self._lock:
                self._pending.discard(texture)
            return
        with self._lock:
            deferred = texture in self._deferred_evict
            if deferred:
                self._deferred_evict.discard(texture)
                self._pending.discard(texture)
            else:
                self._tex_ids[texture] = tex_id
                self._pending.discard(texture)
        if deferred:
            self._gl.glDeleteTextures([tex_id])
            return
        if self._notify is not None:
            GLib.idle_add(self._notify)

    def _upload_now(self, texture: PageTexture, wait_fence: bool) -> int | None:
        gl = self._gl
        try:
            tex_id = gl.glGenTextures(1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D, 0, gl.GL_RGB8,
                texture.width, texture.height, 0,
                gl.GL_RGB, gl.GL_UNSIGNED_BYTE, texture.samples,
            )
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
            if wait_fence:
                fence = gl.glFenceSync(gl.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
                if fence:
                    gl.glClientWaitSync(fence, gl.GL_SYNC_FLUSH_COMMANDS_BIT, gl.GL_TIMEOUT_IGNORED)
                    gl.glDeleteSync(fence)
        except Exception as e:
            print(f"[TextureUploader] texture upload failed: {e}")
            return None
        return int(tex_id)

    def _drain_deletes(self) -> None:
        while True:
            try:
                tex_id = self._deletes.get_nowait()
            except queue.Empty:
                return
            self._gl.glDeleteTextures([tex_id])
