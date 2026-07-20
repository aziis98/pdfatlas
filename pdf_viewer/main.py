import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from .ui.window import MainWindow


class PDFViewerApplication(Adw.Application):
    """
    Main Adw.Application entry point for the PDF viewer.
    Handles startup, activation, and loading initial command-line documents.
    """

    def __init__(self):
        super().__init__(application_id="org.antigravity.pdfviewer", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.filepath_to_open: str | None = None
        self.backend: str = "opengl"
        self.state: str | None = None
        self.screenshot: str | None = None
        self.follow_link: int | None = None

    def do_activate(self):
        # Create and present the main application window
        backend = getattr(self, "backend", "opengl")
        state = getattr(self, "state", None)
        screenshot = getattr(self, "screenshot", None)
        follow_link = getattr(self, "follow_link", None)

        win = MainWindow(
            self,
            backend=backend,
            state=state,
            screenshot_path=screenshot,
            follow_link=follow_link,
        )
        win.present()

        # Load document if passed via command line
        if self.filepath_to_open:
            win.open_document(self.filepath_to_open)

    def do_startup(self):
        Adw.Application.do_startup(self)


def main():
    import argparse
    import os
    import shutil
    import subprocess

    parser = argparse.ArgumentParser(description="PDF Reader with Portals & FTS5 Search")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file to open")
    parser.add_argument("--backend", choices=["cairo", "opengl"], default="opengl", help="Rendering backend")
    parser.add_argument("--state", default=None, help="Initial application state as a JSON string")
    parser.add_argument("--screenshot", default=None, help="Path to save window screenshot after 2 seconds")
    parser.add_argument("--follow-link", type=int, default=None, help="Index of N-th link in document to follow on open")
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
    app.backend = args.backend
    app.state = args.state
    app.screenshot = args.screenshot
    app.follow_link = args.follow_link

    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()
