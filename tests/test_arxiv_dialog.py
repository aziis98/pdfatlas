from unittest.mock import MagicMock
from pdfatlas.core.pdf_source import PdfSource, RecentFilesManager
from pdfatlas.ui.arxiv_dialog import ArxivDialog, clean_arxiv_title


def test_clean_arxiv_title():
    raw = "A \\textbf{Deep}   -- \\textit{Learning} --- Approach"
    cleaned = clean_arxiv_title(raw)
    assert cleaned == "A Deep – Learning — Approach"


def test_arxiv_dialog_fetch_cached_paper(tmp_path, monkeypatch):
    import pdfatlas.ui.arxiv_dialog as dialog_mod

    cache_root = tmp_path / "cache" / "source-arxiv"
    monkeypatch.setattr(dialog_mod, "ARXIV_CACHE_ROOT", cache_root)

    aid = "2305.12345"
    paper_dir = cache_root / aid
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-cached")

    recent_mgr = RecentFilesManager(path=tmp_path / "recent.json")
    recent_mgr._entries.append(PdfSource(source_type="arxiv", uri=str(pdf_path), display_name="Cached Title"))

    on_source_mock = MagicMock()
    dialog = ArxivDialog(parent_window=None, on_source=on_source_mock, recent_files=recent_mgr)

    dialog.entry.set_text(aid)
    dialog._on_fetch()

    on_source_mock.assert_called_once()
    source_arg = on_source_mock.call_args[0][0]
    assert isinstance(source_arg, PdfSource)
    assert source_arg.uri == str(pdf_path)
    assert source_arg.display_name == "Cached Title"


def test_arxiv_dialog_fetch_uncached_paper(tmp_path, monkeypatch):
    import pdfatlas.ui.arxiv_dialog as dialog_mod

    cache_root = tmp_path / "cache" / "source-arxiv"
    monkeypatch.setattr(dialog_mod, "ARXIV_CACHE_ROOT", cache_root)

    aid = "2401.00001"
    recent_mgr = RecentFilesManager(path=tmp_path / "recent.json")

    on_source_mock = MagicMock()
    dialog = ArxivDialog(parent_window=None, on_source=on_source_mock, recent_files=recent_mgr)

    dialog.entry.set_text(f"https://arxiv.org/abs/{aid}")
    dialog._on_fetch()

    on_source_mock.assert_called_once()
    source_arg = on_source_mock.call_args[0][0]
    assert isinstance(source_arg, PdfSource)
    assert source_arg.uri == str(cache_root / aid / "paper.pdf")
    assert source_arg.display_name == f"arXiv:{aid}"


def test_arxiv_dialog_invalid_input(tmp_path):
    recent_mgr = RecentFilesManager(path=tmp_path / "recent.json")
    on_source_mock = MagicMock()
    dialog = ArxivDialog(parent_window=None, on_source=on_source_mock, recent_files=recent_mgr)

    dialog.entry.set_text("not-a-valid-arxiv-id")
    dialog._on_fetch()

    on_source_mock.assert_not_called()
    assert dialog.status_label.get_text() == "Invalid arXiv ID or URL"
    assert dialog.status_label.get_visible() is True
