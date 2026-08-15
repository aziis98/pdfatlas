import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
import cairo
import fitz
import numpy as np
from gi.repository import Gdk, GLib, Gtk

# Render settings for portals
STRIP_PAD_PT = 8  # padding (in PDF points) above/below the matched block
STRIP_ZOOM = 2.0  # render zoom factor for thumbnail strips
HIGHLIGHT_RGBA = (1.0, 0.85, 0.0, 0.40)  # semi-transparent yellow highlight
WHITESPACE_TRIM_THRESHOLD = 248  # pixel value above which channel counts as "white"
WHITESPACE_TRIM_PAD_PT = 8  # margin kept around trimmed content


_rawdict_cache: dict[tuple[str, int], dict] = {}
_rawdict_cache_lock = threading.Lock()


def get_page_rawdict(pdf_path: str, page_no: int, page) -> dict:
    key = (pdf_path, page_no)
    with _rawdict_cache_lock:
        if key in _rawdict_cache:
            return _rawdict_cache[key]

    try:
        raw_dict = page.get_text("rawdict")
    except (RuntimeError, ValueError) as e:
        print(f"[portal] Failed to get rawdict: {e}", flush=True)
        raw_dict = {}

    with _rawdict_cache_lock:
        if len(_rawdict_cache) > 100:
            _rawdict_cache.clear()
        _rawdict_cache[key] = raw_dict
    return raw_dict


def get_query_match_rects(pdf_path: str, page_no: int, page, query_terms, clip_y0, clip_y1):
    """
    Finds character-level bounding boxes for all non-overlapping occurrences
    of query_terms inside the text spans of the page.
    """
    match_rects = []
    if not query_terms:
        return match_rects

    raw_dict = get_page_rawdict(pdf_path, page_no, page)

    for block in raw_dict.get("blocks", []):
        if block.get("type") != 0:  # Text block
            continue
        for line in block.get("lines", []):
            line_bbox = line.get("bbox", (0, 0, 0, 0))
            if line_bbox[3] < clip_y0 or line_bbox[1] > clip_y1:
                continue

            for span in line.get("spans", []):
                chars = span.get("chars", [])
                if not chars:
                    continue

                span_text = "".join(c["c"] for c in chars)
                span_text_lower = span_text.lower()
                for qt in query_terms:
                    if not qt:
                        continue

                    start_idx = 0
                    while True:
                        idx = span_text_lower.find(qt, start_idx)
                        if idx == -1:
                            break

                        # Extract characters matching the term and compute their union bounds
                        match_chars = chars[idx : min(len(chars), idx + len(qt))]
                        if match_chars:
                            ux0 = min(c["bbox"][0] for c in match_chars)
                            uy0 = min(c["bbox"][1] for c in match_chars)
                            ux1 = max(c["bbox"][2] for c in match_chars)
                            uy1 = max(c["bbox"][3] for c in match_chars)
                            match_rects.append((ux0, uy0, ux1, uy1))

                        start_idx = idx + len(qt)

    return match_rects


def _display_height(y0, y1):
    # Fixed vertical viewport height (52pt * 1.2 = ~62 DIPs)
    return 62


# Thread-local storage for PyMuPDF Document instances to ensure thread safety
class _ThreadDocStorage(threading.local):
    doc: fitz.Document | None
    pdf_path: str | None

    def __init__(self):
        self.doc = None
        self.pdf_path = None


_thread_local = _ThreadDocStorage()


def _thread_doc(pdf_path: str) -> fitz.Document:
    doc = _thread_local.doc
    if doc is None or _thread_local.pdf_path != pdf_path:
        doc = fitz.open(pdf_path)
        _thread_local.doc = doc
        _thread_local.pdf_path = pdf_path
    return doc


from .portal_preview import LinkPortalPreviewCard


