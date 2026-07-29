from abc import ABC, abstractmethod
from typing import Any
from gi.repository import Gtk


class GtkComponent(ABC):
    """
    A single unified interface for composable GTK UI components used in collections (such as overlay_components).
    """

    @abstractmethod
    def build_widget(self) -> Gtk.Widget:
        """Constructs and returns the top-level Gtk.Widget."""
        pass

    def update_state(self, state: dict[str, Any]) -> None:
        """Optional state update callback."""
        pass
