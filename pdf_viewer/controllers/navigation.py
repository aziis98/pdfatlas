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
        self.win.canvas.queue_draw()

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
        new_zoom = max(0.25, min(8.0, new_zoom))

        # Max out zoom at window width size if page width exceeds viewport width
        viewport_w = float(self.win.scrolled_window.get_width())
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
                max_zoom_fit = (viewport_w - 16.0) / (max_page_w * self.win.canvas.dpi_scale_factor)
                if max_zoom_fit > 0.1:
                    new_zoom = min(new_zoom, max_zoom_fit)

        if abs(old_zoom - new_zoom) < 1e-4:
            return

        # Halt active GTK 4 kinetic scrolling animations
        self.win.scrolled_window.set_kinetic_scrolling(False)
        self.win.scrolled_window.set_kinetic_scrolling(True)

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

        self.win.zoom = new_zoom
        if hasattr(self.win, "zoom_label") and self.win.zoom_label is not None:
            self.win.zoom_label.set_label(f"{int(new_zoom * 100)}%")

        # Apply to canvas (recomputes layout & updates vadjustment upper bounds instantly)
        self.win.canvas.set_zoom(new_zoom)

        # Re-accumulate new center_y: unscaled fixed_gaps + scaled content_y
        new_center_y = fixed_gaps + content_y * ratio
        new_val_v = new_center_y - (viewport_h / 2.0)

        lower = self.win.vadjustment.get_lower()
        upper = self.win.vadjustment.get_upper()
        max_y = max(lower, upper - viewport_h)
        target_v = clamp(lower, new_val_v, max_y)

        self.win.vadjustment.set_value(target_v)
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
        viewport_w = float(self.win.scrolled_window.get_width())
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
            target_zoom = (viewport_w - 24.0) / (max_page_w * self.win.canvas.dpi_scale_factor)
            self.set_zoom_level(target_zoom)

    def zoom_fit_page(self):
        if not self.win.doc_model:
            return
        viewport_w = float(self.win.scrolled_window.get_width())
        viewport_h = float(self.win.scrolled_window.get_height())
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
