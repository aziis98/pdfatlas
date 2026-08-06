#!/usr/bin/env python
"""Benchmark main-thread GIL stalls during a 5-page prefetch burst.

Compares the old in-process threaded renderer against the new child-process
renderer by measuring the maximum wall-clock gap between consecutive
``time.perf_counter()`` reads of a busy main-thread loop while a burst of
pages is rasterized. In the threaded case the render thread re-acquires the
GIL in bursts (the "spiky lag"); in the child-process case it cannot.

Usage:
    uv run python scripts/benchmark_render.py [pdf_path]
"""

import multiprocessing
import os
import sys
import threading
import time

import numpy as np

from pdfatlas.core.render_child import child_main

SCALE = 1.0
BURST = 5
DURATION = 3.0
THRESHOLD_MS = 5.0
NOTABLE_MS = 0.5


def measure_main_thread_gaps(duration: float):
    """Busy-loop on the calling (main) thread for ``duration`` seconds.

    Tracks only notable gaps (>= NOTABLE_MS) to keep memory bounded; returns
    (max_gap_ms, count_of_hitches_at_or_above_threshold).
    """
    max_gap = 0.0
    hitches = 0
    end = time.perf_counter() + duration
    last = time.perf_counter()
    while time.perf_counter() < end:
        now = time.perf_counter()
        gap_ms = (now - last) * 1000.0
        if gap_ms >= NOTABLE_MS:
            if gap_ms > max_gap:
                max_gap = gap_ms
            if gap_ms >= THRESHOLD_MS:
                hitches += 1
        last = now
    return max_gap, hitches


def bgra_from_rgb(arr: np.ndarray) -> np.ndarray:
    """Contiguous BGRA buffer (identical cost to the real pipeline)."""
    h, w, _ = arr.shape
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, :3] = arr[:, :, ::-1]
    bgra[:, :, 3] = 255
    return bgra


def threaded_burst(pdf_path: str):
    """Old architecture: a background thread rasterizes 5 pages in-process."""
    import cairo
    import fitz

    doc = fitz.open(pdf_path)

    def work():
        for i in range(BURST):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
            cairo.ImageSurface.create_for_data(
                bgra_from_rgb(arr), cairo.FORMAT_RGB24, pix.width, pix.height, pix.width * 4
            )

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    max_gap, hitches = measure_main_thread_gaps(DURATION)
    thread.join(timeout=120)
    doc.close()
    return max_gap, hitches


def child_process_burst(pdf_path: str):
    """New architecture: a child process rasterizes the burst; the parent only
    spins and drains raw bytes (never blocked by PyMuPDF GIL bursts)."""
    ctx = multiprocessing.get_context("spawn")
    jobs_q = ctx.Queue(maxsize=8)
    results_q = ctx.Queue(maxsize=2)
    proc = ctx.Process(target=child_main, args=(jobs_q, results_q), daemon=True)
    proc.start()

    for i in range(BURST):
        jobs_q.put({"op": "render", "seq": i, "filepath": pdf_path, "page": i, "scale": SCALE, "clip": None})

    stop = threading.Event()

    def drain():
        received = 0
        while not stop.is_set() and received < BURST:
            try:
                msg = results_q.get(timeout=0.5)
            except Exception:
                continue
            if msg and msg.get("kind") == "render_result":
                received += 1

    consumer = threading.Thread(target=drain, daemon=True)
    consumer.start()
    max_gap, hitches = measure_main_thread_gaps(DURATION)
    stop.set()
    consumer.join(timeout=10)
    jobs_q.put("shutdown", timeout=1.0)
    proc.join(timeout=5)
    if proc.is_alive():
        proc.terminate()
    return max_gap, hitches


def summarize(name: str, max_gap: float, hitches: int) -> None:
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
    print(f"burst={BURST} pages at scale={SCALE}, measuring {DURATION:.1f}s main-thread tick gaps\n")

    max_gap, hitches = threaded_burst(pdf_path)
    summarize("threaded (old)", max_gap, hitches)

    max_gap, hitches = child_process_burst(pdf_path)
    summarize("child-process (new)", max_gap, hitches)

    return 0


if __name__ == "__main__":
    sys.exit(main())
