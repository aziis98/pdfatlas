import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..core.resources import get_assets_dir


def load_window_css() -> Gtk.CssProvider:
    """Load the shared window CSS into a new Gtk.CssProvider."""
    provider = Gtk.CssProvider()
    css_path = get_assets_dir() / "window.css"
    provider.load_from_path(str(css_path))
    return provider
