import os
import sqlite3
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from pdfatlas.core.index import (
    delete_note_from_db,
    ensure_notes_table,
    load_notes_from_db,
    save_note_to_db,
    update_note_to_db,
)
from pdfatlas.core.layout import pdf_point_to_page_margin


class _FakeCropRect:
    """Minimal stand-in for fitz.Rect exposing x0/y0."""

    def __init__(self, x0, y0):
        self.x0 = x0
        self.y0 = y0


def test_note_db_operations():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        ensure_notes_table(conn)
        assert load_notes_from_db(conn) == []

        id1 = save_note_to_db(conn, 0, 10.5, 20.25, "# Hello\n\nSome markdown")
        id2 = save_note_to_db(conn, 2, 30.0, 40.0, "second note")
        assert id1 > 0
        assert id2 > 0

        notes = load_notes_from_db(conn)
        assert [n["id"] for n in notes] == [id1, id2]  # ordered by (page, id)
        assert notes[0]["page"] == 0
        assert notes[0]["x"] == 10.5
        assert isinstance(notes[0]["x"], float)
        assert notes[0]["y"] == 20.25
        assert notes[0]["markdown"] == "# Hello\n\nSome markdown"
        assert notes[1]["page"] == 2

        update_note_to_db(conn, id1, "updated markdown")
        assert load_notes_from_db(conn)[0]["markdown"] == "updated markdown"

        delete_note_from_db(conn, id1)
        survivors = load_notes_from_db(conn)
        assert [n["id"] for n in survivors] == [id2]
        assert survivors[0]["markdown"] == "second note"

        conn.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_pdf_point_to_page_margin():
    # No crop, scale 2.0, 400x600 page, 24px icon: centered on the point.
    assert pdf_point_to_page_margin(2.0, 100.0, 100.0, None, 400.0, 600.0) == (188.0, 188.0)

    # Clamped inside the page box.
    assert pdf_point_to_page_margin(2.0, 5.0, 5.0, None, 400.0, 600.0) == (0.0, 0.0)
    assert pdf_point_to_page_margin(2.0, 395.0, 595.0, None, 400.0, 600.0) == (376.0, 576.0)

    # Crop offset shifts the anchor point: (100-10)*2-12 = 168, (100-20)*2-12 = 148
    crop = _FakeCropRect(10.0, 20.0)
    assert pdf_point_to_page_margin(2.0, 100.0, 100.0, crop, 400.0, 600.0) == (168.0, 148.0)


def test_jump_to_note():
    from unittest.mock import MagicMock

    from pdfatlas.controllers.navigation import NavigationController

    win = MagicMock()
    win.doc_model = MagicMock()
    win.canvas.page_layout = [(0.0, 500.0, 700.0, None), (720.0, 500.0, 700.0, None)]
    win.vadjustment.get_page_size.return_value = 400.0
    win.vadjustment.get_upper.return_value = 2000.0
    win.zoom = 1.0
    win.canvas.dpi_scale_factor = 1.0

    nav = NavigationController(win)

    note = {"page": 1, "x": 100.0, "y": 300.0}
    nav.jump_to_note(note)

    # Page 1 offset = 720.0, y*scale = 300.0, centered -> 720.0 + 300.0 - 200.0
    win.vadjustment.set_value.assert_called_with(820.0)


def test_annotations_popover_includes_notes():
    app = Adw.Application(application_id="com.example.testnotes")
    app.register(None)
    from pdfatlas.ui.window import MainWindow

    win = MainWindow(app)
    assert not win.annotations_btn.get_visible()

    win.notes = [{"id": 1, "page": 0, "x": 1.0, "y": 1.0, "markdown": "hello"}]
    win._update_annotations_button()
    assert win.annotations_btn.get_visible()
    assert "1" in win.annotations_count_label.get_text()
    assert _contains_text(win.annotations_list, "hello")

    win.notes = []
    win._update_annotations_button()
    assert not win.annotations_btn.get_visible()


def _contains_text(widget, text: str) -> bool:
    """Recursively check whether a widget subtree contains a label with `text`."""
    if isinstance(widget, Gtk.Label) and widget.get_text() == text:
        return True
    child = widget.get_first_child()
    while child is not None:
        if _contains_text(child, text):
            return True
        child = child.get_next_sibling()
    return False


def test_note_editor_escape_closes_window():
    from unittest.mock import MagicMock, patch
    from gi.repository import Gdk
    from pdfatlas.ui.notes import NoteEditorWindow

    mock_win = MagicMock()
    mock_win.notes_layer._editors = {}
    note = {"id": 1, "page": 0, "markdown": "test content"}

    editor = NoteEditorWindow(mock_win, note)
    mock_win.notes_layer._editors[1] = editor

    # Simulate pressing Escape key
    with patch.object(editor, "close") as mock_close:
        res = editor._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
        assert res is True
        assert mock_close.called

    # Verify close request handler flushes content and unregisters editor
    editor._on_close_request(editor)
    mock_win.notes_layer.save_content.assert_called_with(1, "test content")
    assert 1 not in mock_win.notes_layer._editors
