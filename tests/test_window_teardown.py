"""Regression tests for window/worker lifecycle teardown.

Before this fix every ``MainWindow`` created its own ``RenderWorker`` (two
child processes plus a 256 MB shared-memory pool) and nothing shut it down when
the window closed, so a long-running master accumulated leaked children, SHM
segments and threads until it became sluggish.
"""

import atexit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from pdfatlas.core.renderer import RenderWorker
from pdfatlas.ui.window import MainWindow


class _SharedWorkerApp(Adw.Application):
    """Application exposing the production ``get_render_worker`` factory."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.worker: RenderWorker | None = None

    def get_render_worker(self) -> RenderWorker:
        if self.worker is None:
            self.worker = RenderWorker(num_workers=1, use_shm=False)
        return self.worker


def test_window_close_shuts_down_owned_render_worker():
    app = Adw.Application(application_id="com.example.testteardown1")
    app.register(None)

    win = MainWindow(app)
    worker = win.render_worker
    assert win._owns_render_worker
    assert isinstance(worker, RenderWorker)
    assert not worker._stop.is_set()

    # ``Gtk.Window.close()`` is a no-op for windows that were never shown, so
    # emit the signal the window manager would emit (avoids popping a window
    # on the developer's desktop during tests).
    win.emit("close-request")

    assert worker._stop.is_set()
    assert win.render_worker is None


def test_windows_share_app_level_worker_and_do_not_own_it():
    app = _SharedWorkerApp(application_id="com.example.testteardown2")
    app.register(None)

    win1 = MainWindow(app)
    win2 = MainWindow(app)
    try:
        assert not win1._owns_render_worker
        assert not win2._owns_render_worker
        assert win1.render_worker is app.worker
        assert win2.render_worker is app.worker

        # Closing one window must not tear down the process-wide worker.
        win1.emit("close-request")
        assert app.worker is not None
        assert not app.worker._stop.is_set()
        assert win2.render_worker is app.worker
    finally:
        win2.emit("close-request")
        if app.worker is not None:
            app.worker.shutdown()


def test_app_shutdown_shuts_down_shared_worker():
    from pdfatlas.main import PDFViewerApplication

    app = PDFViewerApplication(
        application_id="com.example.testteardown3", use_shm=False
    )
    worker = app.get_render_worker()
    assert app.get_render_worker() is worker

    app.do_shutdown()

    assert worker._stop.is_set()
    assert app._render_worker is None


def test_render_worker_shutdown_unregisters_atexit_hook():
    ncallbacks = getattr(atexit, "_ncallbacks")

    before = ncallbacks()
    worker = RenderWorker(num_workers=1, use_shm=False)
    assert ncallbacks() == before + 1

    worker.shutdown()

    # The worker must not be kept alive (and re-shut-down at interpreter exit)
    # by a stale atexit registration.
    assert ncallbacks() == before
