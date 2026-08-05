import os
import sqlite3
import tempfile

from pdfatlas.core.index import (
    delete_highlight_from_db,
    ensure_highlights_table,
    load_highlights_from_db,
    save_highlight_to_db,
)
from pdfatlas.ui.cairo_utils import hex_to_rgba


def test_hex_to_rgba():
    r, g, b, a = hex_to_rgba("#FFEE55")
    assert round(r, 2) == 1.0
    assert round(g, 2) == 0.93
    assert round(b, 2) == 0.33
    assert a == 1.0

    assert hex_to_rgba("invalid") == (1.0, 0.933, 0.333, 1.0)


def test_highlight_db_operations():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conn = sqlite3.connect(db_path)
        ensure_highlights_table(conn)

        # Test initial load (empty)
        assert load_highlights_from_db(conn) == []

        # Save highlight
        rects = [(10.0, 20.0, 100.0, 40.0)]
        hid = save_highlight_to_db(conn, page=0, char_start=0, char_end=5, color="#FFEE55", rects=rects, text="Hello world")
        assert hid > 0

        # Load highlights
        highlights = load_highlights_from_db(conn)
        assert len(highlights) == 1
        assert highlights[0]["id"] == hid
        assert highlights[0]["page"] == 0
        assert highlights[0]["color"] == "#FFEE55"
        assert highlights[0]["text"] == "Hello world"
        assert highlights[0]["rects"] == rects

        # Delete highlight
        delete_highlight_from_db(conn, hid)
        assert load_highlights_from_db(conn) == []

        conn.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_double_click_word_selection():
    from pdfatlas.core.document import DocumentModel
    from pdfatlas.core.text_selection import TextSelection

    doc = DocumentModel("./assets/sample-files/attention_is_all_you_need.pdf")
    sel = TextSelection(doc)

    sel.select_word_at(0, 2)
    assert sel.has_selection()
    assert sel.anchor_char_idx == 0
    assert sel.focus_char_idx == 7
    assert sel.get_selected_text(0) == "Provided"


def test_annotations_popover_visibility():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    app = Adw.Application(application_id="com.example.testannotations")
    app.register(None)
    from pdfatlas.ui.window import MainWindow

    win = MainWindow(app)
    assert not win.annotations_btn.get_visible()

    win.highlights = [{"id": 1, "page": 0, "color": "#FFEE55", "text": "Test Highlight", "rects": []}]
    win._update_annotations_button()
    assert win.annotations_btn.get_visible()
    assert "1" in win.annotations_count_label.get_text()

    win.highlights = []
    win._update_annotations_button()
    assert not win.annotations_btn.get_visible()


def test_jump_to_annotation():
    from unittest.mock import MagicMock
    from pdfatlas.controllers.navigation import NavigationController

    win = MagicMock()
    win.doc_model = MagicMock()
    win.canvas.page_layout = [(0.0, 500.0, 700.0, None), (720.0, 500.0, 700.0, None)]
    win.canvas.page_gap = 20.0
    win.vadjustment.get_page_size.return_value = 400.0
    win.vadjustment.get_upper.return_value = 2000.0

    pdf_page = MagicMock()
    pdf_page.rect.height = 700.0
    win.doc_model.get_page.return_value = pdf_page

    nav = NavigationController(win)

    hl = {"page": 1, "rects": [(100.0, 300.0, 200.0, 350.0)]}
    nav.jump_to_annotation(hl)

    # Page 1 offset = 720.0, center_pts = 325.0, scale = 1.0 -> y_pixels = 1045.0
    # target_y = 1045.0 - (400.0 / 2.0) = 845.0
    win.vadjustment.set_value.assert_called_with(845.0)
