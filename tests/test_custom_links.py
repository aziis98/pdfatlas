import os
import sqlite3
import tempfile
import fitz

from pdfatlas.core.document import DocumentModel
from pdfatlas.core.index import (
    delete_custom_link_from_db,
    ensure_custom_links_table,
    load_custom_links_from_db,
    save_custom_link_to_db,
)
from pdfatlas.ui.components.link_search_dialog import LinkSearchDialog


def test_custom_links_db_crud():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conn = sqlite3.connect(db_path)
        ensure_custom_links_table(conn)

        # Empty initially
        assert load_custom_links_from_db(conn) == []

        # Save web link
        rects1 = [(50.0, 100.0, 150.0, 120.0)]
        lid1 = save_custom_link_to_db(
            conn,
            page=0,
            char_start=10,
            char_end=25,
            rects=rects1,
            text="attention is all you need",
            uri="https://arxiv.org/abs/1706.03762",
            file_path="/path/to/paper.pdf",
            title="Attention Is All You Need",
        )
        assert lid1 > 0

        # Save local PDF link
        rects2 = [(20.0, 40.0, 80.0, 60.0)]
        lid2 = save_custom_link_to_db(
            conn,
            page=1,
            char_start=0,
            char_end=15,
            rects=rects2,
            text="local reference",
            uri=None,
            file_path="/home/user/docs/report.pdf",
            title="Project Report",
        )
        assert lid2 > 0

        # Load and verify
        links = load_custom_links_from_db(conn)
        assert len(links) == 2
        assert links[0]["id"] == lid1
        assert links[0]["page"] == 0
        assert links[0]["char_start"] == 10
        assert links[0]["char_end"] == 25
        assert links[0]["rects"] == rects1
        assert links[0]["text"] == "attention is all you need"
        assert links[0]["uri"] == "https://arxiv.org/abs/1706.03762"
        assert links[0]["file_path"] == "/path/to/paper.pdf"
        assert links[0]["title"] == "Attention Is All You Need"

        assert links[1]["id"] == lid2
        assert links[1]["page"] == 1
        assert links[1]["uri"] is None
        assert links[1]["file_path"] == "/home/user/docs/report.pdf"

        # Delete link
        delete_custom_link_from_db(conn, lid1)
        remaining = load_custom_links_from_db(conn)
        assert len(remaining) == 1
        assert remaining[0]["id"] == lid2

        conn.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_document_model_custom_links_merging():
    # Create temporary PDF
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name

    try:
        doc = fitz.open()
        p0 = doc.new_page(width=600, height=800)
        p0.insert_text((50, 100), "Hello World Link Test")
        doc.save(pdf_path)
        doc.close()

        model = DocumentModel(pdf_path)
        assert model.page_count == 1
        initial_links = model.get_page_links(0)
        assert initial_links == []

        # Add custom link
        custom_link = {
            "id": 42,
            "page": 0,
            "char_start": 0,
            "char_end": 5,
            "rects": [[50.0, 90.0, 120.0, 105.0]],
            "text": "Hello",
            "uri": "https://example.com",
            "file_path": "/tmp/test.pdf",
            "title": "Example",
        }
        model.set_custom_links([custom_link])
        assert model.get_custom_links() == [custom_link]

        page_links = model.get_page_links(0)
        assert len(page_links) == 1
        link_entry = page_links[0]
        assert link_entry["custom"] is True
        assert link_entry["custom_id"] == 42
        assert link_entry["uri"] == "https://example.com"
        assert link_entry["file_path"] == "/tmp/test.pdf"
        assert link_entry["title"] == "Example"
        assert link_entry["from"] == fitz.Rect(50.0, 90.0, 120.0, 105.0)

        # Clear custom links
        model.set_custom_links([])
        assert model.get_page_links(0) == []
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


def test_link_search_dialog_clean_query():
    raw = "Attention   is all\n   you\nneed\n\t(Vaswani et al.)"
    cleaned = LinkSearchDialog._clean_query(raw)
    assert cleaned == "Attention is all you need (Vaswani et al.)"


