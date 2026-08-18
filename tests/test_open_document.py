"""Tests for arXiv-source resolution priority in MainWindow.open_document."""

from contextlib import ExitStack
from itertools import count
from typing import cast
from unittest.mock import MagicMock, patch

_APP_IDS = count()


def _make_window(tmp_path):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id=f"com.example.testopendoc{next(_APP_IDS)}")
    app.register(None)
    win = MainWindow(app)
    # Isolate recent files into a temp store.
    win.recent_files._path = tmp_path / "recent.json"
    win.recent_files.clear()
    return win


def _patch_collaborators(win) -> ExitStack:
    """Patch the heavy/networkful collaborators used by open_document."""
    doc_mock = MagicMock()
    doc_mock.doc.metadata = None
    stack = ExitStack()
    stack.enter_context(patch("pdfatlas.ui.window.DocumentModel", return_value=doc_mock))
    stack.enter_context(patch("pdfatlas.ui.window.CropAnalyzer", return_value=MagicMock()))
    stack.enter_context(patch.object(win, "_arxiv_diff_worker", MagicMock()))
    stack.enter_context(patch.object(win.canvas, "set_document", MagicMock()))
    stack.enter_context(patch.object(win.notes_layer, "prepare", MagicMock()))
    stack.enter_context(patch.object(win, "render_worker", MagicMock()))
    stack.enter_context(patch.object(win.db_service, "open_db", MagicMock()))
    return stack


def test_arxiv_recent_with_missing_path_resolves_to_download(tmp_path, monkeypatch):
    from pdfatlas.core.pdf_source import PdfSource
    import pdfatlas.core.arxiv_mapper as arxiv_mod

    win = _make_window(tmp_path)
    # Simulate the stale recents entry (arxiv source, local path gone).
    source = PdfSource(
        source_type="arxiv",
        uri=str(tmp_path / "gone" / "2507.09369v1.pdf"),
        display_name="A Taxonomy of Omnicidal Futures",
    )
    win.recent_files.add(source)

    fake_pdf = tmp_path / "downloaded.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(
        arxiv_mod, "download_arxiv_source", lambda aid: (fake_pdf, tmp_path)
    )

    with _patch_collaborators(win):
        win.open_document(source)

        # The arxiv cache path is the file actually opened, not the stale local one.
        cast(MagicMock, win.render_worker).set_document.assert_called_once_with(str(fake_pdf))
        assert win.current_source is not None
        assert win.current_source.uri == str(fake_pdf)
        assert win.current_source.display_name == "A Taxonomy of Omnicidal Futures"


def test_existing_plain_file_with_arxiv_like_name_keeps_local(tmp_path, monkeypatch):
    import pdfatlas.core.arxiv_mapper as arxiv_mod
    from pdfatlas.core.pdf_source import PdfSource

    win = _make_window(tmp_path)
    local = tmp_path / "2305.12345-notes.pdf"
    local.write_bytes(b"%PDF-local")
    source = PdfSource(source_type="file", uri=str(local), display_name="2305.12345-notes.pdf")

    download_mock = MagicMock(side_effect=AssertionError("must not hit network"))
    monkeypatch.setattr(arxiv_mod, "download_arxiv_source", download_mock)

    with _patch_collaborators(win):
        win.open_document(source)

        cast(MagicMock, win.render_worker).set_document.assert_called_once_with(str(local))
        assert win.current_source is not None
        assert win.current_source.uri == str(local)
        download_mock.assert_not_called()


def test_offline_fallback_opens_local_pdf(tmp_path, monkeypatch):
    import pdfatlas.core.arxiv_mapper as arxiv_mod
    from pdfatlas.core.pdf_source import PdfSource

    win = _make_window(tmp_path)
    local = tmp_path / "2603.20268v1.pdf"
    local.write_bytes(b"%PDF-local-arxiv")
    source = PdfSource(source_type="arxiv", uri=str(local), display_name="2603.20268v1.pdf")

    # Simulate network down
    download_mock = MagicMock(side_effect=OSError("Network unreachable"))
    monkeypatch.setattr(arxiv_mod, "download_arxiv_source", download_mock)

    with _patch_collaborators(win):
        win.open_document(source)

        cast(MagicMock, win.render_worker).set_document.assert_called_once_with(str(local))
        assert win.current_source is not None
        assert win.current_source.uri == str(local)


def test_cached_arxiv_paper_opens_when_offline(tmp_path, monkeypatch):
    import urllib.request
    import pdfatlas.core.arxiv_mapper as arxiv_mod
    from pdfatlas.core.pdf_source import PdfSource

    win = _make_window(tmp_path)
    aid = "2603.20268v1"
    cache_root = tmp_path / "cache" / "source-arxiv"
    cache_pdf = cache_root / aid / "paper.pdf"
    cache_pdf.parent.mkdir(parents=True, exist_ok=True)
    cache_pdf.write_bytes(b"%PDF-cached")

    monkeypatch.setattr(arxiv_mod, "ARXIV_CACHE_ROOT", cache_root)

    source = PdfSource(source_type="arxiv", uri=f"arxiv:{aid}", display_name=f"arXiv:{aid}")

    # Mock urlopen to simulate offline error if anything attempts network access
    urlopen_mock = MagicMock(side_effect=OSError("Network unreachable"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen_mock)

    with _patch_collaborators(win):
        win.open_document(source)

        cast(MagicMock, win.render_worker).set_document.assert_called_once_with(str(cache_pdf))
        assert win.current_source is not None
        assert win.current_source.uri == str(cache_pdf)