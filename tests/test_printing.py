from __future__ import annotations

from unittest.mock import MagicMock
import pymupdf as fitz
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from pdfatlas.controllers.printing import PrintController
from pdfatlas.core.document import DocumentModel
from pdfatlas.core.settings import CropSettings
from pdfatlas.ui.canvas import PageContainer, PDFCanvas
from pdfatlas.ui.window import MainWindow


def test_page_shadow_settings_and_container():
    settings = CropSettings()
    assert settings.page_shadow is True

    container = PageContainer(0)
    assert container.has_css_class("page-shadow")

    container.set_shadow(False)
    assert not container.has_css_class("page-shadow")

    container.set_shadow(True)
    assert container.has_css_class("page-shadow")
    assert not container.has_css_class("no-radius")

    container.set_shadow(True, has_radius=False)
    assert container.has_css_class("page-shadow")
    assert container.has_css_class("no-radius")

    container.set_shadow(True, has_radius=True)
    assert container.has_css_class("page-shadow")
    assert not container.has_css_class("no-radius")


def test_canvas_page_shadow_toggle(tmp_path):
    doc_path = tmp_path / "test_shadow.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=800)
    doc.new_page(width=500, height=800)
    doc.save(str(doc_path))
    doc.close()

    doc_model = DocumentModel(str(doc_path))
    canvas = PDFCanvas()
    settings = CropSettings(page_shadow=True)
    canvas.set_document(doc_model, MagicMock(), MagicMock(), crop_analyzer=None, settings=settings)

    assert len(canvas.containers) == 2
    assert all(c.has_css_class("page-shadow") for c in canvas.containers)

    canvas.set_page_shadow(False)
    assert all(not c.has_css_class("page-shadow") for c in canvas.containers)

    settings.page_shadow = False
    canvas.update_layout()
    assert all(not c.has_css_class("page-shadow") for c in canvas.containers)

    # In gapless mode, shadows are suppressed even if settings.page_shadow is True
    settings.page_shadow = True
    settings.page_gaps = False
    canvas.update_layout()
    assert all(not c.has_css_class("page-shadow") for c in canvas.containers)
    assert canvas.page_shadow is False

    # When a page matches the exact viewport width, .no-radius is set
    settings.page_gaps = True
    canvas.update_layout()
    page_w = canvas.page_layout[0][1]
    canvas._reposition_pages(viewport_w=page_w)
    assert all(c.has_css_class("no-radius") for c in canvas.containers)

    # When viewport is wider, .no-radius is removed
    canvas._reposition_pages(viewport_w=page_w + 100.0)
    assert all(not c.has_css_class("no-radius") for c in canvas.containers)


def test_print_controller_print_file(tmp_path, monkeypatch):
    doc_path = tmp_path / "test_print.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.draw_rect(fitz.Rect(50, 50, 200, 200), color=(1, 0, 0))
    doc.save(str(doc_path))
    doc.close()

    doc_model = DocumentModel(str(doc_path))

    Adw.init()
    win = Gtk.Window()
    controller = PrintController(win)

    captured_calls = []

    def mock_print_file(dialog, parent, setup, file, cancellable, callback):
        captured_calls.append({
            "parent": parent,
            "file_path": file.get_path(),
            "dialog_title": dialog.get_title(),
        })
        if callback:
            mock_res = MagicMock()
            callback(dialog, mock_res)

    monkeypatch.setattr(Gtk.PrintDialog, "print_file", mock_print_file)
    monkeypatch.setattr(Gtk.PrintDialog, "print_file_finish", lambda self, res: True)

    controller.print_document(doc_model, title="Test Doc")
    assert len(captured_calls) == 1
    assert captured_calls[0]["file_path"] == str(doc_path)
    assert captured_calls[0]["dialog_title"] == "Print — Test Doc"


def test_print_action_in_main_window():
    Adw.init()
    app = Adw.Application(application_id="com.example.testprint")
    win = MainWindow(app)
    try:
        assert win.has_action("print-file")
        # Trigger print when no document is open - should safely show toast
        win.print_file()
        assert win.toast_overlay is not None
    finally:
        win.close()
