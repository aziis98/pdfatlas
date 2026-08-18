import os
import sys

from .core.process_utils import init_child_process_prelude

# Early initialization for multiprocessing spawn workers before GTK/WebKit imports
init_child_process_prelude()

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

    def __init__(
        self,
        filepath_to_open: str | None = None,
        state: str | None = None,
        follow_link: int | None = None,
        debug: bool = False,
        debug_note_rect: bool = False,
        render_mode: str = "mp",
        render_workers: int = 2,
        use_shm: bool = True,
    ):
        super().__init__(application_id="com.aziis98.pdfatlas", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.filepath_to_open = filepath_to_open
        self.state = state
        self.follow_link = follow_link
        self.debug = debug
        self.debug_note_rect = debug_note_rect
        self.render_mode = render_mode
        self.render_workers = render_workers
        self.use_shm = use_shm

    def do_activate(self):
        from .core.installation import get_installation_mode_info
        mode, reason = get_installation_mode_info()
        print(f"[PDFAtlas] Startup mode: '{mode}' ({reason})", flush=True)

        # Create and present the main application window
        win = MainWindow(
            self,
            state=self.state,
            follow_link=self.follow_link,
            debug_mode=self.debug,
            debug_note_rect=self.debug_note_rect,
            render_mode=self.render_mode,
            render_workers=self.render_workers,
            use_shm=self.use_shm,
        )
        win.present()

        # Load document if passed via command line
        if self.filepath_to_open:
            raw_arg = self.filepath_to_open.strip()
            from .core.arxiv_mapper import arxiv_id_from_path, extract_arxiv_id_from_raw

            expanded = os.path.abspath(os.path.expanduser(raw_arg)) if os.path.exists(os.path.expanduser(raw_arg)) else raw_arg
            aid = extract_arxiv_id_from_raw(raw_arg) or arxiv_id_from_path(raw_arg)
            existing = win.recent_files.get_by_uri(expanded) or win.recent_files.get_by_uri(raw_arg)
            if not existing and aid:
                existing = win.recent_files.get_by_arxiv_id(aid)

            if existing:
                source = existing
            elif os.path.exists(os.path.expanduser(raw_arg)):
                source = PdfSource(
                    source_type="file",
                    uri=expanded,
                    display_name=os.path.basename(expanded),
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
    parser.add_argument("--follow-link", type=int, default=None, help="Index of N-th link in document to follow on open")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay for page layout values")
    parser.add_argument("--debug-note-rect", action="store_true",
                        help="Draw a red overlay at the note preview anchor rect")
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
    parser.add_argument(
        "--no-shm",
        action="store_false",
        dest="use_shm",
        default=True,
        help="Disable zero-copy shared memory IPC for multiprocessing render backend",
    )
    parser.add_argument("--headless", action="store_true", help="Run inside a virtual display using xvfb-run if available")

    args = parser.parse_args(sys.argv[1:])

    if args.headless and not os.environ.get("XVFB_RUNNING"):
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            os.environ["XVFB_RUNNING"] = "1"
            cmd = [xvfb, "-a", sys.executable] + sys.argv
            sys.exit(subprocess.call(cmd))

    app = PDFViewerApplication(
        filepath_to_open=args.pdf_path,
        state=args.state,
        follow_link=args.follow_link,
        debug=args.debug,
        debug_note_rect=args.debug_note_rect,
        render_mode=args.render_mode,
        render_workers=args.render_workers,
        use_shm=args.use_shm,
    )

    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()

