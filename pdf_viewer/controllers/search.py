import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from concurrent.futures import ThreadPoolExecutor
import string
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.window import MainWindow

from ..core.index import search as fts_search
from ..ui.portal import ResultRow

DEBOUNCE_MS = 150


def clamp(val, min_val, max_val):
    return max(min_val, min(val, max_val))


class SearchController:
    """
    Controller managing FTS5 search queries, debouncing, and search portal result views.
    """

    def __init__(self, main_window: "MainWindow"):
        self.win = main_window
        self._debounce_source_id: int | None = None
        self._last_query = ""

    def on_search_changed_debounced(self, _entry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
        self._debounce_source_id = GLib.timeout_add(DEBOUNCE_MS, self._debounced_fire)

    def _debounced_fire(self):
        self._debounce_source_id = None
        self.run_search(self.win.entry.get_text())
        return False

    def on_activate_immediate(self, entry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        self.run_search(entry.get_text())

    def clear_results_box(self):
        child = self.win.results_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.win.results_box.remove(child)
            child = nxt

    def on_toggle_pin(self, result: dict, query_terms: list | set, pinned: bool):
        if pinned:
            self.win.pinned[result["id"]] = {"result": result, "query_terms": query_terms}
        else:
            self.win.pinned.pop(result["id"], None)
        self.run_search(self.win.entry.get_text())

    def on_row_clicked(self, result: dict, _query_terms: list | set):
        """Scrolls the document canvas to center the selected matched block."""
        if not self.win.doc_model:
            return

        page_no = result["page"]
        page_idx = page_no - 1

        if page_idx < 0 or page_idx >= len(self.win.canvas.page_layout):
            return

        # Set visual outline highlight on main canvas
        self.win.canvas.set_highlighted_block(page_idx, (result["x0"], result["y0"], result["x1"], result["y1"]))

        # Switch view back to reader mode
        self.win.stack.set_visible_child_name("document-view")

        def scroll_to_target():
            if not self.win.doc_model or page_idx >= len(self.win.canvas.page_layout):
                return False

            y_offset, _dw, _dh, crop_rect = self.win.canvas.page_layout[page_idx]
            crop_y0 = crop_rect.y0 if crop_rect is not None else 0.0

            # Calculate midpoint of the match block relative to cropped Y top boundary
            block_rel_y0 = max(0.0, result["y0"] - crop_y0)
            block_rel_y1 = max(0.0, result["y1"] - crop_y0)
            block_rel_mid = block_rel_y0 + (block_rel_y1 - block_rel_y0) / 2.0

            # Convert points to layout pixels
            scale = self.win.zoom * self.win.canvas.dpi_scale_factor
            block_pixel_y = block_rel_mid * scale

            # Absolute target Y including the page gap offset
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
        query = query or ""
        if not query.strip():
            self.clear_results_box()
            self._last_query = ""
            if self.win.stack.get_visible_child_name() == "search-view":
                self.win.stack.set_visible_child_name("document-view")
                self.win.canvas.grab_focus()
            return

        # Cancel any previous/pending search result renderings by shutting down and recreating the thread pool.
        self.win.executor.shutdown(wait=False, cancel_futures=True)
        self.win.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")

        self._last_query = query
        self.clear_results_box()
        self.win.stack.set_visible_child_name("search-view")

        if hasattr(self.win, "search_scrolled") and self.win.search_scrolled:
            vadj = self.win.search_scrolled.get_vadjustment()
            if vadj:
                vadj.set_value(vadj.get_lower())

        query_terms = {t.strip(string.punctuation).lower() for t in query.strip().split() if t}

        live_results = fts_search(self.win.index_conn, query, limit=30) if self.win.index_conn else []

        is_grid = getattr(self.win.settings, "search_layout", "grid") == "grid"

        pinned_grid = None
        live_grid = None

        # 1. Pinned Portals Header + FlowBox/List
        if self.win.pinned:
            pinned_label = Gtk.Label(label="📌 Pinned Portals", xalign=0)
            pinned_label.add_css_class("heading")
            pinned_label.set_margin_top(14)
            pinned_label.set_margin_start(16)
            pinned_label.set_margin_bottom(6)
            self.win.results_box.append(pinned_label)

            if is_grid:
                pinned_grid = Gtk.FlowBox()
                pinned_grid.set_valign(Gtk.Align.START)
                pinned_grid.set_halign(Gtk.Align.CENTER)
                pinned_grid.set_hexpand(True)
                pinned_grid.set_selection_mode(Gtk.SelectionMode.NONE)
                pinned_grid.set_column_spacing(8)
                pinned_grid.set_row_spacing(8)
                pinned_grid.set_margin_start(12)
                pinned_grid.set_margin_end(12)
                self.win.results_box.append(pinned_grid)

            for entry in self.win.pinned.values():
                if not self.win.doc_model:
                    break
                row = ResultRow(
                    self.win.doc_model.filepath,
                    self.win.executor,
                    entry["result"],
                    entry["query_terms"],
                    pinned=True,
                    on_toggle_pin=self.on_toggle_pin,
                    on_row_clicked=self.on_row_clicked,
                )
                if is_grid and pinned_grid is not None:
                    child_wrapper = Gtk.FlowBoxChild()
                    child_wrapper.set_child(row)
                    child_wrapper.set_halign(Gtk.Align.CENTER)
                    child_wrapper.set_valign(Gtk.Align.CENTER)
                    pinned_grid.append(child_wrapper)
                else:
                    self.win.results_box.append(row)
                    self.win.results_box.append(Gtk.Separator())

            if live_results:
                live_label = Gtk.Label(label="Search Results", xalign=0)
                live_label.add_css_class("heading")
                live_label.set_margin_top(10)
                live_label.set_margin_start(16)
                live_label.set_margin_bottom(6)
                self.win.results_box.append(live_label)

        # 2. Main Search Results List/FlowBox
        if not live_results:
            placeholder = Gtk.Label(label="No matches found.", margin_top=32)
            placeholder.add_css_class("dim-label")
            self.win.results_box.append(placeholder)
            return

        if is_grid:
            live_grid = Gtk.FlowBox()
            live_grid.set_valign(Gtk.Align.START)
            live_grid.set_halign(Gtk.Align.CENTER)
            live_grid.set_hexpand(True)
            live_grid.set_selection_mode(Gtk.SelectionMode.NONE)
            live_grid.set_column_spacing(8)
            live_grid.set_row_spacing(8)
            live_grid.set_margin_start(12)
            live_grid.set_margin_end(12)
            self.win.results_box.append(live_grid)

        for i, result in enumerate(live_results):
            if not self.win.doc_model:
                break
            already_pinned = result["id"] in self.win.pinned
            row = ResultRow(
                self.win.doc_model.filepath,
                self.win.executor,
                result,
                query_terms,
                pinned=already_pinned,
                on_toggle_pin=self.on_toggle_pin,
                on_row_clicked=self.on_row_clicked,
            )
            if is_grid and live_grid is not None:
                child_wrapper = Gtk.FlowBoxChild()
                child_wrapper.set_child(row)
                child_wrapper.set_halign(Gtk.Align.CENTER)
                child_wrapper.set_valign(Gtk.Align.CENTER)
                live_grid.append(child_wrapper)
            else:
                self.win.results_box.append(row)
                if i < len(live_results) - 1:
                    self.win.results_box.append(Gtk.Separator())
