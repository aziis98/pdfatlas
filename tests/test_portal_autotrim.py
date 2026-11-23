from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pymupdf as fitz

from pdfatlas.core.settings import CropSettings
from pdfatlas.ui.portal import (
    ResultRow,
    _page_trim_cache,
    get_page_horizontal_trim,
    render_strip_surface,
)


def _create_margin_pdf(path: Path, x0: float, x1: float) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    # Draw dark content bar across [x0, x1] at y=200..220
    page.draw_rect(fitz.Rect(x0, 200, x1, 220), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


def test_search_autotrim_setting_default_and_persistence(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    s = CropSettings()
    assert s.search_autotrim is True
    s.search_autotrim = False
    s.save(cfg)

    loaded = CropSettings.load(cfg)
    assert loaded.search_autotrim is False


def test_get_page_horizontal_trim_normal_and_sparse(tmp_path: Path):
    _page_trim_cache.clear()
    pdf_normal = _create_margin_pdf(tmp_path / "normal.pdf", 120.0, 480.0)
    doc = fitz.open(str(pdf_normal))
    x0, x1 = get_page_horizontal_trim(str(pdf_normal), 1, doc[0])
    doc.close()
    # Content is [120, 480] -> 360pt (+ 16pt padding = 376pt >= 360pt floor), strictly narrower than 600pt
    assert 100.0 <= x0 <= 120.0
    assert 480.0 <= x1 <= 500.0
    assert (x1 - x0) < 420.0

    # Sparse narrow content [270, 330] (60pt wide) must expand to >= 60% of 600pt (= 360pt)
    pdf_sparse = _create_margin_pdf(tmp_path / "sparse.pdf", 270.0, 330.0)
    doc_s = fitz.open(str(pdf_sparse))
    sx0, sx1 = get_page_horizontal_trim(str(pdf_sparse), 1, doc_s[0])
    doc_s.close()
    assert (sx1 - sx0) >= 359.0


def test_render_strip_surface_autotrim_enlarges_content(tmp_path: Path):
    _page_trim_cache.clear()
    pdf_path = _create_margin_pdf(tmp_path / "strip.pdf", 120.0, 480.0)

    surf_trim, _ = render_strip_surface(
        str(pdf_path), 1, 120.0, 200.0, 480.0, 220.0, set(), target_w=360, target_h=120, autotrim=True
    )
    surf_full, _ = render_strip_surface(
        str(pdf_path), 1, 120.0, 200.0, 480.0, 220.0, set(), target_w=360, target_h=120, autotrim=False
    )

    def _dark_col_span(surface) -> int:
        arr = np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(
            (surface.get_height(), surface.get_width(), 4)
        )
        mid_row = surface.get_height() // 2
        row = arr[mid_row, 10:-10]  # exclude 1px border stroke
        dark = (row[:, 3] > 200) & np.all(row[:, :3] < 100, axis=1)
        idx = np.where(dark)[0]
        return int(idx[-1] - idx[0]) if len(idx) else 0

    # Trimmed surface crops out outer white margins, so content spans many more pixels horizontally
    assert _dark_col_span(surf_trim) > _dark_col_span(surf_full) * 1.25


def test_result_row_autotrim_flag():
    mock_exec = MagicMock()
    res = {"page": 1, "x0": 100.0, "y0": 100.0, "x1": 500.0, "y1": 160.0}
    r_trim = ResultRow("/tmp/fake.pdf", mock_exec, res, set(), autotrim=True)
    r_full = ResultRow("/tmp/fake.pdf", mock_exec, res, set(), autotrim=False)
    assert r_trim.autotrim is True
    assert r_full.autotrim is False
    assert r_trim.card_h >= r_full.card_h
