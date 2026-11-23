from __future__ import annotations

import itertools
from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from pdfatlas.controllers.search import SearchState
from pdfatlas.ui.window import MainWindow

_APP_IDS = itertools.count(1)


def _make_window() -> tuple[Adw.Application, MainWindow]:
    app = Adw.Application(application_id=f"com.example.testsearchsm{next(_APP_IDS)}")
    app.register(None)
    win = MainWindow(app)
    win.present()
    while GLib.MainContext.default().iteration(False):
        pass
    return app, win


def _make_fake_doc_env(win: MainWindow) -> tuple[MagicMock, MagicMock]:
    doc_model = MagicMock()
    doc_model.filepath = "/tmp/fake.pdf"
    doc_model.doc = []
    win.doc_model = doc_model

    canvas = MagicMock()
    canvas.page_layout = [(0.0, 600.0, 800.0, None)]
    canvas.dpi_scale_factor = 1.0
    canvas.page_gap = 16.0
    canvas.grab_focus.return_value = True
    win.canvas = canvas

    db_service = MagicMock()
    win.db_service = db_service
    win.entry.set_sensitive(True)
    return canvas, db_service


def test_search_state_transitions_and_unfocus():
    _app, win = _make_window()
    canvas, db_service = _make_fake_doc_env(win)
    sc = win.search_controller

    assert sc.state == SearchState.IDLE
    assert not sc.is_entry_focused()

    # Focusing search enters EDITING and focuses the entry (including inner Gtk.Text)
    win.focus_search()
    assert sc.state == SearchState.EDITING
    assert sc.is_entry_focused()
    assert win._is_entry_focused()

    # Running a non-empty search enters SEARCH_VIEW
    sc.run_search("attention")
    assert sc.state == SearchState.SEARCH_VIEW
    assert sc.last_query == "attention"
    assert win.stack.get_visible_child_name() == "search-view"
    db_service.search.assert_called_once()

    # Clicking a result switches to document-view, unfocuses entry, and enters RESULT_ACTIVE
    canvas.grab_focus.reset_mock()
    sc.on_row_clicked({"page": 1, "y0": 10.0, "y1": 20.0})
    assert sc.state == SearchState.RESULT_ACTIVE
    assert win.stack.get_visible_child_name() == "document-view"
    canvas.grab_focus.assert_called()
    assert not sc.is_entry_focused()

    win.close()


def test_clear_button_and_escape_unfocus_and_clear():
    _app, win = _make_window()
    canvas, _db = _make_fake_doc_env(win)
    sc = win.search_controller

    # Start search while entry is focused
    win.focus_search()
    sc.set_query_silently("transformer")
    assert sc._debounce_source_id is None
    sc.run_search("transformer")
    assert sc.state == SearchState.SEARCH_VIEW

    # Clicking Clear (×) returns to document-view, clears query & highlights, and unfocuses
    canvas.grab_focus.reset_mock()
    canvas.set_search_highlights.reset_mock()
    sc.on_clear_clicked()
    assert sc.state == SearchState.IDLE
    assert win.entry.get_text() == ""
    assert sc.last_query == ""
    assert sc._debounce_source_id is None
    assert win.stack.get_visible_child_name() == "document-view"
    canvas.set_search_highlights.assert_called_with({})
    canvas.grab_focus.assert_called()
    assert not sc.is_entry_focused()

    # Escape from EDITING (without searching) unfocuses and returns to IDLE
    win.focus_search()
    assert sc.is_entry_focused()
    assert sc.state == SearchState.EDITING
    canvas.grab_focus.reset_mock()
    assert win._on_escape() is True
    assert sc.state == SearchState.IDLE
    canvas.grab_focus.assert_called()
    assert not sc.is_entry_focused()

    # Escape from SEARCH_VIEW clears query, exits to document-view, and unfocuses
    win.focus_search()
    sc.set_query_silently("residual")
    sc.run_search("residual")
    assert sc.state == SearchState.SEARCH_VIEW
    canvas.grab_focus.reset_mock()
    assert win._on_escape() is True
    assert sc.state == SearchState.IDLE
    assert win.entry.get_text() == ""
    assert win.stack.get_visible_child_name() == "document-view"
    canvas.grab_focus.assert_called()
    assert not sc.is_entry_focused()

    win.close()


def test_backspace_to_empty_keeps_entry_focused_in_editing():
    _app, win = _make_window()
    canvas, _db = _make_fake_doc_env(win)
    sc = win.search_controller

    win.focus_search()
    sc.set_query_silently("foo")
    sc.run_search("foo")
    assert sc.state == SearchState.SEARCH_VIEW

    # Typing/backspacing to empty returns to document-view, keeps focus in entry
    canvas.grab_focus.reset_mock()
    sc.run_search("")
    assert win.stack.get_visible_child_name() == "document-view"
    assert sc.state == SearchState.EDITING
    assert sc.is_entry_focused()
    canvas.grab_focus.assert_not_called()

    win.close()


def test_exit_to_document_cancels_pending_and_unfocuses():
    _app, win = _make_window()
    canvas, _db = _make_fake_doc_env(win)
    sc = win.search_controller

    win.focus_search()
    sc.on_search_changed_debounced(win.entry)
    assert sc._debounce_source_id is not None

    sc.run_search("query")
    assert sc.state == SearchState.SEARCH_VIEW

    canvas.grab_focus.reset_mock()
    sc.exit_to_document(clear_query=False)
    assert sc.state == SearchState.IDLE
    assert sc._debounce_source_id is None
    assert win.stack.get_visible_child_name() == "document-view"
    canvas.grab_focus.assert_called()
    assert not sc.is_entry_focused()

    win.close()
