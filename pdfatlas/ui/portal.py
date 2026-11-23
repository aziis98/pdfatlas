import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
import cairo
import pymupdf as fitz
import numpy as np
from gi.repository import Gdk, GLib, Gtk

from ..core.crop import CropAnalyzer

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


_page_trim_cache: dict[tuple[str, int], tuple[float, float]] = {}
_page_trim_lock = threading.Lock()


def get_page_horizontal_trim(
    pdf_path: str,
    page_no: int,
    page: fitz.Page,
    pad_pt: float = WHITESPACE_TRIM_PAD_PT,
    min_width_ratio: float = 0.60,
) -> tuple[float, float]:
    """
    Detect page-level horizontal content bounds [x0, x1] in PDF points.
    Applies a sparse-page floor (min_width_ratio * page_width) so cards retain
    consistent font scaling across normal and sparse pages.
    """
    page_w = float(page.rect.width)
    if page_w <= 0.0:
        return 0.0, page_w

    key = (pdf_path, int(page_no))
    with _page_trim_lock:
        cached = _page_trim_cache.get(key)
        if cached is not None:
            return cached

    scale = CropAnalyzer.ANALYSIS_SCALE
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    bbox = CropAnalyzer.analyze_pixmap(pix.width, pix.height, pix.n, pix.samples, scale=scale)

    if bbox is None:
        bounds = (0.0, page_w)
    else:
        x0 = max(0.0, float(bbox.x0) - pad_pt)
        x1 = min(page_w, float(bbox.x1) + pad_pt)
        min_w = page_w * min_width_ratio
        if x1 - x0 < min_w:
            mid = (x0 + x1) * 0.5
            x0 = max(0.0, mid - min_w * 0.5)
            x1 = min(page_w, x0 + min_w)
            if x1 - x0 < min_w:
                x0 = max(0.0, x1 - min_w)
        bounds = (x0, x1)

    with _page_trim_lock:
        if len(_page_trim_cache) > 200:
            _page_trim_cache.clear()
        _page_trim_cache[key] = bounds
    return bounds


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
    autotrim: bool = True,
):
    """
    Renders one search result page-strip pre-scaled to target card pixel dimensions
    and sets device scale so GTK scroll passes perform a direct 1:1 memory blit.
    """
    doc = _thread_doc(pdf_path)
    page = doc[page_no - 1]

    # 1. Horizontal bounds: page-level trimmed width (with sparse floor) or full page width
    if autotrim:
        clip_x0, clip_x1 = get_page_horizontal_trim(pdf_path, page_no, page)
    else:
        clip_x0 = 0.0
        clip_x1 = float(page.rect.width)
    clip_w = max(1.0, clip_x1 - clip_x0)

    # 2. Fixed height vertical window scaled proportionally to avoid distortion
    zoom_x = (target_w * scale_factor) / clip_w
    zoom_y = zoom_x  # Enforce uniform 1:1 aspect ratio scaling

    # Enforce maximum portal viewport height to 50% page height
    max_window_pt = page.rect.height * 0.5
    window_height_pt = (target_h * scale_factor) / zoom_x if zoom_x > 0 else 180.0
    if window_height_pt > max_window_pt:
        window_height_pt = max_window_pt

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

    match_count = 0
    if query_terms:
        # Get precise character-level matched ranges
        match_rects = get_query_match_rects(pdf_path, page_no, page, query_terms, clip_y0, clip_y1)
        match_count = len(match_rects)
        for ux0, uy0, ux1, uy1 in match_rects:
            px0 = (ux0 - clip.x0) * (zoom_x / scale_factor)
            py0 = (uy0 - clip.y0) * (zoom_y / scale_factor)
            pw = (ux1 - ux0) * (zoom_x / scale_factor)
            ph = (uy1 - uy0) * (zoom_y / scale_factor)
            ctx.rectangle(px0, py0, pw, ph)
        ctx.fill()

    # 4. Bake rounded corners transparency clip and border stroke into surface
    apply_card_decorations(surface, scale_factor)

    return surface, match_count


