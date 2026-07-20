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
            if surface is not None:
                cr.save()
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

        # 2. Block Highlights (Search matches)
        if canvas.highlighted_block is not None:
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
                cr.fill_preserve()

                cr.set_source_rgba(0.85, 0.1, 0.1, 0.9)
                cr.set_line_width(2.5)
                cr.stroke()
                cr.restore()

        # 3. Interactive PDF Link Stroke Outlines (TOC, internal jumps, external URLs)
        if canvas.doc_model:
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
        self.settings = None

        self.zoom = 1.0
        self.crop_active = False
        self.page_gap = 12
        self.highlighted_block = None
        self.containers = []
        self.in_flight = set()
        self.page_layout = []
        self.vadjustment = None

        # Interactive link state
        self.hovered_link: tuple[int, dict] | None = None
        self.on_link_clicked: Any = None
        self.on_link_hovered: Any = None

        # Display DPI scale settings
        self.dpi_scale_factor = 1.0
        self.screen_physical_dpi = 192.0

        # Backend settings
        self.backend = "opengl"
        self.gl_canvas: Any = None

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

    def _hit_test_link(self, x: float, y: float) -> tuple[int, dict] | None:
        if not self.doc_model or not self.containers:
            return None

        scale = self.zoom * self.dpi_scale_factor
        canvas_w = float(self.get_width())

        for container in self.containers:
            alloc = container.get_allocation()
            if alloc.width > 0 and alloc.height > 0:
                page_x0 = float(alloc.x)
                page_y0 = float(alloc.y)
                page_x1 = page_x0 + float(alloc.width)
                page_y1 = page_y0 + float(alloc.height)
            else:
                page_x0 = max(0.0, (canvas_w - container.w) / 2) if canvas_w > container.w else 0.0
                page_y0 = container.y_offset
                page_x1 = page_x0 + container.w
                page_y1 = page_y0 + container.h

            if page_x0 <= x <= page_x1 and page_y0 <= y <= page_y1:
                rel_x = x - page_x0
                rel_y = y - page_y0

                crop_off_x = container.crop_rect.x0 if container.crop_rect is not None else 0.0
                crop_off_y = container.crop_rect.y0 if container.crop_rect is not None else 0.0

                pt_x = (rel_x / scale) + crop_off_x
                pt_y = (rel_y / scale) + crop_off_y

                links = self.doc_model.get_page_links(container.page_index)
                for link in links:
                    from_rect = link.get("from")
                    if from_rect and (from_rect.x0 <= pt_x <= from_rect.x1 and from_rect.y0 <= pt_y <= from_rect.y1):
                        return (container.page_index, link)
                break
        return None

    def queue_draw_overlays(self):
        for c in self.containers:
            if c.drawing_area:
                c.drawing_area.queue_draw()

    def _on_motion(self, controller, x, y):
        hit = self._hit_test_link(x, y)
        if hit != self.hovered_link:
            self.hovered_link = hit
            cursor_name = "pointer" if hit is not None else "default"
            self.set_cursor(Gdk.Cursor.new_from_name(cursor_name))
            self.queue_draw_overlays()
            if self.on_link_hovered:
                if hit is not None:
                    self.on_link_hovered(hit[0], hit[1])
                else:
                    self.on_link_hovered(None, None)

    def _on_leave(self, controller):
        if self.hovered_link is not None:
            self.hovered_link = None
            self.set_cursor(Gdk.Cursor.new_from_name("default"))
            self.queue_draw_overlays()
            if self.on_link_hovered:
                self.on_link_hovered(None, None)

    def _on_click(self, gesture, n_press, x, y):
        if n_press == 1:
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
        self.zoom = max(0.25, min(8.0, zoom))
        self.in_flight.clear()
        if self.render_worker:
            self.render_worker.clear_canvas_render_jobs()
        if self.cache:
            self.cache.clear()
        self.update_layout()

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

        current_y = 0.0
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
                if self.backend == "opengl":
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

        # 2. Queue prefetch jobs for adjacent pages
        if first_visible is not None and last_visible is not None and self.cache and self.render_worker:
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
        current_zoom_key = round(self.zoom, 2)
        current_scale_factor = self.get_scale_factor()
        if zoom_key == current_zoom_key and scale_factor == current_scale_factor:
            if self.backend == "opengl":
                if self.gl_canvas:
                    self.gl_canvas.queue_draw()
            else:
                if 0 <= page_index < len(self.containers):
                    container = self.containers[page_index]
                    if container.drawing_area:
                        container.drawing_area.queue_draw()
