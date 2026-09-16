from __future__ import annotations

from unittest.mock import MagicMock
from pdfatlas.core.search_provider import SearchResult, SingleDocumentSearchProvider
from pdfatlas.controllers.search import SearchCoordinator
from pdfatlas.ui.components.search_header_entry import SearchHeaderEntry
from pdfatlas.ui.components.search_results_view import SearchResultsView


def test_search_result_dataclass_and_dict():
    res = SearchResult(
        id=1,
        page=2,
        snippet="Sample match <b>text</b>",
        x0=10.0,
        y0=20.0,
        x1=100.0,
        y1=120.0,
        filepath="/tmp/paper.pdf",
        doc_title="Sample Paper",
    )
    d = res.to_dict()
    assert d["id"] == 1
    assert d["page"] == 2
    assert d["filepath"] == "/tmp/paper.pdf"

    rebuilt = SearchResult.from_dict(d)
    assert rebuilt.id == 1
    assert rebuilt.page == 2
    assert rebuilt.snippet == "Sample match <b>text</b>"
    assert rebuilt.filepath == "/tmp/paper.pdf"


def test_single_document_search_provider():
    mock_db = MagicMock()
    provider = SingleDocumentSearchProvider(
        db_service=mock_db,
        filepath="/tmp/test.pdf",
        doc_title="Test Document",
    )

    def dummy_search(query, limit, search_id, on_results):
        on_results(
            [{"id": 42, "page": 3, "snippet": "matched snippet", "x0": 5, "y0": 10, "x1": 50, "y1": 60}],
            search_id,
        )

    mock_db.search.side_effect = dummy_search

    captured_results = []
    def on_results(results: list[SearchResult], sid: int):
        captured_results.extend(results)

    provider.search("query", limit=10, search_id=1, on_results=on_results)
    assert len(captured_results) == 1
    assert captured_results[0].id == 42
    assert captured_results[0].filepath == "/tmp/test.pdf"
    assert captured_results[0].doc_title == "Test Document"


def test_search_coordinator_orchestration():
    header = SearchHeaderEntry()
    results_view = SearchResultsView()
    mock_provider = MagicMock()

    activated_results = []
    view_changes = []

    coordinator = SearchCoordinator(
        header_entry=header,
        results_view=results_view,
        provider_getter=lambda: mock_provider,
        on_result_activated=lambda res, terms: activated_results.append(res),
        on_view_changed=lambda v: view_changes.append(v),
        get_active_filepath=lambda: "/tmp/doc.pdf",
        get_is_grid=lambda: False,
    )

    # Empty query should switch to document-view
    coordinator.run_search("")
    assert view_changes[-1] == "document-view"

    # Non-empty query should trigger provider search and switch to search-view
    coordinator.run_search("transformer")
    assert view_changes[-1] == "search-view"
    mock_provider.search.assert_called_once()


def test_search_results_view_empty_state():
    results_view = SearchResultsView()
    assert results_view.stack.get_visible_child_name() == "results"

    # Render with no results
    results_view.render_results([], query="quantum entanglement", active_filepath="/tmp/test.pdf")
    assert results_view.stack.get_visible_child_name() == "empty"
    assert results_view.status_page.get_title() == "No Results Found"
    desc = results_view.status_page.get_description()
    assert desc is not None and "quantum entanglement" in desc

    # Render with results switches back to results view
    sample = {
        "id": 101,
        "page": 1,
        "block_no": 2,
        "x0": 50,
        "y0": 100,
        "x1": 200,
        "y1": 120,
        "text": "Quantum mechanics foundation",
    }
    results_view.render_results([sample], query="quantum", active_filepath="/tmp/test.pdf")
    assert results_view.stack.get_visible_child_name() == "results"

    # Clearing resets view to results
    results_view.clear()
    assert results_view.stack.get_visible_child_name() == "results"


def test_search_results_view_pinned_with_empty_live():
    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    results_view = SearchResultsView()
    sample_pinned = {
        "id": 202,
        "page": 2,
        "block_no": 1,
        "x0": 40,
        "y0": 60,
        "x1": 150,
        "y1": 80,
        "text": "Pinned note",
    }
    results_view.pinned[202] = {"result": sample_pinned, "query_terms": {"pinned"}}

    # When pinned items exist but live search has 0 results, stack stays on results
    # and results_box contains pinned cards + empty status page
    results_view.render_results([], query="unmatched_keyword", active_filepath="/tmp/test.pdf")
    assert results_view.stack.get_visible_child_name() == "results"

    # Verify there is an Adw.StatusPage inside results_box
    child = results_view.results_box.get_first_child()
    found_status = False
    while child is not None:
        if isinstance(child, Adw.StatusPage):
            found_status = True
            assert child.get_title() == "No Results Found"
            assert "unmatched_keyword" in (child.get_description() or "")
        child = child.get_next_sibling()
    assert found_status is True


def test_focus_search_with_selected_text():
    from unittest.mock import MagicMock
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk
    from pdfatlas.ui.window import MainWindow

    win = MagicMock(spec=MainWindow)
    win.entry = Gtk.SearchEntry()
    win.run_search = MagicMock()
    win.get_active_doc_view = MagicMock()

    mock_doc_view = MagicMock()
    mock_canvas = MagicMock()
    mock_sel = MagicMock()
    mock_sel.has_selection.return_value = True
    mock_sel.get_selected_text.return_value = "quantum\n  entanglement"
    mock_canvas.text_selection = mock_sel
    mock_doc_view.canvas = mock_canvas
    win.get_active_doc_view.return_value = mock_doc_view

    MainWindow.focus_search(win)

    assert win.entry.get_text() == "quantum entanglement"
    win.run_search.assert_called_once_with("quantum entanglement")


def test_focus_search_without_selection():
    from unittest.mock import MagicMock
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk
    from pdfatlas.ui.window import MainWindow

    win = MagicMock(spec=MainWindow)
    win.entry = Gtk.SearchEntry()
    win.entry.set_text("existing query")
    win.run_search = MagicMock()
    win.get_active_doc_view = MagicMock()

    mock_doc_view = MagicMock()
    mock_canvas = MagicMock()
    mock_sel = MagicMock()
    mock_sel.has_selection.return_value = False
    mock_canvas.text_selection = mock_sel
    mock_doc_view.canvas = mock_canvas
    win.get_active_doc_view.return_value = mock_doc_view

    MainWindow.focus_search(win)

    # Should keep existing query and not trigger new run_search
    assert win.entry.get_text() == "existing query"
    win.run_search.assert_not_called()


