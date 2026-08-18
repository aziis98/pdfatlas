"""Tests for link target Y resolution and portal rendering in DocumentModel."""

import fitz
from pdfatlas.core.document import DocumentModel


def test_resolve_link_target_y_kind_goto(tmp_path):
    # Create dummy PDF
    pdf_file = tmp_path / "test_goto.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))

    # Kind 1 (LINK_GOTO): to_point.y is top-down (e.g. 180.0 pt from top)
    link_kind_1 = {
        "kind": fitz.LINK_GOTO,
        "page": 1,
        "to": fitz.Point(70.0, 180.0),
    }

    target_y = model.resolve_link_target_y(link_kind_1)
    assert abs(target_y - 180.0) < 1e-3

    model.close()


def test_resolve_link_target_y_kind_named(tmp_path):
    pdf_file = tmp_path / "test_named.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))

    # Kind 4 (LINK_NAMED): to_point.y is PDF native bottom-up (e.g. 640.0 pt from bottom of an 800 pt page -> 160 pt from top)
    link_kind_4 = {
        "kind": fitz.LINK_NAMED,
        "page": 1,
        "to": fitz.Point(70.0, 640.0),
    }

    target_y = model.resolve_link_target_y(link_kind_4)
    assert abs(target_y - 160.0) < 1e-3

    model.close()


def test_render_portal_pixmap(tmp_path):
    pdf_file = tmp_path / "test_portal.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((100, 200), "Hello Portal")
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))
    pix = model.render_portal_pixmap(page_index=0, target_y=200.0, target_w=300, target_h=100)

    assert pix is not None
    assert pix.width > 0
    assert pix.height > 0

    model.close()


def test_arxiv_link_click_opens_new_instance(tmp_path):
    from unittest.mock import MagicMock, patch
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id="com.example.testlinkclick")
    app.register(None)
    win = MainWindow(app)
    win.doc_model = MagicMock()
    win.doc_model.page_count = 5
    win.canvas.page_layout = [(0.0, 600.0, 800.0, None)]

    link_arxiv = {
        "kind": 2,
        "page": -1,
        "uri": "arxiv:2305.12345",
    }

    with patch.object(win, "_open_new_instance_for_source") as mock_open:
        win._on_link_clicked(0, link_arxiv)
        mock_open.assert_called_once_with("arxiv:2305.12345")


def test_arxiv_url_link_click_opens_new_instance(tmp_path):
    from unittest.mock import MagicMock, patch
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id="com.example.testlinkurlclick")
    app.register(None)
    win = MainWindow(app)
    win.doc_model = MagicMock()
    win.doc_model.page_count = 5
    win.canvas.page_layout = [(0.0, 600.0, 800.0, None)]

    link_arxiv_url = {
        "kind": 2,
        "page": -1,
        "uri": "https://arxiv.org/abs/2603.20268v1",
    }

    with patch.object(win, "_open_new_instance_for_source") as mock_open:
        win._on_link_clicked(0, link_arxiv_url)
        mock_open.assert_called_once_with("arxiv:2603.20268v1")


def test_regular_uri_link_click_launches_external_browser(tmp_path):
    from unittest.mock import MagicMock, patch
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id="com.example.testregurlclick")
    app.register(None)
    win = MainWindow(app)
    win.doc_model = MagicMock()
    win.doc_model.page_count = 5
    win.canvas.page_layout = [(0.0, 600.0, 800.0, None)]

    link_regular = {
        "kind": 2,
        "page": -1,
        "uri": "https://example.com/some/page",
    }

    with patch.object(win, "_open_new_instance_for_source") as mock_open, \
         patch.object(Gtk, "show_uri") as mock_show_uri:
        win._on_link_clicked(0, link_regular)
        mock_open.assert_not_called()
        mock_show_uri.assert_called_once()


