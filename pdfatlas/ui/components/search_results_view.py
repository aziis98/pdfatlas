from __future__ import annotations

import string
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..gui import box, scrolled_window
from ..portal import ResultRow


class SearchResultsView(Gtk.Box):
    """
    Self-contained search results container with scrolled portal cards,
    pinned portals, support for list / grid layouts, and empty state feedback.
    """

    def __init__(
        self,
        on_row_clicked: Callable[[dict, set[str]], None] | None = None,
        on_toggle_pin: Callable[[dict, set[str], bool], None] | None = None,
        use_masonry: bool = True,
        page_height_getter: Callable[[int], float] | None = None,
        page_dims_getter: Callable[[int], tuple[float, float]] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_row_clicked = on_row_clicked
        self.on_toggle_pin = on_toggle_pin
        self.use_masonry = use_masonry
        self.page_height_getter = page_height_getter
        self.page_dims_getter = page_dims_getter

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.pinned: dict[int, dict] = {}

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        self.scrolled = scrolled_window()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.results_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=24,
        )
        self.scrolled.set_child(self.results_box)

        self.status_page = Adw.StatusPage()
        self.status_page.set_icon_name("system-search-symbolic")
        self.status_page.set_title("No Results Found")
        self.status_page.set_description("Try searching with different keywords or terms.")
        self.status_page.set_vexpand(True)
        self.status_page.set_hexpand(True)

        self.stack.add_named(self.scrolled, "results")
        self.stack.add_named(self.status_page, "empty")
        self.append(self.stack)

    def reset_executor(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")

    def reset_scroll(self):
        vadj = self.scrolled.get_vadjustment()
        vadj.set_value(vadj.get_lower())

    def clear(self):
        child = self.results_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.results_box.remove(child)
            child = nxt
        self.stack.set_visible_child_name("results")

    def render_results(
        self,
        results: list[dict],
        query: str,
        active_filepath: str,
        is_grid: bool = False,
        autotrim: bool = True,
    ):
        self.clear()
        query_terms = {t.strip(string.punctuation).lower() for t in query.strip().split() if t}

        # If there are no results and no pinned portals, display empty state
        if not results and not self.pinned:
            clean_query = query.strip()
            self.status_page.set_title("No Results Found")
            if clean_query:
                self.status_page.set_description(f"No matches found for “{clean_query}”. Try different keywords.")
            else:
                self.status_page.set_description("Try searching with different keywords or terms.")
            self.stack.set_visible_child_name("empty")
            return

        self.stack.set_visible_child_name("results")

        pinned_grid = None
        live_grid = None

        # 1. Pinned Portals Header + FlowBox/List
        if self.pinned:
            pinned_label = Gtk.Label(label="📌 Pinned Portals", xalign=0)
            pinned_label.add_css_class("heading")
            pinned_label.set_margin_top(14)
            pinned_label.set_margin_start(16)
            pinned_label.set_margin_bottom(6)
            self.results_box.append(pinned_label)

            if is_grid:
                if self.use_masonry:
                    from .masonry_layout import MasonryBox

                    pinned_grid = MasonryBox(
                        column_spacing=12,
                        row_spacing=12,
                        min_column_width=450,
                        max_columns=3,
                    )
                    pinned_grid.set_halign(Gtk.Align.FILL)
                else:
                    pinned_grid = Gtk.FlowBox()
                    pinned_grid.set_selection_mode(Gtk.SelectionMode.NONE)
                    pinned_grid.set_column_spacing(12)
                    pinned_grid.set_row_spacing(12)
                    pinned_grid.set_halign(Gtk.Align.CENTER)
                pinned_grid.set_valign(Gtk.Align.START)
                pinned_grid.set_hexpand(True)
                pinned_grid.set_margin_start(16)
                pinned_grid.set_margin_end(16)
                self.results_box.append(pinned_grid)

            for entry in self.pinned.values():
                pdf_path = entry["result"].get("filepath", active_filepath)
                p_page = entry["result"].get("page", 1)
                pw, ph = 600.0, 792.0
                if self.page_dims_getter:
                    try:
                        pw, ph = self.page_dims_getter(p_page)
                    except Exception:
                        pass
                elif self.page_height_getter:
                    try:
                        ph = self.page_height_getter(p_page)
                    except Exception:
                        pass
                row = ResultRow(
                    pdf_path,
                    self.executor,
                    entry["result"],
                    entry["query_terms"],
                    pinned=True,
                    page_w=pw,
                    page_h=ph,
                    autotrim=autotrim,
                    on_toggle_pin=self._handle_toggle_pin,
                    on_row_clicked=self.on_row_clicked,
                )
                if is_grid and pinned_grid is not None:
                    pinned_grid.append(row)
                else:
                    row.set_halign(Gtk.Align.CENTER)
                    row.set_hexpand(False)
                    self.results_box.append(row)

            # Separator between pinned and live results (or pinned and empty notice)
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(16)
            sep.set_margin_bottom(16)
            sep.set_margin_start(16)
            sep.set_margin_end(16)
            self.results_box.append(sep)

            if not results:
                pinned_empty = Adw.StatusPage()
                pinned_empty.set_icon_name("system-search-symbolic")
                pinned_empty.set_title("No Results Found")
                clean_query = query.strip()
                if clean_query:
                    pinned_empty.set_description(f"No matches found for “{clean_query}”.")
                else:
                    pinned_empty.set_description("Try searching with different keywords or terms.")
                self.results_box.append(pinned_empty)

        # 2. Live Search Results
        if is_grid and results:
            if self.use_masonry:
                from .masonry_layout import MasonryBox
                from pdfatlas.core.search_cluster import merge_search_results

                display_results = merge_search_results(
                    results,
                    query_terms=query_terms,
                    page_height_getter=self.page_height_getter,
                )
                live_grid = MasonryBox(
                    column_spacing=12,
                    row_spacing=12,
                    min_column_width=450,
                    max_columns=3,
                )
                live_grid.set_halign(Gtk.Align.FILL)
            else:
                display_results = results
                live_grid = Gtk.FlowBox()
                live_grid.set_selection_mode(Gtk.SelectionMode.NONE)
                live_grid.set_column_spacing(12)
                live_grid.set_row_spacing(12)
                live_grid.set_halign(Gtk.Align.CENTER)
            live_grid.set_valign(Gtk.Align.START)
            live_grid.set_hexpand(True)
            live_grid.set_margin_start(16)
            live_grid.set_margin_end(16)
            self.results_box.append(live_grid)

            for res in display_results:
                is_pinned = res["id"] in self.pinned or any(
                    mid in self.pinned for mid in res.get("merged_ids", [])
                )
                pdf_path = res.get("filepath", active_filepath)
                p_page = res.get("page", 1)
                pw, ph = 600.0, 792.0
                if self.page_dims_getter:
                    try:
                        pw, ph = self.page_dims_getter(p_page)
                    except Exception:
                        pass
                elif self.page_height_getter:
                    try:
                        ph = self.page_height_getter(p_page)
                    except Exception:
                        pass
                row = ResultRow(
                    pdf_path,
                    self.executor,
                    res,
                    query_terms,
                    pinned=is_pinned,
                    page_w=pw,
                    page_h=ph,
                    autotrim=autotrim,
                    on_toggle_pin=self._handle_toggle_pin,
                    on_row_clicked=self.on_row_clicked,
                )
                live_grid.append(row)
        elif results:
            for res in results:
                is_pinned = res["id"] in self.pinned
                pdf_path = res.get("filepath", active_filepath)
                p_page = res.get("page", 1)
                pw, ph = 600.0, 792.0
                if self.page_dims_getter:
                    try:
                        pw, ph = self.page_dims_getter(p_page)
                    except Exception:
                        pass
                elif self.page_height_getter:
                    try:
                        ph = self.page_height_getter(p_page)
                    except Exception:
                        pass
                row = ResultRow(
                    pdf_path,
                    self.executor,
                    res,
                    query_terms,
                    pinned=is_pinned,
                    page_w=pw,
                    page_h=ph,
                    autotrim=autotrim,
                    on_toggle_pin=self._handle_toggle_pin,
                    on_row_clicked=self.on_row_clicked,
                )
                row.set_halign(Gtk.Align.CENTER)
                row.set_hexpand(False)
                self.results_box.append(row)

    def _handle_toggle_pin(self, result: dict, query_terms: set[str], pinned: bool):
        if pinned:
            self.pinned[result["id"]] = {"result": result, "query_terms": query_terms}
        else:
            self.pinned.pop(result["id"], None)
            for mid in result.get("merged_ids", []):
                self.pinned.pop(mid, None)
        if self.on_toggle_pin:
            self.on_toggle_pin(result, query_terms, pinned)
