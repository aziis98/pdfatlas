import time
from pathlib import Path
from typing import cast

import pytest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from pdfatlas.core.cache import RenderCache
from pdfatlas.core.document import DocumentModel
from pdfatlas.core.renderer import RenderWorker, create_render_worker
from pdfatlas.core.renderer_mt import RenderWorkerMT
from pdfatlas.core.texture import PageTexture

SAMPLE_PDF = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "sample-files"
    / "attention_is_all_you_need.pdf"
)


def test_create_mp_worker():
    worker = create_render_worker("mp")
    assert isinstance(worker, RenderWorker)
    worker.shutdown()


def test_create_mt_worker():
    worker = create_render_worker("mt")
    assert isinstance(worker, RenderWorkerMT)
    worker.shutdown()


def test_invalid_render_mode_raises():
    with pytest.raises(ValueError):
        create_render_worker("bogus")


def test_mt_renders_page_texture():
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF not available")
    doc = DocumentModel(str(SAMPLE_PDF))
    cache = RenderCache(4)
    worker = create_render_worker("mt")
    worker.set_document(str(SAMPLE_PDF))

    done = {"fired": False}

    def redraw():
        done["fired"] = True

    worker.queue_render_job(0, doc, 0, 1.0, 1, None, False, cache, redraw)

    ctx = GLib.MainContext.default()
    deadline = time.perf_counter() + 15.0
    while not done["fired"] and time.perf_counter() < deadline:
        ctx.iteration(False)

    worker.shutdown()
    doc.close()

    assert done["fired"], "render callback never fired"
    texture = cache.get(0, 1.0, 1, None)
    assert isinstance(texture, PageTexture)
    assert texture.channels == 3
    assert texture.width > 0 and texture.height > 0
    assert texture.byte_size == texture.width * texture.height * texture.channels


def _render_all_pages(mode: str, num_workers: int, page_count: int):
    """Queues a render job per page and waits for all callbacks to fire."""
    doc = DocumentModel(str(SAMPLE_PDF))
    cache = RenderCache(page_count * 2)
    worker = create_render_worker(mode, num_workers=num_workers)
    worker.set_document(str(SAMPLE_PDF))

    done = {"count": 0}

    def redraw():
        done["count"] += 1

    for i in range(page_count):
        worker.queue_render_job(0, doc, i, 1.0, 1, None, False, cache, redraw)

    ctx = GLib.MainContext.default()
    deadline = time.perf_counter() + 30.0
    while done["count"] < page_count and time.perf_counter() < deadline:
        ctx.iteration(False)

    worker.shutdown()
    doc.close()
    return done["count"], cache


@pytest.mark.parametrize("num_workers", [2, 4])
def test_mp_parallel_renders_all_pages(num_workers):
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF not available")
    doc = DocumentModel(str(SAMPLE_PDF))
    page_count = min(doc.page_count, 6)
    doc.close()

    count, cache = _render_all_pages("mp", num_workers, page_count)
    assert count == page_count, f"{count}/{page_count} render callbacks fired"
    for i in range(page_count):
        assert isinstance(cache.get(i, 1.0, 1, None), PageTexture)


@pytest.mark.parametrize("num_workers", [2, 4])
def test_mt_parallel_renders_all_pages(num_workers):
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF not available")
    doc = DocumentModel(str(SAMPLE_PDF))
    page_count = min(doc.page_count, 6)
    doc.close()

    count, cache = _render_all_pages("mt", num_workers, page_count)
    assert count == page_count, f"{count}/{page_count} render callbacks fired"
    for i in range(page_count):
        assert isinstance(cache.get(i, 1.0, 1, None), PageTexture)


def test_render_worker_spawns_requested_children():
    worker = cast(RenderWorker, create_render_worker("mp", num_workers=3))
    assert len(worker._children) == 3
    assert all(c.is_alive() for c in worker._children)
    worker.shutdown()
