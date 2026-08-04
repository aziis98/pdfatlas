
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from OpenGL import GL as gl

from .cairo_utils import hex_to_rgba
from .gl_renderer import QuadRenderer

if TYPE_CHECKING:
    from .canvas import PDFCanvas


class GLCanvas(Gtk.GLArea):
    """
    Hardware-accelerated OpenGL background rendering canvas.
    Renders visible pages from the RenderCache as GPU textures behind the transparent Gtk.ScrolledWindow.
    """

    def __init__(self, canvas_layout_provider: "PDFCanvas"):
        super().__init__()
        self.layout_provider: PDFCanvas = canvas_layout_provider
        self._renderer: QuadRenderer | None = None

        self.set_required_version(3, 3)
        self.set_has_depth_buffer(False)
        self.set_has_stencil_buffer(False)

        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)

    def _on_realize(self, area):
        self.make_current()
        err = self.get_error()
        if err is not None:
            print(f"[GLCanvas] Context realization error: {err.message}")
            return
        try:
            self._renderer = QuadRenderer()
            self._renderer.initialize()
        except Exception as e:
            print(f"[GLCanvas] Failed to initialize OpenGL pipeline: {e}")
            self.set_error(GLib.Error.new_literal(GLib.quark_from_string("opengl"), str(e), 1))

    def _on_unrealize(self, area):
        self.make_current()
        if self._renderer:
            self._renderer.cleanup()
            self._renderer = None

    def texture_bytes(self) -> int:
        if not self._renderer:
            return 0
        return sum(
            tex.get_width() * tex.get_height() * 4
            for tex in self._renderer.textures.keys()
        )

    def _draw_rect(self, r: QuadRenderer, x: float, y: float, w: float, h: float,
                   color: tuple[float, float, float, float], border: float = 0.0):
        if border > 0.0:
            r.fill_rect(x, y, w, border, color)
            r.fill_rect(x, y + h - border, w, border, color)
            r.fill_rect(x, y, border, h, color)
            r.fill_rect(x + w - border, y, border, h, color)
        else:
            r.fill_rect(x, y, w, h, color)

    def _on_render(self, area, context):
        canvas = self.layout_provider
        r = self._renderer
        if not canvas or not canvas.page_layout or not canvas.vadjustment or not r:
            return False

        x_min = canvas.hadjustment.get_value() if canvas.hadjustment else 0.0
        y_min = canvas.vadjustment.get_value()
        page_size = canvas.vadjustment.get_page_size()
        y_max = y_min + page_size

        scale_factor = canvas.get_scale_factor()
        viewport_w = self.get_allocated_width()
        viewport_h = self.get_allocated_height()
        gl_scale = self.get_scale_factor()
        scale = canvas.zoom * canvas.dpi_scale_factor

        r.begin(viewport_w, viewport_h, x_min, y_min, gl_scale)
        active_surfaces = set()

        page_count = len(canvas.page_layout)
        for i in range(page_count):
            y_offset, dw, dh, crop_rect = canvas.page_layout[i]
            page_y0 = float(round(y_offset))
            page_y1 = page_y0 + dh

            if page_y1 < y_min or page_y0 > y_max:
                continue

            x_offset = float(round((viewport_w - dw) / 2.0))

            r.white_card(x_offset, page_y0, dw, dh)

            if canvas.cache is None:
                continue

            surface = canvas.cache.get(i, canvas.zoom, scale_factor, crop_rect)
            if surface is None:
                surface = canvas.cache.get_best(i, canvas.zoom, scale_factor, crop_rect)

            if surface is not None:
                active_surfaces.add(surface)
                tex_id = r.upload_surface(surface)
                r.textured(tex_id, x_offset, page_y0, dw, dh)

                hl_block = getattr(canvas, "highlighted_block", None)
                if hl_block is not None:
                    h_page_idx, h_bbox = hl_block
                    if h_page_idx == i:
                        bx0, by0, bx1, by1 = h_bbox
                        co_x = crop_rect.x0 if crop_rect is not None else 0.0
                        co_y = crop_rect.y0 if crop_rect is not None else 0.0
                        hx = x_offset + (bx0 - co_x) * scale
                        hy = page_y0 + (by0 - co_y) * scale
                        hw = (bx1 - bx0) * scale
                        hh = (by1 - by0) * scale
                        r.fill_rect(hx, hy, hw, hh, (0.35, 0.2975, 0.0, 0.35))

                if hasattr(canvas, "highlights") and canvas.highlights:
                    co_x = crop_rect.x0 if crop_rect is not None else 0.0
                    co_y = crop_rect.y0 if crop_rect is not None else 0.0
                    for hl in canvas.highlights:
                        if hl.get("page") == i:
                            color_hex = hl.get("color", "#FFEE55")
                            cr_val, cg_val, cb_val, ca_val = hex_to_rgba(color_hex, 1.0)
                            for rx0, ry0, rx1, ry1 in hl.get("rects", []):
                                hx0 = x_offset + (rx0 - co_x) * scale
                                hy0 = page_y0 + (ry0 + 2.0 - co_y) * scale
                                hw = (rx1 - rx0) * scale
                                hh = (ry1 - ry0) * scale
                                r.fill_rect(hx0, hy0, hw, hh, (cr_val, cg_val, cb_val, ca_val), mode="multiply")

                if canvas.text_selection is not None and (canvas.text_selection.is_selecting or canvas.text_selection.has_selection()):
                    win_obj = getattr(canvas, "win", None)
                    arxiv_mapper = getattr(win_obj, "arxiv_mapper", None) if win_obj else None
                    if arxiv_mapper and arxiv_mapper.is_ready and arxiv_mapper.word_metadata:
                        pi = canvas.text_selection.get_page_index(i)
                        co_x = crop_rect.x0 if crop_rect is not None else 0.0
                        co_y = crop_rect.y0 if crop_rect is not None else 0.0
                        light_green = (0.2, 0.9, 0.3, 0.15)
                        mapped_set = getattr(arxiv_mapper, "mapped_pdf_indices", set(arxiv_mapper.tex_to_pdf_map.values()))
                        for w_idx, w_meta in enumerate(arxiv_mapper.word_metadata):
                            if w_meta[0] == i and w_idx in mapped_set:
                                _, wc_start, wc_end = w_meta
                                if 0 <= wc_start < len(pi.chars) and 0 <= wc_end < len(pi.chars):
                                    w_chars = pi.chars[wc_start : wc_end + 1]
                                    if w_chars:
                                        wx0 = min(c.bbox[0] for c in w_chars)
                                        wy0 = min(c.bbox[1] for c in w_chars)
                                        wx1 = max(c.bbox[2] for c in w_chars)
                                        wy1 = max(c.bbox[3] for c in w_chars)
                                        sx = x_offset + (wx0 - co_x) * scale
                                        sy = page_y0 + (wy0 - co_y) * scale
                                        sw = (wx1 - wx0) * scale
                                        sh = (wy1 - wy0) * scale
                                        r.fill_rect(sx, sy, sw, sh, light_green)

                    sel_rects = canvas.text_selection.get_selection_rects(i)
                    if sel_rects:
                        co_x = crop_rect.x0 if crop_rect is not None else 0.0
                        co_y = crop_rect.y0 if crop_rect is not None else 0.0
                        for rx0, ry0, rx1, ry1 in sel_rects:
                            sx = x_offset + (rx0 - co_x) * scale
                            sy = page_y0 + (ry0 - co_y) * scale
                            sw = (rx1 - rx0) * scale
                            sh = (ry1 - ry0) * scale
                            r.fill_rect(sx, sy, sw, sh, (0.07, 0.175, 0.35, 0.35))

                if canvas.debug_mode and canvas.debug_arxiv_data is not None and canvas.debug_arxiv_data.get("page_index") == i:
                    d_data = canvas.debug_arxiv_data
                    co_x = crop_rect.x0 if crop_rect is not None else 0.0
                    co_y = crop_rect.y0 if crop_rect is not None else 0.0

                    for rx0, ry0, rx1, ry1 in d_data.get("curr_word_rects", []):
                        r.fill_rect(
                            x_offset + (rx0 - co_x) * scale,
                            page_y0 + (ry0 - co_y) * scale,
                            (rx1 - rx0) * scale, (ry1 - ry0) * scale,
                            (0.525, 0.2625, 0.75, 0.75),
                        )

                    bt = 1.0
                    for rx0, ry0, rx1, ry1 in d_data.get("forward_char_rects", []):
                        sx = x_offset + (rx0 - co_x) * scale
                        sy = page_y0 + (ry0 - co_y) * scale
                        sw = (rx1 - rx0) * scale
                        sh = (ry1 - ry0) * scale
                        c = (0.175, 0.0875, 0.25, 0.25)
                        r.fill_rect(sx, sy, sw, bt, c)
                        r.fill_rect(sx, sy + sh - bt, sw, bt, c)
                        r.fill_rect(sx, sy, bt, sh, c)
                        r.fill_rect(sx + sw - bt, sy, bt, sh, c)

                    c_rect = d_data.get("curr_char_rect")
                    if c_rect:
                        rx0, ry0, rx1, ry1 = c_rect
                        sx = x_offset + (rx0 - co_x) * scale
                        sy = page_y0 + (ry0 - co_y) * scale
                        sw = (rx1 - rx0) * scale
                        sh = (ry1 - ry0) * scale
                        c = (0.7, 0.35, 1.0, 1.0)
                        bt = 1.5
                        r.fill_rect(sx, sy, sw, bt, c)
                        r.fill_rect(sx, sy + sh - bt, sw, bt, c)
                        r.fill_rect(sx, sy, bt, sh, c)
                        r.fill_rect(sx + sw - bt, sy, bt, sh, c)

                if canvas.hover_caret is not None and canvas.hover_caret[0] == i:
                    _page_idx, (cx, cy0, cy1) = canvas.hover_caret
                    co_x = crop_rect.x0 if crop_rect is not None else 0.0
                    co_y = crop_rect.y0 if crop_rect is not None else 0.0
                    sx = x_offset + (cx - co_x) * scale
                    sy = page_y0 + (cy0 - co_y) * scale
                    sh = (cy1 - cy0) * scale
                    r.fill_rect(sx - 1.0, sy, 2.0, sh, (0.48, 0.48, 0.48, 0.9))

                if (canvas.debug_mode and canvas.text_selection is not None
                        and (canvas.text_selection.is_selecting or canvas.text_selection.has_selection())):
                    pi = canvas.text_selection.get_page_index(i)
                    co_x = crop_rect.x0 if crop_rect is not None else 0.0
                    co_y = crop_rect.y0 if crop_rect is not None else 0.0

                    # 1. Highlight snapped start and end word boxes in debug selection mode
                    rng = canvas.text_selection._selection_range(i)
                    if rng is not None and pi.chars:
                        s_char, e_char = rng

                        win_obj = getattr(canvas, "win", None)
                        arxiv_mapper = getattr(win_obj, "arxiv_mapper", None) if win_obj else None

                        if arxiv_mapper and arxiv_mapper.is_ready and arxiv_mapper.word_metadata:
                            w_start, w_end = arxiv_mapper.find_pdf_word_range(i, s_char, e_char)
                            if w_start <= w_end:
                                total_snapped = w_end - w_start + 1
                                for idx_in_range, w_idx in enumerate(range(w_start, w_end + 1)):
                                    w_meta = arxiv_mapper.word_metadata[w_idx]
                                    if w_meta[0] == i:
                                        _, wc_start, wc_end = w_meta
                                        if 0 <= wc_start < len(pi.chars) and 0 <= wc_end < len(pi.chars):
                                            w_chars = pi.chars[wc_start : wc_end + 1]
                                            if w_chars:
                                                wx0 = min(c.bbox[0] for c in w_chars)
                                                wy0 = min(c.bbox[1] for c in w_chars)
                                                wx1 = max(c.bbox[2] for c in w_chars)
                                                wy1 = max(c.bbox[3] for c in w_chars)

                                                sx = x_offset + (wx0 - co_x) * scale
                                                sy = page_y0 + (wy0 - co_y) * scale
                                                sw = (wx1 - wx0) * scale
                                                sh = (wy1 - wy0) * scale

                                                is_first = (idx_in_range == 0)
                                                is_last = (idx_in_range == total_snapped - 1)

                                                if is_first:
                                                    r.fill_rect(sx, sy, sw, sh, (1.0, 0.2, 0.5, 0.45))
                                                    r.fill_rect(sx, sy, sw, 2.0, (1.0, 0.2, 0.5, 1.0))
                                                elif is_last:
                                                    r.fill_rect(sx, sy, sw, sh, (1.0, 0.6, 0.0, 0.45))
                                                    r.fill_rect(sx, sy, sw, 2.0, (1.0, 0.6, 0.0, 1.0))
                                                else:
                                                    r.fill_rect(sx, sy, sw, sh, (1.0, 0.85, 0.2, 0.2))
                        else:
                            # Universal fallback for local PDFs: calculate start and end word boxes
                            sw_start = canvas.text_selection.get_word_start_char_idx(i, s_char)
                            sw_end = sw_start
                            while (sw_end < len(pi.chars) - 1
                                   and pi.chars[sw_end].char != " "
                                   and abs(pi.chars[sw_end + 1].line_y - pi.chars[sw_start].line_y) < 3.0):
                                sw_end += 1

                            s_chars = pi.chars[sw_start : sw_end + 1]
                            if s_chars:
                                sx0 = x_offset + (min(c.bbox[0] for c in s_chars) - co_x) * scale
                                sy0 = page_y0 + (min(c.bbox[1] for c in s_chars) - co_y) * scale
                                sw = (max(c.bbox[2] for c in s_chars) - min(c.bbox[0] for c in s_chars)) * scale
                                sh = (max(c.bbox[3] for c in s_chars) - min(c.bbox[1] for c in s_chars)) * scale
                                r.fill_rect(sx0, sy0, sw, sh, (1.0, 0.2, 0.5, 0.45))
                                r.fill_rect(sx0, sy0, sw, 2.0, (1.0, 0.2, 0.5, 1.0))

                            ew_start = canvas.text_selection.get_word_start_char_idx(i, e_char)
                            ew_end = ew_start
                            while (ew_end < len(pi.chars) - 1
                                   and pi.chars[ew_end].char != " "
                                   and abs(pi.chars[ew_end + 1].line_y - pi.chars[ew_start].line_y) < 3.0):
                                ew_end += 1

                            e_chars = pi.chars[ew_start : ew_end + 1]
                            if e_chars:
                                ex0 = x_offset + (min(c.bbox[0] for c in e_chars) - co_x) * scale
                                ey0 = page_y0 + (min(c.bbox[1] for c in e_chars) - co_y) * scale
                                ew = (max(c.bbox[2] for c in e_chars) - min(c.bbox[0] for c in e_chars)) * scale
                                eh = (max(c.bbox[3] for c in e_chars) - min(c.bbox[1] for c in e_chars)) * scale
                                r.fill_rect(ex0, ey0, ew, eh, (1.0, 0.6, 0.0, 0.45))
                                r.fill_rect(ex0, ey0, ew, 2.0, (1.0, 0.6, 0.0, 1.0))

                    bt = 0.8
                    gc = (0.0, 0.8, 0.0, 0.4)
                    for c in pi.chars:
                        cx0 = x_offset + (c.bbox[0] - co_x) * scale
                        cy0 = page_y0 + (c.bbox[1] - co_y) * scale
                        cw = (c.bbox[2] - c.bbox[0]) * scale
                        ch = (c.bbox[3] - c.bbox[1]) * scale
                        r.fill_rect(cx0, cy0, cw, bt, gc)
                        r.fill_rect(cx0, cy0 + ch - bt, cw, bt, gc)
                        r.fill_rect(cx0, cy0, bt, ch, gc)
                        r.fill_rect(cx0 + cw - bt, cy0, bt, ch, gc)

                    if canvas.text_selection.anchor_char_idx is not None:
                        ac = pi.chars[canvas.text_selection.anchor_char_idx]
                        ax = x_offset + ((ac.bbox[0] + ac.bbox[2]) / 2.0 - co_x) * scale - 4.0
                        ay = page_y0 + ((ac.bbox[1] + ac.bbox[3]) / 2.0 - co_y) * scale - 4.0
                        r.fill_rect(ax, ay, 8.0, 8.0, (1.0, 0.0, 0.0, 0.9))

                    if canvas.text_selection.focus_char_idx is not None:
                        fc = pi.chars[canvas.text_selection.focus_char_idx]
                        fx = x_offset + ((fc.bbox[0] + fc.bbox[2]) / 2.0 - co_x) * scale - 4.0
                        fy = page_y0 + ((fc.bbox[1] + fc.bbox[3]) / 2.0 - co_y) * scale - 4.0
                        r.fill_rect(fx, fy, 8.0, 8.0, (0.0, 0.0, 1.0, 0.9))

                if canvas.doc_model:
                    links = canvas.doc_model.get_page_links(i)
                    co_x = crop_rect.x0 if crop_rect is not None else 0.0
                    co_y = crop_rect.y0 if crop_rect is not None else 0.0

                    for link in links:
                        from_rect = link.get("from")
                        if not from_rect:
                            continue

                        lx0 = x_offset + (from_rect.x0 - co_x) * scale
                        ly0 = page_y0 + (from_rect.y0 - co_y) * scale
                        lw = (from_rect.x1 - from_rect.x0) * scale
                        lh = (from_rect.y1 - from_rect.y0) * scale

                        is_hovered = (
                            canvas.hovered_link is not None
                            and canvas.hovered_link[0] == i
                            and canvas.hovered_link[1] is link
                        )
                        is_uri = link.get("kind") == 2

                        if is_hovered:
                            if is_uri:
                                r.fill_rect(lx0, ly0, lw, lh, (0.054, 0.228, 0.147, 0.30))
                            else:
                                r.fill_rect(lx0, ly0, lw, lh, (0.06, 0.156, 0.27, 0.30))

                        if is_uri:
                            ec = (0.153, 0.646, 0.4165, 0.85)
                        else:
                            ec = (0.17, 0.442, 0.765, 0.85)
                        bt = 1.8
                        r.fill_rect(lx0, ly0, lw, bt, ec)
                        r.fill_rect(lx0, ly0 + lh - bt, lw, bt, ec)
                        r.fill_rect(lx0, ly0, bt, lh, ec)
                        r.fill_rect(lx0 + lw - bt, ly0, bt, lh, ec)

                if getattr(canvas, "debug_mode", False):
                    mc = (0.9, 0.0, 0.9, 0.9)
                    mb_t = 2.0
                    r.fill_rect(x_offset, page_y0, dw, mb_t, mc)
                    r.fill_rect(x_offset, page_y0 + dh - mb_t, dw, mb_t, mc)
                    r.fill_rect(x_offset, page_y0, mb_t, dh, mc)
                    r.fill_rect(x_offset + dw - mb_t, page_y0, mb_t, dh, mc)
            else:
                r.fill_rect(x_offset, page_y0, dw, dh, (0.95, 0.95, 0.95, 1.0))

        evicted = [s for s in r.textures if s not in active_surfaces]
        for s in evicted:
            tex_id = r.textures.pop(s)
            gl.glDeleteTextures([tex_id])

        r.end()
        gl.glUseProgram(0)
        gl.glDisable(gl.GL_BLEND)
        return False