def compute_portal_card_height(
    y0: float,
    y1: float,
    target_w: int = 360,
    page_w: float = 600.0,
    page_h: float = 792.0,
    min_h: int = 60,
    max_h: int | None = None,
) -> int:
    """
    Computes content-adaptive portal card pixel height based on matched text block bounds.
    Taller paragraphs and multi-line clusters receive larger card heights, while short
    one-line matches produce compact cards. Max card portal viewport height is capped at
    50% of page height.
    """
    block_h = max(12.0, float(y1) - float(y0))
    context_pad = max(14.0, min(26.0, 12.0 + block_h * 0.12))
    span_h = block_h + 2.0 * context_pad

    max_span_pt = page_h * 0.5
    span_h = min(span_h, max_span_pt)

    zoom = target_w / page_w if page_w > 0 else 0.6
    card_h = int(round(span_h * zoom))

    max_card_h = int(round(max_span_pt * zoom))
    limit_max_h = min(max_h, max_card_h) if max_h is not None else max_card_h
    return max(min_h, min(limit_max_h, card_h))


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
        target_w=360,
        target_h=None,
        page_w=600.0,
        page_h=792.0,
        autotrim=True,
        on_toggle_pin=None,
        on_render_done=None,
        on_row_clicked=None,
    ):
        super().__init__()
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)

        self.result = result
        self.query_terms = query_terms
        self.autotrim = bool(autotrim)
        self.on_toggle_pin = on_toggle_pin
        self.on_render_done = on_render_done
        self.on_row_clicked = on_row_clicked

        page = result["page"]
        x0, y0, x1, y1 = result["x0"], result["y0"], result["x1"], result["y1"]
        self._y0 = float(y0)
        self._y1 = float(y1)
        effective_page_w = float(page_w) * 0.78 if self.autotrim else float(page_w)
        self._page_w = effective_page_w
        self._page_h = float(page_h)
        self._fixed_h = int(target_h) if target_h is not None else None

        self.target_w = int(target_w)
        if target_h is None:
            self.card_h = compute_portal_card_height(
                y0,
                y1,
                target_w=self.target_w,
                page_w=self._page_w,
                page_h=page_h,
            )
        else:
            self.card_h = int(target_h)

        # Main Child: Uniform LinkPortalPreviewCard widget
        self.portal_card = LinkPortalPreviewCard()
        fixed_h = int(target_h) if target_h is not None else None

        def height_fn(w: int) -> int:
            if fixed_h is not None:
                return fixed_h
            return compute_portal_card_height(
                self._y0,
                self._y1,
                target_w=w,
                page_w=self._page_w,
                page_h=self._page_h,
            )
        self.portal_card.set_portal_size(
            self.target_w, self.card_h, flexible=True, height_fn=height_fn
        )
        self.portal_card.set_loading()
        self.portal_card.set_hexpand(True)
        self.portal_card.set_halign(Gtk.Align.FILL)
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
        match_count = int(result.get("match_count", 1))
        pill_text = f"Page {page} • {match_count} matches" if match_count > 1 else f"Page {page}"
        self.page_label = Gtk.Label(label=pill_text)
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

        executor.submit(
            self._render_worker,
            pdf_path,
            page,
            x0,
            y0,
            x1,
            y1,
            query_terms,
            self.target_w,
            self.card_h,
        )

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

    def _render_worker(self, pdf_path, page_no, x0, y0, x1, y1, query_terms, target_w, target_h):
        try:
            scale_factor = float(self.get_scale_factor() or 2.0)
            surface, match_count = render_strip_surface(
                pdf_path,
                page_no,
                x0,
                y0,
                x1,
                y1,
                query_terms,
                target_w=target_w,
                target_h=target_h,
                scale_factor=scale_factor,
                autotrim=self.autotrim,
            )
        except Exception as e:
            print(f"Error rendering portal strip surface: {e}")
            surface = None
            match_count = 0
        GLib.idle_add(self._apply_render, surface, match_count)

    def _apply_render(self, surface, match_count: int = 0):
        if surface is not None:
            self._cached_surface = surface
            self.portal_card.set_surface(surface)
            page = self.result["page"]
            effective_matches = match_count if match_count > 0 else int(self.result.get("match_count", 1))
            if effective_matches > 1:
                self.page_label.set_label(f"Page {page} • {effective_matches} matches")
            else:
                self.page_label.set_label(f"Page {page}")
            self.result["match_count"] = effective_matches
        if self.on_render_done:
            self.on_render_done()
        return False
