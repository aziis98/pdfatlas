import gi
from typing import Any

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import math

import cairo
from gi.repository import Adw, GLib, Gtk

from ..core.cache import MiniMapCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.settings import CropSettings


class MiniMap(Gtk.DrawingArea):
    """
    Sidebar Minimap displaying thumbnails in a vertical column-wrapping layout.
    Features:
      - Active page highlighted in purple.
      - Translucent viewport tracker showing current scroll extent.
      - Faint dashed rect showing the cropped area boundary.
      - Click navigation to jump to pages.
    """

    THUMB_W = 90  # Thumbnail width in pixels
    THUMB_GAP = 6  # Gap between thumbnails in pixels

    def __init__(self):
        super().__init__()
        self.doc_model = None
        self.cache = None
        self.render_worker = None
        self.crop_analyzer = None
        self.settings = None
        self.main_zoom = 1.0
        self.on_page_clicked: Any = None
        self.current_page = 0
        self.thumb_h = 120  # Dynamically calculated
        self.items_per_column = 1
        self.n_cols = 1
        self.n_rows = 1
        self.in_flight = set()

        # Set a small natural content size to allow shrinking/resizing the window down
        self.set_content_width(100)
        self.set_content_height(100)

        # Debounce timer for resizing to avoid queueing heavy render jobs while dragging
        self.resize_timer_id = None
        self.resize_settled = True
        self.last_width = 0
        self.last_height = 0
        self.resize_cache_surface = None

        self.connect("destroy", self._on_destroy)

        self.on_page_clicked = None  # Callback signature: func(page_index)

        # Set up draw callback and resize notifier
        self.set_draw_func(self._draw_func)
        self.connect("resize", self._on_resize)

        # Connect click gesture
        self.click_gesture = Gtk.GestureClick()
        self.click_gesture.connect("pressed", self._on_pressed)
        self.add_controller(self.click_gesture)

    def set_document(
        self,
        doc_model: DocumentModel,
        cache: MiniMapCache,
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

        # Reset resize tracking on document change
        if self.resize_timer_id is not None:
            GLib.source_remove(self.resize_timer_id)
            self.resize_timer_id = None
        self.resize_settled = True
        self.last_width = 0
        self.last_height = 0
        self.resize_cache_surface = None

        # Trigger relayout with current size allocation
        self._relayout(self.get_allocated_width(), self.get_allocated_height())

    def _on_destroy(self, widget):
        if self.resize_timer_id is not None:
            GLib.source_remove(self.resize_timer_id)
            self.resize_timer_id = None
        self.resize_cache_surface = None

    def set_vadjustment(self, vadjustment: Gtk.Adjustment):
        self.main_vadjustment = vadjustment
        # Connect change listener to redraw the viewport strip
        self.main_vadjustment.connect("value-changed", lambda adj: self.queue_draw())

    def set_current_page(self, page_index: int):
        if self.current_page != page_index:
            self.current_page = page_index
            self.queue_draw()

    def _on_resize(self, drawing_area, width, height):
        self._relayout(width, height)

    def _on_resize_settled(self):
        self.resize_timer_id = None
        self.resize_settled = True
        self.queue_draw()
        return False

    def _relayout(self, allocated_width, allocated_height):
        if not self.doc_model or allocated_width <= 0 or allocated_height <= 0:
            return

        # Check if size actually changed to avoid redundant layout passes and timer resets
        if allocated_width == self.last_width and allocated_height == self.last_height:
            return

        self.last_width = allocated_width
        self.last_height = allocated_height

        # Mark as not settled while resizing is active
        self.resize_settled = False

        if self.resize_timer_id is not None:
            GLib.source_remove(self.resize_timer_id)
            self.resize_timer_id = None

        self.resize_timer_id = GLib.timeout_add(200, self._on_resize_settled)

        page_count = self.doc_model.page_count
        if page_count == 0:
            return

        # Get first page dimensions
        first_page = self.doc_model.page_rect(0)
        w_p = first_page.width
        h_p = first_page.height

        # Find best grid (C x R) that maximizes page thumbnail size inside allocated bounds
        best_scale = 0.0
        best_C = 1
        best_R = page_count

        for C in range(1, page_count + 1):
            R = math.ceil(page_count / C)
            cell_w = allocated_width / C
            cell_h = allocated_height / R
            # Maximum scale factor for portrait/landscape page to fit in cell
            scale = min(cell_w / w_p, cell_h / h_p)
            if scale > best_scale:
                best_scale = scale
                best_C = C
                best_R = R

        self.n_cols = best_C
        self.n_rows = best_R
        self.thumb_scale = best_scale

        # Save actual thumbnail dimensions and cell dimensions
        self.thumb_w = w_p * best_scale
        self.thumb_h = h_p * best_scale
        self.cell_w = allocated_width / self.n_cols
        self.cell_h = allocated_height / self.n_rows

        self.queue_draw()

    def _on_pressed(self, gesture, n_press, x, y):
        if not self.doc_model or self.n_cols <= 0 or self.n_rows <= 0 or self.cell_w <= 0 or self.cell_h <= 0:
            return

        col = int(x // self.cell_w)
        row = int(y // self.cell_h)

        if col >= self.n_cols:
            col = self.n_cols - 1
        if row >= self.n_rows:
            row = self.n_rows - 1

        page_index = col * self.n_rows + row
        if 0 <= page_index < self.doc_model.page_count:
            if self.on_page_clicked:
                self.on_page_clicked(page_index)

    def _draw_func(self, drawing_area, widget_cr, width, height):
        if not self.doc_model or not self.cache or self.n_cols <= 0 or self.n_rows <= 0:
            widget_cr.set_source_rgb(0.95, 0.95, 0.95)
            widget_cr.paint()
            return

        scale_factor = self.get_scale_factor()

        # If currently resizing, render the cached backing texture scaled to new dimensions
        if not self.resize_settled and self.resize_cache_surface is not None:
            widget_cr.save()
            c_w = self.resize_cache_surface.get_width() / scale_factor
            c_h = self.resize_cache_surface.get_height() / scale_factor
            if c_w > 0 and c_h > 0:
                widget_cr.scale(width / c_w, height / c_h)
                widget_cr.set_source_surface(self.resize_cache_surface, 0, 0)
                widget_cr.paint()
            widget_cr.restore()
            return

        # Otherwise, render onto a temporary ImageSurface to refresh the cache
        physical_w = int(width * scale_factor)
        physical_h = int(height * scale_factor)
        if physical_w <= 0 or physical_h <= 0:
            widget_cr.set_source_rgb(0.95, 0.95, 0.95)
            widget_cr.paint()
            return

        temp_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, physical_w, physical_h)
        temp_surface.set_device_scale(scale_factor, scale_factor)

        cr = cairo.Context(temp_surface)

        # Background
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.paint()

        page_count = self.doc_model.page_count

        viewport_strips = []

        for i in range(page_count):
            col = i // self.n_rows
            row = i % self.n_rows

            cell_x = col * self.cell_w
            cell_y = row * self.cell_h

            # Scale and center page inside cell preserving aspect ratio
            page_rect = self.doc_model.page_rect(i)
            w_i = page_rect.width * self.thumb_scale
            h_i = page_rect.height * self.thumb_scale

            x = cell_x + (self.cell_w - w_i) / 2
            y = cell_y + (self.cell_h - h_i) / 2

            # 1. ALWAYS draw solid white page background
            cr.save()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(x, y, w_i, h_i)
            cr.fill()
            cr.restore()

            surface = self.cache.get(i)

            # Check if cached surface size is correct
            is_correct_size = False
            if surface is not None:
                sw = surface.get_width()
                target_physical_w = int(w_i * scale_factor)
                if abs(sw - target_physical_w) <= 1:
                    is_correct_size = True

            if surface is not None:
                # Paint existing surface (scaled dynamically if needed during window resizing)
                cr.save()
                cr.translate(x, y)
                sw = surface.get_width()
                sh = surface.get_height()
                cr.scale(w_i / (sw / scale_factor), h_i / (sh / scale_factor))
                cr.set_source_surface(surface, 0, 0)
                cr.paint()
                cr.restore()
            else:
                # Render loading placeholder text on the white background
                cr.save()
                cr.set_source_rgb(0.5, 0.5, 0.5)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(min(12.0, max(8.0, h_i * 0.2)))
                text = f"Ld {i + 1}"
                te = cr.text_extents(text)
                tx = x + (w_i - te.width) / 2 - te.x_bearing
                ty = y + (h_i - te.height) / 2 - te.y_bearing
                cr.move_to(tx, ty)
                cr.show_text(text)
                cr.restore()

            # Queue render job if not in cache or if size is wrong (only when resize has settled)
            if (surface is None or not is_correct_size) and self.resize_settled:
                if i not in self.in_flight and self.render_worker:
                    self.in_flight.add(i)
                    self.render_worker.queue_render_job(
                        priority=3,
                        doc_model=self.doc_model,
                        page_index=i,
                        zoom=self.thumb_scale,
                        scale_factor=scale_factor,
                        crop_rect=None,  # Minimap always renders full page (uncropped)
                        is_minimap=True,
                        target_cache=self.cache,
                        redraw_callback=lambda idx=i: self._on_thumbnail_complete(idx),
                    )

            if i == self.current_page:
                cr.save()
                cr.set_source_rgb(0.494, 0.247, 0.949)
                cr.set_line_width(2.0)
                cr.rectangle(x - 1, y - 1, w_i + 2, h_i + 2)
                cr.stroke()
                cr.restore()

            # Viewport strip tracker (on all pages the viewport overlaps)
            if self.main_vadjustment:
                main_zoom = getattr(self, "main_zoom", 1.0)
                main_page_gap = 12 if (self.settings and getattr(self.settings, "page_gaps", True)) else 0

                page_canvas_y0 = 0.0
                for j in range(i):
                    j_rect = None
                    if self.settings and self.settings.enabled and self.crop_analyzer:
                        j_rect = self.crop_analyzer.crop_rects[j]
                    j_rect = j_rect if j_rect is not None else self.doc_model.page_rect(j)
                    page_canvas_y0 += j_rect.height * main_zoom + main_page_gap

                main_crop_rect = None
                if self.settings and self.settings.enabled and self.crop_analyzer:
                    main_crop_rect = self.crop_analyzer.crop_rects[i]

                active_rect = main_crop_rect if main_crop_rect is not None else page_rect
                page_canvas_h = active_rect.height * main_zoom

                viewport_val = self.main_vadjustment.get_value()
                viewport_h = self.main_vadjustment.get_page_size()
                viewport_y0 = viewport_val
                viewport_y1 = viewport_val + viewport_h

                page_canvas_y1 = page_canvas_y0 + page_canvas_h

                strip_canvas_y0 = max(page_canvas_y0, viewport_y0)
                strip_canvas_y1 = min(page_canvas_y1, viewport_y1)

                if strip_canvas_y1 > strip_canvas_y0:
                    thumb_crop_y = (active_rect.y0 * self.thumb_scale) if main_crop_rect is not None else 0.0
                    thumb_crop_h = active_rect.height * self.thumb_scale

                    scale = thumb_crop_h / page_canvas_h

                    strip_rel_y = strip_canvas_y0 - page_canvas_y0

                    strip_thumb_y = y + thumb_crop_y + strip_rel_y * scale
                    strip_thumb_h = (strip_canvas_y1 - strip_canvas_y0) * scale

                    viewport_strips.append((x, strip_thumb_y, strip_thumb_h, w_i))

                    cr.save()
                    cr.set_source_rgba(0.494, 0.247, 0.949, 0.15)
                    cr.rectangle(x, strip_thumb_y, w_i, strip_thumb_h)
                    cr.fill()
                    cr.restore()

        # Draw single cohesive outline per column around the combined viewport strips
        if viewport_strips:
            col_groups = {}
            for sx, sy, sh, sw in viewport_strips:
                col_groups.setdefault(sx, []).append((sy, sh, sw))
            for col_x, strips in col_groups.items():
                strips.sort()
                overall_y0 = strips[0][0]
                overall_y1 = strips[-1][0] + strips[-1][1]
                overall_w = strips[0][2]
                cr.save()
                cr.set_source_rgba(0.494, 0.247, 0.949, 0.4)
                cr.set_line_width(1.0)
                cr.rectangle(col_x, overall_y0, overall_w, overall_y1 - overall_y0)
                cr.stroke()
                cr.restore()

        # Blit the rendered temporary surface to the actual widget context
        widget_cr.save()
        widget_cr.set_source_surface(temp_surface, 0, 0)
        widget_cr.paint()
        widget_cr.restore()

        # Save backing texture for resize frame scaling
        self.resize_cache_surface = temp_surface

    def _on_thumbnail_complete(self, page_index: int):
        self.in_flight.discard(page_index)
        self.queue_draw()


class MinimapWindow(Gtk.Window):
    """
    A centered modal window containing the fitting grid Minimap.
    Clicking a page thumbnail jumps to it in the main viewer and closes this window.
    """

    def __init__(
        self,
        parent_window,
        doc_model,
        cache,
        render_worker,
        crop_analyzer,
        settings,
        main_vadjustment,
        main_zoom,
        on_page_selected,
    ):
        super().__init__(
            title="Page Navigator", transient_for=parent_window, modal=True, destroy_with_parent=True
        )
        self.set_default_size(700, 520)

        # Titlebar using Adw.HeaderBar
        header = Adw.HeaderBar()
        self.set_titlebar(header)

        # Minimap widget (added directly to window, no scrolled window, no scrollbars)
        self.minimap = MiniMap()
        self.minimap.main_zoom = main_zoom
        self.minimap.set_document(doc_model, cache, render_worker, crop_analyzer, settings)
        self.minimap.set_vadjustment(main_vadjustment)
        self.set_child(self.minimap)

        # Connect callback to scroll and close the window
        self.minimap.on_page_clicked = lambda idx: (on_page_selected(idx), self.destroy())

        # Close on Escape key
        shortcut_controller = Gtk.ShortcutController.new()
        trigger = Gtk.ShortcutTrigger.parse_string("Escape")
        action = Gtk.CallbackAction.new(lambda w, a: (self.destroy(), True)[1])
        shortcut_controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        self.add_controller(shortcut_controller)
