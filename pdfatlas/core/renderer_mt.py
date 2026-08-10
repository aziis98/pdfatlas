"""
Multithreaded background render backend (``--render-mode mt``).

Rasterization runs on ``num_workers`` daemon threads inside the parent process.
PyMuPDF ``fitz.Document`` objects are not thread-safe to share, so each worker
thread lazily opens its own ``fitz.Document`` from the filepath, mirroring how
each ``mp`` child owns an independent copy. PyMuPDF re-acquires the GIL in
bursts during image decode/scaling, so long scans can still stall the GTK main
thread (see RESEARCH.md 1.16). Kept as a swappable alternative to the
multiprocessing backend for benchmarking and comparison.

The public API mirrors ``renderer.RenderWorker`` (the ``mp`` backend):
  queue_render_job / queue_crop_job / queue_portal_job /
  clear_canvas_render_jobs / set_document / shutdown

Page-canvas renders produce ``PageTexture`` raw RGB (no channel swap), while
minimap and portal paths still rebuild cairo surfaces from a BGRA buffer,
matching the ``mp`` backend's delivery behaviour.
"""

import queue
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
import cairo
import fitz
from gi.repository import GLib

from .renderer import bgra_from_rgb
from .settings import CropSettings
from .texture import PageTexture


class RenderWorkerMT:
    """
    Background rendering thread coordinator.

    Uses a priority queue shared by all worker threads to process:
      Priority 0: Visible canvas pages
      Priority 1: Canvas pages ±1
      Priority 2: Canvas pages ±2 / portal previews
      Priority 3: Minimap thumbnails
      Priority 4: Crop analysis scans
    """

    def __init__(self, num_workers: int = 2):
        self.queue = queue.PriorityQueue()
        self.counter = 0
        self.lock = threading.Lock()
        self._generation = 0
        self._active_filepath = None
        self._stop = threading.Event()
        self._thread_local = threading.local()

        self.num_workers = max(1, num_workers)
        self._threads = []
        for _ in range(self.num_workers):
            t = threading.Thread(target=self._run, daemon=True)
            t.start()
            self._threads.append(t)

    # --- Public API (same signatures as the mp RenderWorker) ---

    def queue_render_job(
        self,
        priority: int,
        doc_model,
        page_index: int,
        zoom: float,
        scale_factor: int,
        crop_rect,
        is_minimap: bool,
        target_cache,
        redraw_callback,
        screen_physical_dpi: float = 192.0,
    ):
        with self.lock:
            self.counter += 1
            cnt = self.counter
            gen = self._generation
        self.queue.put(
            (
                priority,
                cnt,
                "render",
                (
                    doc_model,
                    page_index,
                    zoom,
                    scale_factor,
                    crop_rect,
                    is_minimap,
                    target_cache,
                    redraw_callback,
                    gen,
                ),
            )
        )

    def queue_crop_job(
        self,
        doc_model,
        crop_analyzer,
        page_index: int,
        settings: CropSettings,
        progress_callback,
        completion_callback,
    ):
        with self.lock:
            self.counter += 1
            cnt = self.counter
        self.queue.put(
            (
                4,
                cnt,
                "crop",
                (doc_model, crop_analyzer, page_index, settings, progress_callback, completion_callback),
            )
        )

    def queue_portal_job(
        self,
        doc_model,
        page_index: int,
        target_y: float,
        target_w: int,
        target_h: int,
        scale_factor: float,
        target_cache,
        completion_callback,
    ):
        with self.lock:
            self.counter += 1
            cnt = self.counter
        self.queue.put(
            (
                2,
                cnt,
                "portal",
                (doc_model, page_index, target_y, target_w, target_h, scale_factor, target_cache, completion_callback),
            )
        )

    def clear_canvas_render_jobs(self):
        """Removes queued page renders (keeps minimap and crop scans), bumping
        the generation so stale in-flight page renders are dropped on arrival."""
        with self.lock:
            temp_list = []
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] >= 3:
                    temp_list.append(item)
            for item in temp_list:
                self.queue.put(item)
            self._generation += 1

    def set_document(self, filepath: str):
        """Switches the active document: drops queued jobs and bumps the
        generation so stale results from the previous document are discarded."""
        with self.lock:
            self._active_filepath = filepath
            self._generation += 1
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break

    def shutdown(self):
        if self._stop.is_set():
            return
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)

    # --- Worker threads ---

    def _thread_doc(self, filepath: str) -> fitz.Document:
        """Each worker thread opens its own fitz.Document (lazily cached per
        filepath) because PyMuPDF documents are not safe to share across threads."""
        cached = getattr(self._thread_local, "doc", None)
        if cached is None or getattr(self._thread_local, "filepath", None) != filepath:
            cached = fitz.open(filepath)
            self._thread_local.doc = cached
            self._thread_local.filepath = filepath
        return cached

    def _run(self):
        while not self._stop.is_set():
            try:
                _priority, _cnt, job_type, args = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if job_type == "render":
                    self._do_render(args)
                elif job_type == "crop":
                    self._do_crop(args)
                elif job_type == "portal":
                    self._do_portal(args)
            except Exception as e:
                print(f"Error in RenderWorkerMT thread: {e}")
            finally:
                self.queue.task_done()

    def _is_stale(self, doc_model) -> bool:
        return self._active_filepath is not None and doc_model.filepath != self._active_filepath

    def _do_render(self, args):
        (
            doc_model,
            page_index,
            zoom,
            scale_factor,
            crop_rect,
            is_minimap,
            target_cache,
            redraw_callback,
            gen,
        ) = args
        stale = self._is_stale(doc_model) or (not is_minimap and gen != self._generation)

        doc = self._thread_doc(doc_model.filepath)
        page = doc[page_index]
        mat = fitz.Matrix(zoom * scale_factor, zoom * scale_factor)
        start_time = time.perf_counter()
        pix = page.get_pixmap(matrix=mat, clip=crop_rect, alpha=False)

        if not stale:
            if is_minimap:
                bgra = bgra_from_rgb(pix.samples, pix.width, pix.height)
                surface = cairo.ImageSurface.create_for_data(
                    bgra, cairo.FORMAT_RGB24, pix.width, pix.height, pix.width * 4
                )
                surface.set_device_scale(scale_factor, scale_factor)
                target_cache.set(page_index, surface, bgra)
            else:
                crop_key = (
                    (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)
                    if crop_rect is not None
                    else None
                )
                texture = PageTexture(pix.width, pix.height, pix.n, pix.samples)
                target_cache.set(page_index, zoom, scale_factor, crop_key, texture, None)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        sys.stderr.write(
            f"[RenderWorkerMT] Render END page={page_index}, zoom={zoom:.2f}, time={elapsed_ms:.1f}ms\n"
        )
        sys.stderr.flush()

        GLib.idle_add(redraw_callback)

    def _do_crop(self, args):
        doc_model, crop_analyzer, page_index, settings, progress_callback, completion_callback = args

        doc = self._thread_doc(doc_model.filepath)
        page = doc[page_index]
        scale = crop_analyzer.ANALYSIS_SCALE
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        bbox = crop_analyzer.analyze_pixmap(pix.width, pix.height, pix.n, pix.samples, scale=scale)

        if self._is_stale(doc_model):
            return
        with self.lock:
            crop_analyzer.raw_bboxes[page_index] = bbox
            crop_analyzer.scanned[page_index] = True
            all_scanned = all(crop_analyzer.scanned)
        if progress_callback:
            GLib.idle_add(progress_callback, page_index)
        if all_scanned:
            crop_analyzer.compute_crop_rects(settings)
            if completion_callback:
                GLib.idle_add(completion_callback)

    def _do_portal(self, args):
        doc_model, page_index, target_y, target_w, target_h, scale_factor, target_cache, completion_callback = args

        doc = self._thread_doc(doc_model.filepath)
        page = doc[page_index]
        page_rect = page.rect
        matrix_x = target_w / page_rect.width if page_rect.width > 0 else 1.0
        matrix_y = matrix_x  # Enforce uniform 1:1 aspect ratio scaling
        crop_h = (target_h / matrix_x) if matrix_x > 0 else 160.0
        crop_y0 = max(0.0, target_y - (crop_h / 2.0))
        crop_y1 = min(page_rect.height, crop_y0 + crop_h)
        clip = fitz.Rect(0.0, crop_y0, page_rect.width, crop_y1)
        mat = fitz.Matrix(matrix_x, matrix_y)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)

        bgra = bgra_from_rgb(pix.samples, pix.width, pix.height)
        surface = cairo.ImageSurface.create_for_data(
            bgra, cairo.FORMAT_ARGB32, pix.width, pix.height, pix.width * 4
        )
        surface.set_device_scale(scale_factor, scale_factor)
        from ..ui.portal import apply_card_decorations

        apply_card_decorations(surface, scale_factor)

        if self._is_stale(doc_model):
            return
        target_cache.set(page_index, target_y, target_w, target_h, surface, bgra)
        GLib.idle_add(completion_callback, page_index, target_y, surface)