def apply_card_decorations(surface: cairo.ImageSurface, scale_factor: float, r_dip: float = 8.0):
    """
    Bakes 8px rounded corners clip and 1px border stroke directly into the ARGB32 ImageSurface
    in background threads. GTK frame repaints become pure 1:1 hardware memory blits.
    """
    w_dip = float(surface.get_width()) / scale_factor
    h_dip = float(surface.get_height()) / scale_factor

    ctx = cairo.Context(surface)
    ctx.save()

    r = r_dip
    ctx.new_sub_path()
    ctx.arc(w_dip - r, r, r, -1.5707963, 0)
    ctx.arc(w_dip - r, h_dip - r, r, 0, 1.5707963)
    ctx.arc(r, h_dip - r, r, 1.5707963, 3.14159265)
    ctx.arc(r, r, r, 3.14159265, 4.71238898)
    ctx.close_path()

    ctx.set_operator(cairo.OPERATOR_DEST_IN)
    ctx.fill_preserve()

    ctx.set_operator(cairo.OPERATOR_OVER)
    ctx.set_source_rgba(0.0, 0.0, 0.0, 0.12)
    ctx.set_line_width(1.0)
    ctx.stroke()

    ctx.restore()


def render_strip_surface(
    pdf_path,
    page_no,
    x0,
    y0,
    x1,
    y1,
    query_terms,
    target_w: int = 450,
    target_h: int = 140,
    scale_factor: float = 2.0,
):
    """
    Renders one search result page-strip pre-scaled to target card pixel dimensions
    and sets device scale so GTK scroll passes perform a direct 1:1 memory blit.
    """
    doc = _thread_doc(pdf_path)
    page = doc[page_no - 1]

    # 1. Full page width bounds
    clip_x0 = 0.0
    clip_x1 = page.rect.width

    # 2. Fixed height vertical window scaled proportionally to avoid distortion
    zoom_x = (target_w * scale_factor) / page.rect.width if page.rect.width > 0 else 1.0
    zoom_y = zoom_x  # Enforce uniform 1:1 aspect ratio scaling

    window_height_pt = (target_h * scale_factor) / zoom_x if zoom_x > 0 else 180.0
    mid_y = (y0 + y1) / 2.0

    clip_y0 = mid_y - (window_height_pt / 2.0)
    clip_y1 = mid_y + (window_height_pt / 2.0)

    if clip_y0 < 0.0:
        clip_y0 = 0.0
        clip_y1 = min(page.rect.height, window_height_pt)
    elif clip_y1 > page.rect.height:
        clip_y1 = page.rect.height
        clip_y0 = max(0.0, page.rect.height - window_height_pt)

    clip = fitz.Rect(clip_x0, clip_y0, clip_x1, clip_y1)
    mat = fitz.Matrix(zoom_x, zoom_y)

    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=True)

    # Convert the pixmap raw bytes into a NumPy array
    arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))

    # Swap channels: RGBA to Cairo's native-endian memory BGRA (ARGB32)
    bgra = arr[:, :, [2, 1, 0, 3]].copy()

    h, w, _ = bgra.shape
    surface = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, w, h, w * 4)
    surface.set_device_scale(scale_factor, scale_factor)

    # 3. Draw Highlights with Cairo
    ctx = cairo.Context(surface)
    ctx.set_source_rgba(*HIGHLIGHT_RGBA)

    if query_terms:
        # Get precise character-level matched ranges
        match_rects = get_query_match_rects(pdf_path, page_no, page, query_terms, clip_y0, clip_y1)
        for ux0, uy0, ux1, uy1 in match_rects:
            px0 = (ux0 - clip.x0) * (zoom_x / scale_factor)
            py0 = (uy0 - clip.y0) * (zoom_y / scale_factor)
            pw = (ux1 - ux0) * (zoom_x / scale_factor)
            ph = (uy1 - uy0) * (zoom_y / scale_factor)
            ctx.rectangle(px0, py0, pw, ph)
        ctx.fill()

    # 4. Bake rounded corners transparency clip and border stroke into surface
    apply_card_decorations(surface, scale_factor)

    return surface


