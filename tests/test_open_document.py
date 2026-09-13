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

    stack.enter_context(patch("pdfatlas.ui.window.GLib.idle_add", side_effect=_immediate_idle_add))
    return stack


def test_arxiv_recent_with_missing_path_resolves_to_download(tmp_path, monkeypatch):
    import threading
    from pdfatlas.core.pdf_source import PdfSource
    import pdfatlas.core.arxiv_mapper as arxiv_mod

    win = _make_window(tmp_path)
    aid = "9999.99999v1"
    cache_root = tmp_path / "cache" / "source-arxiv"
    fake_pdf = cache_root / aid / "paper.pdf"
    monkeypatch.setattr(arxiv_mod, "ARXIV_CACHE_ROOT", cache_root)

    source = PdfSource(
        source_type="arxiv",
        uri=str(tmp_path / "gone" / f"{aid}.pdf"),
        display_name="A Taxonomy of Omnicidal Futures",
    )
    win.recent_files.add(source)

    def mock_download(aid, download_pdf=True, progress_callback=None, **kwargs):
        fake_pdf.parent.mkdir(parents=True, exist_ok=True)
        fake_pdf.write_bytes(b"%PDF-fake")
        return fake_pdf, fake_pdf.parent

    monkeypatch.setattr(arxiv_mod, "download_arxiv_source", mock_download)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())

    with _patch_collaborators(win):
        win.open_document(source)

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


def test_uncached_arxiv_paper_async_download(tmp_path, monkeypatch):
    import threading
    import pdfatlas.core.arxiv_mapper as arxiv_mod
    from pdfatlas.core.pdf_source import PdfSource

    win = _make_window(tmp_path)
    aid = "2603.20268v1"
    cache_root = tmp_path / "cache" / "source-arxiv"
    cache_pdf = cache_root / aid / "paper.pdf"

    monkeypatch.setattr(arxiv_mod, "ARXIV_CACHE_ROOT", cache_root)

    source = PdfSource(source_type="arxiv", uri=f"arxiv:{aid}", display_name=f"arXiv:{aid}")

    started_threads = []

    def mock_start(self):
        started_threads.append(self)
        # Execute synchronously for test determinism
        self.run()

    monkeypatch.setattr(threading.Thread, "start", mock_start)

    def mock_download(aid, download_pdf=True, progress_callback=None, **kwargs):
        cache_pdf.parent.mkdir(parents=True, exist_ok=True)
        cache_pdf.write_bytes(b"%PDF-downloaded")
        if progress_callback:
            progress_callback(0.5, "Downloading...")
            progress_callback(1.0, "Done")
        return cache_pdf, cache_pdf.parent

    monkeypatch.setattr(arxiv_mod, "download_arxiv_source", mock_download)

    with _patch_collaborators(win):
        win.open_document(source)
        assert any("_download_worker" in str(t) for t in started_threads)
        assert cache_pdf.exists()


def test_cli_command_line_opens_in_new_window(tmp_path):
    from unittest.mock import MagicMock, patch
    from pdfatlas.main import PDFViewerApplication
    from pdfatlas.ui.window import MainWindow

    pdf1 = tmp_path / "doc1.pdf"
    pdf1.write_bytes(b"%PDF-1")
    pdf2 = tmp_path / "doc2.pdf"
    pdf2.write_bytes(b"%PDF-2")

    app = PDFViewerApplication(application_id=f"com.example.testdbuscmd{next(_APP_IDS)}")
    app.register(None)

    win = MainWindow(app)
    win.present()

    with patch.object(win, "open_document") as mock_open:
        # First invocation opens doc1 in the empty window
        cmd1 = MagicMock()
        cmd1.get_arguments.return_value = ["pdfatlas", str(pdf1)]
        cmd1.get_cwd.return_value = str(tmp_path)
        app.do_command_line(cmd1)

        assert mock_open.call_count == 1
        source1 = mock_open.call_args[0][0]
        assert source1.uri == str(pdf1)
        assert mock_open.call_args[1].get("new_tab") is False

    # Simulate that win now has an active document loaded
    win.doc_model = MagicMock()

    # Second invocation opens doc2 in a new window
    cmd2 = MagicMock()
    cmd2.get_arguments.return_value = ["pdfatlas", "doc2.pdf"]
    cmd2.get_cwd.return_value = str(tmp_path)

    with patch.object(MainWindow, "open_document") as mock_open_win:
        app.do_command_line(cmd2)
        assert mock_open_win.call_count == 1
        source2 = mock_open_win.call_args[0][0]
        assert source2.uri == str(pdf2)
        assert mock_open_win.call_args[1].get("new_tab") is False

    windows = [w for w in app.get_windows() if isinstance(w, MainWindow)]
    assert len(windows) == 2

    for w in windows:
        w.close()


def test_seed_initial_previews_populates_render_cache(tmp_path):
    import pymupdf as fitz
    from pdfatlas.core.cache import RenderCache
    from pdfatlas.core.document import DocumentModel
    from pdfatlas.core.texture import PageTexture
    from pdfatlas.ui.canvas import PDFCanvas

    pdf_path = tmp_path / "test_preview.pdf"
    doc = fitz.open()
    doc.new_page(width=400, height=600)
    doc.save(str(pdf_path))
    doc.close()

    doc_model = DocumentModel(str(pdf_path))
    cache = RenderCache(10)
    canvas = PDFCanvas()
    canvas.set_document(doc_model, cache, MagicMock())

    # Verify that initial low-res preview was seeded into cache
    best = cache.get_best(0, 1.0, 2, None)
    assert best is not None
    assert isinstance(best, PageTexture)
    assert best.width == 100
    assert best.height == 150


def test_last_open_dir_settings_persistence(tmp_path):
    from pdfatlas.core.settings import CropSettings

    settings_path = tmp_path / "settings.json"
    settings = CropSettings.load(settings_path)
    assert settings.last_open_dir is None

    test_dir = str(tmp_path / "my_docs")
    settings.last_open_dir = test_dir
    settings.save(settings_path)

    reloaded = CropSettings.load(settings_path)
    assert reloaded.last_open_dir == test_dir


def test_get_default_open_dir_priority(tmp_path):
    win = _make_window(tmp_path)
    settings_file = tmp_path / "settings.json"
    win.settings = win.settings.__class__.load(settings_file)

    # 1. When neither settings nor doc_model is set
    assert win._get_default_open_dir() is None

    # 2. When doc_model is set with a valid file
    doc_dir = tmp_path / "academic_papers"
    doc_dir.mkdir()
    doc_file = doc_dir / "paper.pdf"
    doc_file.write_text("fake pdf")

    mock_doc = MagicMock()
    mock_doc.filepath = str(doc_file)
    win.doc_model = mock_doc
    assert win._get_default_open_dir() == str(doc_dir)

    # 3. When settings.last_open_dir is set, it takes priority over doc_model
    custom_dir = tmp_path / "custom_folder"
    custom_dir.mkdir()
    win._record_last_open_dir(str(custom_dir))
    assert win._get_default_open_dir() == str(custom_dir)
    assert win.settings.last_open_dir == str(custom_dir)

    win.close()