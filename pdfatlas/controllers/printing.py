from __future__ import annotations

from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from ..core.document import DocumentModel


class PrintController:
    """
    Handles native PDF printing by delegating the file directly to Gtk.PrintDialog.
    """

    def __init__(self, parent_window: Gtk.Window):
        self.parent_window = parent_window
        self.dialog = Gtk.PrintDialog.new()

    def print_document(self, doc_model: DocumentModel, title: str = "Document") -> None:
        if not doc_model or not doc_model.filepath:
            return

        path = Path(doc_model.filepath)
        if not path.exists():
            return

        self.dialog.set_title(f"Print — {title}")
        gfile = Gio.File.new_for_path(str(path))

        def on_print_finished(dialog: Gtk.PrintDialog, result: Gio.AsyncResult):
            try:
                dialog.print_file_finish(result)
            except Exception:
                # User cancelled or print operation completed/dismissed
                pass

        self.dialog.print_file(
            self.parent_window,
            None,
            gfile,
            None,
            on_print_finished,
        )
