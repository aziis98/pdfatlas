from contextlib import ExitStack
from itertools import count
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch
import pytest

from pdfatlas.controllers.document_loader import (
    download_remote_pdf,
    get_cached_url_path,
)
from pdfatlas.core.pdf_source import PdfSource, RecentFilesManager
from pdfatlas.ui.welcome import _shorten_path

_APP_IDS = count()


def _make_window(tmp_path):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id=f"com.example.testurlsource{next(_APP_IDS)}")
    app.register(None)
    win = MainWindow(app)
    win.recent_files._path = tmp_path / "recent.json"
    win.recent_files.clear()
    return win


def _patch_collaborators(win) -> ExitStack:
    doc_mock = MagicMock()
    doc_mock.doc.metadata = {"title": "Attention Is All You Need"}
    doc_mock.page_count = 1
    doc_mock.filepath = "/tmp/fake.pdf"
    doc_mock.page_rect = MagicMock(return_value=MagicMock(width=612, height=792, y0=0, x0=0, x1=612, y1=792))
    doc_mock.get_page_links = MagicMock(return_value=[])
    doc_mock.get_toc = MagicMock(return_value=[])
    stack = ExitStack()
    stack.enter_context(patch("pdfatlas.core.document.DocumentModel", return_value=doc_mock))
    stack.enter_context(patch("pdfatlas.ui.window.DocumentModel", return_value=doc_mock))
    stack.enter_context(patch("pdfatlas.core.crop.CropAnalyzer", return_value=MagicMock()))
    stack.enter_context(patch("pdfatlas.ui.document_view.CropAnalyzer", return_value=MagicMock()))
    stack.enter_context(patch("pdfatlas.ui.window.CropAnalyzer", return_value=MagicMock()))
    stack.enter_context(patch("pdfatlas.ui.canvas.PDFCanvas.set_document", MagicMock()))
    stack.enter_context(patch("pdfatlas.ui.notes.NotesLayer.prepare", MagicMock()))
    stack.enter_context(patch.object(win, "_save_current_doc_state", MagicMock()))
    stack.enter_context(patch.object(win, "_arxiv_diff_worker", MagicMock()))
    stack.enter_context(patch.object(win, "render_worker", MagicMock()))
    stack.enter_context(patch.object(win.db_service, "open_db", MagicMock()))

    def _immediate_idle_add(func, *args, **kwargs):
        func(*args, **kwargs)
        return False

    stack.enter_context(patch("pdfatlas.controllers.document_loader.GLib.idle_add", side_effect=_immediate_idle_add))
    stack.enter_context(patch("pdfatlas.ui.window.GLib.idle_add", side_effect=_immediate_idle_add))
    return stack


def test_url_source_cache_path_generation():
    url1 = "https://www.aclweb.org/anthology/D17-1151.pdf"
    path1 = get_cached_url_path(url1)
    assert path1.name == "D17-1151.pdf"
    assert "pdfatlas/urls" in str(path1)

    url2 = "https://example.org/download?id=12345"
    path2 = get_cached_url_path(url2)
    assert path2.name.endswith(".pdf")
    assert path2.name.startswith("document_")


def test_download_remote_pdf_success(tmp_path):
    dest = tmp_path / "paper.pdf"
    url = "https://example.com/test.pdf"

    class FakeResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "14"}

        def __init__(self):
            self._chunks = [b"%PDF-1.5 test!"]

        def read(self, size):
            return self._chunks.pop(0) if self._chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    progress_calls = []

    def on_progress(frac, msg):
        progress_calls.append((frac, msg))

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        download_remote_pdf(url, dest, progress_callback=on_progress)

    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-1.5 test!"
    assert len(progress_calls) > 0


def test_download_remote_pdf_invalid_header(tmp_path):
    dest = tmp_path / "blocked.pdf"
    url = "https://example.com/paywall"

    class FakeHtmlResponse:
        headers = {"Content-Type": "text/html"}

        def __init__(self):
            self._chunks = [b"<!DOCTYPE html><html>Paywall</html>"]

        def read(self, size):
            return self._chunks.pop(0) if self._chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeHtmlResponse()):
        with pytest.raises(ValueError, match="did not return a valid PDF"):
            download_remote_pdf(url, dest)

    assert not dest.exists()


