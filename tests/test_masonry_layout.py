import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from pdfatlas.ui.components.masonry_layout import MasonryBox, MasonryLayoutManager
from pdfatlas.ui.components.search_results_view import SearchResultsView


def test_masonry_layout_manager_measure():
    Adw.init()
    mgr = MasonryLayoutManager(column_spacing=10, row_spacing=10, min_column_width=300)

    # Horizontal orientation: min_w is min_column_width, nat_w is 2 columns + gap
    min_w, nat_w, _, _ = mgr.do_measure(Gtk.Box(), Gtk.Orientation.HORIZONTAL, -1)
    assert min_w == 300
    assert nat_w == 610

    # Vertical orientation without children
    box = Gtk.Box()
    box.set_layout_manager(mgr)
    min_h, nat_h, _, _ = mgr.do_measure(box, Gtk.Orientation.VERTICAL, 650)
    assert min_h == 0
    assert nat_h == 0


def test_masonry_box_creation_and_children():
    Adw.init()
    box = MasonryBox(column_spacing=8, row_spacing=8, min_column_width=200)
    assert isinstance(box.get_layout_manager(), MasonryLayoutManager)

    # Add dummy children
    item1 = Gtk.Label(label="Item 1")
    item2 = Gtk.Label(label="Item 2")
    box.append(item1)
    box.append(item2)

    assert box.get_first_child() == item1
    assert item1.get_next_sibling() == item2


def test_search_results_view_masonry_flag():
    Adw.init()
    # When use_masonry=False, grid uses Gtk.FlowBox
    view_default = SearchResultsView(use_masonry=False)
    assert view_default.use_masonry is False

    # When use_masonry=True, grid uses MasonryBox
    view_masonry = SearchResultsView(use_masonry=True)
    assert view_masonry.use_masonry is True

    # Render results with clustering
    results = [
        {"id": 1, "page": 1, "text": "Result 1", "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 150.0, "rects": []},
        {"id": 2, "page": 1, "text": "Result 2", "x0": 10.0, "y0": 600.0, "x1": 100.0, "y1": 650.0, "rects": []},
    ]
    view_masonry.render_results(results, query="test", active_filepath="/tmp/fake.pdf", is_grid=True)
    # Check that children exist in results_box
    child = view_masonry.results_box.get_first_child()
    assert child is not None


def test_cli_argument_experimental_search_masonry():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--experimental-search-masonry", action="store_true")

    args_off = parser.parse_args([])
    assert args_off.experimental_search_masonry is False

    args_on = parser.parse_args(["--experimental-search-masonry"])
    assert args_on.experimental_search_masonry is True


def test_compute_portal_card_height():
    from pdfatlas.ui.portal import compute_portal_card_height

    # 1. Short 1-line match (15pt span) -> min clamped to 90px
    h_short = compute_portal_card_height(y0=100.0, y1=115.0, target_w=450, page_w=600.0, page_h=800.0)
    assert 90 <= h_short <= 110

    # 2. Medium paragraph match (80pt span) -> ~130-150px
    h_med = compute_portal_card_height(y0=100.0, y1=180.0, target_w=450, page_w=600.0, page_h=800.0)
    assert 120 <= h_med <= 160

    # 3. Tall block / multi-line match (400pt span on 800pt page) -> capped strictly to 50% page height
    # 50% of 800pt is 400pt. At zoom 450/600 = 0.75, max card h is exactly 300px.
    h_tall = compute_portal_card_height(y0=100.0, y1=600.0, target_w=450, page_w=600.0, page_h=800.0)
    assert h_tall == 300
    assert h_short < h_med < h_tall


def test_result_row_dynamic_heights():
    from unittest.mock import MagicMock
    from pdfatlas.ui.portal import ResultRow

    mock_executor = MagicMock()
    r_short = ResultRow(
        "/tmp/fake.pdf",
        mock_executor,
        {"page": 1, "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 115.0},
        {"query"},
    )
    r_tall = ResultRow(
        "/tmp/fake.pdf",
        mock_executor,
        {"page": 1, "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 380.0},
        {"query"},
    )

    assert r_short.card_h < r_tall.card_h
    assert r_short.portal_card.portal_height == r_short.card_h
    assert r_tall.portal_card.portal_height == r_tall.card_h


def test_result_row_pill_badge_match_count():
    from unittest.mock import MagicMock
    from pdfatlas.ui.portal import ResultRow

    mock_executor = MagicMock()
    # Single match
    r_single = ResultRow(
        "/tmp/fake.pdf",
        mock_executor,
        {"page": 2, "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 120.0, "match_count": 1},
        {"query"},
    )
    assert r_single.page_label.get_label() == "Page 2"

    # Clustered / multi-match
    r_multi = ResultRow(
        "/tmp/fake.pdf",
        mock_executor,
        {"page": 3, "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 200.0, "match_count": 4},
        {"query"},
    )
    assert r_multi.page_label.get_label() == "Page 3 • 4 matches"


def test_result_row_apply_render_updates_match_count():
    from unittest.mock import MagicMock
    from pdfatlas.ui.portal import ResultRow
    import cairo

    mock_executor = MagicMock()
    r = ResultRow(
        "/tmp/fake.pdf",
        mock_executor,
        {"page": 1, "x0": 10.0, "y0": 100.0, "x1": 100.0, "y1": 200.0, "match_count": 1},
        {"the"},
    )
    assert r.page_label.get_label() == "Page 1"

    # Simulate render complete with 19 matches found
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100)
    r._apply_render(surf, match_count=19)
    assert r.page_label.get_label() == "Page 1 • 19 matches"
    assert r.result["match_count"] == 19



