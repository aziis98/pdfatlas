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


def get_query_match_rects(page, query_terms, clip_y0, clip_y1):
    """
    Finds character-level bounding boxes for all non-overlapping occurrences
    of query_terms inside the text spans of the page.
    """
    match_rects = []
    if not query_terms:
        return match_rects

    try:
        raw_dict = page.get_text("rawdict")
    except Exception as e:
        print(f"[portal] Failed to get rawdict: {e}", flush=True)
        return match_rects

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
_thread_local = threading.local()


def _thread_doc(pdf_path):
    doc = getattr(_thread_local, "doc", None)
    if doc is None or getattr(_thread_local, "pdf_path", None) != pdf_path:
        doc = fitz.open(pdf_path)
        _thread_local.doc = doc
        _thread_local.pdf_path = pdf_path
    return doc


from .portal_preview import LinkPortalPreviewCard


def render_strip_surface(pdf_path, page_no, x0, y0, x1, y1, query_terms):
    """
    Renders one search result page-strip directly to a cairo.ImageSurface.
    Crops across the full page width (0 to page.rect.width) and a fixed-height
    vertical window (about 3-4 lines of text) centered on the block midpoint.
    """
    doc = _thread_doc(pdf_path)
    page = doc[page_no - 1]

    # 1. Full page width bounds
    clip_x0 = 0.0
    clip_x1 = page.rect.width

    # 2. Vertical bounds: 160pt height window centered on block midpoint to match card aspect ratio
    WINDOW_HEIGHT_PT = 160.0
    mid_y = (y0 + y1) / 2.0
    clip_y0 = max(0.0, mid_y - WINDOW_HEIGHT_PT / 2.0)
    clip_y1 = min(page.rect.height, mid_y + WINDOW_HEIGHT_PT / 2.0)

    clip = fitz.Rect(clip_x0, clip_y0, clip_x1, clip_y1)

    mat = fitz.Matrix(STRIP_ZOOM, STRIP_ZOOM)
    # Render with alpha so we get a clean buffer
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=True)

    # Convert the pixmap raw bytes into a NumPy array
    arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))

    # Swap channels: RGBA to Cairo's native-endian memory BGRA (ARGB32)
    bgra = arr[:, :, [2, 1, 0, 3]].copy()

    h, w, _ = bgra.shape
    surface = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, w, h, w * 4)

    # 3. Draw Highlights with Cairo
    ctx = cairo.Context(surface)
    ctx.set_source_rgba(*HIGHLIGHT_RGBA)

    if query_terms:
        # Get precise character-level matched ranges
        match_rects = get_query_match_rects(page, query_terms, clip_y0, clip_y1)
        for ux0, uy0, ux1, uy1 in match_rects:
            px0 = (ux0 - clip.x0) * STRIP_ZOOM
            py0 = (uy0 - clip.y0) * STRIP_ZOOM
            pw = (ux1 - ux0) * STRIP_ZOOM
            ph = (uy1 - uy0) * STRIP_ZOOM
            ctx.rectangle(px0, py0, pw, ph)
    ctx.fill()

    return surface


class ResultRow(Gtk.Box):
    """
    A single Search Result card: location header with pin button, and a
    full-width, uniform fixed-height page clip rendered via LinkPortalPreviewCard.
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
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.CENTER)

        self.result = result
        self.query_terms = query_terms
        self.on_toggle_pin = on_toggle_pin
        self.on_render_done = on_render_done
        self.on_row_clicked = on_row_clicked

        page = result["page"]
        x0, y0, x1, y1 = result["x0"], result["y0"], result["x1"], result["y1"]

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        label = Gtk.Label(
            label=f"Page {page}  ·  y {int(y0)}–{int(y1)} pt",
            xalign=0,
        )
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_hexpand(True)
        header_box.append(label)

        self.pin_button = Gtk.ToggleButton(label="📌 Pinned" if pinned else "📌 Pin")
        self.pin_button.add_css_class("flat")
        self.pin_button.set_active(pinned)
        self.pin_button.connect("toggled", self._on_pin_toggled)
        header_box.append(self.pin_button)

        self.append(header_box)

        # Uniform LinkPortalPreviewCard widget for uniform search portal preview UI
        self.portal_card = LinkPortalPreviewCard()
        self.portal_card.set_portal_size(450, 120)
        self.portal_card.set_loading()
        self.portal_card.set_hexpand(False)
        self.portal_card.set_halign(Gtk.Align.CENTER)
        self.append(self.portal_card)

        # Handle mouse clicks on the row
        click = Gtk.GestureClick.new()
        click.connect("released", self._on_clicked)
        self.add_controller(click)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        executor.submit(self._render_worker, pdf_path, page, x0, y0, x1, y1, query_terms)

    def _on_pin_toggled(self, btn):
        active = btn.get_active()
        btn.set_label("📌 Pinned" if active else "📌 Pin")
        if self.on_toggle_pin:
            self.on_toggle_pin(self.result, self.query_terms, active)

    def _on_clicked(self, gesture, n_press, x, y):
        if self.on_row_clicked:
            self.on_row_clicked(self.result, self.query_terms)

    def _render_worker(self, pdf_path, page_no, x0, y0, x1, y1, query_terms):
        try:
            surface = render_strip_surface(pdf_path, page_no, x0, y0, x1, y1, query_terms)
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
