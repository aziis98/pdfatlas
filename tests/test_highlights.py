import os
import sqlite3
import tempfile

from pdfatlas.core.index import (
    delete_highlight_from_db,
    ensure_highlights_table,
    load_highlights_from_db,
    save_highlight_to_db,
)
from pdfatlas.ui.cairo_utils import hex_to_rgba, hsl_to_hex


def test_hex_to_rgba():
    r, g, b, a = hex_to_rgba("#FFEE55")
    assert round(r, 2) == 1.0
    assert round(g, 2) == 0.93
    assert round(b, 2) == 0.33
    assert a == 1.0

    assert hex_to_rgba("invalid") == (1.0, 0.933, 0.333, 1.0)


def test_hsl_to_hex():
    assert hsl_to_hex(0, 100, 50) == "#FF0000"
    assert hsl_to_hex(120, 100, 50) == "#00FF00"
    assert hsl_to_hex(240, 100, 50) == "#0000FF"
    assert hsl_to_hex(50, 95, 68) == "#FBE160"
    assert hsl_to_hex(360, 100, 50) == "#FF0000"
    assert hsl_to_hex(-120, 100, 50) == "#0000FF"


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

    doc = DocumentModel("./sandbox.local/sample-files/attention_is_all_you_need.pdf")

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


def test_highlight_at_point():
    from pdfatlas.ui.canvas import highlight_at_point

    hl = {"page": 1, "rects": [(100.0, 200.0, 300.0, 250.0)]}
    highlights = [{"page": 0, "rects": [(0.0, 0.0, 10.0, 10.0)]}, hl]

    assert highlight_at_point(highlights, 1, (200.0, 225.0)) is hl
    assert highlight_at_point(highlights, 1, (302.0, 225.0)) is hl
    assert highlight_at_point(highlights, 1, (97.0, 225.0)) is None
    assert highlight_at_point(highlights, 0, (5.0, 5.0)) is highlights[0]
    assert highlight_at_point(highlights, 2, (200.0, 225.0)) is None
    assert highlight_at_point([], 1, (200.0, 225.0)) is None
    assert highlight_at_point(highlights, 1, (200.0, 225.0), tolerance=0.0) is hl


def test_annotations_delete_button():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    app = Adw.Application(application_id="com.example.testannotationsdel")
    app.register(None)
    from pdfatlas.ui.window import MainWindow

    win = MainWindow(app)
    win.highlights = [
        {"id": 1, "page": 0, "color": "#FFEE55", "text": "One", "rects": []},
        {"id": 2, "page": 1, "color": "#FFEE55", "text": "Two", "rects": []},
    ]
    win._update_annotations_button()
    assert win.annotations_btn.get_visible()

    def walk(widget, acc):
        acc.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child, acc)
            child = child.get_next_sibling()
        return acc

    delete_buttons = [w for w in walk(win.annotations_list, [])
                      if isinstance(w, Gtk.Button) and w.get_icon_name() == "user-trash-symbolic"]
    assert len(delete_buttons) == 2

    delete_buttons[0].emit("clicked")
    assert len(win.highlights) == 1
    assert win.highlights[0]["id"] == 2


def test_remove_matching_highlights():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    app = Adw.Application(application_id="com.example.testremovematching")
    app.register(None)
    from pdfatlas.ui.window import MainWindow

    win = MainWindow(app)
    win.highlights = [
        {"id": 1, "page": 0, "char_start": 0, "char_end": 5, "color": "#FFEE55", "text": "A", "rects": []},
        {"id": 2, "page": 0, "char_start": 10, "char_end": 15, "color": "#FFEE55", "text": "B", "rects": []},
        {"id": 3, "page": 1, "char_start": 0, "char_end": 5, "color": "#FFEE55", "text": "C", "rects": []},
    ]

    from unittest.mock import MagicMock
    from pdfatlas.core.text_selection import TextSelection
    sel = TextSelection(MagicMock())
    win.canvas.text_selection = sel
    sel.anchor_page = 0
    sel.anchor_char_idx = 0
    sel.focus_page = 0
    sel.focus_char_idx = 5

    assert win._selection_matching_highlights() == [win.highlights[0]]
    win._remove_matching_highlights()
    assert [h["id"] for h in win.highlights] == [2, 3]
    assert not sel.has_selection()


def test_crop_bboxes_db_operations(tmp_path):
    import fitz
    from pdfatlas.core.index import (
        ensure_crop_table,
        save_crop_bboxes_to_db,
        load_crop_bboxes_from_db,
    )

    db_path = tmp_path / "crop_test.db"
    conn = sqlite3.connect(str(db_path))
    ensure_crop_table(conn)

    bboxes = [
        fitz.Rect(10.0, 20.0, 500.0, 700.0),
        None,
        fitz.Rect(15.5, 25.5, 490.0, 680.0),
    ]

    save_crop_bboxes_to_db(conn, bboxes)

    loaded = load_crop_bboxes_from_db(conn, page_count=3)
    assert loaded is not None
    assert len(loaded) == 3
    assert loaded[0] == fitz.Rect(10.0, 20.0, 500.0, 700.0)
    assert loaded[1] is None
    assert loaded[2] == fitz.Rect(15.5, 25.5, 490.0, 680.0)

    # If page_count doesn't match, returns None
    assert load_crop_bboxes_from_db(conn, page_count=5) is None
    conn.close()


def test_arxiv_diff_db_operations(tmp_path):
    from pdfatlas.core.index import (
        ensure_arxiv_diff_table,
        save_arxiv_diff_to_db,
        load_arxiv_diff_from_db,
    )

    db_path = tmp_path / "arxiv_diff_test.db"
    conn = sqlite3.connect(str(db_path))
    ensure_arxiv_diff_table(conn)

    sample_data = {
        "pdf_text": "sample text",
        "tex_text": "sample \\textbf{text}",
        "pdf_words": ["sample", "text"],
        "tex_words": ["sample", "\\textbf{text}"],
        "word_metadata": [[0, 0, 6], [0, 7, 11]],
        "diff_opcodes": [["equal", 0, 1, 0, 1]],
        "pdf_to_tex_map": {"0": 0, "1": 1},
        "tex_to_pdf_map": {"0": 0, "1": 1},
        "mapped_pdf_indices": [0, 1],
        "moved_blocks": [],
    }

    save_arxiv_diff_to_db(conn, sample_data)
    loaded = load_arxiv_diff_from_db(conn)
    assert loaded == sample_data
    conn.close()

