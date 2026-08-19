from __future__ import annotations

from typing import Protocol, runtime_checkable
import fitz


@runtime_checkable
class PointCropRect(Protocol):
    x0: float
    y0: float


@runtime_checkable
class CropRect(Protocol):
    x0: float
    y0: float
    x1: float
    y1: float


def layout_scale(zoom: float, dpi_scale_factor: float) -> float:
    return zoom * dpi_scale_factor


#: Page textures never render beyond 250% zoom by default; the GPU upscales beyond it.
MAX_TEXTURE_ZOOM = 2.5


def texture_zoom(
    zoom: float, dpi_scale_factor: float, max_zoom: float | None = MAX_TEXTURE_ZOOM
) -> float:
    """Zoom value passed to the render worker so texture resolution caps at
    ``max_zoom`` (default 250%). Beyond it the GL canvas upscales the capped
    texture to the full-size quad instead of rendering a larger one.
    ``max_zoom=None`` disables the cap entirely (texture renders at true zoom)."""
    if max_zoom is None:
        return layout_scale(zoom, dpi_scale_factor)
    return layout_scale(min(zoom, max_zoom), dpi_scale_factor)


def content_width(layout: list[tuple], viewport_w: float) -> float:
    """
    Width of the scrollable content box in device pixels.

    The GTK scroll container stretches to the viewport width when every page
    fits, otherwise it takes the widest page's width. Pages are centered within
    this box, so their left edge is `(content_width - dw) / 2`.
    """
    max_dw = max((dw for _, dw, _, _ in layout), default=0.0)
    return max(viewport_w, max_dw)


def page_rect_at(layout: list[tuple], page_index: int, viewport_w: float) -> tuple[float, float, float, float]:
    y_offset, dw, dh, _crop_rect = layout[page_index]
    page_x0 = (content_width(layout, viewport_w) - dw) / 2.0
    return (page_x0, y_offset, dw, dh)


def screen_to_pdf(
    layout: list[tuple],
    page_index: int,
    scale: float,
    x: float,
    y: float,
    scroll_x: float,
    scroll_y: float,
    viewport_w: float,
) -> tuple[float, float] | None:
    if page_index < 0 or page_index >= len(layout):
        return None
    y_offset, dw, _dh, crop_rect = layout[page_index]
    page_x0 = (content_width(layout, viewport_w) - dw) / 2.0

    rel_x = (x + scroll_x) - page_x0
    rel_y = (y + scroll_y) - y_offset

    crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
    crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

    pt_x = (rel_x / scale) + crop_off_x
    pt_y = (rel_y / scale) + crop_off_y
    return (pt_x, pt_y)


def pdf_rect_to_screen(
    layout: list[tuple],
    page_index: int,
    scale: float,
    viewport_w: float,
    scroll_x: float,
    scroll_y: float,
    pdf_x0: float,
    pdf_y0: float,
    pdf_x1: float,
    pdf_y1: float,
    crop_rect: CropRect | fitz.Rect | None,
) -> tuple[float, float, float, float] | None:
    if page_index < 0 or page_index >= len(layout):
        return None
    y_offset, dw, _dh, _cr = layout[page_index]
    page_x0 = (content_width(layout, viewport_w) - dw) / 2.0

    crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
    crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

    sx = page_x0 + (pdf_x0 - crop_off_x) * scale - scroll_x
    sy = y_offset + (pdf_y0 - crop_off_y) * scale - scroll_y
    sw = (pdf_x1 - pdf_x0) * scale
    sh = (pdf_y1 - pdf_y0) * scale
    return (sx, sy, sw, sh)


def pdf_point_to_page_margin(
    scale: float,
    pdf_x: float,
    pdf_y: float,
    crop_rect: PointCropRect | fitz.Rect | None,
    page_w: float,
    page_h: float,
    icon_size: float = 24.0,
) -> tuple[float, float]:
    """Margin (mx, my) from the top-left of a page container that places an
    icon centered on a PDF point, clamped inside the page box."""
    crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
    crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
    mx = (pdf_x - crop_off_x) * scale - icon_size / 2.0
    my = (pdf_y - crop_off_y) * scale - icon_size / 2.0
    mx = max(0.0, min(mx, max(0.0, page_w - icon_size)))
    my = max(0.0, min(my, max(0.0, page_h - icon_size)))
    return (mx, my)


