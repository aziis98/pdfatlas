import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
_WINDOW_CSS_PATH = os.path.join(_ASSETS_DIR, "window.css")


def load_window_css() -> Gtk.CssProvider:
    """Load the shared window CSS into a new Gtk.CssProvider."""
    provider = Gtk.CssProvider()
    provider.load_from_path(_WINDOW_CSS_PATH)
    return provider
