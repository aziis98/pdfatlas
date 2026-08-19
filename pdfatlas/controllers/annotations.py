from typing import Any
import re
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gtk, Pango

from ..ui.cairo_utils import hsl_to_hex
from ..ui.gui import box, button, label, spacer

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
    text = re.sub(r"(\$+|\*+|_+)", "", " ".join(lines))
    text = re.sub(r"\s{2,}", " ", text).strip()[:MAX_NOTE_PREVIEW_CHARS]
    return text or "(Note)"


# Fluorescent highlighter pen colors sorted strictly by hue with increased lightness (74%-82%)
PALETTE_COLS = 6
PALETTE_COLORS = [
    # Red-Orange to Yellow (H: 18° to 54°)
    (18, 100, 80),   # Peach
    (28, 100, 76),   # Orange
    (42, 100, 74),   # Golden Amber
    (54, 100, 75),   # Fluorescent Yellow
    (82, 100, 74),   # Lemon Lime
    (115, 100, 76),  # Neon Green
    # Green to Cyan-Blue (H: 138° to 222°)
    (138, 90, 78),   # Sea Green
    (152, 95, 78),   # Mint Green
    (172, 95, 78),   # Turquoise
    (188, 100, 78),  # Electric Cyan
    (208, 100, 80),  # Sky Blue
    (222, 100, 82),  # Ice Blue
    # Violet to Red-Pink (H: 245° to 350°)
    (245, 95, 82),   # Lavender
    (265, 95, 80),   # Bright Violet
    (282, 90, 80),   # Bright Plum
    (325, 100, 78),  # Hot Pink
    (338, 100, 80),  # Neon Magenta
    (350, 100, 78),  # Bright Coral
]


