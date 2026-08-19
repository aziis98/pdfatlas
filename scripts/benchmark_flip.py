#!/usr/bin/env python
"""Benchmark the main-thread GL texture-upload stall during page flips.

Compares two texture-upload strategies on a virtual/software GL display:

  * synchronous: ``glTexImage2D`` runs on the main thread inside the render
    pass (the pre-``TextureUploader`` behavior) — every flip blocks the UI
    thread for the full pixel-copy time.
  * async: uploads + eviction run on a worker thread with a shared GL
    context (``TextureUploader``) — the main thread only queues work and
    should never stall.

Run under a virtual display backed by a software GL implementation:

    xvfb-run -s "-screen 0 1600x1200x24" uv run python scripts/benchmark_flip.py [pdf_path]

Usage:
    uv run python scripts/benchmark_flip.py [pdf_path]
"""

import os
import sys
import time

from benchmark_render import measure_main_thread_gaps

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from pdfatlas.core.cache import RenderCache
from pdfatlas.core.document import DocumentModel
from pdfatlas.core.renderer import RenderWorker
from pdfatlas.ui.texture_uploader import TextureUploader

BURST = 5
GAP_DURATION = 2.0
THRESHOLD_MS = 5.0
NOTABLE_MS = 0.5


def rasterize(pdf_path: str, count: int) -> list:
    """Rasterize the first ``count`` pages via the child-process renderer."""
    worker = RenderWorker()
    doc = DocumentModel(pdf_path)
    cache = RenderCache(count + 4)
    worker.set_document(pdf_path)

    def bump():
        pass

    for i in range(count):
        worker.queue_render_job(0, doc, i, 1.0, 1, None, False, cache, bump)

    deadline = time.perf_counter() + 60.0
    while cache.total_entries() < count and time.perf_counter() < deadline:
        time.sleep(0.02)

    worker.shutdown()
    doc.close()

    textures = [cache.get(i, 1.0, 1, None) for i in range(count)]
    return [t for t in textures if t is not None]


def make_context():
    Gtk.init_check()
    display = Gdk.Display.get_default()
    if display is None:
        return None
    ctx = display.create_gl_context()
    ctx.set_required_version(3, 3)
    ctx.realize()
    return ctx


def sync_upload_bench(textures) -> tuple[float, int] | None:
    """Old path: upload every texture on the main thread, timing each one."""
    uploader = TextureUploader()
    ctx = make_context()
    if ctx is None:
        return None
    ctx.make_current()
    try:
        per_page = []
        for tex in textures:
            t0 = time.perf_counter()
            uploader.request_upload(tex)
            per_page.append((time.perf_counter() - t0) * 1000.0)
        max_gap = max(per_page, default=0.0)
        hitches = sum(1 for ms in per_page if ms >= THRESHOLD_MS)
        return max_gap, hitches
    finally:
        ctx.clear_current()


def async_upload_bench(textures) -> tuple[float, int] | None:
    """New path: worker-thread uploads while the main thread busy-spins."""
    uploader = TextureUploader()
    ctx = make_context()
    if ctx is None:
        return None
    uploader._start_worker(ctx)
    try:
        for tex in textures:
            uploader.request_upload(tex)
        max_gap, hitches = measure_main_thread_gaps(GAP_DURATION)
        deadline = time.perf_counter() + 30.0
        while len(uploader.textures()) < len(textures) and time.perf_counter() < deadline:
            time.sleep(0.005)
        return max_gap, hitches
    finally:
        uploader.shutdown()


def summarize(name: str, result) -> None:
    if result is None:
        print(f"[{name}] skipped (no GL display — run under xvfb-run)")
        return
    max_gap, hitches = result
    verdict = "PASS" if max_gap < THRESHOLD_MS else "FAIL"
    print(f"[{name}] max={max_gap:.2f}ms  hitches>=5ms={hitches}  ->  {verdict} "
          f"(max must be < {THRESHOLD_MS:.1f}ms)")


def main() -> int:
    pdf_path = os.path.expanduser(
        sys.argv[1] if len(sys.argv) > 1 else "~/Documents/Academia/Calcolo-Tensoriale/appunti.pdf"
    )
    if not os.path.exists(pdf_path):
        print(f"error: PDF not found: {pdf_path}")
        return 1

    print(f"Benchmarking against: {pdf_path}")
    textures = rasterize(pdf_path, BURST)
    if not textures:
        print("error: no pages were rasterized")
        return 1
    sizes = ", ".join(f"{t.width}x{t.height}" for t in textures[:3])
    print(f"rasterized {len(textures)} pages ({sizes}, ...)\n")

    summarize("sync (main-thread upload)", sync_upload_bench(textures))
    summarize("async (TextureUploader worker)", async_upload_bench(textures))

    return 0


if __name__ == "__main__":
    sys.exit(main())
