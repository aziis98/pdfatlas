from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

DEBOUNCE_MS = 150


class SearchHeaderEntry(Gtk.Box):
    """
    Search entry widget designed for the window HeaderBar, managing
    debounced input and activation signals.
    """

    def __init__(
        self,
        on_query_changed: Callable[[str], None] | None = None,
        on_activate: Callable[[str], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.on_query_changed = on_query_changed
        self.on_activate = on_activate
        self._debounce_source_id: int | None = None

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Search document...")
        self.entry.set_size_request(240, -1)
        self.entry.connect("search-changed", self._on_entry_changed)
        self.entry.connect("activate", self._on_entry_activate)
        self.entry.connect("stop-search", self._on_stop_search)
        self.append(self.entry)

    def _on_entry_changed(self, entry: Gtk.SearchEntry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
        self._debounce_source_id = GLib.timeout_add(DEBOUNCE_MS, self._debounced_fire)

    def _debounced_fire(self):
        self._debounce_source_id = None
        if self.on_query_changed:
            self.on_query_changed(self.get_text())
        return False

    def _on_entry_activate(self, entry: Gtk.SearchEntry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        if self.on_activate:
            self.on_activate(self.get_text())
        elif self.on_query_changed:
            self.on_query_changed(self.get_text())

    def _on_stop_search(self, entry: Gtk.SearchEntry):
        self.clear()

    def get_text(self) -> str:
        return self.entry.get_text().strip()

    def set_text(self, text: str):
        self.entry.set_text(text)

    def clear(self):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        self.entry.set_text("")

    def focus(self):
        self.entry.grab_focus()

    def select_all(self):
        self.entry.select_region(0, -1)

    def set_sensitive(self, sensitive: bool):
        self.entry.set_sensitive(sensitive)

    def set_placeholder_text(self, text: str):
        self.entry.set_placeholder_text(text)
