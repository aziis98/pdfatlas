from __future__ import annotations

import string
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

import pymupdf as fitz
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from ..core.search_provider import SearchProvider, SearchResult
from ..ui.components.search_header_entry import SearchHeaderEntry
from ..ui.components.search_results_view import SearchResultsView

if TYPE_CHECKING:
    from ..ui.window import MainWindow


def clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(val, max_val))


class SearchCoordinator:
    """
    Coordinates between SearchHeaderEntry, SearchProvider, and SearchResultsView.
    """

    def __init__(
        self,
        header_entry: SearchHeaderEntry,
        results_view: SearchResultsView,
        provider_getter: Callable[[], SearchProvider | None],
        on_result_activated: Callable[[dict, set[str]], None],
        on_view_changed: Callable[[str], None],
        get_active_filepath: Callable[[], str],
        get_is_grid: Callable[[], bool],
    ):
        self.header_entry = header_entry
        self.results_view = results_view
        self.provider_getter = provider_getter
        self.on_result_activated = on_result_activated
        self.on_view_changed = on_view_changed
        self.get_active_filepath = get_active_filepath
        self.get_is_grid = get_is_grid

        self._current_search_id = 0
        self._last_query = ""

        # Connect entry callbacks
        self.header_entry.on_query_changed = self.run_search
        self.header_entry.on_activate = self.run_search

        # Connect results view callbacks
        self.results_view.on_row_clicked = self.on_result_activated
        self.results_view.on_toggle_pin = self._on_toggle_pin

    def _on_toggle_pin(self, result: dict, query_terms: set[str], pinned: bool):
        self.run_search(self.header_entry.get_text())

    def run_search(self, query: str):
        query = (query or "").strip()
        if not query:
            self.results_view.clear()
            self._last_query = ""
            self.on_view_changed("document-view")
            return

        self._current_search_id += 1
        search_id = self._current_search_id
        self._last_query = query

        self.results_view.reset_executor()
        self.results_view.reset_scroll()
        self.on_view_changed("search-view")

        provider = self.provider_getter()
        if not provider:
            self.results_view.render_results(
                [],
                query=query,
                active_filepath=self.get_active_filepath(),
                is_grid=self.get_is_grid(),
            )
            return

        def _on_results(results: list[SearchResult], sid: int):
            if sid != self._current_search_id:
                return
            raw_dicts = [r.to_dict() for r in results]
            self.results_view.render_results(
                raw_dicts,
                query=query,
                active_filepath=self.get_active_filepath(),
                is_grid=self.get_is_grid(),
            )

        provider.search(query, limit=30, search_id=search_id, on_results=_on_results)


class SearchState(Enum):
    """Explicit state machine states for the window search bar."""

    IDLE = auto()
    EDITING = auto()
    SEARCH_VIEW = auto()
    RESULT_ACTIVE = auto()


