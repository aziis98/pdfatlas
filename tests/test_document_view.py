from __future__ import annotations

from unittest.mock import MagicMock
import fitz

from pdfatlas.core.document import DocumentModel
from pdfatlas.core.pdf_source import PdfSource
from pdfatlas.ui.document_view import PdfDocumentView


def test_document_view_initialization():
    view = PdfDocumentView()
    assert view.zoom == 1.0
    assert view.doc_model is None
    assert view.current_source is None
    assert view.canvas is not None
    assert view.notes_layer is not None
    assert view.link_preview_manager is not None


def test_document_view_set_document_and_zoom(tmp_path):
    doc_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=800)
    doc.new_page(width=500, height=800)
    doc.save(str(doc_path))
    doc.close()

    doc_model = DocumentModel(str(doc_path))
    source = PdfSource(source_type="file", uri=str(doc_path), display_name="Sample")

    page_changes = []
    zoom_changes = []

    mock_render_worker = MagicMock()

    view = PdfDocumentView(
        render_worker=mock_render_worker,
        on_page_changed=lambda cur, tot: page_changes.append((cur, tot)),
        on_zoom_changed=lambda z: zoom_changes.append(z),
    )

    view.set_document(doc_model, source)
    assert view.doc_model is not None
    assert view.doc_model.page_count == 2
    assert (1, 2) in page_changes

    view.set_zoom(1.5)
    assert view.zoom == 1.5
    assert 1.5 in zoom_changes

    view.zoom_in()
    assert view.zoom > 1.5

    view.zoom_out()
    assert view.zoom < 2.0

    view.close()
    assert view.doc_model is None
    mock_render_worker.clear_canvas_render_jobs.assert_called()


def test_document_view_scroll_restoration(tmp_path):
    doc_path = tmp_path / "sample_scroll.pdf"
    doc = fitz.open()
    for _ in range(5):
        doc.new_page(width=500, height=800)
    doc.save(str(doc_path))
    doc.close()

    doc_model = DocumentModel(str(doc_path))
    source = PdfSource(source_type="file", uri=str(doc_path), display_name="Sample")
    view = PdfDocumentView(render_worker=MagicMock())
    view.set_document(doc_model, source)

    view.vadjustment.set_upper(4000.0)
    view.hadjustment.set_upper(2000.0)
    view.vadjustment.set_page_size(600.0)
    view.hadjustment.set_page_size(500.0)
    view.vadjustment.set_value(1200.0)
    assert view.saved_scroll_y == 1200.0

    # Save scroll position before unmapping
    saved_y = view.saved_scroll_y
    # Simulate tab switch / unmapping where vadjustment resets to 0
    view.vadjustment.set_value(0.0)
    assert view.vadjustment.get_value() == 0.0
    view.saved_scroll_y = saved_y

    # Restoring scroll position recovers the saved offset
    view.restore_scroll_position()
    assert view.vadjustment.get_value() == 1200.0

    view.close()

