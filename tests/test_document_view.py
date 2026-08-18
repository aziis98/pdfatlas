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