class SearchController:
    """Controller managing the search bar state machine and search operations."""

    def __init__(self, main_window: MainWindow):
        self.win = main_window
        self.state: SearchState = SearchState.IDLE
        self._debounce_source_id: int | None = None
        self._last_query = ""
        self._current_search_id = 0
        self._suppress_changed = False

    @property
    def last_query(self) -> str:
        return self._last_query

    def attach_entry_signals(self, entry: Gtk.SearchEntry) -> None:
        """Wire focus, clear-button, and stop-search signals on the header SearchEntry."""
        entry.connect("search-changed", self.on_search_changed_debounced)
        entry.connect("activate", self.on_activate_immediate)
        entry.connect("stop-search", lambda _e: self.on_escape())

        focus_ctrl = Gtk.EventControllerFocus.new()
        focus_ctrl.connect("enter", self._on_entry_focus_enter)
        focus_ctrl.connect("leave", self._on_entry_focus_leave)
        entry.add_controller(focus_ctrl)

        # Gtk.SearchEntry's trailing Gtk.Image is its clear ("×") button.
        # Listen on release so clicking Clear immediately exits search and unfocuses.
        clear_icon = self._find_clear_icon(entry)
        if clear_icon is not None:
            click = Gtk.GestureClick.new()
            click.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
            click.connect("released", lambda _g, _n, _x, _y: self.on_clear_clicked())
            clear_icon.add_controller(click)

    @staticmethod
    def _find_clear_icon(entry: Gtk.SearchEntry) -> Gtk.Widget | None:
        images: list[Gtk.Widget] = []
        child = entry.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Image):
                images.append(child)
            child = child.get_next_sibling()
        return images[-1] if len(images) >= 2 else None

    def _on_entry_focus_enter(self, _ctrl) -> None:
        if self.state in (SearchState.IDLE, SearchState.RESULT_ACTIVE):
            self.state = SearchState.EDITING

    def _on_entry_focus_leave(self, _ctrl) -> None:
        if self.state == SearchState.EDITING:
            self.state = SearchState.IDLE

    def is_entry_focused(self) -> bool:
        """Return True when the search entry or its internal Gtk.Text holds focus."""
        entry = getattr(self.win, "entry", None)
        if entry is None:
            return False
        if entry.has_focus():
            return True
        focus = self.win.get_focus()
        w: Gtk.Widget | None = focus
        while w is not None:
            if w is entry:
                return True
            w = w.get_parent()
        return False

    def unfocus_to_canvas(self) -> None:
        """Move keyboard focus off the search entry onto the active canvas."""
        canvas = getattr(self.win, "canvas", None)
        if canvas is not None and canvas.grab_focus():
            return
        if self.is_entry_focused():
            self.win.set_focus(None)

    def set_query_silently(self, text: str) -> None:
        """Update the search entry text without triggering debounced search-changed."""
        entry = getattr(self.win, "entry", None)
        if entry is None or entry.get_text() == text:
            return
        self._suppress_changed = True
        try:
            entry.set_text(text)
        finally:
            self._suppress_changed = False

    def focus_search(self) -> None:
        """Focus the search entry, auto-searching any active canvas text selection."""
        doc_view = self.win.get_active_doc_view()
        canvas = doc_view.canvas if (doc_view and hasattr(doc_view, "canvas")) else getattr(self.win, "canvas", None)
        sel = canvas.text_selection if (canvas and hasattr(canvas, "text_selection")) else None

        if sel is not None and sel.has_selection():
            selected_text = " ".join(sel.get_selected_text().split())
            if selected_text:
                self.set_query_silently(selected_text)
                self.win.run_search(selected_text)
        elif self.state in (SearchState.IDLE, SearchState.RESULT_ACTIVE):
            self.state = SearchState.EDITING

        self.win.entry.grab_focus()
        self.win.entry.select_region(0, -1)

    def on_search_changed_debounced(self, _entry):
        if self._suppress_changed:
            return
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
        self._debounce_source_id = GLib.timeout_add(150, self._debounced_fire)

    def _debounced_fire(self):
        self._debounce_source_id = None
        self.run_search(self.win.entry.get_text())
        return False

    def on_activate_immediate(self, entry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        self.run_search(entry.get_text())

    def cancel_pending(self) -> None:
        """Cancel debounced timers and invalidate in-flight search results."""
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        # Bump the search id so any queued DB result callback is dropped.
        self._current_search_id += 1

    def clear_results_box(self):
        if self.win.search_results_view is not None:
            self.win.search_results_view.clear()

    def _clear_canvas_highlights(self) -> None:
        canvas = getattr(self.win, "canvas", None)
        if canvas is not None:
            canvas.set_search_highlights({})

    def on_clear_clicked(self) -> None:
        """Explicitly clear search (Clear button or Escape): return to document view and unfocus."""
        self.exit_to_document(clear_query=True, clear_highlights=True)

    def on_escape(self) -> bool:
        """Handle Escape when in search-view or while search entry is focused."""
        if self.win.stack.get_visible_child_name() == "search-view" or self.is_entry_focused():
            self.exit_to_document(clear_query=True, clear_highlights=True)
            return True
        return False

    def exit_to_document(
        self,
        *,
        clear_query: bool = False,
        clear_highlights: bool = False,
    ) -> None:
        """Return to document-view, cancel pending work, and unfocus the search entry."""
        self.cancel_pending()
        if clear_query:
            self._last_query = ""
            self.set_query_silently("")
            self.clear_results_box()
        if clear_highlights:
            self._clear_canvas_highlights()
        if self.win.stack.get_visible_child_name() == "search-view":
            self.win.stack.set_visible_child_name("document-view")
        self.unfocus_to_canvas()
        self.state = SearchState.IDLE

    def on_toggle_pin(self, result: dict, query_terms: list | set, pinned: bool):
        if pinned:
            self.win.pinned[result["id"]] = {"result": result, "query_terms": query_terms}
        else:
            self.win.pinned.pop(result["id"], None)
        self.run_search(self.win.entry.get_text())

    def _extract_search_highlights(
        self,
        query: str,
        terms: list[str] | set[str] | None = None,
    ) -> dict[int, list[tuple[float, float, float, float]]]:
        if not self.win.doc_model:
            return {}
        doc = self.win.doc_model.doc
        if doc is None:
            return {}

        results: dict[int, list[tuple[float, float, float, float]]] = {}
        clean_query = query.strip().strip("\"'")
        query_words = [
            t.strip(string.punctuation)
            for t in (list(terms) if terms else clean_query.split())
            if t.strip(string.punctuation)
        ]

        for page_idx in range(len(doc)):
            try:
                page = doc[page_idx]
                page_rects: list[tuple[float, float, float, float]] = []
                seen: set[tuple[float, float, float, float]] = set()

                if len(query_words) > 1 and clean_query:
                    phrase_matches = page.search_for(clean_query, flags=fitz.TEXT_DEHYPHENATE)
                    if phrase_matches:
                        for r in phrase_matches:
                            key = (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))
                            if key not in seen:
                                seen.add(key)
                                page_rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))

                if not page_rects:
                    for term in query_words:
                        if not term:
                            continue
                        matches = page.search_for(term, flags=fitz.TEXT_DEHYPHENATE)
                        if not matches:
                            matches = page.search_for(term)
                        for r in matches:
                            key = (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))
                            if key not in seen:
                                seen.add(key)
                                page_rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))

                if page_rects:
                    results[page_idx] = page_rects
            except Exception as e:
                print(f"[SearchController] Error searching occurrences on page {page_idx}: {e}", flush=True)

        return results

    def on_row_clicked(self, result: dict, query_terms: list | set | None = None):
        if not self.win.doc_model:
            return

        page_no = result["page"]
        page_idx = page_no - 1

        if page_idx < 0 or page_idx >= len(self.win.canvas.page_layout):
            return

        query_str = self.win.entry.get_text() if hasattr(self.win, "entry") else ""
        highlights = self._extract_search_highlights(query_str, query_terms)
        self.win.canvas.set_search_highlights(highlights)
        self.win.stack.set_visible_child_name("document-view")
        self.unfocus_to_canvas()
        self.state = SearchState.RESULT_ACTIVE

        def scroll_to_target():
            if not self.win.doc_model or page_idx >= len(self.win.canvas.page_layout):
                return False

            y_offset, _dw, _dh, crop_rect = self.win.canvas.page_layout[page_idx]
            crop_y0 = crop_rect.y0 if crop_rect is not None else 0.0

            block_rel_y0 = max(0.0, result["y0"] - crop_y0)
            block_rel_y1 = max(0.0, result["y1"] - crop_y0)
            block_rel_mid = block_rel_y0 + (block_rel_y1 - block_rel_y0) / 2.0

            scale = self.win.zoom * self.win.canvas.dpi_scale_factor
            block_pixel_y = block_rel_mid * scale
            block_absolute_y = y_offset + self.win.canvas.page_gap + block_pixel_y

            viewport_h = self.win.vadjustment.get_page_size()
            lower = self.win.vadjustment.get_lower()
            upper = self.win.vadjustment.get_upper()
            max_y = max(lower, upper - viewport_h)
            target_y = clamp(lower, block_absolute_y - (viewport_h / 2.0), max_y)

            self.unfocus_to_canvas()
            self.win.vadjustment.set_value(target_y)
            self.win._on_scroll_page_changed(self.win.vadjustment)
            self.win._queue_canvas_redraw()
            return False

        GLib.idle_add(scroll_to_target)

    def run_search(self, query: str):
        query = query.strip()
        if not query:
            self.cancel_pending()
            self._last_query = ""
            self.clear_results_box()
            self._clear_canvas_highlights()
            if self.win.stack.get_visible_child_name() == "search-view":
                self.win.stack.set_visible_child_name("document-view")
            self.state = SearchState.EDITING if self.is_entry_focused() else SearchState.IDLE
            return

        if not self.win.db_service:
            print("[Search] Warning: DatabaseService not available", flush=True)
            if self.win.search_results_view is not None:
                self.win.search_results_view.render_results(
                    [],
                    query=query,
                    active_filepath=self.win.doc_model.filepath if self.win.doc_model else "",
                    is_grid=self.win.settings.search_layout == "grid",
                    autotrim=self.win.settings.search_autotrim,
                )
            return

        self._current_search_id += 1
        search_id = self._current_search_id
        self._last_query = query
        self.state = SearchState.SEARCH_VIEW

        if self.win.search_results_view is not None:
            self.win.search_results_view.reset_executor()
            self.win.search_results_view.reset_scroll()
        elif self.win.executor is not None:
            self.win.executor.shutdown(wait=False, cancel_futures=True)
            from concurrent.futures import ThreadPoolExecutor

            self.win.executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="search-portal"
            )

        self.win.stack.set_visible_child_name("search-view")

        self.win.db_service.search(
            query, limit=30, search_id=search_id, on_results=self._on_search_results
        )

    def _on_search_results(self, live_results: list[dict], search_id: int):
        if search_id != self._current_search_id:
            return

        is_grid = self.win.settings.search_layout == "grid"
        active_filepath = self.win.doc_model.filepath if self.win.doc_model else ""
        if self.win.search_results_view is not None:
            self.win.search_results_view.pinned = self.win.pinned
            self.win.search_results_view.render_results(
                live_results,
                query=self._last_query,
                active_filepath=active_filepath,
                is_grid=is_grid,
                autotrim=self.win.settings.search_autotrim,
            )
