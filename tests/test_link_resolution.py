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

    with patch.object(win, "open_document") as mock_open:
        win._on_link_clicked(0, link_arxiv)
        assert mock_open.call_count == 1
        source_arg = mock_open.call_args[0][0]
        assert source_arg.is_arxiv
        assert source_arg.uri == "arxiv:2305.12345"
        assert mock_open.call_args[1].get("new_tab") is True


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

    with patch.object(win, "open_document") as mock_open:
        win._on_link_clicked(0, link_arxiv_url)
        assert mock_open.call_count == 1
        source_arg = mock_open.call_args[0][0]
        assert source_arg.is_arxiv
        assert source_arg.uri == "arxiv:2603.20268v1"
        assert mock_open.call_args[1].get("new_tab") is True


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


def test_detect_text_arxiv_links_synthetic(tmp_path):
    import fitz
    from pdfatlas.core.document import DocumentModel

    pdf_file = tmp_path / "test_arxiv_text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 100), "See reference arXiv:2102.03384 for details.")
    page.insert_text((50, 200), "Also see https://arxiv.org/abs/math/9903146.")
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))
    links = model.get_page_links(0)
    assert len(links) == 2
    assert links[0]["auto_detected"] is True
    assert "2102.03384" in links[0]["uri"]
    assert links[1]["auto_detected"] is True
    assert "math/9903146" in links[1]["uri"]
    model.close()


def test_detect_text_arxiv_links_sample_pdf():
    import os
    from pdfatlas.core.document import DocumentModel

    sample_path = "sandbox.local/sample-files/2603.20268v1.pdf"
    if not os.path.exists(sample_path):
        return

    model = DocumentModel(sample_path)
    # Page 42 (0-indexed 41) has 6 plain text arXiv citations
    p42_links = model.get_page_links(41)
    auto_links = [lnk for lnk in p42_links if lnk.get("auto_detected")]
    assert len(auto_links) == 6
    aids = [lnk.get("arxiv_id") for lnk in auto_links]
    assert "2102.03384" in aids
    assert "math/9903146" in aids
    assert "2307.06944" in aids
    assert "1805.11574" in aids
    assert "2502.03415" in aids
    assert "math/9901113" in aids
    model.close()


def test_normalize_link_dict_string_page(tmp_path):
    import fitz
    from pdfatlas.core.document import normalize_link_dict

    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)

    # String page '2' -> 0-indexed page 1
    raw_link = {
        "kind": 4,
        "page": "2",
        "view": "FitH,51",
    }
    norm = normalize_link_dict(doc, raw_link, len(doc))
    assert norm["page"] == 1
    assert norm["to"] == fitz.Point(0.0, 51.0)

    # String page '99' out of bounds -> None
    raw_link_oob = {
        "kind": 4,
        "page": "99",
    }
    norm_oob = normalize_link_dict(doc, raw_link_oob, len(doc))
    assert norm_oob["page"] is None

    doc.close()


def test_link_preview_hover_string_page_safe():
    from unittest.mock import MagicMock
    from pdfatlas.ui.link_preview import LinkPreviewManager

    mock_win = MagicMock()
    mock_win.canvas = None
    mock_win.doc_model = MagicMock()
    mock_win.doc_model.page_count = 10
    manager = LinkPreviewManager(mock_win)

    # Link with int page
    manager.on_link_hovered(0, {"kind": 4, "page": 1})
    mock_win.link_preview_label.set_text.assert_called_with("Go to Page 2")

    # Link with leftover or unnormalized string page (fallback safety)
    manager.on_link_hovered(0, {"kind": 4, "page": "2"})
    # Should not raise TypeError and should fallback gracefully




