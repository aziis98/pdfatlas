from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.window import MainWindow


def clamp(val, min_val, max_val):
    return max(min_val, min(val, max_val))


class NavigationController:
    """
    Controller handling document navigation, viewport scroll position, and zoom calculations.
    """

    def __init__(self, main_window: "MainWindow"):
        self.win = main_window

    def jump_to_page(self, page_index: int, smooth: bool = True):
        """
        Navigates the viewport directly to the top of the specified page index.
        """
        if not self.win.doc_model or not self.win.canvas.page_layout:
            return

        if not (0 <= page_index < len(self.win.canvas.page_layout)):
            return

        y_offset, _dw, _dh, _crop_rect = self.win.canvas.page_layout[page_index]
        page_gap = self.win.canvas.page_gap

        target_y = max(0.0, y_offset - (page_gap / 2.0))

        if smooth:
            self.win.vadjustment.set_value(target_y)
        else:
            self.win.vadjustment.set_value(target_y)

        self.win.page_input.set_text(str(page_index + 1))
        self.win.canvas.queue_draw_overlays("jump-to-page")

    def jump_to_annotation(self, hl: dict):
        """
        Navigates the viewport to center vertically around the specified highlight annotation.
        """
        if not self.win.doc_model or not self.win.canvas.page_layout:
            return

        page_index = hl.get("page", 0)
        if not (0 <= page_index < len(self.win.canvas.page_layout)):
            return

        rects = hl.get("rects", [])
        if not rects:
            self.jump_to_page(page_index)
            return

        page_y_offset, _dw, display_h, _crop_rect = self.win.canvas.page_layout[page_index]
        pdf_page = self.win.doc_model.get_page(page_index)
        pdf_page_h = pdf_page.rect.height if pdf_page else 1.0
        scale_y = display_h / pdf_page_h if pdf_page_h > 0 else 1.0

        min_y = min(r[1] for r in rects)
        max_y = max(r[3] for r in rects)
        annotation_center_pts = (min_y + max_y) / 2.0

        annotation_y_pixels = page_y_offset + (annotation_center_pts * scale_y)
        viewport_h = self.win.vadjustment.get_page_size()

        target_y = max(0.0, annotation_y_pixels - (viewport_h / 2.0))
        max_scroll = self.win.vadjustment.get_upper() - viewport_h
        target_y = min(target_y, max(0.0, max_scroll))

        self.win.vadjustment.set_value(target_y)
        self.win.page_input.set_text(str(page_index + 1))
        self.win.canvas.queue_draw_overlays("jump-to-annotation")

    def set_zoom_level(
        self,
        new_zoom: float,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        center_x: float | None = None,
        center_y: float | None = None,
    ):
        """
        Updates canvas zoom level anchored at mouse position or viewport center.
        """
        if anchor_x is None:
            anchor_x = center_x
        if anchor_y is None:
            anchor_y = center_y

        if not self.win.doc_model:
            return

        old_zoom = self.win.zoom
        new_zoom = max(0.25, min(50.0, new_zoom))

        # Cap zoom: at max, only ~25% of page width visible in viewport
        viewport_w = float(self.win.canvas.viewport_width())
        if viewport_w > 50 and self.win.doc_model:
            max_page_w = 0.0
            for i in range(self.win.doc_model.page_count):
                rect = None
                if self.win.settings.enabled and self.win.crop_analyzer:
                    rect = self.win.crop_analyzer.crop_rects[i]
                if rect is None:
                    rect = self.win.doc_model.page_rect(i)
                if rect.width > max_page_w:
                    max_page_w = rect.width
            if max_page_w > 0:
                max_zoom_fit = viewport_w / (0.25 * max_page_w * self.win.canvas.dpi_scale_factor)
                new_zoom = min(new_zoom, max_zoom_fit)

        if abs(old_zoom - new_zoom) < 1e-4:
            return

        # Halt active GTK 4 kinetic scrolling animations
        self.win.canvas.set_kinetic_scrolling(False)
        self.win.canvas.set_kinetic_scrolling(True)

        val_v = self.win.vadjustment.get_value()
        viewport_h = self.win.vadjustment.get_page_size()
        if viewport_h <= 1.0:
            viewport_h = 700.0

        center_y = anchor_y if anchor_y is not None else (val_v + (viewport_h / 2.0))

        # Determine page index at center_y to separate unscaled page_gaps from scaled content height
        current_page_idx = self.win.get_current_page_index()
        gap_count = current_page_idx + 1
        fixed_gaps = gap_count * self.win.canvas.page_gap

        content_y = max(0.0, center_y - fixed_gaps)
        ratio = new_zoom / old_zoom

        # Horizontal anchor (content-space x): cursor position or viewport center.
        # Pages are centered within a content box that is as wide as the widest
        # page (or the viewport when everything fits).
        val_h = self.win.hadjustment.get_value()
        viewport_w_h = self.win.hadjustment.get_page_size()
        if viewport_w_h <= 1.0:
            viewport_w_h = 800.0
        center_x = anchor_x if anchor_x is not None else (val_h + (viewport_w_h / 2.0))
        box_w_old = max((dw for _, dw, _, _ in self.win.canvas.page_layout), default=0.0)

        self.win.zoom = new_zoom
        if hasattr(self.win, "zoom_label") and self.win.zoom_label is not None:
            self.win.zoom_label.set_label(f"{int(new_zoom * 100)}%")

        # Apply to canvas (recomputes layout & updates vadjustment upper bounds instantly)
        self.win.canvas.set_zoom(new_zoom)

        # Re-accumulate new center_y: unscaled fixed_gaps + scaled content_y
        new_center_y = fixed_gaps + content_y * ratio
        # Keep the anchor point at the same screen position it was before zoom
        old_screen_pos = center_y - val_v
        new_val_v = new_center_y - old_screen_pos

        lower = self.win.vadjustment.get_lower()
        upper = self.win.vadjustment.get_upper()
        max_y = max(lower, upper - viewport_h)
        target_v = clamp(lower, new_val_v, max_y)

        self.win.vadjustment.set_value(target_v)

        # Keep the horizontal anchor at the same screen position: the page stays
        # centered when zooming at its middle, and the point under the cursor
        # stays put otherwise. When everything fits, scroll_x collapses to 0.
        box_w_new = max((dw for _, dw, _, _ in self.win.canvas.page_layout), default=0.0)
        if box_w_old > 0.0:
            new_center_x = (box_w_new / 2.0) + (center_x - (box_w_old / 2.0)) * ratio
        else:
            new_center_x = box_w_new / 2.0
        old_screen_x = center_x - val_h
        new_val_h = new_center_x - old_screen_x

        h_upper = max(box_w_new, viewport_w_h)
        max_x = max(0.0, h_upper - viewport_w_h)
        target_h = clamp(0.0, new_val_h, max_x)

        self.win.hadjustment.set_upper(h_upper)
        self.win.hadjustment.set_value(target_h)

        self.win._on_scroll_page_changed(self.win.vadjustment)
        self.win.canvas._update_visibility()
        self.win._queue_canvas_redraw()

    def zoom_in(self):
        self.set_zoom_level(self.win.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom_level(self.win.zoom / 1.2)

    def zoom_reset(self):
        self.set_zoom_level(1.0)

    def zoom_fit_width(self):
        if not self.win.doc_model:
            return
        viewport_w = float(self.win.canvas.viewport_width())
        if viewport_w <= 100:
            return

        max_page_w = 0.0
        for i in range(self.win.doc_model.page_count):
            rect = None
            if self.win.settings.enabled and self.win.crop_analyzer:
                rect = self.win.crop_analyzer.crop_rects[i]
            if rect is None:
                rect = self.win.doc_model.page_rect(i)
            if rect.width > max_page_w:
                max_page_w = rect.width

        if max_page_w > 0:
            target_zoom = viewport_w / (max_page_w * self.win.canvas.dpi_scale_factor)
            self.set_zoom_level(target_zoom)

    def zoom_fit_page(self):
        if not self.win.doc_model:
            return
        viewport_w = float(self.win.canvas.viewport_width())
        viewport_h = float(self.win.canvas.viewport_height())
        if viewport_w <= 100 or viewport_h <= 100:
            return

        max_w = 0.0
        max_h = 0.0
        for i in range(self.win.doc_model.page_count):
            rect = None
            if self.win.settings.enabled and self.win.crop_analyzer:
                rect = self.win.crop_analyzer.crop_rects[i]
            if rect is None:
                rect = self.win.doc_model.page_rect(i)
            if rect.width > max_w:
                max_w = rect.width
            if rect.height > max_h:
                max_h = rect.height

        if max_w > 0 and max_h > 0:
            target_zoom_w = (viewport_w - 24.0) / (max_w * self.win.canvas.dpi_scale_factor)
            target_zoom_h = (viewport_h - 24.0) / (max_h * self.win.canvas.dpi_scale_factor)
            target_zoom = min(target_zoom_w, target_zoom_h)
            self.set_zoom_level(target_zoom)

    def scroll_page(self, forward: bool = True):
        page_size = self.win.vadjustment.get_page_size()
        delta = page_size if forward else -page_size
        self.win.vadjustment.set_value(max(0.0, self.win.vadjustment.get_value() + delta))

    def scroll_step(self, forward: bool = True):
        step_size = 80.0
        delta = step_size if forward else -step_size
        self.win.vadjustment.set_value(max(0.0, self.win.vadjustment.get_value() + delta))
