import gi
from unittest.mock import MagicMock

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from pdfatlas.core.pdf_source import PdfSource
from pdfatlas.ui.window import MainWindow


def test_init_with_initial_source_activates_document_view(tmp_path):
    import pymupdf as fitz

    pdf_path = tmp_path / "test_init_doc.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(str(pdf_path))
    doc.close()

    app = Adw.Application(application_id="com.example.testflash1")
    app.register(None)
    src = PdfSource(source_type="file", uri=str(pdf_path), display_name="test_init_doc.pdf")

    win = None
    try:
        win = MainWindow(app, initial_source=src)
        assert win.stack.get_visible_child_name() == "document-view"
        assert win.doc_model is not None
        assert win.tab_view.get_n_pages() == 1
    finally:
        if win is not None:
            win.close()


def test_init_without_source_defaults_to_welcome_view():
    app = Adw.Application(application_id="com.example.testflash2")
    app.register(None)

    win = None
    try:
        win = MainWindow(app)
        assert win.stack.get_visible_child_name() == "welcome-view"
        assert win.doc_model is None
        assert win.tab_view.get_n_pages() == 0
    finally:
        if win is not None:
            win.close()


def test_stack_transition_none_before_realize_and_crossfade_after():
    app = Adw.Application(application_id="com.example.testflash3")
    app.register(None)

    win = None
    try:
        win = MainWindow(app)
        assert win.stack.get_transition_type() == Gtk.StackTransitionType.NONE
        win._on_window_realized(win)
        assert win.stack.get_transition_type() == Gtk.StackTransitionType.CROSSFADE
    finally:
        if win is not None:
            win.close()


def test_initial_source_file_not_found_falls_back_to_welcome():
    app = Adw.Application(application_id="com.example.testflash4")
    app.register(None)
    src = PdfSource(source_type="file", uri="/nonexistent/path/paper.pdf", display_name="paper.pdf")

    win = None
    try:
        MainWindow._show_error_dialog = MagicMock()  # type: ignore
        win = MainWindow(app, initial_source=src)
        assert win.stack.get_visible_child_name() == "welcome-view"
        assert win.doc_model is None
    finally:
        if win is not None:
            win.close()
