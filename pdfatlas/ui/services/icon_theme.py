import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk


# Discovered once per process: the filesystem walk over /usr/share/icons is
# comparatively expensive (~50 ms) and the result never changes at runtime.
_ICON_SEARCH_PATHS: list[str] | None = None


def _discover_icon_search_paths() -> list[str]:
    """Return icon theme directories containing the app's custom symbolic icons."""
    global _ICON_SEARCH_PATHS
    if _ICON_SEARCH_PATHS is not None:
        return _ICON_SEARCH_PATHS

    icon_roots = [
        "/usr/share/icons",
        "/usr/local/share/icons",
        os.path.expanduser("~/.local/share/icons"),
        os.path.expanduser("~/.icons"),
    ]
    target_icons = {"map-symbolic.svg", "image-crop-symbolic.svg", "crop-symbolic.svg"}

    paths: list[str] = []
    seen: set[str] = set()
    for root in icon_roots:
        if not os.path.exists(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if dirpath in seen:
                continue
            if any(filename in target_icons for filename in filenames):
                paths.append(dirpath)
                seen.add(dirpath)

    _ICON_SEARCH_PATHS = paths
    return paths


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

        from ...core.resources import get_assets_dir
        assets_dir = str(get_assets_dir())
        if os.path.exists(assets_dir):
            theme.add_search_path(assets_dir)

        user_icons = os.path.expanduser("~/.local/share/icons")
        if os.path.exists(user_icons):
            theme.add_search_path(user_icons)

        icon_name = IconThemeManager.get_app_icon_name(theme)
        Gtk.Window.set_default_icon_name(icon_name)
        window.set_icon_name(icon_name)

        for dirpath in _discover_icon_search_paths():
            theme.add_search_path(dirpath)