class ResultRow(Gtk.Overlay):
    """
    A single compact Search Result card: full-width portal snippet with floating top-left pin button
    (visible on hover or when pinned) and floating bottom-left page location pill.
    """

    def __init__(
        self,
        pdf_path,
        executor,
        result,
        query_terms,
        pinned=False,
        on_toggle_pin=None,
        on_render_done=None,
        on_row_clicked=None,
    ):
        super().__init__()
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.CENTER)

        self.result = result
        self.query_terms = query_terms
        self.on_toggle_pin = on_toggle_pin
        self.on_render_done = on_render_done
        self.on_row_clicked = on_row_clicked

        page = result["page"]
        x0, y0, x1, y1 = result["x0"], result["y0"], result["x1"], result["y1"]

        # Main Child: Uniform LinkPortalPreviewCard widget
        self.portal_card = LinkPortalPreviewCard()
        self.portal_card.set_portal_size(450, 140)
        self.portal_card.set_loading()
        self.portal_card.set_hexpand(False)
        self.portal_card.set_halign(Gtk.Align.CENTER)
        self.set_child(self.portal_card)

        # Top-Right Overlay: Floating Pin Button (view-pin-symbolic icon, visible on hover or if pinned)
        self.pin_button = Gtk.ToggleButton()
        self.pin_button.set_icon_name("view-pin-symbolic")
        self.pin_button.add_css_class("flat")
        self.pin_button.add_css_class("circular")
        self.pin_button.add_css_class("portal-overlay-pin")
        self.pin_button.set_tooltip_text("Pin result")
        self.pin_button.set_active(pinned)
        self.pin_button.set_margin_top(8)
        self.pin_button.set_margin_end(8)
        self.pin_button.set_halign(Gtk.Align.END)
        self.pin_button.set_valign(Gtk.Align.START)
        self.pin_button.set_opacity(1.0 if pinned else 0.0)
        self.pin_button.connect("toggled", self._on_pin_toggled)
        self.add_overlay(self.pin_button)

        # Bottom-Left Overlay: Floating Page Info Pill (opacity 0.5 by default, 1.0 on hover)
        self.page_label = Gtk.Label(label=f"Page {page}")
        self.page_label.add_css_class("caption")
        self.page_label.add_css_class("portal-overlay-pill")
        self.page_label.set_margin_bottom(8)
        self.page_label.set_margin_start(8)
        self.page_label.set_halign(Gtk.Align.START)
        self.page_label.set_valign(Gtk.Align.END)
        self.page_label.set_opacity(0.5)
        self.add_overlay(self.page_label)

        # Motion Controller to update overlays on hover
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", lambda ctrl, x, y: self._on_hover_changed(True))
        motion.connect("leave", lambda ctrl: self._on_hover_changed(False))
        self.add_controller(motion)

        # Handle mouse clicks on the row
        click = Gtk.GestureClick.new()
        click.connect("released", self._on_clicked)
        self.add_controller(click)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        executor.submit(self._render_worker, pdf_path, page, x0, y0, x1, y1, query_terms)

    def _on_hover_changed(self, is_hovered: bool):
        if self.pin_button.get_active():
            self.pin_button.set_opacity(1.0)
        else:
            self.pin_button.set_opacity(1.0 if is_hovered else 0.0)
        self.page_label.set_opacity(1.0 if is_hovered else 0.5)

    def _on_pin_toggled(self, btn):
        active = btn.get_active()
        btn.set_opacity(1.0 if active else 0.0)
        if self.on_toggle_pin:
            self.on_toggle_pin(self.result, self.query_terms, active)

    def _on_clicked(self, gesture, n_press, x, y):
        if self.on_row_clicked:
            self.on_row_clicked(self.result, self.query_terms)

    def _render_worker(self, pdf_path, page_no, x0, y0, x1, y1, query_terms):
        try:
            scale_factor = float(self.get_scale_factor() or 2.0)
            surface = render_strip_surface(
                pdf_path,
                page_no,
                x0,
                y0,
                x1,
                y1,
                query_terms,
                target_w=450,
                target_h=140,
                scale_factor=scale_factor,
            )
        except Exception as e:
            print(f"Error rendering portal strip surface: {e}")
            surface = None
        GLib.idle_add(self._apply_render, surface)

    def _apply_render(self, surface):
        if surface is not None:
            self._cached_surface = surface
            self.portal_card.set_surface(surface)
        if self.on_render_done:
            self.on_render_done()
        return False
