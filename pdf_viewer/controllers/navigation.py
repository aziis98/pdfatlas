class NavigationController:
    """
    Controller handling document navigation, viewport scroll position, and zoom calculations.
    """

    def __init__(self, main_window):
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
        new_zoom = max(0.2, min(5.0, new_zoom))
        if abs(self.win.zoom - new_zoom) < 1e-4:
            return

        old_zoom = self.win.zoom

        viewport_w = float(self.win.scrolled_window.get_width())
        viewport_h = float(self.win.scrolled_window.get_height())

        val_v = self.win.vadjustment.get_value()
        val_h = self.win.hadjustment.get_value()

        center_y = anchor_y if anchor_y is not None else (val_v + (viewport_h / 2.0))
        center_x = anchor_x if anchor_x is not None else (val_h + (viewport_w / 2.0))

        # Anchor calculation isolating fixed unscaled page gaps from scaled content height
        active_page_index = 0
        if self.win.canvas.page_layout:
            for k, (y_off, _dw, dh, _crop) in enumerate(self.win.canvas.page_layout):
                if y_off + dh >= center_y:
                    active_page_index = k
                    break

        page_gap = self.win.canvas.page_gap
        fixed_gaps = (active_page_index + 1) * page_gap
        content_center_y = max(0.0, center_y - fixed_gaps)

        zoom_ratio = new_zoom / old_zoom
        new_content_center_y = content_center_y * zoom_ratio
        new_center_y = fixed_gaps + new_content_center_y

        new_center_x = center_x * zoom_ratio

        # Halt active GTK 4 kinetic scrolling animations
        self.win.scrolled_window.set_kinetic_scrolling(False)
        self.win.scrolled_window.set_kinetic_scrolling(True)

        self.win.zoom = new_zoom
        self.win.canvas.zoom = new_zoom
        self.win.canvas.update_layout()

        new_val_v = max(0.0, new_center_y - (viewport_h / 2.0) if anchor_y is None else new_center_y - anchor_y)
        new_val_h = max(0.0, new_center_x - (viewport_w / 2.0) if anchor_x is None else new_center_x - anchor_x)

        self.win.vadjustment.set_value(new_val_v)
        self.win.hadjustment.set_value(new_val_h)

        # Update zoom label
        pct = int(round(new_zoom * 100))
        if hasattr(self.win, "zoom_label") and self.win.zoom_label is not None:
            self.win.zoom_label.set_label(f"{pct}%")

        self.win.render_cache.clear()
        self.win.render_worker.clear_canvas_render_jobs()
        self.win.canvas.queue_draw_overlays()

    def zoom_in(self):
        self.set_zoom_level(self.win.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom_level(self.win.zoom / 1.2)

    def zoom_reset(self):
        self.set_zoom_level(1.0)

    def zoom_fit_width(self):
        if not self.win.doc_model or not self.win.canvas.page_layout:
            return
        viewport_w = float(self.win.scrolled_window.get_width())
        if viewport_w <= 100:
            return

        max_page_w = max(page.width for page in self.win.doc_model.pages)
        if max_page_w > 0:
            target_dw = max(100.0, viewport_w - (2.0 * self.win.canvas.page_gap) - 32.0)
            target_zoom = target_dw / (max_page_w * self.win.canvas.dpi_scale_factor)
            self.set_zoom_level(target_zoom)

    def zoom_fit_page(self):
        if not self.win.doc_model or not self.win.canvas.page_layout:
            return
        viewport_h = float(self.win.scrolled_window.get_height())
        if viewport_h <= 100:
            return

        max_page_h = max(page.height for page in self.win.doc_model.pages)
        if max_page_h > 0:
            target_dh = max(100.0, viewport_h - (2.0 * self.win.canvas.page_gap) - 32.0)
            target_zoom = target_dh / (max_page_h * self.win.canvas.dpi_scale_factor)
            self.set_zoom_level(target_zoom)

    def scroll_page(self, forward: bool = True):
        page_size = self.win.vadjustment.get_page_size()
        delta = page_size if forward else -page_size
        self.win.vadjustment.set_value(max(0.0, self.win.vadjustment.get_value() + delta))

    def scroll_step(self, forward: bool = True):
        step_size = 80.0
        delta = step_size if forward else -step_size
        self.win.vadjustment.set_value(max(0.0, self.win.vadjustment.get_value() + delta))
