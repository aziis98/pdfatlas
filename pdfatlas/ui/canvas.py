
import gi
from typing import Any, Callable

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk

from ..core.cache import RenderCache
from ..core.crop import CropAnalyzer, CropSettings
from ..core.document import DocumentModel
from ..core.text_selection import TextSelection
from .gl_canvas import GLCanvas


class PageContainer(Gtk.Box):
    """
    A lightweight layout container representing a single PDF page.
    Maintains a fixed size and position within the vertical canvas so the
    OpenGL background layer can align page textures to the GTK scroll layout.
    """

    def __init__(self, page_index):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.page_index = page_index
        self.y_offset = 0.0
        self.w = 0.0
        self.h = 0.0
        self.crop_rect = None

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



class PDFCanvas(Gtk.Overlay):
    """
    Self-contained document display widget.

    Owns the OpenGL background layer (GLCanvas) and the Gtk.ScrolledWindow
    that hosts the continuous-scroll page layout. The overlay paints GL page
    textures behind the transparent GTK layout boxes so both layers stay
    pixel-aligned.
    """

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_focus_on_click(True)
        self.add_css_class("pdf-canvas")

        self.doc_model = None
        self.cache = None
        self.render_worker = None
        self.crop_analyzer = None
        self.debug_mode: bool = False
        self.settings = None

        self.zoom = 1.0
        self.crop_active = False
        self.page_gap = 12
        self.highlighted_block = None
        self.containers = []
        self.in_flight = set()
        self.page_layout = []
        self.vadjustment: Gtk.Adjustment
        self.hadjustment: Gtk.Adjustment

        # Interactive link state
        self.hovered_link: tuple[int, dict] | None = None
        self.on_link_clicked: Any = None
        self.on_link_hovered: Any = None
        self.on_page_hovered: Any = None
        self.on_selection_changed: Callable[[bool], None] | None = None
        self.text_selection: TextSelection | None = None
        self.debug_arxiv_data: dict[str, Any] | None = None
        self.hover_caret: tuple[int, tuple[float, float, float]] | None = None
        self._pending_drag_start: tuple[int, int] | None = None

        # Display DPI scale settings
        self.dpi_scale_factor = 1.0
        self.screen_physical_dpi = 192.0

        # Pinch-to-zoom state
        self.is_pinching = False
        self.pinch_center_x: float = 0.0
        self.pinch_center_y: float = 0.0

        # Base layer: OpenGL hardware-accelerated background
        self.gl_canvas = GLCanvas(canvas_layout_provider=self)
        self.gl_canvas.set_hexpand(True)
        self.gl_canvas.set_vexpand(True)
        self.set_child(self.gl_canvas)

        # Top layer: transparent scroll container holding the page layout
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_hexpand(True)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        self.add_overlay(self.scrolled_window)

        self._layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scrolled_window.set_child(self._layout)

        # Internal scroll wiring: adjustments feed visibility / render scheduling
        self.hadjustment = self.scrolled_window.get_hadjustment()
        self.vadjustment = self.scrolled_window.get_vadjustment()
        self.vadjustment.connect("value-changed", self._on_scroll)
        self.hadjustment.connect("value-changed", self._on_scroll)

        self.set_focusable(False)

        self._setup_scroll_input_controllers()
        self._setup_link_controllers()

    def _setup_scroll_input_controllers(self):
        """Attach full-viewport input controllers to the scroll container."""
        scroll_click = Gtk.GestureClick.new()
        scroll_click.set_button(1)
        scroll_click.connect("pressed", self._on_scrolled_window_click)
        self.scrolled_window.add_controller(scroll_click)

        scroll_drag = Gtk.GestureDrag.new()
        scroll_drag.set_button(1)
        scroll_drag.connect("drag-begin", self.on_drag_begin)
        scroll_drag.connect("drag-update", self.on_drag_update)
        scroll_drag.connect("drag-end", self.on_drag_end)
        self.scrolled_window.add_controller(scroll_drag)

        scroll_motion = Gtk.EventControllerMotion.new()
        scroll_motion.connect("motion", self._on_motion)
        scroll_motion.connect("leave", self._on_leave)
        self.scrolled_window.add_controller(scroll_motion)

    def _on_scrolled_window_click(self, gesture, n_press, x, y):
        self._on_click(gesture, n_press, x, y)

    def viewport_width(self) -> int:
        """Width of the visible document viewport, in device pixels."""
        return self.scrolled_window.get_width()

    def viewport_height(self) -> int:
        """Height of the visible document viewport, in device pixels."""
        return self.scrolled_window.get_height()

    def set_kinetic_scrolling(self, enabled: bool) -> None:
        """Enable or disable kinetic (inertial) scrolling of the document."""
        self.scrolled_window.set_kinetic_scrolling(enabled)

    def texture_bytes(self) -> int:
        """Total bytes currently held by the OpenGL texture cache."""
        return self.gl_canvas.texture_bytes()

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
        """Convert screen coordinates (viewport relative) to PDF point coordinates on a given page."""
        if not self.page_layout or page_index >= len(self.page_layout):
            return None

        scale = self.zoom * self.dpi_scale_factor
        viewport_w = (
            self.hadjustment.get_page_size()
            if self.hadjustment and self.hadjustment.get_page_size() > 0
            else float(self.get_width())
        )
        scroll_x = self.hadjustment.get_value() if self.hadjustment else 0.0
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        y_offset, dw, dh, crop_rect = self.page_layout[page_index]
        page_x0 = (viewport_w - dw) / 2.0

        rel_x = (x + scroll_x) - page_x0
        rel_y = (y + scroll_y) - y_offset

        crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
        crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

        pt_x = (rel_x / scale) + crop_off_x
        pt_y = (rel_y / scale) + crop_off_y
        return (pt_x, pt_y)

    def on_drag_begin(self, gesture, start_x, start_y):
        """Handle drag begin - prepare text selection if no link is hit."""
        if self.text_selection is None or not self.doc_model:
            return

        hit = self._hit_test_link(start_x, start_y)
        if hit is not None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        page_idx = self._hit_test_page(start_x, start_y)
        if page_idx is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        pt = self._screen_to_pdf_point(start_x, start_y, page_idx)
        if pt is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        char_idx = self.text_selection.hit_test(page_idx, pt[0], pt[1])
        if char_idx is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        # Record pending drag start; do not start selection yet (wait until mouse actually moves)
        self._pending_drag_start = (page_idx, char_idx)

    def on_drag_update(self, gesture, offset_x, offset_y):
        """Handle drag update - activate and extend text selection once drag starts."""
        if self.text_selection is None:
            return

        # Check if drag movement threshold (3px) is met
        dist_sq = offset_x * offset_x + offset_y * offset_y
        if dist_sq < 9.0 and not self.text_selection.is_selecting:
            return

        if self._pending_drag_start is not None and not self.text_selection.is_selecting:
            p_idx, c_idx = self._pending_drag_start
            self.text_selection.start_selection(p_idx, c_idx)
            self._pending_drag_start = None

        if not self.text_selection.is_selecting:
            return

        success, start_x, start_y = gesture.get_start_point()
        if not success:
            return

        cur_x = start_x + offset_x
        cur_y = start_y + offset_y

        page_idx = self._hit_test_page(cur_x, cur_y)
        if page_idx is None:
            return

        pt = self._screen_to_pdf_point(cur_x, cur_y, page_idx)
        if pt is None:
            return

        char_idx = self.text_selection.hit_test(page_idx, pt[0], pt[1])
        if char_idx is None:
            return

        self.text_selection.update_focus(page_idx, char_idx)
        self.queue_draw_overlays("selection-update")
        if self.on_selection_changed:
            self.on_selection_changed(self.text_selection.has_selection())

    def clear_selection(self):
        """Clear text selection and notify UI components."""
        if self.text_selection is not None:
            self.text_selection.clear_selection()
            self.queue_draw_overlays("selection-cleared")
            if self.on_selection_changed:
                self.on_selection_changed(False)

    def on_drag_end(self, gesture, offset_x, offset_y):
        """Handle drag end - finalize selection or clear if drag didn't start."""
        self._pending_drag_start = None
        if self.text_selection is not None:
            if abs(offset_x) < 3 and abs(offset_y) < 3:
                self.clear_selection()
            else:
                self.text_selection.end_selection()
                if self.on_selection_changed:
                    self.on_selection_changed(self.text_selection.has_selection())


    def _hit_test_page(self, x: float, y: float) -> int | None:
        if not self.page_layout:
            return None
        half_gap = self.page_gap / 2.0
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0
        canvas_y = y + scroll_y
        for i, layout in enumerate(self.page_layout):
            y_offset, dw, dh, crop_rect = layout
            if y_offset - half_gap <= canvas_y <= y_offset + dh + half_gap:
                return i
        return None

    def _hit_test_link(self, x: float, y: float) -> tuple[int, dict] | None:
        if not self.doc_model or not self.page_layout:
            return None

        scale = self.zoom * self.dpi_scale_factor
        viewport_w = (
            self.hadjustment.get_page_size()
            if self.hadjustment and self.hadjustment.get_page_size() > 0
            else float(self.get_width())
        )
        scroll_x = self.hadjustment.get_value() if self.hadjustment else 0.0
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        canvas_x = x + scroll_x
        canvas_y = y + scroll_y

        for i, layout in enumerate(self.page_layout):
            y_offset, dw, dh, crop_rect = layout
            page_x0 = (viewport_w - dw) / 2.0
            page_x1 = page_x0 + dw

            page_y0 = y_offset
            page_y1 = y_offset + dh

            if page_x0 <= canvas_x <= page_x1 and page_y0 <= canvas_y <= page_y1:
                rel_x = canvas_x - page_x0
                rel_y = canvas_y - y_offset

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
        viewport_w = (
            self.hadjustment.get_page_size()
            if self.hadjustment and self.hadjustment.get_page_size() > 0
            else float(self.get_width())
        )
        scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0

        if 0 <= page_index < len(self.page_layout):
            y_offset, dw, dh, crop_rect = self.page_layout[page_index]
            page_x0 = (viewport_w - dw) / 2.0
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
        self.gl_canvas.queue_draw()

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

        # Update text cursor caret overlay (left/right border of character box under pointer)
        new_caret: tuple[int, tuple[float, float, float]] | None = None
        hovered_page_idx = self._hit_test_page(x, y)
        if hovered_page_idx is not None and self.text_selection:
            pt = self._screen_to_pdf_point(x, y, hovered_page_idx)
            if pt is not None:
                char_idx = self.text_selection.hit_test(hovered_page_idx, pt[0], pt[1])
                if char_idx is not None:
                    pi = self.text_selection.get_page_index(hovered_page_idx)
                    if 0 <= char_idx < len(pi.chars):
                        c = pi.chars[char_idx]
                        if c.char and any(ch.isascii() for ch in c.char):
                            x0, y0, x1, y1 = c.bbox
                            dx = 0.0 if (x0 <= pt[0] <= x1) else min(abs(pt[0] - x0), abs(pt[0] - x1))
                            dy = 0.0 if (y0 <= pt[1] <= y1) else min(abs(pt[1] - y0), abs(pt[1] - y1))
                            dist = (dx * dx + dy * dy) ** 0.5
                            if dist <= 25.0:

                                w_str = c.char
                                L = max(1, len(w_str))
                                char_w = (x1 - x0) / float(L)
                                offset_i = max(0, min(L - 1, int((pt[0] - x0) / char_w))) if char_w > 0 else 0
                                c_left = x0 + offset_i * char_w
                                c_right = x0 + (offset_i + 1) * char_w
                                cx = c_left if (pt[0] < (c_left + c_right) / 2.0) else c_right
                                new_caret = (hovered_page_idx, (cx, y0, y1))




        if self.hover_caret != new_caret:
            self.hover_caret = new_caret
            self.queue_draw_overlays("hover-caret")

        if self.on_page_hovered:
            self.on_page_hovered(hovered_page_idx, x, y)

    def _on_leave(self, controller):
        if self.hover_caret is not None:
            self.hover_caret = None
            self.queue_draw_overlays("leave-caret")

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
        if self.text_selection:
            self.clear_selection()
        self.text_selection = TextSelection(doc_model) if doc_model else None

        # Remove old containers
        child = self._layout.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._layout.remove(child)
            child = nxt

        self.containers = []
        self.page_layout = []
        self.update_layout()

    def _on_scroll(self, adj):
        self._update_visibility()
        self.gl_canvas.queue_draw()

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
        anchor = self._anchor_layout_point()
        self.in_flight.clear()
        if self.render_worker:
            self.render_worker.clear_canvas_render_jobs()
        if self.cache:
            self.cache.clear()
        self.update_layout()
        self._restore_anchor(anchor)
        self.queue_draw_overlays("crop-changed")

    def _anchor_layout_point(self) -> tuple[int, float] | None:
        """Capture the (page_index, pdf_y) point currently at viewport center."""
        if not self.page_layout or self.vadjustment is None:
            return None
        point_y = self.vadjustment.get_value() + self.vadjustment.get_page_size() * 0.5
        scale = self.zoom * self.dpi_scale_factor
        for i, (y0, _dw, dh, crop) in enumerate(self.page_layout):
            if y0 + dh >= point_y - 1e-6:
                crop_off = crop.y0 if crop is not None else 0.0
                pdf_y = crop_off + (point_y - y0) / scale
                return (i, pdf_y)
        if self.page_layout:
            _y0, _dw, _dh, crop = self.page_layout[-1]
            return (len(self.page_layout) - 1, crop.y0 if crop is not None else 0.0)
        return None

    def _restore_anchor(self, anchor: tuple[int, float] | None) -> None:
        """Keep the anchored document point at viewport center after a layout change."""
        if anchor is None or self.vadjustment is None or not self.page_layout:
            return
        page_index, pdf_y = anchor
        if page_index >= len(self.page_layout):
            return
        y0, _dw, _dh, crop = self.page_layout[page_index]
        crop_off = crop.y0 if crop is not None else 0.0
        target_center = y0 + (pdf_y - crop_off) * (self.zoom * self.dpi_scale_factor)

        page_size = self.vadjustment.get_page_size()
        target_val = target_center - page_size * 0.5
        lower = self.vadjustment.get_lower()
        max_y = max(lower, self.vadjustment.get_upper() - page_size)
        self.vadjustment.set_value(max(lower, min(target_val, max_y)))

    def set_highlighted_block(self, page_index: int, bbox: tuple | None):
        self.highlighted_block = (page_index, bbox) if bbox is not None else None
        self.gl_canvas.queue_draw()

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
        self._layout.set_spacing(self.page_gap)
        self._layout.set_margin_top(int(self.page_gap))
        self._layout.set_margin_bottom(int(self.page_gap))

        # Rebuild/recreate container widgets if size differs
        if len(self.containers) != page_count:
            child = self._layout.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self._layout.remove(child)
                child = nxt

            self.containers = []
            for i in range(page_count):
                container = PageContainer(i)
                self._layout.append(container)
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

        first_visible = None
        last_visible = None

        # 1. Collect visible pages and queue render requests for them
        for i, container in enumerate(self.containers):
            page_y0 = container.y_offset
            page_y1 = container.y_offset + container.h
            if page_y1 >= y_min and page_y0 <= y_max:
                if first_visible is None:
                    first_visible = i
                last_visible = i

                if not self.is_pinching:
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
        # Always redraw — GL drawing code uses get_best to pick the best available surface
        self.gl_canvas.queue_draw()
