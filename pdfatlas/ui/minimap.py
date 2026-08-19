from __future__ import annotations

import math
from typing import Callable

import cairo
import fitz
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..core.cache import MiniMapCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.settings import CropSettings


def compute_grid(page_count: int, alloc_w: float, alloc_h: float,
                 first_page_w: float, first_page_h: float):
    best_scale = 0.0
    best_C = 1
    best_R = page_count
    for C in range(1, page_count + 1):
        R = math.ceil(page_count / C)
        cell_w = alloc_w / C
        cell_h = alloc_h / R
        scale = min(cell_w / first_page_w, cell_h / first_page_h)
        if scale > best_scale:
            best_scale = scale
            best_C = C
            best_R = R
    thumb_w = first_page_w * best_scale
    thumb_h = first_page_h * best_scale
    cell_w = alloc_w / best_C
    cell_h = alloc_h / best_R
    return best_C, best_R, best_scale, thumb_w, thumb_h, cell_w, cell_h


class MiniMap(Gtk.DrawingArea):
    """
    Sidebar Minimap displaying thumbnails in a vertical column-wrapping layout.
    Features:
      - Dark mode and light mode adaptive rendering.
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
        self.page_layout: list[tuple[float, float, float, fitz.Rect | None]] | None = None
        self.page_gap: int = 12
        self.main_vadjustment: Gtk.Adjustment | None = None
        self._vadj_handler_id: int | None = None
        self.on_page_clicked: Callable[[int], None] | None = None
        self.current_page = 0
        self.thumb_scale = 0.15
        self.thumb_w = 90
        self.thumb_h = 120  # Dynamically calculated
        self.cell_w = 100
        self.cell_h = 130
        self.items_per_column = 1
        self.n_cols = 1
        self.n_rows = 1
        self.in_flight: set[int] = set()

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
        page_layout: list[tuple[float, float, float, fitz.Rect | None]] | None = None,
        page_gap: int = 12,
    ):
        self.doc_model = doc_model
        self.cache = cache
        self.render_worker = render_worker
        self.crop_analyzer = crop_analyzer
        self.settings = settings
        self.page_layout = page_layout
        self.page_gap = page_gap
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
        if self.main_vadjustment and self._vadj_handler_id is not None:
            if self.main_vadjustment.handler_is_connected(self._vadj_handler_id):
                self.main_vadjustment.disconnect(self._vadj_handler_id)
            self._vadj_handler_id = None

    def set_vadjustment(self, vadjustment: Gtk.Adjustment):
        if self.main_vadjustment and self._vadj_handler_id is not None:
            if self.main_vadjustment.handler_is_connected(self._vadj_handler_id):
                self.main_vadjustment.disconnect(self._vadj_handler_id)
            self._vadj_handler_id = None

        self.main_vadjustment = vadjustment
        if self.main_vadjustment is not None:
            self._vadj_handler_id = self.main_vadjustment.connect(
                "value-changed", lambda adj: self.queue_draw()
            )

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
        if allocated_width == self.last_width and allocated_height == self.last_height:
            return

        self.last_width = allocated_width
        self.last_height = allocated_height
        self.resize_settled = False
        self.in_flight.clear()

        if self.resize_timer_id is not None:
            GLib.source_remove(self.resize_timer_id)
            self.resize_timer_id = None
        self.resize_timer_id = GLib.timeout_add(200, self._on_resize_settled)

        page_count = self.doc_model.page_count
        if page_count == 0:
            return

        first_page = self.doc_model.page_rect(0)
        self.n_cols, self.n_rows, self.thumb_scale, self.thumb_w, self.thumb_h, self.cell_w, self.cell_h = (
            compute_grid(page_count, allocated_width, allocated_height, first_page.width, first_page.height)
        )

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
        if self.doc_model and (width != self.last_width or height != self.last_height or self.n_cols <= 0):
            self._relayout(width, height)

        is_dark = Adw.StyleManager.get_default().get_dark()
        bg_rgb = (0.12, 0.12, 0.12) if is_dark else (0.94, 0.94, 0.94)

        if not self.doc_model or not self.cache or self.n_cols <= 0 or self.n_rows <= 0:
            widget_cr.set_source_rgb(*bg_rgb)
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

        physical_w = int(width * scale_factor)
        physical_h = int(height * scale_factor)
        if physical_w <= 0 or physical_h <= 0:
            widget_cr.set_source_rgb(*bg_rgb)
            widget_cr.paint()
            return

        temp_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, physical_w, physical_h)
        temp_surface.set_device_scale(scale_factor, scale_factor)

        cr = cairo.Context(temp_surface)

        # Background
        cr.set_source_rgb(*bg_rgb)
        cr.paint()

        page_count = self.doc_model.page_count

        for i in range(page_count):
            col = i // self.n_rows
            row = i % self.n_rows

            cell_x = col * self.cell_w
            cell_y = row * self.cell_h

            page_rect = self.doc_model.page_rect(i)
            w_i = page_rect.width * self.thumb_scale
            h_i = page_rect.height * self.thumb_scale

            x = cell_x + (self.cell_w - w_i) / 2
            y = cell_y + (self.cell_h - h_i) / 2

            # 1. Subtle drop shadow behind page
            cr.save()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.35 if is_dark else 0.10)
            cr.rectangle(x + 1, y + 2, w_i, h_i)
            cr.fill()
            cr.restore()

            # 2. Solid page background
            cr.save()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(x, y, w_i, h_i)
            cr.fill()
            cr.restore()

            # 3. Page thumbnail texture
            surface = self.cache.get(i)
            is_correct_size = False
            if surface is not None:
                sw = surface.get_width()
                target_physical_w = int(w_i * scale_factor)
                if abs(sw - target_physical_w) <= 1:
                    is_correct_size = True

            if surface is not None:
                cr.save()
                cr.translate(x, y)
                sw = surface.get_width()
                sh = surface.get_height()
                cr.scale(w_i / (sw / scale_factor), h_i / (sh / scale_factor))
                cr.set_source_surface(surface, 0, 0)
                cr.paint()
                cr.restore()
            else:
                cr.save()
                cr.set_source_rgb(0.5, 0.5, 0.5)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(min(12.0, max(8.0, h_i * 0.2)))
                text = f"{i + 1}"
                te = cr.text_extents(text)
                tx = x + (w_i - te.width) / 2 - te.x_bearing
                ty = y + (h_i - te.height) / 2 - te.y_bearing
                cr.move_to(tx, ty)
                cr.show_text(text)
                cr.restore()

            # 4. Page 1px border
            cr.save()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.15) if is_dark else cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
            cr.set_line_width(1.0)
            cr.rectangle(x, y, w_i, h_i)
            cr.stroke()
            cr.restore()

            # Queue render job if not in cache or if size is wrong
            if (surface is None or not is_correct_size) and self.resize_settled:
                if i not in self.in_flight and self.render_worker:
                    self.in_flight.add(i)
                    self.render_worker.queue_render_job(
                        priority=3,
                        doc_model=self.doc_model,
                        page_index=i,
                        zoom=self.thumb_scale,
                        scale_factor=scale_factor,
                        crop_rect=None,
                        is_minimap=True,
                        target_cache=self.cache,
                        redraw_callback=lambda idx=i: self._on_thumbnail_complete(idx),
                    )

            # 5. Cropped boundary dashed rectangle
            crop_rect = None
            if self.settings and self.settings.enabled and self.crop_analyzer:
                if i < len(self.crop_analyzer.crop_rects):
                    crop_rect = self.crop_analyzer.crop_rects[i]

            if crop_rect is not None:
                cr.save()
                crop_x = x + (crop_rect.x0 * self.thumb_scale)
                crop_y = y + (crop_rect.y0 * self.thumb_scale)
                crop_w = crop_rect.width * self.thumb_scale
                crop_h = crop_rect.height * self.thumb_scale
                cr.set_source_rgba(0.2, 0.2, 0.2, 0.45) if not is_dark else cr.set_source_rgba(0.8, 0.8, 0.8, 0.45)
                cr.set_line_width(1.0)
                cr.set_dash([3.0, 2.0])
                cr.rectangle(crop_x, crop_y, crop_w, crop_h)
                cr.stroke()
                cr.restore()

            # 6. Active page selection indicator
            if i == self.current_page:
                cr.save()
                cr.set_source_rgb(0.494, 0.247, 0.949)
                cr.set_line_width(2.5)
                cr.rectangle(x - 2, y - 2, w_i + 4, h_i + 4)
                cr.stroke()
                cr.restore()

            # 7. Viewport strip tracker
            if self.main_vadjustment:
                if self.page_layout and i < len(self.page_layout):
                    page_canvas_y0, _dw, page_canvas_h, active_crop = self.page_layout[i]
                else:
                    page_gap = self.page_gap
                    page_canvas_y0 = 0.0
                    for j in range(i):
                        j_rect = (
                            self.crop_analyzer.crop_rects[j]
                            if (self.settings and self.settings.enabled and self.crop_analyzer and j < len(self.crop_analyzer.crop_rects))
                            else None
                        )
                        if j_rect is None:
                            j_rect = self.doc_model.page_rect(j)
                        page_canvas_y0 += (j_rect.height if j_rect is not None else 800.0) * self.main_zoom + page_gap
                    active_rect = (
                        crop_rect if (self.settings and self.settings.enabled and crop_rect is not None) else page_rect
                    )
                    page_canvas_h = (active_rect.height if active_rect is not None else 800.0) * self.main_zoom

                viewport_val = self.main_vadjustment.get_value()
                viewport_h = self.main_vadjustment.get_page_size()
                viewport_y0 = viewport_val
                viewport_y1 = viewport_val + viewport_h

                page_canvas_y1 = page_canvas_y0 + page_canvas_h

                strip_canvas_y0 = max(page_canvas_y0, viewport_y0)
                strip_canvas_y1 = min(page_canvas_y1, viewport_y1)

                if strip_canvas_y1 > strip_canvas_y0 and page_canvas_h > 0:
                    thumb_crop_x = (crop_rect.x0 * self.thumb_scale) if crop_rect is not None else 0.0
                    thumb_crop_y = (crop_rect.y0 * self.thumb_scale) if crop_rect is not None else 0.0
                    thumb_crop_w = (crop_rect.width * self.thumb_scale) if crop_rect is not None else w_i
                    thumb_crop_h = (crop_rect.height * self.thumb_scale) if crop_rect is not None else h_i

                    scale_y = thumb_crop_h / page_canvas_h
                    strip_rel_y = strip_canvas_y0 - page_canvas_y0

                    strip_thumb_x = x + thumb_crop_x
                    strip_thumb_y = y + thumb_crop_y + (strip_rel_y * scale_y)
                    strip_thumb_w = thumb_crop_w
                    strip_thumb_h = (strip_canvas_y1 - strip_canvas_y0) * scale_y

                    cr.save()
                    cr.set_source_rgba(0.494, 0.247, 0.949, 0.18)
                    cr.rectangle(strip_thumb_x, strip_thumb_y, strip_thumb_w, strip_thumb_h)
                    cr.fill()

                    cr.set_source_rgba(0.494, 0.247, 0.949, 0.65)
                    cr.set_line_width(1.5)
                    cr.rectangle(strip_thumb_x, strip_thumb_y, strip_thumb_w, strip_thumb_h)
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


class MinimapWindow(Adw.Dialog):
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
        page_layout: list[tuple[float, float, float, fitz.Rect | None]] | None = None,
        page_gap: int = 12,
    ):
        super().__init__(title="Minimap")
        self.set_content_width(700)
        self.set_content_height(520)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Titlebar using Adw.HeaderBar
        header = Adw.HeaderBar()
        content_box.append(header)

        # Minimap widget
        self.minimap = MiniMap()
        self.minimap.set_vexpand(True)
        self.minimap.set_hexpand(True)
        self.minimap.main_zoom = main_zoom
        self.minimap.set_document(
            doc_model, cache, render_worker, crop_analyzer, settings, page_layout=page_layout, page_gap=page_gap
        )
        self.minimap.set_vadjustment(main_vadjustment)
        content_box.append(self.minimap)

        self.set_child(content_box)

        # Connect callback to scroll and close the window
        def _handle_page_clicked(idx: int) -> None:
            on_page_selected(idx)
            self.close()

        self.minimap.on_page_clicked = _handle_page_clicked

        # Close on Escape key
        shortcut_controller = Gtk.ShortcutController.new()
        trigger = Gtk.ShortcutTrigger.parse_string("Escape")
        action = Gtk.CallbackAction.new(lambda w, a: (self.close(), True)[1])
        shortcut_controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        self.add_controller(shortcut_controller)

