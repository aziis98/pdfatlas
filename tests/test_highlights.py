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
