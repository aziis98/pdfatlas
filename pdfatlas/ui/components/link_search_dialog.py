from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango

from ..gui import box, label


class LinkSearchDialog(Gtk.Window):
    """
    Modal dialog for searching academic papers via OpenAlex and linking (DOI, OA PDF).
    """

    def __init__(
        self,
        parent_window: Gtk.Window,
        initial_query: str,
        on_selected: Callable[[str | None, str | None, str], None],
    ):
        super().__init__(
            title="Link Academic Article",
            transient_for=parent_window,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(560, 480)
        self._on_selected = on_selected
        self._results: list[dict] = []
        self._search_debounce_id: int | None = None
        self._active_search_thread: threading.Thread | None = None

        header = Adw.HeaderBar()
        self.set_titlebar(header)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda b: self.destroy())
        header.pack_start(btn_cancel)

        main_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=14,
            margin_bottom=14,
        )
        self.set_child(main_box)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search paper title, DOI, or author...")
        self.search_entry.set_text(self._clean_query(initial_query))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda e: self._trigger_search_immediate())
        main_box.append(self.search_entry)

        # Status and spinner bar
        status_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(16, 16)
        status_box.append(self.spinner)

        self.status_label = label(text="Type to search OpenAlex...", css_class="dim-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)
        main_box.append(status_box)

        # Results Scrolled List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(260)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("boxed-list")
        self.list_box.connect("row-selected", self._on_row_selected)
        self.list_box.connect("row-activated", lambda lb, row: self._on_confirm_selected())
        scrolled.set_child(self.list_box)
        main_box.append(scrolled)

        # Bottom action bar
        bottom_bar = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.target_preview_label = label(text="", css_class="dim-label")
        self.target_preview_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.target_preview_label.set_hexpand(True)
        self.target_preview_label.set_halign(Gtk.Align.START)
        bottom_bar.append(self.target_preview_label)

        self.btn_confirm = Gtk.Button(label="Link Article")
        self.btn_confirm.add_css_class("suggested-action")
        self.btn_confirm.set_sensitive(False)
        self.btn_confirm.connect("clicked", lambda b: self._on_confirm_selected())
        bottom_bar.append(self.btn_confirm)
        main_box.append(bottom_bar)

        # Start search if query is non-empty
        if self.search_entry.get_text().strip():
            self._trigger_search_immediate()

    @staticmethod
    def _clean_query(raw: str) -> str:
        import re
        q = raw.strip()
        # Remove citation brackets like [12], 1., etc.
        q = re.sub(r"^\[\d+\]\s*", "", q)
        q = re.sub(r"^\d+\.\s*", "", q)
        # Collapse all whitespace and newlines
        q = re.sub(r"\s+", " ", q)
        return q.strip()

    def _on_search_changed(self, entry):
        if self._search_debounce_id is not None:
            GLib.source_remove(self._search_debounce_id)
            self._search_debounce_id = None
        self._search_debounce_id = GLib.timeout_add(300, self._trigger_search_immediate)

    def _trigger_search_immediate(self):
        self._search_debounce_id = None
        query = self.search_entry.get_text().strip()
        if not query:
            self._clear_results()
            self.status_label.set_text("Type to search OpenAlex...")
            self.spinner.stop()
            return False

        self.spinner.start()
        self.status_label.set_text(f"Searching for '{query}'...")

        def _worker(search_term: str):
            try:
                encoded = urllib.parse.quote(search_term)
                url = f"https://api.openalex.org/works?search={encoded}&per_page=6"
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "PDFAtlas/1.0 (https://github.com/aziis98/pdfatlas; mailto:user@pdfatlas.local)"
                    },
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    raw_items = payload.get("results", [])

                parsed = []
                for item in raw_items:
                    title = item.get("display_name") or "Untitled Work"
                    year = item.get("publication_year")
                    doi = item.get("doi")
                    oa_info = item.get("open_access", {})
                    is_oa = bool(oa_info.get("is_oa", False))
                    oa_url = oa_info.get("oa_url")
                    
                    authorships = item.get("authorships", [])
                    author_names = []
                    for a in authorships[:3]:
                        author_obj = a.get("author", {})
                        name = author_obj.get("display_name")
                        if name:
                            author_names.append(name)
                    if len(authorships) > 3:
                        author_names.append("et al.")
                    authors_str = ", ".join(author_names) if author_names else "Unknown Authors"

                    parsed.append({
                        "title": title,
                        "year": year,
                        "authors": authors_str,
                        "doi": doi,
                        "is_oa": is_oa,
                        "oa_url": oa_url,
                    })

                def _ui_update():
                    self._populate_results(parsed)
                    return False

                GLib.idle_add(_ui_update)
            except Exception as exc:
                err_msg = str(exc)

                def _ui_error():
                    self.spinner.stop()
                    self.status_label.set_text(f"Search error: {err_msg}")
                    return False

                GLib.idle_add(_ui_error)

        self._active_search_thread = threading.Thread(target=_worker, args=(query,), daemon=True)
        self._active_search_thread.start()
        return False

    def _clear_results(self):
        self._results = []
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        self.btn_confirm.set_sensitive(False)
        self.target_preview_label.set_text("")

    def _populate_results(self, items: list[dict]):
        self.spinner.stop()
        self._clear_results()
        self._results = items

        if not items:
            self.status_label.set_text("No matching articles found.")
            return

        self.status_label.set_text(f"Found {len(items)} works on OpenAlex:")

        for idx, item in enumerate(items):
            row = Gtk.ListBoxRow()
            row_box = box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=3,
                margin_start=10,
                margin_end=10,
                margin_top=8,
                margin_bottom=8,
            )

            # Top: Title + Year
            top_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            title_lbl = label(
                text=item["title"],
                css_class="heading",
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=44,
            )
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_hexpand(True)
            top_box.append(title_lbl)

            if item["year"]:
                year_badge = label(text=str(item["year"]), css_class="caption")
                year_badge.add_css_class("dim-label")
                top_box.append(year_badge)
            row_box.append(top_box)

            # Middle: Authors
            authors_lbl = label(
                text=item["authors"],
                css_class="caption",
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=55,
            )
            authors_lbl.add_css_class("dim-label")
            authors_lbl.set_halign(Gtk.Align.START)
            row_box.append(authors_lbl)

            # Bottom: Badges (OA status, DOI)
            badges_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=2)
            if item["is_oa"]:
                oa_badge = label(text="📄 Open Access PDF", css_class="caption")
                oa_badge.add_css_class("success")
                badges_box.append(oa_badge)
            else:
                closed_badge = label(text="🔒 Closed Access", css_class="caption")
                closed_badge.add_css_class("dim-label")
                badges_box.append(closed_badge)

            if item["doi"]:
                doi_short = item["doi"].replace("https://doi.org/", "DOI: ")
                doi_badge = label(text=doi_short, css_class="caption")
                doi_badge.add_css_class("dim-label")
                badges_box.append(doi_badge)

            row_box.append(badges_box)
            row.set_child(row_box)
            self.list_box.append(row)

        # Select first result by default
        first_row = self.list_box.get_row_at_index(0)
        if first_row:
            self.list_box.select_row(first_row)

    def _on_row_selected(self, list_box, row: Gtk.ListBoxRow | None):
        if row is None:
            self.btn_confirm.set_sensitive(False)
            self.target_preview_label.set_text("")
            return

        idx = row.get_index()
        if 0 <= idx < len(self._results):
            item = self._results[idx]
            self.btn_confirm.set_sensitive(True)
            preview_parts = []
            if item.get("oa_url"):
                preview_parts.append("PDF: Open Access")
            if item.get("doi"):
                preview_parts.append("Link: DOI")
            preview_str = " (" + ", ".join(preview_parts) + ")" if preview_parts else ""
            self.target_preview_label.set_text(f"Selected: {item['title'][:40]}…{preview_str}")

    def _on_confirm_selected(self):
        row = self.list_box.get_selected_row()
        if not row:
            return
        idx = row.get_index()
        if 0 <= idx < len(self._results):
            item = self._results[idx]
            uri = item.get("doi") or ("https://openalex.org" if not item.get("oa_url") else None)
            pdf_url = item.get("oa_url")
            title = item.get("title") or "Academic Article"
            self._on_selected(uri, pdf_url, title)
            self.destroy()
