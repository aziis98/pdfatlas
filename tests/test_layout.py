from pdfatlas.core.layout import (
    anchor_after,
    anchor_before,
    layout_scale,
    link_screen_rect,
    page_at_point,
    page_rect_at,
    pdf_rect_to_screen,
    screen_to_pdf,
)


class MockCrop:
    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


class MockPdfRect:
    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


def test_layout_scale():
    assert layout_scale(1.5, 2.0) == 3.0


def test_page_rect_at():
    layout = [(0.0, 500.0, 700.0, None), (720.0, 500.0, 700.0, None)]
    x0, y0, w, h = page_rect_at(layout, 0, 1000.0)
    assert x0 == 250.0
    assert y0 == 0.0
    assert w == 500.0
    assert h == 700.0


def test_screen_to_pdf():
    layout = [(10.0, 400.0, 600.0, MockCrop(50.0, 50.0, 450.0, 650.0))]
    # viewport_w=1000, page_x0 = (1000-400)/2 = 300.
    # screen x=300, y=10 (with scroll=0) -> rel_x=0, rel_y=0 -> pt_x=50.0, pt_y=50.0
    pt = screen_to_pdf(layout, 0, scale=1.0, x=300.0, y=10.0, scroll_x=0.0, scroll_y=0.0, viewport_w=1000.0)
    assert pt == (50.0, 50.0)

    assert screen_to_pdf(layout, -1, 1.0, 0, 0, 0, 0, 1000) is None
    assert screen_to_pdf(layout, 5, 1.0, 0, 0, 0, 0, 1000) is None


def test_pdf_rect_to_screen():
    crop = MockCrop(50.0, 50.0, 450.0, 650.0)
    layout = [(10.0, 400.0, 600.0, crop)]
    # pdf_x0=50, pdf_y0=50 -> screen (300, 10, 100, 100)
    rect = pdf_rect_to_screen(layout, 0, scale=1.0, viewport_w=1000.0, scroll_x=0.0, scroll_y=0.0,
                               pdf_x0=50.0, pdf_y0=50.0, pdf_x1=150.0, pdf_y1=150.0, crop_rect=crop)
    assert rect == (300.0, 10.0, 100.0, 100.0)
    assert pdf_rect_to_screen(layout, -1, 1.0, 1000.0, 0, 0, 0, 0, 10, 10, crop) is None


def test_page_at_point():
    layout = [
        (0.0, 400.0, 500.0, None),
        (520.0, 400.0, 500.0, None),
    ]
    assert page_at_point([], 20.0, 0.0, 100.0, 0.0) is None
    assert page_at_point(layout, 20.0, 100.0, 250.0, 0.0) == 0
    assert page_at_point(layout, 20.0, 100.0, 600.0, 0.0) == 1
    assert page_at_point(layout, 20.0, 100.0, 1500.0, 0.0) is None


def test_link_screen_rect():
    crop = MockCrop(10.0, 10.0, 100.0, 100.0)
    layout = [(0.0, 200.0, 200.0, crop)]
    pdf_rect = {"from": MockPdfRect(20.0, 20.0, 40.0, 40.0)}
    rect = link_screen_rect(layout, 0, scale=1.0, viewport_w=400.0, scroll_y=0.0, pdf_rect=pdf_rect)
    assert rect == (110.0, 10.0, 20.0, 20.0)
    assert link_screen_rect(layout, -1, 1.0, 400.0, 0.0, pdf_rect) is None
    assert link_screen_rect(layout, 0, 1.0, 400.0, 0.0, {}) is None


def test_anchors():
    layout = [
        (0.0, 400.0, 500.0, MockCrop(0.0, 0.0, 400.0, 500.0)),
        (520.0, 400.0, 500.0, MockCrop(0.0, 0.0, 400.0, 500.0)),
    ]
    assert anchor_before([], 0.0, 1000.0, 1.0, 1.0) is None
    anchor = anchor_before(layout, scroll_value=0.0, page_size=400.0, zoom=1.0, dpi_scale_factor=1.0)
    assert anchor == (0, 200.0)
    assert anchor is not None

    val = anchor_after(layout, anchor, zoom=1.0, dpi_scale_factor=1.0, scroll_upper=2000.0, scroll_page_size=400.0)
    assert val == 0.0