def page_at_point(
    layout: list[tuple],
    page_gap: float,
    x: float,
    y: float,
    scroll_y: float,
) -> int | None:
    if not layout:
        return None
    half_gap = page_gap / 2.0
    canvas_y = y + scroll_y
    for i, (y_offset, _dw, dh, _crop_rect) in enumerate(layout):
        if y_offset - half_gap <= canvas_y <= y_offset + dh + half_gap:
            return i
    return None


def link_screen_rect(
    layout: list[tuple],
    page_index: int,
    scale: float,
    viewport_w: float,
    scroll_y: float,
    pdf_rect: fitz.Rect | dict | None,
    scroll_x: float = 0.0,
) -> tuple[float, float, float, float] | None:
    if not layout or page_index < 0 or page_index >= len(layout):
        return None
    from_rect = pdf_rect.get("from") if isinstance(pdf_rect, dict) else None
    if not from_rect:
        return None

    y_offset, dw, _dh, crop_rect = layout[page_index]
    page_x0 = (content_width(layout, viewport_w) - dw) / 2.0
    page_y0 = y_offset - scroll_y

    crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
    crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0

    sx = page_x0 + (from_rect.x0 - crop_off_x) * scale - scroll_x
    sy = page_y0 + (from_rect.y0 - crop_off_y) * scale
    sw = (from_rect.x1 - from_rect.x0) * scale
    sh = (from_rect.y1 - from_rect.y0) * scale
    return (sx, sy, sw, sh)


def anchor_before(
    layout: list[tuple],
    scroll_value: float,
    page_size: float,
    zoom: float,
    dpi_scale_factor: float,
) -> tuple[int, float] | None:
    if not layout:
        return None
    point_y = scroll_value + page_size * 0.5
    scale = layout_scale(zoom, dpi_scale_factor)
    for i, (y0, _dw, dh, crop) in enumerate(layout):
        if y0 + dh >= point_y - 1e-6:
            crop_off = crop.y0 if crop is not None else 0.0
            pdf_y = crop_off + (point_y - y0) / scale
            return (i, pdf_y)
    _y0, _dw, _dh, crop = layout[-1]
    return (len(layout) - 1, crop.y0 if crop is not None else 0.0)


def anchor_after(
    layout: list[tuple],
    anchor: tuple[int, float],
    zoom: float,
    dpi_scale_factor: float,
    scroll_upper: float,
    scroll_page_size: float,
) -> float:
    page_index, pdf_y = anchor
    if not layout or page_index >= len(layout):
        return 0.0
    y0, _dw, _dh, crop = layout[page_index]
    crop_off = crop.y0 if crop is not None else 0.0
    scale = layout_scale(zoom, dpi_scale_factor)
    target_center = y0 + (pdf_y - crop_off) * scale
    target_val = target_center - scroll_page_size * 0.5
    lower = 0.0
    max_y = max(lower, scroll_upper - scroll_page_size)
    return max(lower, min(target_val, max_y))


#: Vertical tolerance (PDF points) for grouping highlight rects onto the same line.
HL_LINE_TOLERANCE = 4.0
#: Horizontal gap (PDF points) below which adjacent rects on the same line merge.
HL_GAP_TOLERANCE = 8.0


def merge_highlight_runs(rects: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Merge contiguous per-character highlight rects into line runs.

    Highlights are stored as one bounding box per character, so drawing each
    with its own rounded rect produces a "pill" per glyph whose corners clip
    into the neighbouring glyphs. Grouping rects that share a text line and are
    horizontally close into a single run keeps rounded corners only at the
    outer ends of the highlighted line.
    """
    if not rects:
        return []
    runs: list[tuple[float, float, float, float]] = []
    cur_x0, cur_y0, cur_x1, cur_y1 = rects[0]
    for rx0, ry0, rx1, ry1 in rects[1:]:
        cur_center_y = (cur_y0 + cur_y1) / 2.0
        center_y = (ry0 + ry1) / 2.0
        same_line = abs(center_y - cur_center_y) < HL_LINE_TOLERANCE
        continuous = rx0 <= cur_x1 + HL_GAP_TOLERANCE
        if same_line and continuous:
            cur_x0 = min(cur_x0, rx0)
            cur_y0 = min(cur_y0, ry0)
            cur_x1 = max(cur_x1, rx1)
            cur_y1 = max(cur_y1, ry1)
        else:
            runs.append((cur_x0, cur_y0, cur_x1, cur_y1))
            cur_x0, cur_y0, cur_x1, cur_y1 = rx0, ry0, rx1, ry1
    runs.append((cur_x0, cur_y0, cur_x1, cur_y1))
    return runs