def test_url_normalization():
    from pdfatlas.ui.components.url_dialog import normalize_url

    assert normalize_url("10.1145/3386569.3392440") == "https://doi.org/10.1145/3386569.3392440"
    assert normalize_url("https://example.com/paper.pdf") == "https://example.com/paper.pdf"
    assert normalize_url("http://example.com") == "http://example.com"
    assert normalize_url("github.com/aziis98/pdfatlas") == "https://github.com/aziis98/pdfatlas"
    assert normalize_url("") == ""


def test_coalesce_rects_by_line():
    from pdfatlas.core.layout import coalesce_rects_by_line

    assert coalesce_rects_by_line([]) == []

    # Per-character rects on the same line must coalesce into a single line bounding box
    char_rects = [
        (10.0, 20.0, 15.0, 32.0),
        (15.0, 20.0, 20.0, 32.0),
        (20.0, 20.0, 25.0, 32.0),
        (25.0, 20.0, 30.0, 32.0),
    ]
    coalesced = coalesce_rects_by_line(char_rects)
    assert len(coalesced) == 1
    assert coalesced[0] == (10.0, 20.0, 30.0, 32.0)

    # Multi-line rects must coalesce into one rect per line
    line2_rects = [
        (10.0, 40.0, 18.0, 52.0),
        (18.0, 40.0, 28.0, 52.0),
    ]
    multi_line = coalesce_rects_by_line(char_rects + line2_rects)
    assert len(multi_line) == 2
    assert multi_line[0] == (10.0, 20.0, 30.0, 32.0)
    assert multi_line[1] == (10.0, 40.0, 28.0, 52.0)


def test_document_model_coalesces_char_rects():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name

    try:
        doc = fitz.open()
        p0 = doc.new_page(width=600, height=800)
        p0.insert_text((50, 100), "Attention is all you need")
        doc.save(pdf_path)
        doc.close()

        model = DocumentModel(pdf_path)
        # Pass 5 individual character boxes for a single line
        char_rects = [
            (50.0 + i * 10, 90.0, 60.0 + i * 10, 105.0)
            for i in range(5)
        ]
        custom_link = {
            "id": 99,
            "page": 0,
            "char_start": 0,
            "char_end": 5,
            "rects": char_rects,
            "text": "Atten",
            "uri": "https://arxiv.org/abs/1706.03762",
            "file_path": None,
            "title": "Paper",
        }
        model.set_custom_links([custom_link])

        page_links = model.get_page_links(0)
        # Must coalesce the 5 boxes into exactly 1 bounding rect
        assert len(page_links) == 1
        assert page_links[0]["from"] == fitz.Rect(50.0, 90.0, 100.0, 105.0)
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


def test_text_selection_get_selection_line_rects():
    from pdfatlas.core.text_selection import TextSelection

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name

    try:
        doc = fitz.open()
        p0 = doc.new_page(width=600, height=800)
        p0.insert_text((50, 100), "Hello world from custom links test")
        doc.save(pdf_path)
        doc.close()

        model = DocumentModel(pdf_path)
        sel = TextSelection(model)
        pi = sel.get_page_index(0)
        assert len(pi.chars) > 0

        # Select word 'Hello'
        sel.anchor_page = 0
        sel.anchor_char_idx = 0
        sel.focus_page = 0
        sel.focus_char_idx = 4

        line_rects = sel.get_selection_line_rects(0)
        # Should be a single contiguous rect, not 5 individual char boxes
        assert len(line_rects) == 1
        x0, y0, x1, y1 = line_rects[0]
        assert x0 < x1
        assert y0 < y1
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


