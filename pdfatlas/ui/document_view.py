from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

from ..core.arxiv_mapper import ArxivDiffMapper
from ..core.cache import MiniMapCache, RenderCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.layout import layout_scale
from ..core.pdf_source import PdfSource
from ..core.renderer import RenderWorker
from ..core.settings import CropSettings
from .canvas import PDFCanvas
from .components.selection_toolbar import SelectionToolbarComponent, SelectionToolbarState
from .components.zoom import ZoomControlsComponent, ZoomState
from .gui import label
from .link_preview import LinkPreviewManager
from .notes import NotesLayer


class PdfDocumentView(Gtk.Box):
    """
    Self-contained document viewer component containing the continuous scroll canvas,
    OpenGL render pipeline, notes annotation overlay, link hover previews, text selection,
    and floating overlay toolbars.
    """

    def __init__(
        self,
        render_worker: RenderWorker | None = None,
        settings: CropSettings | None = None,
        on_page_changed: Callable[[int, int], None] | None = None,
        on_zoom_changed: Callable[[float], None] | None = None,
        on_link_clicked: Callable[[str, dict], None] | None = None,
        on_note_clicked: Callable[[Any], None] | None = None,
        on_note_create: Callable[[int, float, float], None] | None = None,
        on_selection_changed: Callable[[bool], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.render_worker = render_worker
        self.settings = settings or CropSettings()
        self.on_page_changed = on_page_changed
        self.on_zoom_changed = on_zoom_changed
        self.on_link_clicked = on_link_clicked
        self.on_note_clicked = on_note_clicked
        self.on_note_create = on_note_create
        self.on_selection_changed = on_selection_changed
        self.on_toast = on_toast

        self.doc_model: DocumentModel | None = None
        self.current_source: PdfSource | None = None
        self.crop_analyzer: CropAnalyzer | None = None
        self.arxiv_mapper: ArxivDiffMapper | None = None

        self.render_cache = RenderCache()
        self.minimap_cache = MiniMapCache()
        self.highlights: list[Any] = []
        self.notes: list[Any] = []
        self.zoom: float = 1.0

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

        # Floating zoom controls
        self.zoom_component = ZoomControlsComponent(
            on_zoom_in=self.zoom_in,
            on_zoom_out=self.zoom_out,
        )
        self.zoom_floating_box = self.zoom_component.build_widget()

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

        self.append(self.canvas)

    # --- Document Lifecycle ---

    def set_document(
        self,
        doc_model: DocumentModel,
        source: PdfSource,
        render_worker: RenderWorker | None = None,
    ):
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
        self.notes_layer.prepare()

        if self.on_page_changed and self.doc_model:
            self.on_page_changed(1, self.doc_model.page_count)
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def close(self):
        if self.render_worker and self.doc_model:
            self.render_worker.clear_canvas_render_jobs(self.doc_model.filepath)
        self.render_cache.clear()
        self.minimap_cache.clear()
        self.doc_model = None
        self.current_source = None
        self.crop_analyzer = None
        self.arxiv_mapper = None

    # --- Zoom Controls ---

    def set_zoom(self, zoom: float):
        zoom = max(0.2, min(zoom, 5.0))
        self.zoom = zoom
        self.canvas.set_zoom(zoom)
        self.zoom_component.update_state(ZoomState(zoom=zoom))
        if self.on_zoom_changed:
            self.on_zoom_changed(zoom)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.15)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.15)

    def zoom_fit_width(self):
        if not self.doc_model or not self.canvas.page_layout:
            return
        viewport_w = (
            self.canvas.hadjustment.get_page_size()
            if self.canvas.hadjustment and self.canvas.hadjustment.get_page_size() > 0
            else float(self.canvas.get_width())
        )
        if viewport_w <= 1.0:
            return
        curr_page = self.get_current_page_index()
        curr_page = max(0, min(curr_page, len(self.canvas.page_layout) - 1))
        _, dw, _, _ = self.canvas.page_layout[curr_page]
        if dw > 0:
            ratio = (viewport_w - 40) / dw
            self.set_zoom(self.zoom * ratio)

    def zoom_fit_height(self):
        if not self.doc_model or not self.canvas.page_layout:
            return
        viewport_h = (
            self.canvas.vadjustment.get_page_size()
            if self.canvas.vadjustment and self.canvas.vadjustment.get_page_size() > 0
            else float(self.canvas.get_height())
        )
        if viewport_h <= 1.0:
            return
        curr_page = self.get_current_page_index()
        curr_page = max(0, min(curr_page, len(self.canvas.page_layout) - 1))
        _, _, dh, _ = self.canvas.page_layout[curr_page]
        if dh > 0:
            ratio = (viewport_h - 40) / dh
            self.set_zoom(self.zoom * ratio)

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
        if not self.doc_model or not self.canvas.page_layout:
            return
        page_idx = max(0, min(page_idx, len(self.canvas.page_layout) - 1))
        page_y, _, _, crop_rect = self.canvas.page_layout[page_idx]

        if y_offset is not None:
            scale = layout_scale(self.zoom, self.canvas.dpi_scale_factor)
            crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
            target_y = page_y + self.canvas.page_gap + (max(0.0, y_offset - crop_off_y) * scale)
        else:
            target_y = page_y + self.canvas.page_gap

        self.vadjustment.set_value(target_y)
        self.canvas.grab_focus()
        self.canvas.gl_canvas.queue_draw()

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
        curr_page = self.get_current_page_index() + 1
        if self.on_page_changed and self.doc_model:
            self.on_page_changed(curr_page, self.doc_model.page_count)

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
