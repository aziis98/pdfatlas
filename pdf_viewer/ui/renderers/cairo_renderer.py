from typing import Any
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .base import CanvasRenderer


class CairoCanvasRenderer(CanvasRenderer):
    """
    Software 2D Cairo rendering backend.
    """

    def __init__(self, canvas_widget: Gtk.Widget):
        self.canvas_widget = canvas_widget

    def initialize(self, canvas_widget: Gtk.Widget) -> None:
        self.canvas_widget = canvas_widget

    def redraw(self) -> None:
        if self.canvas_widget:
            self.canvas_widget.queue_draw()

    def get_debug_info(self) -> dict[str, Any]:
        return {"backend": "cairo"}
