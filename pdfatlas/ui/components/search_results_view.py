from __future__ import annotations

import string
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..gui import box, scrolled_window
from ..portal import ResultRow


class SearchResultsView(Gtk.Box):
    """
    Self-contained search results container with scrolled portal cards,
    pinned portals, and support for list / grid layouts.
    """

    def __init__(
        self,
        on_row_clicked: Callable[[dict, set[str]], None] | None = None,
        on_toggle_pin: Callable[[dict, set[str], bool], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_row_clicked = on_row_clicked
        self.on_toggle_pin = on_toggle_pin

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.pinned: dict[int, dict[str, Any]] = {}

        self.scrolled = scrolled_window()
        self.results_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=24,
        )
        self.scrolled.set_child(self.results_box)
        self.append(self.scrolled)

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

    def render_results(
        self,
        results: list[dict],
        query: str,
        active_filepath: str,
        is_grid: bool = False,
    ):
        self.clear()
        query_terms = {t.strip(string.punctuation).lower() for t in query.strip().split() if t}

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
                pinned_grid = Gtk.FlowBox()
                pinned_grid.set_valign(Gtk.Align.START)
                pinned_grid.set_halign(Gtk.Align.CENTER)
                pinned_grid.set_hexpand(True)
                pinned_grid.set_selection_mode(Gtk.SelectionMode.NONE)
                pinned_grid.set_column_spacing(8)
                pinned_grid.set_row_spacing(8)
                pinned_grid.set_margin_start(12)
                pinned_grid.set_margin_end(12)
                self.results_box.append(pinned_grid)

            for entry in self.pinned.values():
                pdf_path = entry["result"].get("filepath", active_filepath)
                row = ResultRow(
                    pdf_path,
                    self.executor,
                    entry["result"],
                    entry["query_terms"],
                    pinned=True,
                    on_toggle_pin=self._handle_toggle_pin,
                    on_row_clicked=self.on_row_clicked,
                )
                if is_grid and pinned_grid is not None:
                    pinned_grid.append(row)
                else:
                    self.results_box.append(row)

            # Separator between pinned and live results
            if results:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_top(16)
                sep.set_margin_bottom(16)
                sep.set_margin_start(16)
                sep.set_margin_end(16)
                self.results_box.append(sep)

        # 2. Live Search Results
        if is_grid and results:
            live_grid = Gtk.FlowBox()
            live_grid.set_valign(Gtk.Align.START)
            live_grid.set_halign(Gtk.Align.CENTER)
            live_grid.set_hexpand(True)
            live_grid.set_selection_mode(Gtk.SelectionMode.NONE)
            live_grid.set_column_spacing(8)
            live_grid.set_row_spacing(8)
            live_grid.set_margin_start(12)
            live_grid.set_margin_end(12)
            self.results_box.append(live_grid)

        for res in results:
            is_pinned = res["id"] in self.pinned
            pdf_path = res.get("filepath", active_filepath)
            row = ResultRow(
                pdf_path,
                self.executor,
                res,
                query_terms,
                pinned=is_pinned,
                on_toggle_pin=self._handle_toggle_pin,
                on_row_clicked=self.on_row_clicked,
            )
            if is_grid and live_grid is not None:
                live_grid.append(row)
            else:
                self.results_box.append(row)

    def _handle_toggle_pin(self, result: dict, query_terms: set[str], pinned: bool):
        if pinned:
            self.pinned[result["id"]] = {"result": result, "query_terms": query_terms}
        else:
            self.pinned.pop(result["id"], None)
        if self.on_toggle_pin:
            self.on_toggle_pin(result, query_terms, pinned)
