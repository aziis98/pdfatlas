import os
import sys

from .core.process_utils import init_child_process_prelude, is_dev_mode

# Early initialization for multiprocessing spawn workers before GTK/WebKit imports
init_child_process_prelude()

# WebKitGTK must run without its compositing mode (unstable on some GPUs);
# set before any gi/GTK initialization (see RESEARCH.md §1.21).
os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

import gi
from gi.repository import GLib


def _init_prgname() -> None:
    app_id = None
    if "--app-id" in sys.argv:
        try:
            idx = sys.argv.index("--app-id")
            if idx + 1 < len(sys.argv):
                app_id = sys.argv[idx + 1]
        except ValueError:
            pass

    if app_id is None:
        if is_dev_mode() or "--debug" in sys.argv:
            app_id = "com.aziis98.pdfatlas.dev"
            app_name = "PDF Atlas (Dev)"
        else:
            app_id = "com.aziis98.pdfatlas"
            app_name = "PDF Atlas"
    else:
        app_name = f"PDF Atlas ({app_id.split('.')[-1]})"

    GLib.set_prgname(app_id)
    GLib.set_application_name(app_name)


_init_prgname()

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from .core.pdf_source import PdfSource
from .ui.window import MainWindow



class PDFViewerApplication(Adw.Application):
    """
    Main Adw.Application entry point for the PDF viewer.
    Handles startup, activation, and loading initial/subsequent documents as tabs
    within a single process.
    """

    def __init__(
        self,
        filepath_to_open: str | None = None,
        state: str | None = None,
        follow_link: int | None = None,
        debug: bool = False,
        debug_note_rect: bool = False,
        render_workers: int = 2,
        use_shm: bool = True,
        application_id: str | None = None,
        flags: Gio.ApplicationFlags | None = None,
    ):
        if application_id is None:
            application_id = "com.aziis98.pdfatlas.dev" if is_dev_mode() else "com.aziis98.pdfatlas"
        if flags is None:
            flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE

        super().__init__(
            application_id=application_id,
            flags=flags,
        )
        self.filepath_to_open = filepath_to_open
        self.state = state
        self.follow_link = follow_link
        self.debug = debug
        self.debug_note_rect = debug_note_rect
        self.render_workers = render_workers
        self.use_shm = use_shm
        self.connect("window-removed", self._on_window_removed)

    def _on_window_removed(self, app, win):
        if not app.get_windows():
            self.quit()

    def _resolve_source(self, win: MainWindow | None, raw_arg: str, cwd: str | None = None) -> PdfSource:
        from .core.arxiv_mapper import arxiv_id_from_path, extract_arxiv_id_from_raw
        from .core.pdf_source import RecentFilesManager

        raw_str = raw_arg.strip()
        aid = extract_arxiv_id_from_raw(raw_str) or arxiv_id_from_path(raw_str)

        if raw_str.startswith(("http://", "https://")):
            expanded = raw_str
        else:
            expanded_candidate = (
                os.path.normpath(os.path.join(cwd, os.path.expanduser(raw_str)))
                if cwd and not os.path.isabs(os.path.expanduser(raw_str))
                else os.path.abspath(os.path.expanduser(raw_str))
            )
            expanded = expanded_candidate if os.path.exists(expanded_candidate) else raw_str

        recent_files = win.recent_files if win is not None else RecentFilesManager()
        existing = recent_files.get_by_uri(expanded) or recent_files.get_by_uri(raw_str)
        if not existing and aid:
            existing = recent_files.get_by_arxiv_id(aid)

        if existing:
            return existing
        elif not raw_str.startswith(("http://", "https://")) and os.path.exists(expanded):
            return PdfSource(
                source_type="file",
                uri=expanded,
                display_name=os.path.basename(expanded),
            )
        elif aid:
            return PdfSource(
                source_type="arxiv",
                uri=raw_str,
                display_name=f"arXiv:{aid}",
            )
        elif raw_str.startswith(("http://", "https://")):
            url_path = raw_str.split("?")[0].split("#")[0]
            display = os.path.basename(url_path) or raw_str
            return PdfSource(
                source_type="url",
                uri=raw_str,
                display_name=display,
            )
        else:
            return PdfSource(
                source_type="file",
                uri=expanded,
                display_name=os.path.basename(expanded),
            )

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        raw_args = command_line.get_arguments()[1:]
        cwd = command_line.get_cwd()

        raw_target = None
        skip_next = False
        for a in raw_args:
            if skip_next:
                skip_next = False
                continue
            if a in ("--state", "--follow-link", "--render-workers"):
                skip_next = True
                continue
            if a.startswith("--") or a.startswith("-"):
                continue
            raw_target = a
            break

        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        active_win = self.get_active_window()
        win = active_win if isinstance(active_win, MainWindow) else (windows[0] if windows else None)

        target_to_open = raw_target if raw_target else self.filepath_to_open

        if target_to_open:
            # If an existing window is empty (on welcome screen with no doc), reuse it;
            # otherwise, always open in a new window.
            if win and win.doc_model is None:
                target_win = win
                source = self._resolve_source(target_win, target_to_open.strip(), cwd=cwd)
                target_win.open_document(source, new_tab=False)
                target_win.present()
            else:
                from .core.installation import get_installation_mode_info
                mode, reason = get_installation_mode_info()
                print(f"[PDFAtlas] Startup mode: '{mode}' ({reason})", flush=True)

                source = self._resolve_source(None, target_to_open.strip(), cwd=cwd)
                target_win = MainWindow(
                    self,
                    initial_source=source,
                    state=self.state,
                    follow_link=self.follow_link,
                    debug_mode=self.debug,
                    debug_note_rect=self.debug_note_rect,
                    render_workers=self.render_workers,
                    use_shm=self.use_shm,
                )
                target_win.present()
        else:
            if not win:
                from .core.installation import get_installation_mode_info
                mode, reason = get_installation_mode_info()
                print(f"[PDFAtlas] Startup mode: '{mode}' ({reason})", flush=True)

                win = MainWindow(
                    self,
                    state=self.state,
                    follow_link=self.follow_link,
                    debug_mode=self.debug,
                    debug_note_rect=self.debug_note_rect,
                    render_workers=self.render_workers,
                    use_shm=self.use_shm,
                )
            win.present()

        return 0

    def do_activate(self):
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows and not self.filepath_to_open:
            windows[0].present()
            return

        win = windows[0] if (windows and windows[0].doc_model is None) else None
        if not win:
            from .core.installation import get_installation_mode_info
            mode, reason = get_installation_mode_info()
            print(f"[PDFAtlas] Startup mode: '{mode}' ({reason})", flush=True)

            source = self._resolve_source(None, self.filepath_to_open.strip()) if self.filepath_to_open else None
            win = MainWindow(
                self,
                initial_source=source,
                state=self.state,
                follow_link=self.follow_link,
                debug_mode=self.debug,
                debug_note_rect=self.debug_note_rect,
                render_workers=self.render_workers,
                use_shm=self.use_shm,
            )
            win.present()
        else:
            if self.filepath_to_open:
                source = self._resolve_source(win, self.filepath_to_open.strip())
                win.open_document(source, new_tab=False)
            win.present()

    def do_startup(self):
        Adw.Application.do_startup(self)


