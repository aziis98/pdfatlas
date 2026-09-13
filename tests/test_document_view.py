from __future__ import annotations

from unittest.mock import MagicMock
import pymupdf as fitz

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


def test_document_view_selection_change_callback():
    selection_events = []
    view = PdfDocumentView(
        on_selection_changed=lambda has_sel: selection_events.append(has_sel),
    )
    assert not hasattr(view, "selection_toolbar")
    view._handle_selection_changed(True)
    assert selection_events == [True]
    view._handle_selection_changed(False)
    assert selection_events == [True, False]


def test_document_view_isolated_settings():
    from pdfatlas.core.settings import CropSettings

    base_settings = CropSettings(enabled=False, page_gaps=True)
    view1 = PdfDocumentView(settings=base_settings)
    view2 = PdfDocumentView(settings=base_settings)

    # They should have cloned settings, not shared reference
    assert view1.settings is not view2.settings
    assert view1.settings is not base_settings

    # Mutate view1
    view1.settings.enabled = True
    view1.settings.page_gaps = False

    # view2 and base_settings remain unaffected
    assert view2.settings.enabled is False
    assert view2.settings.page_gaps is True
    assert base_settings.enabled is False
    assert base_settings.page_gaps is True


def test_doc_state_save_and_load_crop_and_gaps():
    import sqlite3
    from pdfatlas.core.index import load_doc_state, save_doc_state

    conn = sqlite3.connect(":memory:")
    save_doc_state(conn, zoom=1.5, scroll_y=250.0, scroll_x=50.0, crop_enabled=True, page_gaps=False)

    state = load_doc_state(conn)
    assert state["zoom"] == 1.5
    assert state["scroll_y"] == 250.0
    assert state["scroll_x"] == 50.0
    assert state["crop_enabled"] is True
    assert state["page_gaps"] is False

    # Update state
    save_doc_state(conn, zoom=2.0, scroll_y=0.0, scroll_x=0.0, crop_enabled=False, page_gaps=True)
    updated = load_doc_state(conn)
    assert updated["zoom"] == 2.0
    assert updated["crop_enabled"] is False
    assert updated["page_gaps"] is True


def test_tab_switch_syncs_crop_and_gapless_action_states(tmp_path):
    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    # Create two temporary sample PDFs
    doc1_path = tmp_path / "doc1.pdf"
    doc2_path = tmp_path / "doc2.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=800)
    doc.save(str(doc1_path))
    doc.save(str(doc2_path))
    doc.close()

    app = Adw.Application(application_id="com.example.testcropgapsync")
    win = MainWindow(app)

    source1 = PdfSource(source_type="file", uri=str(doc1_path), display_name="Doc 1")
    source2 = PdfSource(source_type="file", uri=str(doc2_path), display_name="Doc 2")

    win.open_document(source1, new_tab=False)
    win.open_document(source2, new_tab=True)

    assert win.tab_view.get_n_pages() == 2

    # Tab 2 is active
    doc_view2 = win.get_active_doc_view()
    assert doc_view2 is not None

    # Switch to Tab 1
    page1 = win.tab_view.get_nth_page(0)
    win.tab_view.set_selected_page(page1)
    doc_view1 = win.get_active_doc_view()
    assert doc_view1 is not None

    # Set Tab 1 to crop enabled and gapless
    win.toggle_crop()
    win.toggle_gapless()
    assert doc_view1.settings.enabled is True
    assert doc_view1.settings.page_gaps is False
    c_state = win.crop_action.get_state()
    g_state = win.gapless_action.get_state()
    assert c_state is not None and c_state.get_boolean() is True
    assert g_state is not None and g_state.get_boolean() is True

    # Switch to Tab 2
    page2 = win.tab_view.get_nth_page(1)
    win.tab_view.set_selected_page(page2)

    # Tab 2 should still have defaults (not cropped, not gapless)
    assert doc_view2.settings.enabled is False
    assert doc_view2.settings.page_gaps is True
    c_state2 = win.crop_action.get_state()
    g_state2 = win.gapless_action.get_state()
    assert c_state2 is not None and c_state2.get_boolean() is False
    assert g_state2 is not None and g_state2.get_boolean() is False

    # Switch back to Tab 1
    win.tab_view.set_selected_page(page1)
    c_state1_again = win.crop_action.get_state()
    g_state1_again = win.gapless_action.get_state()
    assert c_state1_again is not None and c_state1_again.get_boolean() is True
    assert g_state1_again is not None and g_state1_again.get_boolean() is True

    win.close()




