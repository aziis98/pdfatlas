from abc import ABC, abstractmethod
from typing import Any
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class CanvasRenderer(ABC):
    """
    Abstract polymorphic interface for PDF Canvas rendering engines.
    """

    @abstractmethod
    def initialize(self, canvas_widget: Gtk.Widget) -> None:
        """Initializes the renderer with the canvas widget."""
        pass

    @abstractmethod
    def redraw(self) -> None:
        """Queues a redraw of the canvas."""
        pass

    @abstractmethod
    def get_debug_info(self) -> dict[str, Any]:
        """Returns rendering metrics for debug display."""
        pass
