import threading
import time

from OpenGL import GL as _real_gl

from pdfatlas.core.texture import PageTexture
from pdfatlas.ui.texture_uploader import TextureUploader


def _tex(w: int = 4, h: int = 4) -> PageTexture:
    return PageTexture(w, h, 3, bytes(w * h * 3))


class FakeGL:
    """Minimal GL facade wrapping real constants; records upload/delete calls."""

    def __init__(self):
        self.next_tex = 1
        self.uploaded = []
        self.deleted = []
        self.fences = 0
        self.released = True

    def __getattr__(self, name):
        return getattr(_real_gl, name)

    def glGenTextures(self, n):
        tex = self.next_tex
        self.next_tex += 1
        return tex

    def glTexImage2D(self, target, level, internal, w, h, border, fmt, typ, data):
        if not self.released:
            self._started.set()
            self._release.wait(timeout=5.0)
        self.uploaded.append((w, h))

    def glFenceSync(self, condition, flags):
        self.fences += 1
        return self.fences

    def glClientWaitSync(self, fence, flags, timeout):
        return _real_gl.GL_ALREADY_SIGNALED

    def glDeleteSync(self, fence):
        pass

    def glDeleteTextures(self, ids):
        if isinstance(ids, int):
            ids = [ids]
        self.deleted.extend(ids)

    def block_uploads(self):
        """Make the next glTexImage2D block until released."""
        self.released = False
        self._started = threading.Event()
        self._release = threading.Event()
        return self._started, self._release


class FakeCtx:
    def __init__(self):
        self.made_current = False
        self.cleared_current = False

    def make_current(self):
        self.made_current = True

    def clear_current(self):
        self.cleared_current = True


def pump_until(cond, timeout=5.0):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    deadline = time.perf_counter() + timeout
    while not cond() and time.perf_counter() < deadline:
        ctx.iteration(False)
    assert cond(), "condition never became true"


def test_fallback_upload_is_synchronous():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    tex = _tex()

    uploader.request_upload(tex)

    assert uploader.tex_id_for(tex) == 1
    assert gl.uploaded == [(4, 4)]
    assert gl.fences == 0, "fallback path must not insert a fence"

    uploader.request_upload(tex)
    assert gl.uploaded == [(4, 4)], "duplicate request must not re-upload"


def test_fallback_eviction_deletes_synchronously():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    tex = _tex()

    uploader.request_upload(tex)
    uploader.evict_not_active(set())

    assert uploader.tex_id_for(tex) is None
    assert gl.deleted == [1]


def test_evict_not_active_keeps_only_referenced():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    keep, gone = _tex(), _tex()

    uploader.request_upload(keep)
    uploader.request_upload(gone)
    uploader.evict_not_active({keep})

    assert uploader.tex_id_for(keep) == 1
    assert uploader.tex_id_for(gone) is None
    assert gl.deleted == [2]
    assert uploader.textures() == [keep]


def test_worker_upload_is_async_with_notify():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    uploader._start_worker(FakeCtx())
    notified = threading.Event()
    uploader.set_redraw_notify(notified.set)
    tex = _tex()

    assert uploader.tex_id_for(tex) is None
    uploader.request_upload(tex)

    pump_until(notified.is_set)
    assert uploader.tex_id_for(tex) == 1
    assert gl.uploaded == [(4, 4)]
    assert gl.fences == 1, "worker path must wait on a fence"
    uploader.shutdown()


def test_worker_skips_duplicate_pending_requests():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    uploader._start_worker(FakeCtx())
    notified = threading.Event()
    uploader.set_redraw_notify(notified.set)
    tex = _tex()

    uploader.request_upload(tex)
    uploader.request_upload(tex)

    pump_until(notified.is_set)
    assert gl.uploaded == [(4, 4)]
    uploader.shutdown()


def test_worker_eviction_deletes_on_worker_thread():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    uploader._start_worker(FakeCtx())
    notified = threading.Event()
    uploader.set_redraw_notify(notified.set)
    tex = _tex()

    uploader.request_upload(tex)
    pump_until(notified.is_set)
    assert uploader.tex_id_for(tex) == 1

    uploader.on_evicted(tex)
    assert uploader.tex_id_for(tex) is None

    uploader.shutdown()
    assert gl.deleted == [1], "delete must be drained on the worker thread"


def test_eviction_while_pending_never_publishes():
    gl = FakeGL()
    started, release = gl.block_uploads()
    uploader = TextureUploader(gl=gl)
    uploader._start_worker(FakeCtx())
    tex = _tex()

    uploader.request_upload(tex)
    assert started.wait(timeout=5.0), "upload never started"

    uploader.on_evicted(tex)
    assert uploader.tex_id_for(tex) is None

    release.set()
    pump_until(lambda: not uploader._pending)
    assert uploader.tex_id_for(tex) is None, "evicted-in-flight texture must not be published"

    uploader.shutdown()
    assert gl.deleted == [1], "in-flight texture must be deleted after upload"


def test_shutdown_joins_worker_thread():
    gl = FakeGL()
    uploader = TextureUploader(gl=gl)
    uploader._start_worker(FakeCtx())
    uploader.shutdown()
    assert not uploader._using_worker
