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


def test_drag_tab_to_new_window(tmp_path):
    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    doc_path = tmp_path / "sample_drag.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=800)
    doc.save(str(doc_path))
    doc.close()

    app = Adw.Application(application_id="com.example.testdragtab")
    win1 = MainWindow(app)
    source = PdfSource(source_type="file", uri=str(doc_path), display_name="Dragged Doc")
    win1.open_document(source)
    assert win1.stack.get_visible_child_name() == "document-view"

    # Simulate tab drag to new window (Adw.TabView create-window signal)
    new_tab_view = win1._on_create_window(win1.tab_view)
    assert new_tab_view is not None

    # Get the newly created window from the tab view
    win2 = new_tab_view.get_root()
    assert isinstance(win2, MainWindow)
    assert win2.stack.get_visible_child_name() == "document-view"

    # Transfer page from win1 to win2
    page1 = win1.tab_view.get_nth_page(0)
    win1.tab_view.transfer_page(page1, win2.tab_view, 0)

    # Verify win2 is showing document-view and has the active document
    assert win2.stack.get_visible_child_name() == "document-view"
    active_doc = win2.get_active_doc_view()
    assert active_doc is not None
    assert active_doc.doc_model is not None

    win1.close()
    win2.close()


def test_document_view_in_tab_loading(tmp_path):
    view = PdfDocumentView()
    assert view.is_loading is False
    assert view.stack.get_visible_child_name() == "canvas"

    view.show_loading(title="Downloading arXiv:2305.12345", subtitle="Connecting...")
    assert view.is_loading is True
    assert view.stack.get_visible_child_name() == "loading"
    assert view.loading_title.get_label() == "Downloading arXiv:2305.12345"
    assert view.loading_subtitle.get_label() == "Connecting..."

    view.set_loading_progress(0.45, "Downloading 4.5/10.0 MB...")
    assert view.loading_progress_bar.get_fraction() == 0.45
    assert view.loading_subtitle.get_label() == "Downloading 4.5/10.0 MB..."

    # Loading hides when set_document is called
    doc_path = tmp_path / "sample_loading.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=800)
    doc.save(str(doc_path))
    doc.close()

    doc_model = DocumentModel(str(doc_path))
    source = PdfSource(source_type="arxiv", uri=str(doc_path), display_name="Paper")
    view.set_document(doc_model, source)

    assert view.is_loading is False
    assert view.stack.get_visible_child_name() == "canvas"
    view.close()