def main():
    import argparse
    import os
    import shutil
    import subprocess

    from ._version import get_version

    parser = argparse.ArgumentParser(prog="pdfatlas", description="PDF Reader with Portals & FTS5 Search")

    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"%(prog)s {get_version()}",
        help="Show program's version number and exit",
    )
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file to open")
    parser.add_argument("--state", default=None, help="Initial application state as a JSON string")
    parser.add_argument("--follow-link", type=int, default=None, help="Index of N-th link in document to follow on open")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay for page layout values")
    parser.add_argument("--debug-note-rect", action="store_true",
                        help="Draw a red overlay at the note preview anchor rect")
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
    parser.add_argument(
        "--new-instance",
        action="store_true",
        help="Launch a standalone process without single-instance D-Bus IPC forwarding",
    )
    parser.add_argument(
        "--app-id",
        type=str,
        default=None,
        help="Custom D-Bus application ID",
    )

    args = parser.parse_args(sys.argv[1:])

    if args.headless and not os.environ.get("XVFB_RUNNING"):
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            os.environ["XVFB_RUNNING"] = "1"
            cmd = [xvfb, "-a", sys.executable] + sys.argv
            sys.exit(subprocess.call(cmd))

    app_flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
    if args.new_instance:
        app_flags |= Gio.ApplicationFlags.NON_UNIQUE

    app = PDFViewerApplication(
        filepath_to_open=args.pdf_path,
        state=args.state,
        follow_link=args.follow_link,
        debug=args.debug,
        debug_note_rect=args.debug_note_rect,
        render_workers=args.render_workers,
        use_shm=args.use_shm,
        application_id=args.app_id,
        flags=app_flags,
    )

    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()

