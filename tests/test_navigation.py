from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from pdfatlas.controllers.navigation import NavigationController


def _create_mock_window(
    page_dimensions: list[tuple[float, float]],
    page_gap: float = 12.0,
    viewport_h: float = 700.0,
    initial_scroll_y: float = 0.0,
):
    """
    Creates a mock MainWindow/PdfDocumentView with realistic canvas page_layout geometry.
    page_dimensions: list of (width, height)
    """
    win = MagicMock()
    canvas = MagicMock()
    canvas.page_gap = page_gap

    page_layout = []
    current_y = 0.0
    for w, h in page_dimensions:
        page_layout.append((current_y, w, h, None))
        current_y += h + page_gap

    canvas.page_layout = page_layout
    win.canvas = canvas

    total_upper = current_y
    scroll_state = {"val": initial_scroll_y}

    vadj = MagicMock()
    vadj.get_value.side_effect = lambda: scroll_state["val"]
    vadj.set_value.side_effect = lambda v: scroll_state.update({"val": float(v)})
    vadj.get_page_size.return_value = viewport_h
    vadj.get_upper.return_value = total_upper
    vadj.get_lower.return_value = 0.0

    win.vadjustment = vadj
    return win, scroll_state


def test_scroll_page_uniform_forward_and_backward():
    # 3 pages, each 800px tall, gap 12px
    win, scroll_state = _create_mock_window([(600.0, 800.0), (600.0, 800.0), (600.0, 800.0)], page_gap=12.0)
    nav = NavigationController(win)

    # 1. Forward from top (Page 0 -> Page 1)
    # Delta = dh_0 + gap = 800 + 12 = 812.0
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(812.0)

    # 2. Forward from Page 1 -> Page 2
    # Delta = dh_1 + gap = 800 + 12 = 812.0
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(1624.0)

    # 3. Backward from Page 2 -> Page 1
    # Preceding page is Page 1, delta = dh_1 + gap = 812.0
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(812.0)

    # 4. Backward from Page 1 -> Page 0
    # Preceding page is Page 0, delta = dh_0 + gap = 812.0
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(0.0)

    # 5. Backward from Page 0 clamps at 0.0
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(0.0)


def test_scroll_page_heterogeneous_heights():
    # Page 0: 1000px, Page 1: 500px, Page 2: 800px. Gap 10px
    # y0 = 0.0, y1 = 1010.0, y2 = 1520.0
    win, scroll_state = _create_mock_window(
        [(600.0, 1000.0), (600.0, 500.0), (600.0, 800.0)],
        page_gap=10.0,
        viewport_h=600.0,
    )
    nav = NavigationController(win)

    # Forward from Page 0: uses Page 0 height (1000) + 10 -> 1010.0 (Page 1 top)
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(1010.0)

    # Forward from Page 1: uses Page 1 height (500) + 10 -> 1520.0 (Page 2 top)
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(1520.0)

    # Backward from Page 2: uses preceding Page 1 height (500) + 10 -> 1010.0 (Page 1 top)
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(1010.0)

    # Backward from Page 1: uses preceding Page 0 height (1000) + 10 -> 0.0 (Page 0 top)
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(0.0)


def test_scroll_page_gapless_mode():
    # Gapless mode: page_gap = 0.0
    win, scroll_state = _create_mock_window(
        [(600.0, 750.0), (600.0, 850.0)],
        page_gap=0.0,
        viewport_h=600.0,
    )
    nav = NavigationController(win)

    # Forward: 0.0 + 750.0 = 750.0
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(750.0)

    # Backward: 750.0 - 750.0 = 0.0
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(0.0)


def test_scroll_page_mid_page_reading_offset():
    # User is reading halfway down page 1 (y1 = 812.0, scroll = 812.0 + 150.0 = 962.0)
    # 4 pages so page 2 has headroom before document end clamping
    win, scroll_state = _create_mock_window(
        [(600.0, 800.0), (600.0, 800.0), (600.0, 800.0), (600.0, 800.0)],
        page_gap=12.0,
        initial_scroll_y=962.0,
    )
    nav = NavigationController(win)

    # Forward: jumps by 1 page height + gap (812.0) to page 2 at the same 150px offset
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(962.0 + 812.0)

    # Backward: jumps back by 812.0 to restore exact offset on page 1
    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(962.0)


def test_scroll_page_clamped_at_document_end():
    # 2 pages: each 500px, gap 10px. Upper = 1020px. Viewport = 600px.
    # Max scroll = 1020 - 600 = 420px.
    win, scroll_state = _create_mock_window(
        [(600.0, 500.0), (600.0, 500.0)],
        page_gap=10.0,
        viewport_h=600.0,
        initial_scroll_y=300.0,
    )
    nav = NavigationController(win)

    nav.scroll_page(forward=True)
    # Target 300 + 510 = 810, clamped to max_scroll 420.0
    assert scroll_state["val"] == pytest.approx(420.0)


def test_scroll_step_clamped():
    win, scroll_state = _create_mock_window(
        [(600.0, 800.0)],
        page_gap=10.0,
        viewport_h=600.0,
        initial_scroll_y=40.0,
    )
    nav = NavigationController(win)

    # Step down: 40 + 80 = 120
    nav.scroll_step(forward=True)
    assert scroll_state["val"] == pytest.approx(120.0)

    # Step up: 120 - 80 = 40
    nav.scroll_step(forward=False)
    assert scroll_state["val"] == pytest.approx(40.0)

    # Step up again: 40 - 80 = -40, clamped to 0.0
    nav.scroll_step(forward=False)
    assert scroll_state["val"] == pytest.approx(0.0)


def test_scroll_page_fallback_without_layout():
    win = MagicMock()
    win.canvas = None
    scroll_state = {"val": 100.0}

    vadj = MagicMock()
    vadj.get_value.side_effect = lambda: scroll_state["val"]
    vadj.set_value.side_effect = lambda v: scroll_state.update({"val": float(v)})
    vadj.get_page_size.return_value = 500.0
    vadj.get_upper.return_value = 2000.0
    win.vadjustment = vadj

    nav = NavigationController(win)
    nav.scroll_page(forward=True)
    assert scroll_state["val"] == pytest.approx(600.0)

    nav.scroll_page(forward=False)
    assert scroll_state["val"] == pytest.approx(100.0)
