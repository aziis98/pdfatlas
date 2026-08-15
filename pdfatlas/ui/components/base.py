from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from gi.repository import Gtk

S = TypeVar("S")


class GtkComponent(ABC, Generic[S]):
    """
    A single unified interface for composable GTK UI components parameterized by their state type S.
    """

    @abstractmethod
    def build_widget(self) -> Gtk.Widget:
        """Constructs and returns the top-level Gtk.Widget."""
        pass

    def update_state(self, state: S) -> None:
        """State update callback."""
        pass
