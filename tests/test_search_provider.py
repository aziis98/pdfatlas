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