class AnnotationsController:
    """
    Manages highlighting palette, text selection toolbar, clipboard export,
    and annotations & notes overview popover list.
    """

    def __init__(self, win: Any):
        self.win = win
        self.active_highlight_color: str = "#FFF49C"

        # Overview popover widgets
        self.annotations_btn: Gtk.MenuButton | None = None
        self.annotations_popover: Gtk.Popover | None = None
        self.annotations_count_label: Gtk.Label | None = None
        self.annotations_list: Gtk.Box | None = None

        # Selection toolbar widgets
        self.selection_toolbar: Gtk.Box | None = None
        self.btn_copy_text: Gtk.Button | None = None
        self.btn_copy_tex: Gtk.Button | None = None
        self.btn_highlight: Adw.SplitButton | None = None
        self.btn_remove_hl: Gtk.Button | None = None
        self.info_menu_btn: Gtk.MenuButton | None = None

    def build_annotations_popover(self) -> Gtk.Popover:
        self.annotations_popover = Gtk.Popover()
        if self.annotations_btn:
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
        self.annotations_count_label = label(text="Annotations (0)", css_class="bold")
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

        self.annotations_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.annotations_list.add_css_class("annotation-list")
        scrolled.set_child(self.annotations_list)
        popover_box.append(scrolled)

        self.annotations_popover.set_child(popover_box)
        self.annotations_popover.connect("notify::visible", self._on_annotations_popover_visibility)
        return self.annotations_popover

    def _on_annotations_popover_visibility(self, popover, pspec):
        if popover.get_visible():
            self.update_annotations_button()

    def update_annotations_button(self):
        count = len(self.win.highlights) + len(self.win.notes)
        if self.annotations_btn:
            self.annotations_btn.set_visible(count > 0)
        if self.annotations_count_label:
            self.annotations_count_label.set_text(f"Annotations ({count})")

        if not self.annotations_list:
            return

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

            item_box = box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                margin_start=4,
                margin_end=4,
                margin_top=2,
                margin_bottom=2,
            )

            if kind == "highlight":
                color_swatch = Gtk.Box()
                color_swatch.set_size_request(14, 14)
                color_swatch.set_valign(Gtk.Align.CENTER)
                color_swatch.add_css_class("highlight-circle-swatch")
                bg_color = item.get("color", "#FFEE55")
                provider = Gtk.CssProvider()
                provider.load_from_string(
                    f".highlight-circle-swatch {{ background-color: {bg_color}; border-radius: 9999px; min-width: 14px; min-height: 14px; }}"
                )
                color_swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                item_box.append(color_swatch)
                txt = (item.get("text", "") or "").strip() or "(Highlight)"
            else:
                note_icon = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
                note_icon.set_pixel_size(14)
                note_icon.set_valign(Gtk.Align.CENTER)
                item_box.append(note_icon)
                md_text = item.get("markdown", "") or ""
                txt = simplify_md_preview(md_text)

            txt_lbl = label(text=txt)
            txt_lbl.set_single_line_mode(True)
            txt_lbl.set_lines(1)
            txt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            txt_lbl.set_halign(Gtk.Align.START)
            txt_lbl.set_xalign(0.0)
            txt_lbl.set_hexpand(True)
            item_box.append(txt_lbl)

            linked_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            linked_box.add_css_class("linked")
            linked_box.set_hexpand(True)

            main_btn = Gtk.Button()
            main_btn.set_hexpand(True)
            main_btn.set_child(item_box)
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
                btn_delete.connect("clicked", lambda b, n=item: self.win.notes_layer.delete_note(n))
            linked_box.append(btn_delete)

            self.annotations_list.append(linked_box)

    def activate_annotation(self, hl: dict):
        self.win.nav_controller.jump_to_annotation(hl)
        if self.annotations_popover:
            self.annotations_popover.popdown()

    def activate_note(self, note: dict):
        self.win.nav_controller.jump_to_note(note)
        if self.annotations_popover:
            self.annotations_popover.popdown()

    def delete_annotation(self, hl: dict):
        if hl in self.win.highlights:
            self.win.highlights.remove(hl)
        self.win.db_service.delete_highlight(hl["id"])
        self.win.canvas.set_highlights(self.win.highlights)
        self.win.canvas.queue_draw()
        self.update_annotations_button()

    def on_highlights_loaded(self, highlights: list[dict]):
        if getattr(self.win, "initial_state", None):
            return
        self.win.highlights = highlights
        doc_view = self.win.get_active_doc_view()
        if doc_view and hasattr(doc_view, "highlights"):
            doc_view.highlights = highlights
        if self.win.canvas:
            self.win.canvas.set_highlights(highlights)
            self.win.canvas.queue_draw()
        self.update_annotations_button()

        if getattr(self.win, "_deferred_state_query", None):
            query = self.win._deferred_state_query
            self.win._deferred_state_query = None
            self.win.entry.set_text(query)
            self.win.run_search(query)

    def on_notes_loaded(self, notes: list[dict]):
        if getattr(self.win, "initial_state", None):
            return
        self.win.notes = notes
        doc_view = self.win.get_active_doc_view()
        if doc_view and hasattr(doc_view, "notes"):
            doc_view.notes = notes
        if hasattr(self.win, "notes_layer"):
            self.win.notes_layer.set_notes(notes)
        self.update_annotations_button()

    # --- Selection Toolbar & Highlighting Palette ---

    def build_selection_toolbar(self) -> Gtk.Box:
        self.btn_copy_text = button(
            label="Copy",
            tooltip="Copy selected PDF text [Ctrl+Shift+C]",
            on_clicked=lambda b: self.copy_pdf_text_to_clipboard(),
        )
        self.btn_copy_tex = button(
            label="Copy Source TeX",
            tooltip="Copy source TeX for selection [Ctrl+C]",
            on_clicked=lambda b: self.copy_tex_to_clipboard(),
        )

        # Highlight SplitButton
        self.btn_highlight = Adw.SplitButton()
        self.btn_highlight.set_tooltip_text("Highlight selected text [Ctrl+H]")
        self.btn_highlight.connect("clicked", lambda b: self.apply_highlight_to_selection())
        self.update_highlight_split_button_label()

        self.btn_remove_hl = button(
            label="Remove",
            tooltip="Remove the selected highlight",
            on_clicked=lambda b: self.remove_matching_highlights(),
        )

        popover_palette = Gtk.Popover()
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)

        for idx, hsl in enumerate(PALETTE_COLORS):
            row = idx // PALETTE_COLS
            col = idx % PALETTE_COLS
            hex_color = hsl_to_hex(*hsl)
            color_btn = Gtk.Button()
            color_btn.set_size_request(24, 24)
            color_btn.set_tooltip_text(hex_color)

            provider = Gtk.CssProvider()
            provider.load_from_data(
                f"button {{ background-color: {hex_color}; background-image: none; border-radius: 4px; border: 1px solid rgba(0,0,0,0.2); min-width: 24px; min-height: 24px; padding: 0; margin: 0; }} button:hover {{ outline: 2px solid #ffffff; outline-offset: -2px; }}".encode(
                    "utf-8"
                )
            )
            color_btn.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

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

        self.selection_toolbar = box(
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
        self.selection_toolbar.set_visible(False)

        self.info_menu_btn = Gtk.MenuButton()
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
        return self.selection_toolbar

    def update_highlight_split_button_label(self):
        if not self.btn_highlight:
            return
        circle_swatch = box(css_class="highlight-circle-swatch")
        provider = Gtk.CssProvider()
        provider.load_from_data(
            f".highlight-circle-swatch {{ min-width: 18px; min-height: 18px; background-color: {self.active_highlight_color}; border-radius: 50%; border: 1px solid rgba(0,0,0,0.3); }}".encode(
                "utf-8"
            )
        )
        circle_swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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

        sel.clear_selection()
        self.update_selection_toolbar(False)
        self.win.canvas.queue_draw()

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

        self.win.canvas.set_highlights(self.win.highlights)
        self.win.canvas.queue_draw()
        self.update_annotations_button()
        sel.clear_selection()
        self.update_selection_toolbar(False)

    def remove_matching_highlights(self):
        to_remove = self.selection_matching_highlights()
        if not to_remove:
            return
        for hl in to_remove:
            self.win.db_service.delete_highlight(hl["id"])
            if hl in self.win.highlights:
                self.win.highlights.remove(hl)
        self.win.canvas.set_highlights(self.win.highlights)
        self.win.canvas.queue_draw()
        self.update_annotations_button()
        sel = self.win.canvas.text_selection if self.win.canvas else None
        if sel:
            sel.clear_selection()
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
                        tex_snippet = self.win.arxiv_mapper.get_latex_for_pdf_range(pi, s_char, e_char)
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
        if self.selection_toolbar:
            if has_selection is None:
                sel = self.win.canvas.text_selection if self.win.canvas else None
                has_selection = sel.has_selection() if sel else False

            if has_selection:
                is_tex_available = bool(self.win.arxiv_mapper and self.win.arxiv_mapper.is_ready)
                if self.btn_copy_tex:
                    self.btn_copy_tex.set_visible(is_tex_available)
                    self.btn_copy_tex.set_sensitive(is_tex_available)
                    if is_tex_available:
                        self.btn_copy_tex.set_tooltip_text("Copy source TeX for selection [Ctrl+C]")
                if self.btn_remove_hl:
                    self.btn_remove_hl.set_visible(bool(self.selection_matching_highlights()))
                self.selection_toolbar.set_visible(True)
            else:
                self.selection_toolbar.set_visible(False)
