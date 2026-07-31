import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk


class IconThemeManager:
    """
    Manages GTK icon theme path registration and custom icon discovery.
    """

    @staticmethod
    def get_app_icon_name(theme: Gtk.IconTheme | None = None) -> str:
        if theme is None:
            display = Gdk.Display.get_default()
            if display:
                theme = Gtk.IconTheme.get_for_display(display)
        if theme:
            for candidate in ["com-aziis98-pdfatlas", "com.aziis98.pdfatlas", "logo"]:
                if theme.has_icon(candidate):
                    return candidate
        return "com-aziis98-pdfatlas"

    @staticmethod
    def setup_system_icons(window: Gtk.Window) -> None:
        display = Gdk.Display.get_default()
        if not display:
            return
        theme = Gtk.IconTheme.get_for_display(display)

        assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets"))
        if os.path.exists(assets_dir):
            theme.add_search_path(assets_dir)

        user_icons = os.path.expanduser("~/.local/share/icons")
        if os.path.exists(user_icons):
            theme.add_search_path(user_icons)

        icon_name = IconThemeManager.get_app_icon_name(theme)
        Gtk.Window.set_default_icon_name(icon_name)
        window.set_icon_name(icon_name)

        icon_roots = [
            "/usr/share/icons",
            "/usr/local/share/icons",
            os.path.expanduser("~/.local/share/icons"),
            os.path.expanduser("~/.icons"),
        ]

        added_paths = set()
        target_icons = {"map-symbolic.svg", "image-crop-symbolic.svg", "crop-symbolic.svg"}

        for root in icon_roots:
            if not os.path.exists(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if filename in target_icons and dirpath not in added_paths:
                        theme.add_search_path(dirpath)
                        added_paths.add(dirpath)
