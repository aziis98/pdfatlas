import time
from pathlib import Path

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
