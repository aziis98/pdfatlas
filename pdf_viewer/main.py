import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib

from .ui.window import MainWindow


class PDFViewerApplication(Adw.Application):
    """
    Main Adw.Application entry point for the PDF viewer.
    Handles startup, activation, and loading initial command-line documents.
    """

    def __init__(self):
        super().__init__(application_id="com.aziis98.pdfatlas", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.filepath_to_open: str | None = None
        self.backend: str = "opengl"
        self.state: str | None = None
        self.screenshot: str | None = None
        self.follow_link: int | None = None
        self.debug: bool = False

    def do_activate(self):
        # Create and present the main application window
        backend = getattr(self, "backend", "opengl")
        state = getattr(self, "state", None)
        screenshot = getattr(self, "screenshot", None)
        follow_link = getattr(self, "follow_link", None)
        debug_mode = getattr(self, "debug", False)

        win = MainWindow(
            self,
            backend=backend,
            state=state,
            screenshot_path=screenshot,
            follow_link=follow_link,
            debug_mode=debug_mode,
        )
        win.present()

        # Load document if passed via command line
        if self.filepath_to_open:
            win.open_document(self.filepath_to_open)

    def do_startup(self):
        Adw.Application.do_startup(self)


def ensure_app_icons_installed():
    """
    Ensures application icon symlinks and .desktop files are installed in the user's
    local share environment before GTK initializes. This allows GNOME Shell / Wayland
    compositors to map window decorations and Alt-Tab switcher icons to assets/logo.png.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(base_dir, "assets", "logo.png")
        if not os.path.exists(logo_path):
            return

        icon_names = ["com.aziis98.pdfatlas.png"]
        sizes = ["512x512", "scalable"]

        user_home = os.path.expanduser("~")
        for size in sizes:
            apps_dir = os.path.join(user_home, ".local", "share", "icons", "hicolor", size, "apps")
            os.makedirs(apps_dir, exist_ok=True)
            for icon_name in icon_names:
                target_symlink = os.path.join(apps_dir, icon_name)
                if os.path.islink(target_symlink) or os.path.exists(target_symlink):
                    try:
                        if os.path.islink(target_symlink) and os.readlink(target_symlink) == logo_path:
                            continue
                        os.remove(target_symlink)
                    except Exception:
                        pass
                try:
                    os.symlink(logo_path, target_symlink)
                except Exception:
                    pass

        # Register .desktop file for GNOME Shell window manager association
        desktop_dir = os.path.join(user_home, ".local", "share", "applications")
        os.makedirs(desktop_dir, exist_ok=True)
        desktop_file = os.path.join(desktop_dir, "com.aziis98.pdfatlas.desktop")
        desktop_contents = f"""[Desktop Entry]
Name=PDF Atlas
Comment=PDF Viewer with Portals & FTS5 Search
Exec=env PYTHONPATH={base_dir} {sys.executable} -m pdf_viewer.main %f
Path={base_dir}
Icon=com.aziis98.pdfatlas
Terminal=false
Type=Application
Categories=Office;Viewer;
MimeType=application/pdf;
StartupWMClass=com.aziis98.pdfatlas
"""
        should_write = True
        if os.path.exists(desktop_file):
            try:
                with open(desktop_file, "r", encoding="utf-8") as f:
                    if f.read() == desktop_contents:
                        should_write = False
            except Exception:
                pass

        if should_write:
            with open(desktop_file, "w", encoding="utf-8") as f:
                f.write(desktop_contents)
            
        import shutil
        import subprocess
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", desktop_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if shutil.which("gtk-update-icon-cache"):
            hicolor_dir = os.path.join(user_home, ".local", "share", "icons", "hicolor")
            subprocess.run(["gtk-update-icon-cache", "-f", "-t", hicolor_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    import argparse
    import os
    import shutil
    import subprocess

    # Set process name & application ID before GTK initializes for Wayland window matching
    GLib.set_prgname("com.aziis98.pdfatlas")
    GLib.set_application_name("PDF Atlas")

    # Automatically install application icon symlinks before loading GTK
    ensure_app_icons_installed()

    parser = argparse.ArgumentParser(description="PDF Reader with Portals & FTS5 Search")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file to open")
    parser.add_argument("--backend", choices=["cairo", "opengl"], default="opengl", help="Rendering backend")
    parser.add_argument("--state", default=None, help="Initial application state as a JSON string")
    parser.add_argument("--screenshot", default=None, help="Path to save window screenshot after 2 seconds")
    parser.add_argument("--follow-link", type=int, default=None, help="Index of N-th link in document to follow on open")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay for page layout values")
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
    app.debug = args.debug

    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()

