import time
from pathlib import Path

import pytest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from pdfatlas.core.cache import RenderCache
from pdfatlas.core.document import DocumentModel
from pdfatlas.core.renderer import RenderWorker
from pdfatlas.core.texture import PageTexture

SAMPLE_PDF = (
    Path(__file__).resolve().parents[1]
    / "sandbox.local"
    / "sample-files"
    / "attention_is_all_you_need.pdf"
)


def test_create_worker():
    worker = RenderWorker()
    assert isinstance(worker, RenderWorker)
    assert len(worker._children) == 2
    assert all(c.is_alive() for c in worker._children)
    worker.shutdown()


def test_render_worker_spawns_requested_children():
    worker = RenderWorker(num_workers=3)
    assert len(worker._children) == 3
    assert all(c.is_alive() for c in worker._children)
    worker.shutdown()


def _render_all_pages(num_workers: int, page_count: int, use_shm: bool = True):
    """Queues a render job per page and waits for all callbacks to fire."""
    doc = DocumentModel(str(SAMPLE_PDF))
    cache = RenderCache(page_count * 2)
    worker = RenderWorker(num_workers=num_workers, use_shm=use_shm)
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
def test_parallel_renders_all_pages(num_workers):
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF not available")
    doc = DocumentModel(str(SAMPLE_PDF))
    page_count = min(doc.page_count, 6)
    doc.close()

    count, cache = _render_all_pages(num_workers, page_count)
    assert count == page_count, f"{count}/{page_count} render callbacks fired"
    for i in range(page_count):
        assert isinstance(cache.get(i, 1.0, 1, None), PageTexture)


def test_shm_renders_page_texture():
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF not available")
    doc = DocumentModel(str(SAMPLE_PDF))
    page_count = min(doc.page_count, 4)
    cache = RenderCache(4)
    worker = RenderWorker(num_workers=2, use_shm=True)
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

    assert done["count"] == page_count
    for i in range(page_count):
        assert isinstance(cache.get(i, 1.0, 1, None), PageTexture)
