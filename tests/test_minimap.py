from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from pdfatlas.ui.minimap import MinimapWindow, compute_grid


def test_compute_grid():
    n_cols, n_rows, scale, thumb_w, thumb_h, cell_w, cell_h = compute_grid(
        page_count=10,
        alloc_w=1000.0,
        alloc_h=800.0,
        first_page_w=100.0,
        first_page_h=150.0,
    )
    assert n_cols * n_rows >= 10
    assert scale > 0
    assert thumb_w > 0
    assert thumb_h > 0
    assert cell_w > 0
    assert cell_h > 0
    # Pages fit to height with no vertical gap:
    assert abs(thumb_h - cell_h) < 1e-6
    # Extra allocated space is placed horizontally:
    assert cell_w >= thumb_w


def test_minimap_window_sizing_from_parent():
    Adw.init()
    parent = Gtk.Window()
    parent.set_default_size(1200, 800)

    doc_mock = MagicMock()
    doc_mock.page_count = 5
    doc_mock.page_rect.return_value = MagicMock(width=600.0, height=800.0)

    win = MinimapWindow(
        parent_window=parent,
        doc_model=doc_mock,
        cache=MagicMock(),
        render_worker=MagicMock(),
        crop_analyzer=None,
        settings=None,
        main_vadjustment=None,
        main_zoom=1.0,
        on_page_selected=lambda idx: None,
    )

    w, h = win.get_default_size()
    assert w == 900  # 75% of 1200
    assert h == 600  # 75% of 800
    assert win.get_resizable() is True


def test_minimap_window_sizing_fallback():
    Adw.init()
    doc_mock = MagicMock()
    doc_mock.page_count = 5
    doc_mock.page_rect.return_value = MagicMock(width=600.0, height=800.0)

    win = MinimapWindow(
        parent_window=None,
        doc_model=doc_mock,
        cache=MagicMock(),
        render_worker=MagicMock(),
        crop_analyzer=None,
        settings=None,
        main_vadjustment=None,
        main_zoom=1.0,
        on_page_selected=lambda idx: None,
    )

    w, h = win.get_default_size()
    assert w == 700
    assert h == 520
    assert win.get_resizable() is True

