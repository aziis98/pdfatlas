import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class PageContainer(Gtk.Box):
    """
    A lightweight layout container representing a single PDF page.
    Maintains a fixed size and dynamically mounts/unmounts its internal
    Gtk.DrawingArea canvas based on visible viewport intersections.
    """

    def __init__(self, page_index: int, canvas):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.page_index = page_index
        self.canvas_parent = canvas
        self.y_offset = 0.0
        self.w = 0.0
        self.h = 0.0
        self.crop_rect = None
        self.drawing_area: Gtk.DrawingArea | None = None
        self.page_is_visible = False

        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_focusable(False)
        self.add_css_class("page-container")

    def set_layout_params(self, y_offset: float, w: float, h: float, crop_rect):
        """Update size requests and positions during zoom or crop events."""
        self.y_offset = y_offset
        self.w = w
        self.h = h
        self.crop_rect = crop_rect
        self.set_size_request(int(w), int(h))
        if self.drawing_area:
            self.drawing_area.set_content_width(int(w))
            self.drawing_area.set_content_height(int(h))
            self.drawing_area.queue_draw()

    def update_visibility(self, y_min: float, y_max: float, buffer: float, zoom: float, scale_factor: float):
        """Mount or unmount Gtk.DrawingArea based on visibility viewport bounds."""
        page_y0 = self.y_offset
        page_y1 = self.y_offset + self.h

        visible = (page_y1 >= y_min - buffer) and (page_y0 <= y_max + buffer)

        if visible:
            if not self.drawing_area:
                self.drawing_area = Gtk.DrawingArea()
                self.drawing_area.set_content_width(int(self.w))
                self.drawing_area.set_content_height(int(self.h))
                self.drawing_area.set_draw_func(self._draw_func)
                self.append(self.drawing_area)
            elif not self.page_is_visible:
                self.drawing_area.queue_draw()
            self.page_is_visible = True
        else:
            if self.drawing_area:
                self.remove(self.drawing_area)
                self.drawing_area = None
            self.page_is_visible = False

    def _draw_func(self, drawing_area, cr, width, height):
        canvas = self.canvas_parent
        zoom_key = round(canvas.zoom, 2)
        scale_factor = canvas.get_scale_factor()
        crop_key = (
            (self.crop_rect.x0, self.crop_rect.y0, self.crop_rect.x1, self.crop_rect.y1)
            if self.crop_rect is not None
            else None
        )

        if canvas.backend != "opengl":
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.paint()

            surface = (
                canvas.cache.get(self.page_index, canvas.zoom, scale_factor, self.crop_rect)
                if canvas.cache
                else None
            )
            if surface is None and canvas.cache is not None:
                surface = canvas.cache.get_best(self.page_index, canvas.zoom, scale_factor, self.crop_rect)

            if surface is not None:
                cr.save()
                sw = surface.get_width()
                sh = surface.get_height()
                sx = width / sw
                sy = height / sh
                if canvas.is_pinching:
                    page_cx = canvas.pinch_center_x - (canvas.get_width() - self.w) / 2
                    page_cy = canvas.pinch_center_y - self.y_offset
                    if 0 <= page_cx <= width and 0 <= page_cy <= height:
                        ox = page_cx / sx
                        oy = page_cy / sy
                        cr.translate(page_cx, page_cy)
                        cr.scale(sx, sy)
                        cr.translate(-ox, -oy)
                    else:
                        cr.scale(sx, sy)
                else:
                    cr.scale(sx, sy)
                cr.set_source_surface(surface, 0, 0)
                cr.paint()
                cr.restore()
            else:
                cr.save()
                cr.set_source_rgb(0.95, 0.95, 0.95)
                cr.paint()
                cr.set_source_rgb(0.5, 0.5, 0.5)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(14)
                cr.move_to(width / 2 - 50, height / 2)
                cr.show_text(f"Loading Page {self.page_index + 1}...")
                cr.restore()

                if not canvas.is_pinching:
                    job_key = (self.page_index, zoom_key, scale_factor, crop_key)
                    if job_key not in canvas.in_flight and canvas.render_worker and canvas.cache:
                        canvas.in_flight.add(job_key)

                        def make_cb(idx, zk, sf, ck):
                            return lambda: canvas._on_render_complete(idx, zk, sf, ck)

                        canvas.render_worker.queue_render_job(
                            priority=0,
                            doc_model=canvas.doc_model,
                            page_index=self.page_index,
                            zoom=canvas.zoom * canvas.dpi_scale_factor,
                            scale_factor=scale_factor,
                            crop_rect=self.crop_rect,
                            is_minimap=False,
                            target_cache=canvas.cache,
                            redraw_callback=make_cb(self.page_index, zoom_key, scale_factor, crop_key),
                            screen_physical_dpi=canvas.screen_physical_dpi,
                        )

        if canvas.backend != "opengl" and canvas.highlighted_block is not None:
            h_page_idx, h_bbox = canvas.highlighted_block
            if h_page_idx == self.page_index:
                bx0, by0, bx1, by1 = h_bbox
                crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
                crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0

                cr.save()
                cr.set_source_rgba(1.0, 0.85, 0.0, 0.35)
                px0 = (bx0 - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                py0 = (by0 - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                pw = (bx1 - bx0) * canvas.zoom * canvas.dpi_scale_factor
                ph = (by1 - by0) * canvas.zoom * canvas.dpi_scale_factor
                cr.rectangle(px0, py0, pw, ph)
                cr.fill()
                cr.restore()

        if canvas.backend != "opengl" and canvas.text_selection is not None:
            sel_rects = canvas.text_selection.get_selection_rects(self.page_index)
            if sel_rects:
                crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
                crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0
                cr.save()
                cr.set_source_rgba(0.2, 0.5, 1.0, 0.35)
                for rx0, ry0, rx1, ry1 in sel_rects:
                    px0 = (rx0 - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                    py0 = (ry0 - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                    pw = (rx1 - rx0) * canvas.zoom * canvas.dpi_scale_factor
                    ph = (ry1 - ry0) * canvas.zoom * canvas.dpi_scale_factor
                    cr.rectangle(px0, py0, pw, ph)
                cr.fill()

            if (
                canvas.debug_mode
                and getattr(canvas, "debug_arxiv_data", None) is not None
                and canvas.debug_arxiv_data.get("page_index") == self.page_index
            ):
                d_data = canvas.debug_arxiv_data
                crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
                crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0
                scale = canvas.zoom * canvas.dpi_scale_factor
                cr.save()

                curr_w_rects = d_data.get("curr_word_rects", [])
                if curr_w_rects:
                    cr.set_source_rgba(0.7, 0.35, 1.0, 0.75)
                    for rx0, ry0, rx1, ry1 in curr_w_rects:
                        px0 = (rx0 - crop_off_x) * scale
                        py0 = (ry0 - crop_off_y) * scale
                        pw = (rx1 - rx0) * scale
                        ph = (ry1 - ry0) * scale
                        cr.rectangle(px0, py0, pw, ph)
                    cr.fill()

                fwd_c_rects = d_data.get("forward_char_rects", [])
                if fwd_c_rects:
                    cr.set_source_rgba(0.7, 0.35, 1.0, 0.25)
                    cr.set_line_width(1.0)
                    for rx0, ry0, rx1, ry1 in fwd_c_rects:
                        px0 = (rx0 - crop_off_x) * scale
                        py0 = (ry0 - crop_off_y) * scale
                        pw = (rx1 - rx0) * scale
                        ph = (ry1 - ry0) * scale
                        cr.rectangle(px0, py0, pw, ph)
                    cr.stroke()

                c_rect = d_data.get("curr_char_rect")
                if c_rect:
                    rx0, ry0, rx1, ry1 = c_rect
                    cr.set_source_rgba(0.7, 0.35, 1.0, 1.0)
                    cr.set_line_width(1.5)
                    px0 = (rx0 - crop_off_x) * scale
                    py0 = (ry0 - crop_off_y) * scale
                    pw = (rx1 - rx0) * scale
                    ph = (ry1 - ry0) * scale
                    cr.rectangle(px0, py0, pw, ph)
                cr.restore()

            if (
                getattr(canvas, "hover_caret", None) is not None
                and canvas.hover_caret[0] == self.page_index
            ):
                _page_idx, (cx, y0, y1) = canvas.hover_caret
                crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
                crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0
                scale = canvas.zoom * canvas.dpi_scale_factor
                sx = (cx - crop_off_x) * scale
                sy0 = (y0 - crop_off_y) * scale
                sy1 = (y1 - crop_off_y) * scale
                cr.save()
                cr.set_source_rgba(0.533, 0.533, 0.533, 0.9)
                cr.set_line_width(2.0)
                cr.move_to(sx, sy0)
                cr.line_to(sx, sy1)
                cr.stroke()
                cr.restore()