def test_url_source_open_document_download(tmp_path, monkeypatch):
    import threading
    import pdfatlas.controllers.document_loader as loader_mod

    win = _make_window(tmp_path)
    url = "https://www.aclweb.org/anthology/D17-1151.pdf"
    cache_root = tmp_path / "cache_urls"
    monkeypatch.setattr(loader_mod, "URL_CACHE_ROOT", cache_root)

    source = PdfSource(
        source_type="url",
        uri=url,
        display_name="Massive Exploration of NMT",
        fallback_uri="https://doi.org/10.18653/v1/d17-1151",
    )

    cached_file = loader_mod.get_cached_url_path(url)

    def mock_download(url_arg, dest_path, progress_callback=None, **kwargs):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"%PDF-1.5 fake paper content")
        if progress_callback:
            progress_callback(1.0, "Done")

    monkeypatch.setattr(loader_mod, "download_remote_pdf", mock_download)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())

    with _patch_collaborators(win):
        win.open_document(source)

        cast(MagicMock, win.render_worker).set_document.assert_called_once_with(str(cached_file))
        assert win.current_source is not None
        assert win.current_source.source_type == "url"
        assert win.current_source.uri == url
        assert win.current_source.fallback_uri == "https://doi.org/10.18653/v1/d17-1151"


def test_url_source_open_document_already_cached(tmp_path, monkeypatch):
    import pdfatlas.controllers.document_loader as loader_mod

    win = _make_window(tmp_path)
    url = "https://www.aclweb.org/anthology/D17-1151.pdf"
    cache_root = tmp_path / "cache_urls"
    monkeypatch.setattr(loader_mod, "URL_CACHE_ROOT", cache_root)

    cached_file = loader_mod.get_cached_url_path(url)
    cached_file.parent.mkdir(parents=True, exist_ok=True)
    cached_file.write_bytes(b"%PDF-1.5 already downloaded")

    source = PdfSource(
        source_type="url",
        uri=url,
        display_name="Massive Exploration of NMT",
    )

    with _patch_collaborators(win):
        with patch.object(loader_mod, "download_remote_pdf") as mock_dl:
            win.open_document(source)
            mock_dl.assert_not_called()

        assert win.current_source is not None
        assert win.current_source.uri == url


def test_url_source_fallback_to_doi_on_error(tmp_path, monkeypatch):
    import threading
    import pdfatlas.controllers.document_loader as loader_mod
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    win = _make_window(tmp_path)
    url = "https://example.com/missing.pdf"
    doi = "https://doi.org/10.1234/missing"

    source = PdfSource(
        source_type="url",
        uri=url,
        display_name="Missing Paper",
        fallback_uri=doi,
    )

    def mock_download_fail(*args, **kwargs):
        raise ValueError("404 Not Found")

    monkeypatch.setattr(loader_mod, "download_remote_pdf", mock_download_fail)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())

    with _patch_collaborators(win):
        with patch.object(Gtk, "show_uri") as mock_show_uri:
            win.open_document(source)
            mock_show_uri.assert_called_once()
            call_args = mock_show_uri.call_args
            assert call_args[0][1] == doi


def test_recent_files_manager_with_url():
    recents_file = Path("/tmp/test_recents_url.json")
    if recents_file.exists():
        recents_file.unlink()

    mgr = RecentFilesManager(path=recents_file)
    src = PdfSource(
        source_type="url",
        uri="https://aclweb.org/anthology/paper.pdf",
        display_name="ACL Paper",
        fallback_uri="https://doi.org/10.18653/v1/paper",
    )
    mgr.add(src)

    loaded = mgr.get_by_uri("https://aclweb.org/anthology/paper.pdf")
    assert loaded is not None
    assert loaded.source_type == "url"
    assert loaded.uri == "https://aclweb.org/anthology/paper.pdf"
    assert loaded.display_name == "ACL Paper"
    assert loaded.fallback_uri == "https://doi.org/10.18653/v1/paper"

    # Re-instantiate from disk
    mgr2 = RecentFilesManager(path=recents_file)
    loaded2 = mgr2.get_by_uri("https://aclweb.org/anthology/paper.pdf")
    assert loaded2 is not None
    assert loaded2.fallback_uri == "https://doi.org/10.18653/v1/paper"

    if recents_file.exists():
        recents_file.unlink()


def test_resolve_source_url(tmp_path):
    from pdfatlas.main import PDFViewerApplication

    app = PDFViewerApplication(application_id="com.example.testresolvesrc")
    url = "https://www.aclweb.org/anthology/D17-1151.pdf"
    resolved = app._resolve_source(None, url)

    assert resolved.source_type == "url"
    assert resolved.uri == url
    assert resolved.display_name == "D17-1151.pdf"
    assert resolved.is_url is True
    assert resolved.is_arxiv is False


def test_welcome_shorten_path_and_url():
    assert _shorten_path("https://example.com/paper.pdf") == "https://example.com/paper.pdf"
    assert _shorten_path("http://example.com/paper.pdf") == "http://example.com/paper.pdf"
