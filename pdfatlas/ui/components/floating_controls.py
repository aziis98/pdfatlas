from typing import Any
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

from ..gui import box, button, label


class FloatingControls:
    """
    Builder and manager for floating overlay widgets on the canvas:
    - Floating bottom-right zoom button controls & percentage label.
    - Floating bottom-left link/hover preview bar & progress indicators.
    - Floating debug cache & layout info overlays.
    """

    def __init__(self, win: Any):
        self.win = win
        self.zoom_label: Gtk.Label | None = None
        self.zoom_floating_box: Gtk.Box | None = None
        self.link_preview_label: Gtk.Label | None = None
        self.link_preview_card_box: Gtk.Box | None = None
        self.progress_label: Gtk.Label | None = None
        self.progress_card_box: Gtk.Box | None = None
        self.link_preview_box: Gtk.Box | None = None
        self.debug_info_label: Gtk.Label | None = None
        self.debug_arxiv_label: Gtk.Label | None = None
        self.debug_cache_label: Gtk.Label | None = None

    def build_floating_zoom_controls(self) -> Gtk.Box:
        self.zoom_label = label(text="100%", css_class="zoom-floating-label")
        self.zoom_floating_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            css_class="zoom-floating-box",
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            margin_end=20,
            margin_bottom=20,
            children=[
                button(
                    icon_name="zoom-in-symbolic",
                    tooltip="Zoom In",
                    css_class="flat",
                    on_clicked=lambda b: self.win.zoom_in(),
                ),
                self.zoom_label,
                button(
                    icon_name="zoom-out-symbolic",
                    tooltip="Zoom Out",
                    css_class="flat",
                    on_clicked=lambda b: self.win.zoom_out(),
                ),
            ],
        )
        return self.zoom_floating_box

    def build_floating_link_preview(self) -> Gtk.Box:
        self.link_preview_label = label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=65)

        self.link_preview_card_box = box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            css_class="link-preview-box",
            halign=Gtk.Align.START,
            children=[self.link_preview_label],
        )
        self.link_preview_card_box.set_visible(False)

        self.progress_label = label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=65)
        self.progress_card_box = box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            css_class="link-preview-box",
            halign=Gtk.Align.START,
            children=[self.progress_label],
        )
        self.progress_card_box.set_visible(False)

        self.link_preview_box = box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            halign=Gtk.Align.START,
            valign=Gtk.Align.END,
            margin_start=8,
            margin_bottom=8,
            children=[self.progress_card_box, self.link_preview_card_box],
        )

        if self.win.debug_mode:
            self.debug_info_label = label(
                xalign=0.0,
                halign=Gtk.Align.START,
                justify=Gtk.Justification.LEFT,
                css_class="debug-info-label",
            )
            self.debug_info_label.set_visible(False)
            self.link_preview_box.append(self.debug_info_label)

            self.debug_arxiv_label = label(
                xalign=0.0,
                halign=Gtk.Align.START,
                justify=Gtk.Justification.LEFT,
                css_class="debug-info-label",
                wrap=True,
                max_width_chars=80,
            )
            self.debug_arxiv_label.set_visible(False)
            self.link_preview_box.append(self.debug_arxiv_label)
        else:
            self.debug_info_label = None
            self.debug_arxiv_label = None

        self.link_preview_box.set_visible(False)
        return self.link_preview_box

    def build_debug_cache_box(self) -> Gtk.Label:
        self.debug_cache_label = Gtk.Label(xalign=0.0)
        self.debug_cache_label.set_halign(Gtk.Align.START)
        self.debug_cache_label.set_justify(Gtk.Justification.LEFT)
        self.debug_cache_label.add_css_class("debug-info-label")
        self.debug_cache_label.set_visible(True)

        if self.link_preview_box:
            self.link_preview_box.append(self.debug_cache_label)

        self.refresh_debug_cache()
        GLib.timeout_add(1000, self.refresh_debug_cache)
        return self.debug_cache_label

    def refresh_debug_cache(self) -> bool:
        if not self.win.debug_mode or not self.debug_cache_label:
            return False
        entries = self.win.render_cache.total_entries()
        cache_mb = self.win.render_cache.total_bytes() / (1024 * 1024)
        tex_mb = self.win.canvas.texture_bytes() / (1024 * 1024) if self.win.canvas else 0.0
        text = f"CACHE:    {entries} entries, {cache_mb:.1f}MB\nTEXTURES: {tex_mb:.1f}MB GPU"
        self.debug_cache_label.set_text(text)
        return True

    def on_page_hovered(self, page_index: int | None, x: float, y: float):
        if not self.win.debug_mode or not self.debug_info_label or not self.win.canvas:
            return

        if (
            page_index is not None
            and self.win.canvas.page_layout
            and 0 <= page_index < len(self.win.canvas.page_layout)
        ):
            y_offset, dw, dh, crop_rect = self.win.canvas.page_layout[page_index]
            crop_str = (
                f"({crop_rect.x0:.1f}, {crop_rect.y0:.1f}, {crop_rect.x1:.1f}, {crop_rect.y1:.1f})"
                if crop_rect is not None
                else "uncropped"
            )
            scroll_y = self.win.vadjustment.get_value() if self.win.vadjustment else 0.0
            total_pages = self.win.doc_model.page_count if self.win.doc_model else "?"
            debug_txt = (
                f"PAGE:     {page_index + 1} / {total_pages} (index {page_index})\n"
                f"LAYOUT:   y_off={y_offset:.1f}px | width={dw:.1f}px | height={dh:.1f}px\n"
                f"CROP:     {crop_str}\n"
                f"VIEWPORT: zoom={self.win.zoom:.2f} | scale={self.win.canvas.dpi_scale_factor:.1f} | scroll_y={scroll_y:.1f}px"
            )
            self.debug_info_label.set_text(debug_txt)
            self.debug_info_label.set_visible(True)

            if (
                self.debug_arxiv_label
                and self.win.arxiv_mapper
                and self.win.arxiv_mapper.is_ready
                and self.win.canvas.text_selection
            ):
                pt = self.win.canvas._screen_to_pdf_point(x, y, page_index)
                if pt is not None:
                    char_idx = self.win.canvas.text_selection.hit_test(page_index, pt[0], pt[1])
                    if char_idx is not None:
                        w_start = self.win.canvas.text_selection.get_word_start_char_idx(page_index, char_idx)
                        pdf_frag, tex_frag = self.win.arxiv_mapper.get_cursor_fragment(
                            page_index, w_start, window_words=50
                        )
                        pi = self.win.canvas.text_selection.get_page_index(page_index)
                        curr_c_rect = pi.chars[char_idx].bbox if 0 <= char_idx < len(pi.chars) else None
                        curr_w_rects = self.win.canvas.text_selection.get_word_rects_for_char(page_index, char_idx)
                        fwd_c_rects = self.win.canvas.text_selection.get_forward_char_rects(
                            page_index, w_start, word_count=50
                        )

                        new_data = {
                            "page_index": page_index,
                            "curr_word_rects": curr_w_rects,
                            "curr_char_rect": curr_c_rect,
                            "forward_char_rects": fwd_c_rects,
                        }

                        if self.win.canvas.debug_arxiv_data != new_data:
                            self.win.canvas.debug_arxiv_data = new_data
                            self.win.canvas.queue_draw_overlays("debug-arxiv-data")

                        if pdf_frag or tex_frag:
                            arxiv_txt = (
                                "ARXIV CURSOR FRAGMENT (~50 words forward):\n\n"
                                f"PDF:  {pdf_frag}\n\n"
                                f"TEX:  {tex_frag}"
                            )
                            self.debug_arxiv_label.set_text(arxiv_txt)
                            self.debug_arxiv_label.set_visible(True)
                        else:
                            self.debug_arxiv_label.set_visible(False)
                    else:
                        self.debug_arxiv_label.set_visible(False)
                        if self.win.canvas.debug_arxiv_data is not None:
                            self.win.canvas.debug_arxiv_data = None
                            self.win.canvas.queue_draw_overlays("debug-arxiv-clear")
                else:
                    self.debug_arxiv_label.set_visible(False)
                    if self.win.canvas.debug_arxiv_data is not None:
                        self.win.canvas.debug_arxiv_data = None
                        self.win.canvas.queue_draw_overlays("debug-arxiv-clear")
            if self.link_preview_box:
                self.link_preview_box.set_visible(True)
        else:
            self.debug_info_label.set_text("")
            self.debug_info_label.set_visible(False)
            if self.debug_arxiv_label:
                self.debug_arxiv_label.set_text("")
                self.debug_arxiv_label.set_visible(False)
            if self.win.canvas and self.win.canvas.debug_arxiv_data is not None:
                self.win.canvas.debug_arxiv_data = None
                self.win.canvas.queue_draw_overlays("debug-arxiv-clear")
            if self.link_preview_card_box and not self.link_preview_card_box.get_visible() and self.link_preview_box:
                self.link_preview_box.set_visible(False)
