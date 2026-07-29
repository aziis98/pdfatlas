import sys

import cairo
import fitz
import gi
from typing import Any

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk

from ..core.cache import RenderCache
from ..core.crop import CropAnalyzer, CropSettings
from ..core.document import DocumentModel
from ..core.text_selection import TextSelection


class PageContainer(Gtk.Box):
    """
    A lightweight layout container representing a single PDF page.
    Maintains a fixed size and dynamically mounts/unmounts its internal
    Gtk.DrawingArea canvas based on visible viewport intersections.
    """

    def __init__(self, page_index, canvas):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.page_index = page_index
        self.canvas_parent = canvas
        self.y_offset = 0.0
        self.w = 0.0
        self.h = 0.0
        self.crop_rect = None
        self.drawing_area = None
        self.page_is_visible = False

        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_focusable(False)
        self.add_css_class("page-container")

    def set_layout_params(self, y_offset, w, h, crop_rect):
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

    def update_visibility(self, y_min, y_max, buffer, zoom, scale_factor):
        """Mount or unmount Gtk.DrawingArea based on visibility viewport bounds."""
        page_y0 = self.y_offset
        page_y1 = self.y_offset + self.h

        # Determine overlap with buffered viewport height
        visible = (page_y1 >= y_min - buffer) and (page_y0 <= y_max + buffer)

        if visible:
            if not self.drawing_area:
                # Mount the page canvas overlay
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
                # Unmount and release GPU textures when offscreen
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

        # 1. Fill page background and surface image (Cairo backend only)
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
                # Loading placeholder
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

        # 2. Block Highlights (Search matches - Cairo backend only)
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

        # 2.5. Text Selection Highlight (Cairo backend only)
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
                cr.restore()

            # Debug: draw all character bboxes when actively selecting
            if (
                canvas.debug_mode
                and canvas.text_selection.is_selecting
                and canvas.text_selection.anchor_page == self.page_index
            ):
                pi = canvas.text_selection.get_page_index(self.page_index)
                crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
                crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0
                cr.save()
                cr.set_line_width(0.5)
                for ci, c in enumerate(pi.chars):
                    cx0 = (c.bbox[0] - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                    cy0 = (c.bbox[1] - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                    cw = (c.bbox[2] - c.bbox[0]) * canvas.zoom * canvas.dpi_scale_factor
                    ch = (c.bbox[3] - c.bbox[1]) * canvas.zoom * canvas.dpi_scale_factor
                    cr.set_source_rgba(0.0, 0.8, 0.0, 0.4)
                    cr.rectangle(cx0, cy0, cw, ch)
                    cr.stroke()

                # Draw anchor char in red
                if canvas.text_selection.anchor_char_idx is not None:
                    ac = pi.chars[canvas.text_selection.anchor_char_idx]
                    ax = (ac.bbox[0] + ac.bbox[2]) / 2.0
                    ay = (ac.bbox[1] + ac.bbox[3]) / 2.0
                    sx = (ax - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                    sy = (ay - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                    cr.set_source_rgba(1.0, 0.0, 0.0, 0.9)
                    cr.arc(sx, sy, 4.0, 0, 6.283)
                    cr.fill()

                # Draw focus char in blue
                if canvas.text_selection.focus_char_idx is not None:
                    fc = pi.chars[canvas.text_selection.focus_char_idx]
                    fx = (fc.bbox[0] + fc.bbox[2]) / 2.0
                    fy = (fc.bbox[1] + fc.bbox[3]) / 2.0
                    sx = (fx - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                    sy = (fy - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                    cr.set_source_rgba(0.0, 0.0, 1.0, 0.9)
                    cr.arc(sx, sy, 4.0, 0, 6.283)
                    cr.fill()

                cr.restore()

        # 3. Interactive PDF Link Stroke Outlines (Cairo backend only)
        if canvas.backend != "opengl" and canvas.doc_model:
            links = canvas.doc_model.get_page_links(self.page_index)
            crop_off_x = self.crop_rect.x0 if self.crop_rect is not None else 0.0
            crop_off_y = self.crop_rect.y0 if self.crop_rect is not None else 0.0

            for link in links:
                from_rect = link.get("from")
                if not from_rect:
                    continue
                lx0 = (from_rect.x0 - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                ly0 = (from_rect.y0 - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                lw = (from_rect.x1 - from_rect.x0) * canvas.zoom * canvas.dpi_scale_factor
                lh = (from_rect.y1 - from_rect.y0) * canvas.zoom * canvas.dpi_scale_factor

                is_hovered = (
                    canvas.hovered_link is not None
                    and canvas.hovered_link[0] == self.page_index
                    and canvas.hovered_link[1] is link
                )

                cr.save()
                is_uri = link.get("kind") == fitz.LINK_URI

                if is_hovered:
                    if is_uri:
                        cr.set_source_rgba(0.18, 0.76, 0.49, 0.30)
                    else:
                        cr.set_source_rgba(0.20, 0.52, 0.90, 0.30)
                    cr.rectangle(lx0, ly0, lw, lh)
                    cr.fill_preserve()

                if is_uri:
                    cr.set_source_rgba(0.18, 0.76, 0.49, 0.85)  # Libadwaita Green outline
                else:
                    cr.set_source_rgba(0.20, 0.52, 0.90, 0.85)  # Libadwaita Blue outline

                cr.set_line_width(1.8)
                cr.rectangle(lx0, ly0, lw, lh)
                cr.stroke()
                cr.restore()

        # 4. Debug Mode: Magenta border for Cairo page canvas
        if canvas.backend != "opengl" and getattr(canvas, "debug_mode", False):
            cr.save()
            cr.set_source_rgba(1.0, 0.0, 1.0, 0.9)  # Magenta (#ff00ff)
            cr.set_line_width(2.0)
            cr.rectangle(0.5, 0.5, width - 1.0, height - 1.0)
            cr.stroke()
            cr.restore()


class PDFCanvas(Gtk.Box):
    """
    Virtual list viewport wrapper that holds individual page layout blocks.
    Acts as a vertical Gtk.Box to avoid massive texture allocation failures on GTK4.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_valign(Gtk.Align.START)
        self.set_focusable(True)
        self.set_focus_on_click(True)
        self.add_css_class("pdf-canvas")

        self.doc_model = None
        self.cache = None
        self.render_worker = None
        self.crop_analyzer = None
        self.gl_canvas = None
        self.backend: str = "cairo"
        self.debug_mode: bool = False
        self.settings = None

        self.zoom = 1.0
        self.crop_active = False
        self.page_gap = 12
        self.highlighted_block = None
        self.containers = []
        self.in_flight = set()
        self.page_layout = []
        self.vadjustment = None
        self.hadjustment = None

        # Interactive link state
        self.hovered_link: tuple[int, dict] | None = None
        self.on_link_clicked: Any = None
        self.on_link_hovered: Any = None
        self.on_page_hovered: Any = None
        self.text_selection: TextSelection | None = None

        # Display DPI scale settings
        self.dpi_scale_factor = 1.0
        self.screen_physical_dpi = 192.0

        # Backend settings
        self.backend = "opengl"
        self.gl_canvas: Any = None
        self.set_focusable(False)

        # Pinch-to-zoom state
        self.is_pinching = False
        self.pinch_center_x: float = 0.0
        self.pinch_center_y: float = 0.0

        self._setup_link_controllers()

    def _setup_link_controllers(self):
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self._on_motion)
        motion_controller.connect("leave", self._on_leave)
        self.add_controller(motion_controller)

        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_button(1)
        click_gesture.connect("pressed", self._on_click)
        self.add_controller(click_gesture)

    def _screen_to_pdf_point(self, x: float, y: float, page_index: int) -> tuple[float, float] | None:
        """Convert screen coordinates to PDF point coordinates on a given page."""
        if not self.page_layout or page_index >= len(self.page_layout):
            return None

        scale = self.zoom * self.dpi_scale_factor
        canvas_w = float(self.get_width())
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        y_offset, dw, dh, crop_rect = self.page_layout[page_index]
        page_x0 = (canvas_w - dw) / 2.0
        page_y0 = y_offset - scroll_y

        rel_x = x - page_x0
        rel_y = y - page_y0

        crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
        crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

        pt_x = (rel_x / scale) + crop_off_x
        pt_y = (rel_y / scale) + crop_off_y
        return (pt_x, pt_y)

    def on_drag_begin(self, gesture, start_x, start_y):
        """Handle drag begin - start text selection if no link is hit."""
        if self.text_selection is None or not self.doc_model:
            if self.debug_mode:
                sys.stderr.write("[TextSel] drag-begin: no text_selection or doc_model\n")
                sys.stderr.flush()
            return

        # Don't start text selection if a link was clicked
        hit = self._hit_test_link(start_x, start_y)
        if hit is not None:
            if self.debug_mode:
                sys.stderr.write(f"[TextSel] drag-begin: link hit at ({start_x:.1f},{start_y:.1f}), denying\n")
                sys.stderr.flush()
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        page_idx = self._hit_test_page(start_x, start_y)
        if page_idx is None:
            if self.debug_mode:
                sys.stderr.write(f"[TextSel] drag-begin: no page hit at ({start_x:.1f},{start_y:.1f}), denying\n")
                sys.stderr.flush()
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        pt = self._screen_to_pdf_point(start_x, start_y, page_idx)
        if pt is None:
            if self.debug_mode:
                sys.stderr.write("[TextSel] drag-begin: screen_to_pdf returned None, denying\n")
                sys.stderr.flush()
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        pt_x, pt_y = pt
        char_idx = self.text_selection.hit_test(page_idx, pt_x, pt_y)
        if self.debug_mode:
            sys.stderr.write(
                f"[TextSel] drag-begin: page={page_idx} pdf_pt=({pt_x:.1f},{pt_y:.1f}) "
                f"char_idx={char_idx} total_chars={len(self.text_selection.get_page_index(page_idx).chars)}\n"
            )
            sys.stderr.flush()
        if char_idx is None:
            if self.debug_mode:
                sys.stderr.write("[TextSel] drag-begin: no char hit, denying\n")
                sys.stderr.flush()
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        self.text_selection.start_selection(page_idx, char_idx)
        self.queue_draw_overlays("selection-start")

    def on_drag_update(self, gesture, offset_x, offset_y):
        """Handle drag update - extend text selection."""
        if self.text_selection is None or not self.text_selection.is_selecting:
            return

        # Get start position from gesture to compute current absolute position
        success, start_x, start_y = gesture.get_start_point()
        if not success:
            if self.debug_mode:
                sys.stderr.write("[TextSel] drag-update: get_start_point failed\n")
                sys.stderr.flush()
            return

        cur_x = start_x + offset_x
        cur_y = start_y + offset_y

        page_idx = self._hit_test_page(cur_x, cur_y)
        if page_idx is None:
            if self.debug_mode:
                sys.stderr.write(f"[TextSel] drag-update: no page at ({cur_x:.1f},{cur_y:.1f})\n")
                sys.stderr.flush()
            return

        pt = self._screen_to_pdf_point(cur_x, cur_y, page_idx)
        if pt is None:
            return

        pt_x, pt_y = pt
        char_idx = self.text_selection.hit_test(page_idx, pt_x, pt_y)
        if char_idx is None:
            if self.debug_mode:
                sys.stderr.write(f"[TextSel] drag-update: no char at ({pt_x:.1f},{pt_y:.1f})\n")
                sys.stderr.flush()
            return

        self.text_selection.update_focus(page_idx, char_idx)
        if self.debug_mode:
            sys.stderr.write(
                f"[TextSel] drag-update: page={page_idx} char={char_idx} "
                f"anchor={self.text_selection.anchor_char_idx} focus={char_idx}\n"
            )
            sys.stderr.flush()
        self.queue_draw_overlays("selection-update")

    def on_drag_end(self, gesture, offset_x, offset_y):
        """Handle drag end - finalize selection. Clear if no drag occurred (click)."""
        if self.text_selection is not None:
            # If no drag happened (click), clear the selection
            if abs(offset_x) < 2 and abs(offset_y) < 2:
                if self.debug_mode:
                    sys.stderr.write(f"[TextSel] drag-end: no drag (offset={offset_x:.1f},{offset_y:.1f}), clearing\n")
                    sys.stderr.flush()
                self.text_selection.clear_selection()
                self.queue_draw_overlays("selection-cleared")
            else:
                self.text_selection.end_selection()
                if self.debug_mode:
                    text = self.text_selection.get_selected_text()
                    sys.stderr.write(
                        f"[TextSel] drag-end: finalized, selected_text={text[:80]!r}\n"
                    )
                    sys.stderr.flush()

    def _hit_test_page(self, x: float, y: float) -> int | None:
        if not self.page_layout:
            return None
        half_gap = self.page_gap / 2.0
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0
        for i, layout in enumerate(self.page_layout):
            y_offset, dw, dh, crop_rect = layout
            page_y0 = y_offset - scroll_y
            page_y1 = page_y0 + dh

            if page_y0 - half_gap <= y <= page_y1 + half_gap:
                return i
        return None

    def _hit_test_link(self, x: float, y: float) -> tuple[int, dict] | None:
        if not self.doc_model or not self.page_layout:
            return None

        scale = self.zoom * self.dpi_scale_factor
        canvas_w = float(self.get_width())
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        for i, layout in enumerate(self.page_layout):
            y_offset, dw, dh, crop_rect = layout
            page_x0 = (canvas_w - dw) / 2.0
            page_y0 = y_offset - scroll_y
            page_x1 = page_x0 + dw
            page_y1 = page_y0 + dh

            if page_x0 <= x <= page_x1 and page_y0 <= y <= page_y1:
                rel_x = x - page_x0
                rel_y = y - page_y0

                crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
                crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

                pt_x = (rel_x / scale) + crop_off_x
                pt_y = (rel_y / scale) + crop_off_y

                links = self.doc_model.get_page_links(i)
                for link in links:
                    from_rect = link.get("from")
                    if from_rect and (from_rect.x0 <= pt_x <= from_rect.x1 and from_rect.y0 <= pt_y <= from_rect.y1):
                        return (i, link)
                break
        return None

    def get_link_screen_rect(
        self, page_index: int, link: dict, overlay_widget: Any = None
    ) -> tuple[float, float, float, float] | None:
        from_rect = link.get("from")
        if not from_rect or not self.page_layout:
            return None

        scale = self.zoom * self.dpi_scale_factor
        canvas_w = float(self.get_width())
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        if 0 <= page_index < len(self.page_layout):
            y_offset, dw, dh, crop_rect = self.page_layout[page_index]
            page_x0 = (canvas_w - dw) / 2.0
            page_y0 = y_offset - scroll_y

            crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
            crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

            link_screen_x0 = page_x0 + (from_rect.x0 - crop_off_x) * scale
            link_screen_y0 = page_y0 + (from_rect.y0 - crop_off_y) * scale
            link_screen_w = (from_rect.x1 - from_rect.x0) * scale
            link_screen_h = (from_rect.y1 - from_rect.y0) * scale

            return (link_screen_x0, link_screen_y0, link_screen_w, link_screen_h)
        return None
        return None

    def queue_draw_overlays(self, reason=""):
        sys.stderr.write(f"[PDFCanvas] queue_draw_overlays backend={self.backend} {reason}\n")
        sys.stderr.flush()
        if self.backend == "opengl" and self.gl_canvas:
            self.gl_canvas.queue_draw()
        else:
            for c in self.containers:
                if c.drawing_area:
                    c.drawing_area.queue_draw()

    def _on_motion(self, controller, x, y):
        hit = self._hit_test_link(x, y)
        is_same = (
            self.hovered_link is not None
            and hit is not None
            and self.hovered_link[0] == hit[0]
            and self.hovered_link[1].get("xref") == hit[1].get("xref")
            and self.hovered_link[1].get("from") == hit[1].get("from")
        )
        if not is_same and (hit is not None or self.hovered_link is not None):
            self.hovered_link = hit
            cursor_name = "pointer" if hit is not None else "default"
            self.set_cursor(Gdk.Cursor.new_from_name(cursor_name))
            self.queue_draw_overlays("hover")
            if self.on_link_hovered:
                if hit is not None:
                    self.on_link_hovered(hit[0], hit[1])
                else:
                    self.on_link_hovered(None, None)

        if self.on_page_hovered:
            hovered_page_idx = self._hit_test_page(x, y)
            self.on_page_hovered(hovered_page_idx, x, y)

    def _on_leave(self, controller):
        if self.hovered_link is not None:
            self.hovered_link = None
            self.set_cursor(Gdk.Cursor.new_from_name("default"))
            self.queue_draw_overlays("leave")
            if self.on_link_hovered:
                self.on_link_hovered(None, None)
        if self.on_page_hovered:
            self.on_page_hovered(None, 0.0, 0.0)

    def _on_click(self, gesture, n_press, x, y):
        if n_press == 1:
            root = self.get_root()
            if root and hasattr(root, "set_focus"):
                root.set_focus(None)

            hit = self._hit_test_link(x, y)
            if hit is not None:
                page_idx, link = hit
                if self.on_link_clicked:
                    self.on_link_clicked(page_idx, link)

    def set_document(
        self,
        doc_model: DocumentModel,
        cache: RenderCache,
        render_worker,
        crop_analyzer: CropAnalyzer,
        settings: CropSettings,
    ):
        self.doc_model = doc_model
        self.cache = cache
        self.render_worker = render_worker
        self.crop_analyzer = crop_analyzer
        self.settings = settings
        self.in_flight.clear()
        self.highlighted_block = None
        self.text_selection = TextSelection(doc_model) if doc_model else None

        # Remove old containers
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        self.containers = []
        self.page_layout = []
        self.update_layout()

    def set_vadjustment(self, vadjustment: Gtk.Adjustment):
        self.vadjustment = vadjustment
        self.vadjustment.connect("value-changed", self._on_scroll)

    def _on_scroll(self, adj):
        self._update_visibility()

    def set_zoom(self, zoom: float):
        self.zoom = zoom
        self.in_flight.clear()
        if self.render_worker:
            self.render_worker.clear_canvas_render_jobs()
        if self.hovered_link is not None:
            self.hovered_link = None
            if self.on_link_hovered:
                self.on_link_hovered(None, None)
        self.update_layout()
        self.queue_draw_overlays("set_zoom")

    def on_crop_changed(self):
        self.in_flight.clear()
        if self.render_worker:
            self.render_worker.clear_canvas_render_jobs()
        if self.cache:
            self.cache.clear()
        self.update_layout()

    def set_highlighted_block(self, page_index: int, bbox: tuple | None):
        self.highlighted_block = (page_index, bbox) if bbox is not None else None
        if self.backend == "opengl":
            if self.gl_canvas:
                self.gl_canvas.queue_draw()
        else:
            if 0 <= page_index < len(self.containers):
                container = self.containers[page_index]
                if container.drawing_area:
                    container.drawing_area.queue_draw()

    def update_layout(self):
        if not self.doc_model:
            self.page_layout = []
            return

        # Update page gap based on settings dynamically
        if self.settings and not getattr(self.settings, "page_gaps", True):
            self.page_gap = 0
        else:
            self.page_gap = 12

        page_count = self.doc_model.page_count
        self.set_spacing(self.page_gap)
        self.set_margin_top(int(self.page_gap))
        self.set_margin_bottom(int(self.page_gap))

        # Rebuild/recreate container widgets if size differs
        if len(self.containers) != page_count:
            child = self.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.remove(child)
                child = nxt

            self.containers = []
            for i in range(page_count):
                container = PageContainer(i, self)
                self.append(container)
                self.containers.append(container)

        current_y = float(self.page_gap)
        self.page_layout = []

        for i in range(page_count):
            page_rect = self.doc_model.page_rect(i)
            crop_rect = None
            if self.settings and self.settings.enabled and self.crop_analyzer:
                crop_rect = self.crop_analyzer.crop_rects[i]

            rect = crop_rect if crop_rect is not None else page_rect

            # Apply dpi_scale_factor to logical layout dimensions
            dw = rect.width * self.zoom * self.dpi_scale_factor
            dh = rect.height * self.zoom * self.dpi_scale_factor

            self.page_layout.append((current_y, dw, dh, crop_rect))

            container = self.containers[i]
            container.set_layout_params(current_y, dw, dh, crop_rect)

            current_y += dh + self.page_gap

        if self.vadjustment:
            self.vadjustment.set_upper(current_y)

        self._update_visibility()

    def _update_visibility(self):
        if not self.vadjustment or not self.doc_model:
            return

        y_min = self.vadjustment.get_value()
        page_size = self.vadjustment.get_page_size()
        y_max = y_min + page_size
        scale_factor = self.get_scale_factor()
        zoom_key = round(self.zoom, 2)

        # Buffer size of 1.5 screen viewport heights to pre-render adjacent pages smoothly
        buffer = page_size * 1.5

        first_visible = None
        last_visible = None

        # 1. Update container drawing area states
        for i, container in enumerate(self.containers):
            container.update_visibility(y_min, y_max, buffer, self.zoom, scale_factor)

            page_y0 = container.y_offset
            page_y1 = container.y_offset + container.h
            if page_y1 >= y_min and page_y0 <= y_max:
                if first_visible is None:
                    first_visible = i
                last_visible = i

                # For OpenGL backend, visible page render requests are queued here
                if self.backend == "opengl" and not self.is_pinching:
                    crop_key = (
                        (
                            container.crop_rect.x0,
                            container.crop_rect.y0,
                            container.crop_rect.x1,
                            container.crop_rect.y1,
                        )
                        if container.crop_rect is not None
                        else None
                    )
                    if self.cache and self.cache.get(i, self.zoom, scale_factor, container.crop_rect) is None:
                        job_key = (i, zoom_key, scale_factor, crop_key)
                        if job_key not in self.in_flight and self.render_worker:
                            self.in_flight.add(job_key)

                            def make_cb(p_idx, zk, sf, ck):
                                return lambda: self._on_render_complete(p_idx, zk, sf, ck)

                            self.render_worker.queue_render_job(
                                priority=0,  # High priority for visible pages
                                doc_model=self.doc_model,
                                page_index=i,
                                zoom=self.zoom * self.dpi_scale_factor,
                                scale_factor=scale_factor,
                                crop_rect=container.crop_rect,
                                is_minimap=False,
                                target_cache=self.cache,
                                redraw_callback=make_cb(i, zoom_key, scale_factor, crop_key),
                                screen_physical_dpi=self.screen_physical_dpi,
                            )

        # 2. Queue prefetch jobs for adjacent pages (skipped during pinch)
        if not self.is_pinching and first_visible is not None and last_visible is not None and self.cache and self.render_worker:
            prefetch_targets = []
            page_count = len(self.containers)
            # Priority 1: Adjacent ±1
            if first_visible - 1 >= 0:
                prefetch_targets.append((first_visible - 1, 1))
            if last_visible + 1 < page_count:
                prefetch_targets.append((last_visible + 1, 1))
            # Priority 2: Adjacent ±2
            if first_visible - 2 >= 0:
                prefetch_targets.append((first_visible - 2, 2))
            if last_visible + 2 < page_count:
                prefetch_targets.append((last_visible + 2, 2))

            for idx, priority in prefetch_targets:
                container = self.containers[idx]
                crop_key = (
                    (
                        container.crop_rect.x0,
                        container.crop_rect.y0,
                        container.crop_rect.x1,
                        container.crop_rect.y1,
                    )
                    if container.crop_rect is not None
                    else None
                )

                if self.cache.get(idx, self.zoom, scale_factor, container.crop_rect) is None:
                    job_key = (idx, zoom_key, scale_factor, crop_key)
                    if job_key not in self.in_flight:
                        self.in_flight.add(job_key)

                        def make_cb(p_idx, zk, sf, ck):
                            return lambda: self._on_render_complete(p_idx, zk, sf, ck)

                        self.render_worker.queue_render_job(
                            priority=priority,
                            doc_model=self.doc_model,
                            page_index=idx,
                            zoom=self.zoom * self.dpi_scale_factor,
                            scale_factor=scale_factor,
                            crop_rect=container.crop_rect,
                            is_minimap=False,
                            target_cache=self.cache,
                            redraw_callback=make_cb(idx, zoom_key, scale_factor, crop_key),
                            screen_physical_dpi=self.screen_physical_dpi,
                        )

    def _on_render_complete(self, page_index, zoom_key, scale_factor, crop_key):
        self.in_flight.discard((page_index, zoom_key, scale_factor, crop_key))
        sys.stderr.write(
            f"[PDFCanvas] _on_render_complete page={page_index} "
            f"zoom_key={zoom_key} current_zoom={round(self.zoom,2)} "
            f"redraw=True\n"
        )
        sys.stderr.flush()
        # Always redraw — drawing code uses get_best to pick the best available surface
        if self.backend == "opengl":
            if self.gl_canvas:
                self.gl_canvas.queue_draw()
        else:
            if 0 <= page_index < len(self.containers):
                container = self.containers[page_index]
                if container.drawing_area:
                    container.drawing_area.queue_draw()
