from __future__ import annotations

import string
from typing import TYPE_CHECKING, Callable

import pymupdf as fitz
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

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


class SearchController:
    """Backwards-compatible controller bridging MainWindow to search operations."""

    def __init__(self, main_window: MainWindow):
        self.win = main_window
        self._debounce_source_id: int | None = None
        self._last_query = ""
        self._current_search_id = 0

    def on_search_changed_debounced(self, _entry):
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

            self.win.canvas.grab_focus()
            self.win.vadjustment.set_value(target_y)
            self.win._on_scroll_page_changed(self.win.vadjustment)
            self.win._queue_canvas_redraw()
            return False

        GLib.idle_add(scroll_to_target)

    def run_search(self, query: str):
        query = query.strip()
        if not query:
            self.clear_results_box()
            self.win.stack.set_visible_child_name("document-view")
            return

        if not self.win.db_service:
            print("[Search] Warning: DatabaseService not available", flush=True)
            if self.win.search_results_view is not None:
                self.win.search_results_view.render_results(
                    [],
                    query=query,
                    active_filepath=self.win.doc_model.filepath if self.win.doc_model else "",
                    is_grid=self.win.settings.search_layout == "grid",
                )
            return

        self._current_search_id += 1
        search_id = self._current_search_id
        self._last_query = query

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
            )