def test_canvas_context_menu_plain_page():
    from unittest.mock import MagicMock
    from gi.repository import Gtk
    from pdfatlas.ui.canvas import PDFCanvas

    canvas = PDFCanvas()
    canvas.on_note_create = MagicMock()

    menu = canvas._build_context_menu(0, (100.0, 200.0), 150.0, 250.0)
    assert isinstance(menu, Gtk.Box)

    labels = []
    child = menu.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            btn_box = child.get_child()
            if isinstance(btn_box, Gtk.Box):
                c = btn_box.get_first_child()
                while c is not None:
                    if isinstance(c, Gtk.Label):
                        labels.append(c.get_label())
                        break
                    c = c.get_next_sibling()
        child = child.get_next_sibling()

    assert labels == ["Add Note Here"]


def test_canvas_context_menu_on_custom_link():
    from unittest.mock import MagicMock
    from gi.repository import Gtk
    from pdfatlas.ui.canvas import PDFCanvas

    canvas = PDFCanvas()
    canvas.on_note_create = MagicMock()
    canvas.on_link_clicked = MagicMock()
    canvas.on_remove_custom_link = MagicMock()

    test_link = {
        "uri": "https://example.com/paper",
        "custom": True,
        "custom_id": 42,
    }
    canvas._hit_test_link = lambda x, y: (0, test_link)

    menu = canvas._build_context_menu(0, (100.0, 200.0), 150.0, 250.0)
    buttons = []
    labels = []
    child = menu.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            buttons.append(child)
            btn_box = child.get_child()
            if isinstance(btn_box, Gtk.Box):
                c = btn_box.get_first_child()
                while c is not None:
                    if isinstance(c, Gtk.Label):
                        labels.append(c.get_label())
                        break
                    c = c.get_next_sibling()
        child = child.get_next_sibling()

    assert "Open Link in Browser" in labels
    assert "Copy Link Address" in labels
    assert "Remove Link" in labels
    assert "Add Note Here" in labels

    remove_btn_idx = labels.index("Remove Link")
    remove_btn = buttons[remove_btn_idx]
    assert remove_btn.has_css_class("destructive-action")
    remove_btn.emit("clicked")
    canvas.on_remove_custom_link.assert_called_once_with(test_link)


def test_canvas_context_menu_on_selection_with_overlapping_link():
    from unittest.mock import MagicMock
    from gi.repository import Gtk
    from pdfatlas.ui.canvas import PDFCanvas

    canvas = PDFCanvas()
    canvas.on_note_create = MagicMock()
    canvas.on_remove_matching_custom_links = MagicMock()
    canvas.on_highlight_selection = MagicMock()
    canvas.on_add_link_selection = MagicMock()

    mock_sel = MagicMock()
    mock_sel.has_selection.return_value = True
    mock_sel.anchor_page = 0
    mock_sel.focus_page = 0
    mock_sel._selection_range.return_value = (5, 20)
    canvas.text_selection = mock_sel

    mock_win = MagicMock()
    mock_win.custom_links = [
        {"id": 7, "page": 0, "char_start": 8, "char_end": 15, "uri": "https://foo.com"}
    ]
    mock_win.arxiv_mapper = None
    canvas.win = mock_win

    menu = canvas._build_context_menu(0, (100.0, 200.0), 150.0, 250.0)
    buttons = []
    labels = []
    child = menu.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            buttons.append(child)
            btn_box = child.get_child()
            if isinstance(btn_box, Gtk.Box):
                c = btn_box.get_first_child()
                while c is not None:
                    if isinstance(c, Gtk.Label):
                        labels.append(c.get_label())
                        break
                    c = c.get_next_sibling()
        child = child.get_next_sibling()

    assert "Copy" in labels
    assert "Highlight" in labels
    assert "Add Link…" in labels
    assert "Remove Link" in labels
    assert "Add Note Here" in labels
    assert "Deselect" in labels

    remove_btn_idx = labels.index("Remove Link")
    remove_btn = buttons[remove_btn_idx]
    assert remove_btn.has_css_class("destructive-action")
    remove_btn.emit("clicked")
    canvas.on_remove_matching_custom_links.assert_called_once()



