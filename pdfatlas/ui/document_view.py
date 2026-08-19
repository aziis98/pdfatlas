from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango

from ..controllers.navigation import NavigationController
from ..core.arxiv_mapper import ArxivDiffMapper
from ..core.cache import MiniMapCache, RenderCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.layout import layout_scale
from ..core.pdf_source import PdfSource
from ..core.settings import CropSettings
from .canvas import PDFCanvas
from .components.selection_toolbar import SelectionToolbarComponent, SelectionToolbarState
from .components.zoom import ZoomControlsComponent, ZoomState
from .gui import label
from .link_preview import LinkPreviewManager
from .notes import NotesLayer


class PdfDocumentView(Gtk.Box):
    """
    Self-contained document view combining canvas layout, notes, link previews,
    floating toolbars, gestures, and per-tab loading/downloading state.
    """

    def __init__(
        self,
        render_worker: Any = None,
        settings: CropSettings | None = None,
        db_service: Any = None,
        on_page_changed: Callable[[int, int], None] | None = None,
        on_zoom_changed: Callable[[float], None] | None = None,
        on_link_clicked: Callable[[str, dict], None] | None = None,
        on_note_clicked: Callable[[Any], None] | None = None,
        on_note_create: Callable[[int, float, float], None] | None = None,
        on_selection_changed: Callable[[bool], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
        on_state_changed: Callable[[], None] | None = None,
        on_annotations_changed: Callable[[], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.render_worker = render_worker
        self.settings = settings or CropSettings()
        self.db_service = db_service
        self.on_page_changed = on_page_changed
        self.on_zoom_changed = on_zoom_changed
        self.on_link_clicked = on_link_clicked
        self.on_note_clicked = on_note_clicked
        self.on_note_create = on_note_create
        self.on_selection_changed = on_selection_changed
        self.on_toast = on_toast
        self.on_state_changed = on_state_changed
        self.on_annotations_changed = on_annotations_changed

        self.doc_model: DocumentModel | None = None
        self.current_source: PdfSource | None = None
        self.crop_analyzer: CropAnalyzer | None = None
        self.arxiv_mapper: ArxivDiffMapper | None = None

        self.render_cache = RenderCache()
        self.minimap_cache = MiniMapCache()
        self.highlights: list[Any] = []
        self.notes: list[Any] = []
        self.zoom: float = 1.0
        self.debug_note_rect: bool = False
        self._pinch_anchor_x: float = 0.0
        self._pinch_anchor_y: float = 0.0

        # Build canvas
        self.canvas = PDFCanvas()
        self.canvas.win = self
        self.canvas.on_link_clicked = self._handle_link_clicked
        self.canvas.on_selection_changed = self._handle_selection_changed

        self.notes_layer = NotesLayer(self)
        self.canvas.notes_layer = self.notes_layer
        self.canvas.on_note_create = self._handle_note_create

        self.link_preview_manager = LinkPreviewManager(self)
        self.canvas.on_link_hovered = self.link_preview_manager.on_link_hovered

        # Link preview floating text
        self.link_preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.link_preview_box.add_css_class("link-preview-box")
        self.link_preview_box.set_halign(Gtk.Align.START)
        self.link_preview_box.set_valign(Gtk.Align.END)
        self.link_preview_box.set_margin_start(16)
        self.link_preview_box.set_margin_bottom(16)
        self.link_preview_box.set_visible(False)
        self.link_preview_label = label(text="", css_class="caption")
        self.link_preview_box.append(self.link_preview_label)

        self.link_preview_card_box = Gtk.Box()
        self.link_preview_card_box.set_visible(False)
        self.debug_info_label: Gtk.Label | None = None
        self.debug_arxiv_label: Gtk.Label | None = None
        # Floating zoom controls
        self.zoom_component = ZoomControlsComponent(
            on_zoom_in=self.zoom_in,
            on_zoom_out=self.zoom_out,
        )
        self.zoom_floating_box = self.zoom_component.build_widget()
        self.zoom_label = self.zoom_component.zoom_label

        # Selection toolbar
        self.selection_toolbar_component = SelectionToolbarComponent(
            on_copy_text=self.copy_selection_text,
            on_copy_tex=self.copy_selection_tex,
        )
        self.selection_toolbar = self.selection_toolbar_component.build_widget()

        # Wire canvas overlays
        self.canvas.add_overlay(self.zoom_floating_box)
        self.canvas.add_overlay(self.link_preview_box)
        self.canvas.add_overlay(self.link_preview_manager.portal_card)
        self.canvas.add_overlay(self.selection_toolbar)

        self.vadjustment = self.canvas.vadjustment
        self.hadjustment = self.canvas.hadjustment
        self.vadjustment.connect("value-changed", self._on_scroll_vchanged)
        self.hadjustment.connect("value-changed", self._on_scroll_hchanged)
        self.saved_scroll_y: float = 0.0
        self.saved_scroll_x: float = 0.0

        # Gestures and Navigation Controller
        self.nav_controller = NavigationController(self)
        self.pointer_x: float = 0.0
        self.pointer_y: float = 0.0
        self._pinch_start_zoom: float = 1.0
        self._setup_canvas_gestures()

        # Stack for Canvas vs In-Tab Loading View
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.add_named(self.canvas, "canvas")

        # Centered In-Tab Loading View
        self.loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
        )
        self.loading_box.set_size_request(480, -1)

        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_size_request(48, 48)
        self.loading_spinner.set_halign(Gtk.Align.CENTER)
        self.loading_box.append(self.loading_spinner)

        self.loading_title = Gtk.Label()
        self.loading_title.add_css_class("title-2")
        self.loading_title.set_justify(Gtk.Justification.CENTER)
        self.loading_title.set_halign(Gtk.Align.CENTER)
        self.loading_title.set_hexpand(True)
        self.loading_title.set_label("Downloading Paper...")
        self.loading_box.append(self.loading_title)

        self.loading_subtitle = Gtk.Label()
        self.loading_subtitle.add_css_class("dim-label")
        self.loading_subtitle.set_justify(Gtk.Justification.CENTER)
        self.loading_subtitle.set_halign(Gtk.Align.CENTER)
        self.loading_subtitle.set_hexpand(True)
        self.loading_subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.loading_subtitle.set_label("Connecting to arXiv...")
        self.loading_box.append(self.loading_subtitle)

        self.loading_progress_bar = Gtk.ProgressBar()
        self.loading_progress_bar.set_size_request(360, 6)
        self.loading_progress_bar.set_halign(Gtk.Align.CENTER)
        self.loading_box.append(self.loading_progress_bar)

        self.stack.add_named(self.loading_box, "loading")
        self.append(self.stack)

    # --- In-Tab Loading Control ---

    @property
    def is_loading(self) -> bool:
        return self.stack.get_visible_child_name() == "loading"

    def show_loading(self, title: str = "Downloading Paper...", subtitle: str = "Connecting to arXiv..."):
        self.loading_title.set_label(title)
        self.loading_subtitle.set_label(subtitle)
        self.loading_progress_bar.set_fraction(0.0)
        self.loading_spinner.start()
        self.stack.set_visible_child_name("loading")

    def set_loading_progress(self, fraction: float, message: str):
        self.loading_progress_bar.set_fraction(fraction)
        self.loading_subtitle.set_label(message)

    def hide_loading(self):
        self.loading_spinner.stop()
        self.stack.set_visible_child_name("canvas")

    # --- Document Lifecycle ---

    def set_document(
        self,
        doc_model: DocumentModel,
        source: PdfSource,
        render_worker: Any = None,
    ):
        self.hide_loading()
        if render_worker:
            self.render_worker = render_worker

        self.doc_model = doc_model
        self.current_source = source
        self.crop_analyzer = CropAnalyzer(self.doc_model)

        self.highlights.clear()
        self.canvas.set_highlights([])

        self.notes.clear()
        self.notes_layer.clear()

        self.render_cache.clear()
        self.minimap_cache.clear()

        self.zoom = 1.0
        self.zoom_component.update_state(ZoomState(zoom=1.0))

        # Calculate display physical DPI
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display is not None else None
        monitor = monitors.get_item(0) if (monitors is not None and monitors.get_n_items() > 0) else None

        if monitor:
            geom = monitor.get_geometry()
            w_mm = monitor.get_width_mm()
            scale = monitor.get_scale_factor()
            if w_mm > 0:
                logical_dpi = (geom.width * 25.4) / w_mm
                physical_dpi = logical_dpi * scale
            else:
                physical_dpi = 96.0 * scale
        else:
            physical_dpi = 192.0

        self.canvas.dpi_scale_factor = 1.0
        self.canvas.screen_physical_dpi = physical_dpi

        self.canvas.set_document(
            self.doc_model,
            self.render_cache,
            self.render_worker,
            self.crop_analyzer,
            self.settings,
        )
        if self.settings:
            is_dark = (self.settings.color_scheme == "dark") or (
                self.settings.color_scheme == "system" and Adw.StyleManager.get_default().get_dark()
            )
            self.canvas.set_night_mode(
                is_dark,
                invert_amount=self.settings.night_mode_invert,
                hue_rotate=self.settings.night_mode_hue_rotate,
            )
        self.notes_layer.prepare()

        if self.on_page_changed and self.doc_model:
            self.on_page_changed(1, self.doc_model.page_count)
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def close(self):
        if self.render_worker and self.doc_model:
            self.render_worker.clear_canvas_render_jobs(self.doc_model.filepath)
        if self.doc_model:
            self.doc_model.close()
        self.render_cache.clear()
        self.minimap_cache.clear()
        self.doc_model = None
        self.current_source = None
        self.crop_analyzer = None
        self.arxiv_mapper = None

    # --- Canvas Gestures & Interactions ---

    def _setup_canvas_gestures(self):
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self._on_canvas_motion)
        self.canvas.add_controller(motion_controller)

        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect("scroll", self._on_canvas_scroll)
        self.canvas.add_controller(scroll_controller)

        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("released", self._on_canvas_clicked)
        self.canvas.add_controller(click_gesture)

        pinch_gesture = Gtk.GestureZoom.new()
        pinch_gesture.connect("begin", self._on_pinch_begin)
        pinch_gesture.connect("scale-changed", self._on_pinch_scale_changed)
        pinch_gesture.connect("end", self._on_pinch_end)
        self.canvas.add_controller(pinch_gesture)

    def _on_pinch_begin(self, gesture, sequence):
        self._pinch_start_zoom = self.zoom
        self.canvas.is_pinching = True
        success, center_x, center_y = gesture.get_bounding_box_center()
        if not success or (center_x == 0.0 and center_y == 0.0):
            center_x, center_y = self.canvas.get_pointer_pos()
        self._pinch_anchor_x = center_x
        self._pinch_anchor_y = center_y
        self.canvas.pinch_center_x = center_x
        self.canvas.pinch_center_y = center_y

    def _on_pinch_scale_changed(self, gesture, scale):
        new_zoom = self._pinch_start_zoom * gesture.get_scale_delta()
        success, center_x, center_y = gesture.get_bounding_box_center()
        if not success or (center_x == 0.0 and center_y == 0.0):
            center_x = self._pinch_anchor_x or self.canvas.get_pointer_pos()[0]
            center_y = self._pinch_anchor_y or self.canvas.get_pointer_pos()[1]
        self.canvas.pinch_center_x = center_x
        self.canvas.pinch_center_y = center_y
        # gesture coords are viewport-relative; convert to document coords for anchoring
        self.set_zoom_level(
            new_zoom,
            center_x=center_x + self.hadjustment.get_value(),
            center_y=center_y + self.vadjustment.get_value(),
        )

    def _on_pinch_end(self, gesture, sequence):
        self.canvas.is_pinching = False
        self.canvas.set_zoom(self.zoom)
        self._queue_canvas_redraw()

    def _on_canvas_clicked(self, gesture, n_press, x, y):
        self.canvas.grab_focus()
        if self.canvas.highlighted_block is not None:
            self.canvas.set_highlighted_block(0, None)
            self.canvas.queue_draw_overlays("clear-highlight")

    def _on_canvas_motion(self, controller, x, y):
        self.pointer_x = x
        self.pointer_y = y

    def _on_canvas_scroll(self, controller, dx, dy):
        modifiers = controller.get_current_event_state()
        if modifiers & Gdk.ModifierType.CONTROL_MASK:
            factor = 1.2 if dy < 0 else (1.0 / 1.2)
            px, py = self.canvas.get_pointer_pos()
            # pointer coords are viewport-relative; convert to document coords for anchoring
            self.set_zoom_level(
                self.zoom * factor,
                center_x=px + self.hadjustment.get_value(),
                center_y=py + self.vadjustment.get_value(),
            )
            return True
        return False

    def _queue_canvas_redraw(self):
        self.canvas.gl_canvas.queue_draw()

    def _schedule_state_save(self):
        if self.on_state_changed:
            self.on_state_changed()

    def _on_scroll_page_changed(self, adj):
        if not self.doc_model:
            return
        curr_page = self.get_current_page_index() + 1
        if self.on_page_changed and self.doc_model:
            self.on_page_changed(curr_page, self.doc_model.page_count)

    # --- Zoom Controls ---

    def set_zoom(self, zoom: float):
        self.set_zoom_level(zoom)

    def set_zoom_level(
        self,
        new_zoom: float,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        center_x: float | None = None,
        center_y: float | None = None,
    ):
        self.nav_controller.set_zoom_level(
            new_zoom,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            center_x=center_x,
            center_y=center_y,
        )
        self.zoom_component.update_state(ZoomState(zoom=self.zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def zoom_in(self):
        self.nav_controller.zoom_in()
        self.zoom_component.update_state(ZoomState(zoom=self.zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def zoom_out(self):
        self.nav_controller.zoom_out()
        self.zoom_component.update_state(ZoomState(zoom=self.zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def zoom_fit_width(self):
        self.nav_controller.zoom_fit_width()
        self.zoom_component.update_state(ZoomState(zoom=self.zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def zoom_fit_page(self):
        self.nav_controller.zoom_fit_page()
        self.zoom_component.update_state(ZoomState(zoom=self.zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def zoom_fit_height(self):
        self.zoom_fit_page()

    # --- Navigation & Positioning ---

    def get_current_page_index(self) -> int:
        if not self.canvas.page_layout:
            return 0
        val = self.vadjustment.get_value()
        curr = 0
        for i, (y, _, _, _) in enumerate(self.canvas.page_layout):
            if val >= y - 10:
                curr = i
            else:
                break
        return curr

    def jump_to_page(self, page_idx: int, y_offset: float | None = None):
        if y_offset is not None and self.canvas.page_layout:
            page_idx = max(0, min(page_idx, len(self.canvas.page_layout) - 1))
            page_y, _, _, crop_rect = self.canvas.page_layout[page_idx]
            scale = layout_scale(self.zoom, self.canvas.dpi_scale_factor)
            crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
            target_y = page_y + self.canvas.page_gap + (max(0.0, y_offset - crop_off_y) * scale)
            self.vadjustment.set_value(target_y)
            self.canvas.grab_focus()
            self.canvas.gl_canvas.queue_draw()
        else:
            self.nav_controller.jump_to_page(page_idx)

    def scroll_step(self, direction: int):
        self.nav_controller.scroll_step(forward=(direction > 0))

    def page_step(self, direction: int):
        self.nav_controller.scroll_page(forward=(direction > 0))

    def set_highlighted_block(self, page_idx: int, bbox: tuple[float, float, float, float] | None):
        self.canvas.set_highlighted_block(page_idx, bbox)

    def on_crop_settings_updated(self):
        self.canvas.on_crop_changed()

    def update_layout(self):
        self.canvas.update_layout()
        self.canvas._update_visibility()
        self.canvas.gl_canvas.queue_draw()
        self.canvas.queue_draw_overlays("docview-update")

    # --- Text Selection & Clipboard ---

    def copy_selection_text(self):
        if not self.canvas.text_selection or not self.canvas.text_selection.has_selection():
            return
        text = self.canvas.text_selection.get_selected_text()
        if text:
            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                clipboard.set(text)
                if self.on_toast:
                    self.on_toast("Copied selected text to clipboard")

    def copy_selection_tex(self):
        if not self.canvas.text_selection or not self.canvas.text_selection.has_selection():
            return
        text = ""
        sel = self.canvas.text_selection
        if self.arxiv_mapper and self.arxiv_mapper.is_ready:
            if sel.anchor_page is not None and sel.focus_page is not None:
                p_start = min(sel.anchor_page, sel.focus_page)
                p_end = max(sel.anchor_page, sel.focus_page)
                latex_parts = []
                for pi in range(p_start, p_end + 1):
                    rng = sel._selection_range(pi)
                    if rng:
                        s_char, e_char = rng
                        tex_snippet = self.arxiv_mapper.get_latex_for_pdf_range(pi, s_char, e_char)
                        if tex_snippet:
                            latex_parts.append(tex_snippet)
                if latex_parts:
                    text = "\n".join(latex_parts)

        if not text:
            text = sel.get_selected_text()

        if text:
            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                clipboard.set(text)
                if self.on_toast:
                    self.on_toast("Copied TeX source to clipboard")

    # --- Internal Event Handlers ---

    def _on_scroll_vchanged(self, adj: Gtk.Adjustment):
        if not self.doc_model:
            return
        self.saved_scroll_y = adj.get_value()
        curr_page = self.get_current_page_index() + 1
        if self.on_page_changed and self.doc_model:
            self.on_page_changed(curr_page, self.doc_model.page_count)

    def _on_scroll_hchanged(self, adj: Gtk.Adjustment):
        self.saved_scroll_x = adj.get_value()

    def restore_scroll_position(self):
        if self.saved_scroll_y > 0 and self.vadjustment:
            upper = self.vadjustment.get_upper()
            page_size = self.vadjustment.get_page_size()
            max_y = max(0.0, upper - page_size) if page_size > 0 else upper
            target_y = min(self.saved_scroll_y, max_y)
            self.vadjustment.set_value(target_y)
        if self.saved_scroll_x > 0 and self.hadjustment:
            upper = self.hadjustment.get_upper()
            page_size = self.hadjustment.get_page_size()
            max_x = max(0.0, upper - page_size) if page_size > 0 else upper
            target_x = min(self.saved_scroll_x, max_x)
            self.hadjustment.set_value(target_x)

    def _handle_link_clicked(self, page_idx: int, link: dict):
        uri = link.get("uri")
        if self.on_link_clicked:
            self.on_link_clicked(uri or "", link)

    def _handle_note_create(self, page: int, x: float, y: float):
        if self.on_note_create:
            self.on_note_create(page, x, y)

    def _handle_selection_changed(self, has_selection: bool):
        self.selection_toolbar_component.update_state(SelectionToolbarState(has_selection=has_selection))
        if self.on_selection_changed:
            self.on_selection_changed(has_selection)
