from __future__ import annotations

import re
from typing import TYPE_CHECKING
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gtk, Pango

from ..ui.cairo_utils import hsl_to_hex
from ..ui.gui import box, button, label, spacer

if TYPE_CHECKING:
    from ..ui.window import MainWindow

#: Max characters of a simplified note preview before GTK ellipsizes it.
MAX_NOTE_PREVIEW_CHARS = 100


def simplify_md_preview(markdown: str) -> str:
    """One-line markdown preview for the overview list row.

    Strips heading markers line-by-line, drops math delimiters ($ / $$) and
    bold/italic markers (*, **, _, __), joins all lines with spaces, collapses
    whitespace, and truncates around MAX_NOTE_PREVIEW_CHARS; GTK ellipsizes
    the rest.
    """
    lines = []
    for ln in (markdown or "").splitlines():
        ln = re.sub(r"^#{1,6}\s*", "", ln).strip()
        if ln:
            lines.append(ln)
    text = " ".join(lines)
    text = re.sub(r"\$\$([^$]+)\$\$", r"\1", text)
    text = re.sub(r"\$([^$]+)\$", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_NOTE_PREVIEW_CHARS:
        text = text[: MAX_NOTE_PREVIEW_CHARS - 1] + "…"
    return text


PALETTE_COLS = 6
PALETTE_COLORS = [
    # Row 1: Warm
    (54, 1.0, 0.80),
    (43, 1.0, 0.77),
    (27, 1.0, 0.81),
    (14, 1.0, 0.83),
    (2, 1.0, 0.83),
    (330, 0.85, 0.84),
    # Row 2: Cool
    (291, 0.65, 0.83),
    (262, 0.65, 0.85),
    (210, 0.90, 0.83),
    (187, 0.75, 0.78),
    (160, 0.65, 0.78),
    (95, 0.65, 0.80),
    # Row 3: Soft / Neutral
    (48, 0.70, 0.88),
    (30, 0.60, 0.88),
    (15, 0.60, 0.88),
    (200, 0.40, 0.88),
    (150, 0.35, 0.88),
    (0, 0.0, 0.80),
]


class AnnotationsController:
    """
    Manages highlighting palette, text selection toolbar, clipboard export,
    and annotations & notes overview popover list.
    """

    def __init__(self, win: MainWindow):
        self.win = win
        self.active_highlight_color: str = "#FFF49C"

        # 1. Overview popover widgets
        self.annotations_btn: Gtk.MenuButton = Gtk.MenuButton()
        self.annotations_popover: Gtk.Popover = Gtk.Popover()
        self.annotations_count_label: Gtk.Label = label(text="Annotations (0)", css_class="bold")
        self.annotations_list: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._init_annotations_popover()

        # 2. Selection toolbar widgets
        self.btn_copy_text: Gtk.Button = button(
            label="Copy",
            tooltip="Copy selected PDF text [Ctrl+Shift+C]",
            on_clicked=lambda b: self.copy_pdf_text_to_clipboard(),
        )
        self.btn_copy_tex: Gtk.Button = button(
            label="Copy Source TeX",
            tooltip="Copy source TeX for selection [Ctrl+C]",
            on_clicked=lambda b: self.copy_tex_to_clipboard(),
        )
        self.btn_highlight: Adw.SplitButton = Adw.SplitButton()
        self.btn_remove_hl: Gtk.Button = button(
            label="Remove",
            tooltip="Remove the selected highlight",
            on_clicked=lambda b: self.remove_matching_highlights(),
        )
        self.info_menu_btn: Gtk.MenuButton = Gtk.MenuButton()
        self.selection_toolbar: Gtk.Box = box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            css_class="selection-toolbar",
            valign=Gtk.Align.END,
            halign=Gtk.Align.FILL,
            children=[
                box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=6,
                    children=[
                        self.btn_highlight,
                        self.btn_remove_hl,
                        self.btn_copy_text,
                        self.btn_copy_tex,
                    ],
                ),
                spacer(),
            ],
        )
        self._init_selection_toolbar()

    def _init_annotations_popover(self):
        self.annotations_btn.set_popover(self.annotations_popover)

        popover_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
        )
        popover_box.set_size_request(280, 370)

        # Header Title
        title_box = box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=4,
            margin_top=2,
            margin_bottom=2,
        )
        self.annotations_count_label.set_hexpand(True)
        self.annotations_count_label.set_halign(Gtk.Align.START)
        title_box.append(self.annotations_count_label)
        popover_box.append(title_box)

        popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Scrollable Annotations List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.annotations_list.add_css_class("annotation-list")
        scrolled.set_child(self.annotations_list)
        popover_box.append(scrolled)

        self.annotations_popover.set_child(popover_box)
        self.annotations_popover.connect("notify::visible", self._on_annotations_popover_visibility)

    def build_annotations_popover(self) -> Gtk.Popover:
        return self.annotations_popover

    def _on_annotations_popover_visibility(self, popover, pspec):
        if popover.get_visible():
            self.update_annotations_button()

    def update_annotations_button(self):
        count = len(self.win.highlights) + len(self.win.notes)
        self.annotations_btn.set_visible(count > 0)
        self.annotations_count_label.set_text(f"Annotations ({count})")

        child = self.annotations_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.annotations_list.remove(child)
            child = nxt

        items: list[tuple[int, int, float, dict, str]] = []
        for hl in self.win.highlights:
            items.append((hl.get("page", 0), 0, float(hl.get("char_start", 0)), hl, "highlight"))
        for note in self.win.notes:
            items.append((note.get("page", 0), 1, float(note.get("y", 0.0)), note, "note"))
        items.sort(key=lambda it: (it[0], it[1], it[2]))

        last_page: int | None = None
        for page_idx, _rank, _pos, item, kind in items:
            if page_idx != last_page:
                hdr_box = box(
                    orientation=Gtk.Orientation.VERTICAL,
                    spacing=1,
                    margin_start=4,
                    margin_top=4,
                    margin_bottom=1,
                )
                lbl = label(text=f"PAGE {page_idx + 1}", css_class="dim-label")
                lbl.add_css_class("caption")
                lbl.add_css_class("bold")
                lbl.set_halign(Gtk.Align.START)
                hdr_box.append(lbl)
                self.annotations_list.append(hdr_box)
                last_page = page_idx

            linked_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            linked_box.add_css_class("linked")
            linked_box.set_hexpand(True)

            main_btn = Gtk.Button()
            main_btn.set_hexpand(True)
            main_btn.add_css_class("annotation-row-btn")

            row_content = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            if kind == "highlight":
                color_dot = box(css_class="annotation-color-dot")
                color_dot.set_valign(Gtk.Align.CENTER)
                color_dot.set_halign(Gtk.Align.CENTER)
                color_dot.set_size_request(10, 10)
                hex_col = item.get("color", "#FFF49C")
                provider = Gtk.CssProvider()
                provider.load_from_data(
                    f".annotation-color-dot {{ min-width: 10px; min-height: 10px; background-color: {hex_col}; border-radius: 9999px; }}".encode(
                        "utf-8"
                    )
                )
                color_dot.get_style_context().add_provider(
                    provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                row_content.append(color_dot)

                txt = (item.get("text") or "").strip().replace("\n", " ")
                if len(txt) > 40:
                    txt = txt[:38] + "…"
                row_content.append(
                    label(
                        text=txt,
                        ellipsize=Pango.EllipsizeMode.END,
                        xalign=0,
                        css_class="caption",
                    )
                )
            else:
                row_content.append(Gtk.Image.new_from_icon_name("mail-attachment-symbolic"))
                md_preview = simplify_md_preview(item.get("markdown", ""))
                row_content.append(
                    label(
                        text=md_preview or "Empty note",
                        ellipsize=Pango.EllipsizeMode.END,
                        xalign=0,
                        css_class="caption",
                    )
                )

            main_btn.set_child(row_content)
            if kind == "highlight":
                main_btn.set_tooltip_text("Go to annotation")
                main_btn.connect("clicked", lambda b, h=item: self.activate_annotation(h))
            else:
                main_btn.set_tooltip_text("Go to note")
                main_btn.connect("clicked", lambda b, n=item: self.activate_note(n))
            linked_box.append(main_btn)

            btn_delete = Gtk.Button(icon_name="user-trash-symbolic")
            if kind == "highlight":
                btn_delete.set_tooltip_text("Delete annotation")
                btn_delete.connect("clicked", lambda b, h=item: self.delete_annotation(h))
            else:
                btn_delete.set_tooltip_text("Delete note")
                btn_delete.connect(
                    "clicked",
                    lambda b, n=item: self.win.notes_layer.delete_note(n)
                    if self.win.notes_layer
                    else None,
                )
            linked_box.append(btn_delete)

            self.annotations_list.append(linked_box)

    def activate_annotation(self, hl: dict):
        self.win.nav_controller.jump_to_annotation(hl)
        self.annotations_popover.popdown()

    def activate_note(self, note: dict):
        self.win.nav_controller.jump_to_note(note)
        self.annotations_popover.popdown()

    def delete_annotation(self, hl: dict):
        if hl in self.win.highlights:
            self.win.highlights.remove(hl)
        self.win.db_service.delete_highlight(hl["id"])
        self.win.canvas.set_highlights(self.win.highlights)
        self.win.canvas.queue_draw()
        self.update_annotations_button()

    def on_highlights_loaded(self, highlights: list[dict]):
        from ..ui.document_view import PdfDocumentView

        if self.win.initial_state is not None:
            return
        self.win.highlights = highlights
        doc_view = self.win.get_active_doc_view()
        if isinstance(doc_view, PdfDocumentView):
            doc_view.highlights = highlights
        if self.win.canvas:
            self.win.canvas.set_highlights(highlights)
            self.win.canvas.queue_draw()
        self.update_annotations_button()

        if self.win._deferred_state_query:
            query = self.win._deferred_state_query
            self.win._deferred_state_query = None
            self.win.entry.set_text(query)
            self.win.run_search(query)

    def on_notes_loaded(self, notes: list[dict]):
        from ..ui.document_view import PdfDocumentView

        if self.win.initial_state is not None:
            return
        self.win.notes = notes
        doc_view = self.win.get_active_doc_view()
        if isinstance(doc_view, PdfDocumentView):
            doc_view.notes = notes
        if self.win.notes_layer is not None:
            self.win.notes_layer.set_notes(notes)
        self.update_annotations_button()

    # --- Selection Toolbar & Highlighting Palette ---

    def _init_selection_toolbar(self):
        self.btn_highlight.set_tooltip_text("Highlight selected text [Ctrl+H]")
        self.btn_highlight.connect("clicked", lambda b: self.apply_highlight_to_selection())
        self.update_highlight_split_button_label()

        popover_palette = Gtk.Popover()
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)

        for idx, hsl in enumerate(PALETTE_COLORS):
            row = idx // PALETTE_COLS
            col = idx % PALETTE_COLS
            hex_color = hsl_to_hex(*hsl)
            color_btn = Gtk.Button()
            color_btn.set_size_request(24, 24)
            color_btn.set_valign(Gtk.Align.CENTER)
            color_btn.set_halign(Gtk.Align.CENTER)
            color_btn.set_tooltip_text(hex_color)

            css = (
                f"button {{ background-color: {hex_color}; background-image: none; "
                f"border-radius: 9999px; border: 1px solid rgba(0,0,0,0.2); "
                f"min-width: 24px; min-height: 24px; padding: 0; margin: 0; }} "
                f"button:hover {{ outline: 2px solid #ffffff; outline-offset: -2px; }}"
            )
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode("utf-8"))
            color_btn.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            def make_color_cb(c: str):
                return lambda b: self.select_highlight_color(c, popover_palette)

            color_btn.connect("clicked", make_color_cb(hex_color))
            grid.attach(color_btn, col, row, 1, 1)

        popover_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
            children=[
                label(text="Highlight Color", css_class="heading", xalign=0),
                grid,
            ],
        )

        btn_clear_hl = button(
            label="Remove Highlights",
            tooltip="Remove highlights in selection",
            on_clicked=lambda b: self.remove_highlights_in_selection(popover_palette),
        )
        popover_box.append(btn_clear_hl)

        popover_palette.set_child(popover_box)
        self.btn_highlight.set_popover(popover_palette)

        self.selection_toolbar.set_visible(False)

        self.info_menu_btn.set_icon_name("dialog-information-symbolic")
        self.info_menu_btn.set_direction(Gtk.ArrowType.UP)
        self.info_menu_btn.set_tooltip_text("Shortcuts Info")
        self.info_menu_btn.add_css_class("flat")

        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.TOP)
        popover_grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        popover.set_child(
            box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
                margin_top=10,
                margin_bottom=10,
                margin_start=12,
                margin_end=12,
                children=[
                    label(text="Text Selection Shortcuts", css_class="heading", xalign=0),
                    popover_grid,
                ],
            )
        )
        popover_grid.attach(label(text="Ctrl+H", css_class="dim-label", xalign=0), 0, 0, 1, 1)
        popover_grid.attach(label(text="Highlight selection", xalign=0), 1, 0, 1, 1)
        popover_grid.attach(label(text="Ctrl+C", css_class="dim-label", xalign=0), 0, 1, 1, 1)
        popover_grid.attach(label(text="Copy source (if available)", xalign=0), 1, 1, 1, 1)
        popover_grid.attach(label(text="Ctrl+Shift+C", css_class="dim-label", xalign=0), 0, 2, 1, 1)
        popover_grid.attach(label(text="Copy PDF text", xalign=0), 1, 2, 1, 1)
        self.info_menu_btn.set_popover(popover)

        self.selection_toolbar.append(self.info_menu_btn)

    def build_selection_toolbar(self) -> Gtk.Box:
        return self.selection_toolbar

    def update_highlight_split_button_label(self):
        circle_swatch = box(css_class="highlight-circle-swatch")
        circle_swatch.set_valign(Gtk.Align.CENTER)
        circle_swatch.set_halign(Gtk.Align.CENTER)
        circle_swatch.set_size_request(18, 18)
        provider = Gtk.CssProvider()
        css = (
            f".highlight-circle-swatch {{ min-width: 18px; min-height: 18px; "
            f"background-color: {self.active_highlight_color}; border-radius: 9999px; "
            f"border: 1px solid rgba(0,0,0,0.3); }}"
        )
        provider.load_from_data(css.encode("utf-8"))
        circle_swatch.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.btn_highlight.set_child(circle_swatch)

    def select_highlight_color(self, hex_color: str, popover: Gtk.Popover):
        self.active_highlight_color = hex_color
        self.update_highlight_split_button_label()
        popover.popdown()
        self.apply_highlight_to_selection()

    def apply_highlight_to_selection(self):
        sel = self.win.canvas.text_selection if self.win.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return

        if sel.anchor_page <= sel.focus_page:
            p_start, p_end = sel.anchor_page, sel.focus_page
        else:
            p_start, p_end = sel.focus_page, sel.anchor_page

        color = self.active_highlight_color

        for page_idx in range(p_start, p_end + 1):
            rng = sel._selection_range(page_idx)
            if rng is None:
                continue
            char_start, char_end = rng
            rects = sel.get_selection_rects(page_idx)
            text = sel.get_selected_text(page_idx)
            if not rects:
                continue

            def make_on_saved(p_i, c_s, c_e, col, rcts, txt):
                def _on_saved(hid: int):
                    hl_obj = {
                        "id": hid,
                        "page": p_i,
                        "char_start": c_s,
                        "char_end": c_e,
                        "color": col,
                        "rects": rcts,
                        "text": txt,
                    }
                    self.win.highlights.append(hl_obj)
                    if self.win.canvas:
                        self.win.canvas.set_highlights(self.win.highlights)
                        self.win.canvas.queue_draw()
                    self.update_annotations_button()

                return _on_saved

            self.win.db_service.save_highlight(
                page_idx,
                char_start,
                char_end,
                color,
                rects,
                text,
                make_on_saved(page_idx, char_start, char_end, color, rects, text),
            )

        if self.win.canvas:
            self.win.canvas.clear_selection()
            self.win.canvas.queue_draw()
        self.update_selection_toolbar(False)

    def remove_highlights_in_selection(self, popover: Gtk.Popover | None = None):
        if popover:
            popover.popdown()

        sel = self.win.canvas.text_selection if self.win.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return

        if sel.anchor_page <= sel.focus_page:
            p_start, p_end = sel.anchor_page, sel.focus_page
        else:
            p_start, p_end = sel.focus_page, sel.anchor_page

        to_remove = []
        for hl in self.win.highlights:
            if p_start <= hl["page"] <= p_end:
                to_remove.append(hl)

        for hl in to_remove:
            self.win.db_service.delete_highlight(hl["id"])
            if hl in self.win.highlights:
                self.win.highlights.remove(hl)

        if self.win.canvas:
            self.win.canvas.set_highlights(self.win.highlights)
            self.win.canvas.queue_draw()
            self.win.canvas.clear_selection()
        self.update_annotations_button()
        self.update_selection_toolbar(False)

    def remove_matching_highlights(self):
        to_remove = self.selection_matching_highlights()
        if not to_remove:
            return
        for hl in to_remove:
            self.win.db_service.delete_highlight(hl["id"])
            if hl in self.win.highlights:
                self.win.highlights.remove(hl)
        if self.win.canvas:
            self.win.canvas.set_highlights(self.win.highlights)
            self.win.canvas.queue_draw()
            self.win.canvas.clear_selection()
        self.update_annotations_button()
        self.update_selection_toolbar(False)

    def selection_matching_highlights(self) -> list[dict]:
        sel = self.win.canvas.text_selection if self.win.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return []

        p_start = min(sel.anchor_page, sel.focus_page)
        p_end = max(sel.anchor_page, sel.focus_page)

        matching = []
        for hl in self.win.highlights:
            hp = hl.get("page", -1)
            if p_start <= hp <= p_end:
                rng = sel._selection_range(hp)
                if rng:
                    sel_start, sel_end = rng
                    hl_start = hl.get("char_start", 0)
                    hl_end = hl.get("char_end", 0)
                    if max(sel_start, hl_start) < min(sel_end, hl_end):
                        matching.append(hl)
        return matching

    def copy_pdf_text_to_clipboard(self):
        """Copy selected PDF plain text to the system clipboard [Ctrl+Shift+C]."""
        sel = self.win.canvas.text_selection if self.win.canvas else None
        if sel is None or not sel.has_selection():
            return
        text = sel.get_selected_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def copy_tex_to_clipboard(self):
        """Copy selected text as LaTeX source TeX if available, otherwise plain PDF text [Ctrl+C]."""
        sel = self.win.canvas.text_selection if self.win.canvas else None
        if sel is None or not sel.has_selection():
            return

        text = ""
        if self.win.arxiv_mapper and self.win.arxiv_mapper.is_ready:
            if sel.anchor_page is not None and sel.focus_page is not None:
                p_start = min(sel.anchor_page, sel.focus_page)
                p_end = max(sel.anchor_page, sel.focus_page)

                latex_parts = []
                for pi in range(p_start, p_end + 1):
                    rng = sel._selection_range(pi)
                    if rng:
                        s_char, e_char = rng
                        tex_snippet = self.win.arxiv_mapper.get_latex_for_pdf_range(
                            pi, s_char, e_char
                        )
                        if tex_snippet:
                            latex_parts.append(tex_snippet)

                if latex_parts:
                    text = "\n".join(latex_parts)

        if not text:
            text = sel.get_selected_text()

        if not text:
            return

        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def copy_selection_to_clipboard(self):
        """Default selection copy handler."""
        self.copy_tex_to_clipboard()

    def update_selection_toolbar(self, has_selection: bool | None = None):
        if has_selection is None:
            sel = self.win.canvas.text_selection if self.win.canvas else None
            has_selection = sel.has_selection() if sel else False

        if has_selection:
            is_tex_available = bool(self.win.arxiv_mapper and self.win.arxiv_mapper.is_ready)
            self.btn_copy_tex.set_visible(is_tex_available)
            self.btn_copy_tex.set_sensitive(is_tex_available)
            if is_tex_available:
                self.btn_copy_tex.set_tooltip_text("Copy source TeX for selection [Ctrl+C]")
            self.btn_remove_hl.set_visible(bool(self.selection_matching_highlights()))
            self.selection_toolbar.set_visible(True)
        else:
            self.selection_toolbar.set_visible(False)
