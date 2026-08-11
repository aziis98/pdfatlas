import multiprocessing
import os
import sys

# Spawn children (RenderWorker) re-import this module during multiprocessing's
# child preparation, before the bootstrap reaches the target function. Their
# stderr is redirected into the log chosen by the parent (see
# ``RenderWorker.__init__``) so a crash in the spawn/import chain (e.g.
# WebKitGTK) is diagnosable instead of "child processes keep dying (unexpected
# death)". ``current_process()._inheriting`` is set by ``spawn_main.prepare()``
# only while the child is importing the main module; the app's own import of
# this module never sees it set.
if getattr(multiprocessing.current_process(), "_inheriting", False):
    # Ctrl+C (SIGINT) goes to the whole foreground process group, including
    # render children; their Python-level handler raises KeyboardInterrupt and
    # kills them mid-import or mid-loop, which the parent misreads as an
    # unexpected crash and respawns (queue/semaphore leak + "giving up" spam).
    # The parent owns child lifecycles (terminate() on shutdown), so children
    # ignore SIGINT entirely (see RESEARCH.md §1.23).
    import signal as _signal

    _signal.signal(_signal.SIGINT, _signal.SIG_IGN)
    _child_log = os.environ.get("PDFATLAS_CHILD_STDERR_LOG")
    if _child_log:
        try:
            import time as _time

            _f = open(_child_log, "a", buffering=1)
            sys.stderr = _f
            sys.stdout = _f
            _f.write(f"\n=== render child pid={os.getpid()} at {_time.time():.3f} ===\n")
        except Exception:
            pass

# WebKitGTK must run without its compositing mode (unstable on some GPUs);
# set before any gi/GTK initialization (see RESEARCH.md §1.21).
os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

import gi
from gi.repository import GLib

# Set process name & application ID before GTK initializes for Wayland window matching
GLib.set_prgname("com.aziis98.pdfatlas")
GLib.set_application_name("PDF Atlas")

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from .core.pdf_source import PdfSource
from .ui.window import MainWindow



class PDFViewerApplication(Adw.Application):
    """
    Main Adw.Application entry point for the PDF viewer.
    Handles startup, activation, and loading initial command-line documents.
    """

    def __init__(self):
        super().__init__(application_id="com.aziis98.pdfatlas", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.filepath_to_open: str | None = None
        self.state: str | None = None
        self.screenshot: str | None = None
        self.follow_link: int | None = None
        self.debug: bool = False
        self.render_mode: str = "mp"
        self.render_workers: int = 2

    def do_activate(self):
        from .core.installation import get_installation_mode_info
        mode, reason = get_installation_mode_info()
        print(f"[PDFAtlas] Startup mode: '{mode}' ({reason})", flush=True)

        # Create and present the main application window
        state = getattr(self, "state", None)
        screenshot = getattr(self, "screenshot", None)
        follow_link = getattr(self, "follow_link", None)
        debug_mode = getattr(self, "debug", False)

        win = MainWindow(
            self,
            state=state,
            screenshot_path=screenshot,
            follow_link=follow_link,
            debug_mode=debug_mode,
            render_mode=self.render_mode,
            render_workers=self.render_workers,
        )
        win.present()

        # Load document if passed via command line
        if self.filepath_to_open:
            raw_arg = self.filepath_to_open.strip()
            from .core.arxiv_mapper import arxiv_id_from_path, extract_arxiv_id_from_raw

            aid = extract_arxiv_id_from_raw(raw_arg) or arxiv_id_from_path(raw_arg)
            existing = win.recent_files.get_by_uri(raw_arg)
            if not existing and aid:
                existing = win.recent_files.get_by_arxiv_id(aid)

            if existing:
                source = existing
            elif aid and not os.path.exists(raw_arg):
                source = PdfSource(
                    source_type="arxiv",
                    uri=raw_arg,
                    display_name=f"arXiv:{aid}",
                )
            elif aid:
                source = PdfSource(
                    source_type="arxiv",
                    uri=raw_arg,
                    display_name=f"arXiv:{aid}",
                )
            else:
                source = PdfSource(
                    source_type="file",
                    uri=raw_arg,
                    display_name=os.path.basename(raw_arg),
                )
            win.open_document(source)


    def do_startup(self):
        Adw.Application.do_startup(self)


def main():
    import argparse
    import os
    import shutil
    import subprocess

    parser = argparse.ArgumentParser(description="PDF Reader with Portals & FTS5 Search")

    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file to open")
    parser.add_argument("--state", default=None, help="Initial application state as a JSON string")
    parser.add_argument("--screenshot", default=None, help="Path to save window screenshot after 2 seconds")
    parser.add_argument("--follow-link", type=int, default=None, help="Index of N-th link in document to follow on open")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay for page layout values")
    parser.add_argument(
        "--render-mode",
        choices=["mt", "mp"],
        default="mp",
        help="Rasterization backend: 'mp' = multiprocessing child processes (default), 'mt' = multithreaded",
    )
    parser.add_argument(
        "--render-workers",
        type=int,
        default=2,
        help="Number of parallel rasterization workers (default: 2)",
    )
    parser.add_argument("--headless", action="store_true", help="Run inside a virtual display using xvfb-run if available")

    args = parser.parse_args(sys.argv[1:])

    if args.headless and not os.environ.get("XVFB_RUNNING"):
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            os.environ["XVFB_RUNNING"] = "1"
            cmd = [xvfb, "-a", sys.executable] + sys.argv
            sys.exit(subprocess.call(cmd))

    app = PDFViewerApplication()
    app.filepath_to_open = args.pdf_path
    app.state = args.state
    app.screenshot = args.screenshot
    app.follow_link = args.follow_link
    app.debug = args.debug
    app.render_mode = args.render_mode
    app.render_workers = args.render_workers

    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()

