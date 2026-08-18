from __future__ import annotations

from unittest.mock import MagicMock
import fitz

from pdfatlas.core.document import DocumentModel
from pdfatlas.core.pdf_source import PdfSource
from pdfatlas.ui.document_view import PdfDocumentView


def _create_sample_pdf(path, pages: int = 4, width: float = 600, height: float = 800):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()


def test_zoom_anchor_preservation_page_0(tmp_path):
    pdf_path = tmp_path / "test_anchor_0.pdf"
    _create_sample_pdf(pdf_path, pages=3, width=600, height=800)

    doc_model = DocumentModel(str(pdf_path))
    source = PdfSource(source_type="file", uri=str(pdf_path), display_name="Test")
    view = PdfDocumentView(render_worker=MagicMock())
    view.set_document(doc_model, source)

    # Set initial viewport geometry
    view.vadjustment.set_page_size(600.0)
    view.hadjustment.set_page_size(800.0)
    view.vadjustment.set_value(100.0)
    view.hadjustment.set_value(0.0)

    # Cursor at screen coordinates (400, 300)
    screen_cursor_x = 400.0
    screen_cursor_y = 300.0
    doc_anchor_x = screen_cursor_x + view.hadjustment.get_value()
    doc_anchor_y = screen_cursor_y + view.vadjustment.get_value()

    view.set_zoom_level(1.5, anchor_x=doc_anchor_x, anchor_y=doc_anchor_y)

    # After zoom, the new document position of the anchor should map back to the same screen coordinate
    new_v_val = view.vadjustment.get_value()
    # Fixed gaps for page 0 = 0. Initial content_y = doc_anchor_y = 400.
    # New content_y = 400 * 1.5 = 600. Screen pos was 300. New val_v = 600 - 300 = 300.
    assert abs(new_v_val - 300.0) < 1.0

    view.close()


def test_zoom_anchor_preservation_multi_page(tmp_path):
    pdf_path = tmp_path / "test_anchor_multi.pdf"
    _create_sample_pdf(pdf_path, pages=5, width=600, height=800)

    doc_model = DocumentModel(str(pdf_path))
    source = PdfSource(source_type="file", uri=str(pdf_path), display_name="Test")
    view = PdfDocumentView(render_worker=MagicMock())
    view.set_document(doc_model, source)

    view.vadjustment.set_page_size(600.0)
    view.hadjustment.set_page_size(800.0)

    # Scroll down to page 2 (page 0: 0..800, gap: 800..812, page 1: 812..1612, gap: 1612..1624, page 2: 1624..2424)
    view.vadjustment.set_value(1700.0)

    screen_cursor_y = 200.0
    doc_anchor_y = screen_cursor_y + view.vadjustment.get_value()  # 1900.0 (on page 2)

    view.set_zoom_level(1.2, anchor_y=doc_anchor_y)

    # Fixed gaps for page 2 = 2 * 12 = 24.
    # Content y = 1900 - 24 = 1876.
    # New content y = 1876 * 1.2 = 2251.2.
    # New anchor y = 24 + 2251.2 = 2275.2.
    # New scroll = 2275.2 - 200 = 2075.2.
    new_v_val = view.vadjustment.get_value()
    assert abs(new_v_val - 2075.2) < 2.0

    view.close()


def test_zoom_horizontal_centering(tmp_path):
    pdf_path = tmp_path / "test_centering.pdf"
    _create_sample_pdf(pdf_path, pages=2, width=400, height=600)

    doc_model = DocumentModel(str(pdf_path))
    source = PdfSource(source_type="file", uri=str(pdf_path), display_name="Test")
    view = PdfDocumentView(render_worker=MagicMock())
    view.set_document(doc_model, source)

    view.vadjustment.set_page_size(600.0)
    view.hadjustment.set_page_size(1000.0)  # Viewport wider than page (1000 vs 400)
    view.hadjustment.set_value(0.0)

    # Zoom centered horizontally without explicit anchor (uses viewport center: 500)
    view.set_zoom_level(1.5)

    # At zoom 1.5, page width is 600, which is still <= viewport 1000.
    # Horizontal scroll offset must collapse / stay at 0.0
    assert view.hadjustment.get_value() == 0.0

    view.close()


def test_zoom_fit_width_and_page(tmp_path):
    pdf_path = tmp_path / "test_fit.pdf"
    _create_sample_pdf(pdf_path, pages=2, width=500, height=1000)

    doc_model = DocumentModel(str(pdf_path))
    source = PdfSource(source_type="file", uri=str(pdf_path), display_name="Test")
    view = PdfDocumentView(render_worker=MagicMock())
    view.set_document(doc_model, source)

    view.canvas.scrolled_window.set_size_request(1000, 800)
    view.hadjustment.set_page_size(1000.0)
    view.vadjustment.set_page_size(800.0)

    # Fit Width on 500pt page in 1000pt viewport -> target zoom ~ 2.0 (divided by dpi factor)
    view.zoom_fit_width()
    expected_w_zoom = 1000.0 / (500.0 * view.canvas.dpi_scale_factor)
    assert abs(view.zoom - expected_w_zoom) < 0.05

    # Fit Page in 1000x800 viewport for 500x1000 page
    view.zoom_fit_page()
    expected_h_zoom = (800.0 - 24.0) / (1000.0 * view.canvas.dpi_scale_factor)
    assert abs(view.zoom - expected_h_zoom) < 0.05

    view.close()


def test_multi_tab_zoom_isolation(tmp_path):
    pdf_path1 = tmp_path / "tab1.pdf"
    pdf_path2 = tmp_path / "tab2.pdf"
    _create_sample_pdf(pdf_path1, pages=2, width=500, height=800)
    _create_sample_pdf(pdf_path2, pages=2, width=600, height=900)

    doc1 = DocumentModel(str(pdf_path1))
    doc2 = DocumentModel(str(pdf_path2))

    view1 = PdfDocumentView(render_worker=MagicMock())
    view2 = PdfDocumentView(render_worker=MagicMock())

    view1.set_document(doc1, PdfSource(source_type="file", uri=str(pdf_path1), display_name="Tab 1"))
    view2.set_document(doc2, PdfSource(source_type="file", uri=str(pdf_path2), display_name="Tab 2"))

    view1.set_zoom(1.25)
    view2.set_zoom(2.0)

    assert view1.zoom == 1.25
    assert view2.zoom == 2.0

    view1.zoom_in()
    assert abs(view1.zoom - 1.5) < 0.01
    assert view2.zoom == 2.0

    view1.close()
    view2.close()
