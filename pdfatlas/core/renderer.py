import queue
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
import cairo
import fitz
import numpy as np
from gi.repository import GLib

from .settings import CropSettings


class RenderWorker:
    """
    Background rendering thread coordinator.
    Uses a priority queue to process:
      Priority 0: Visible canvas pages
      Priority 1: Canvas pages ±1
      Priority 2: Canvas pages ±2
      Priority 3: Minimap thumbnails
      Priority 4: Crop analysis scans
    """

    def __init__(self):
        self.queue = queue.PriorityQueue()
        self.counter = 0
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

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
        """
        Pushes a new rendering job to the queue.
        """
        with self.lock:
            self.counter += 1
            cnt = self.counter
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
                    screen_physical_dpi,
                    crop_rect,
                    is_minimap,
                    target_cache,
                    redraw_callback,
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
        """
        Pushes a crop analysis job for a single page.
        """
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
        """
        Pushes a portal crop rendering job to the queue.
        """
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
        """
        Removes all page rendering jobs from the queue (retaining crop and thumbnail jobs).
        """
        with self.lock:
            temp_list = []
            while not self.queue.empty():
                item = self.queue.get_nowait()
                # Keep minimap jobs (priority 3) and crop scans (priority 4)
                if item[0] >= 3:
                    temp_list.append(item)
            for item in temp_list:
                self.queue.put(item)

    def _run(self):
        while True:
            priority, cnt, job_type, args = self.queue.get()
            try:
                if job_type == "render":
                    (
                        doc_model,
                        page_index,
                        zoom,
                        scale_factor,
                        screen_physical_dpi,
                        crop_rect,
                        is_minimap,
                        target_cache,
                        redraw_callback,
                    ) = args

                    page = doc_model.get_page(page_index)
                    physical_zoom = zoom * scale_factor

                    mat = fitz.Matrix(physical_zoom, physical_zoom)
                    start_time = time.perf_counter()
                    pix = page.get_pixmap(matrix=mat, clip=crop_rect, alpha=False)

                    arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(
                        (pix.height, pix.width, pix.n)
                    )
                    bgr = arr[:, :, [2, 1, 0]]
                    bgra = np.dstack((bgr, np.full((pix.height, pix.width, 1), 255, dtype=np.uint8))).copy()
                    surface = cairo.ImageSurface.create_for_data(
                        bgra, cairo.FORMAT_RGB24, pix.width, pix.height, pix.width * 4
                    )
                    buf = bgra

                    cairo_scale = physical_zoom / zoom
                    surface.set_device_scale(cairo_scale, cairo_scale)

                    if is_minimap:
                        target_cache.set(page_index, surface, buf)
                    else:
                        crop_key = (
                            (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)
                            if crop_rect is not None
                            else None
                        )
                        target_cache.set(page_index, zoom, scale_factor, crop_key, surface, buf)

                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                    sys.stderr.write(
                        f"[RenderWorker] Render END page={page_index}, zoom={zoom:.2f}, time={elapsed_ms:.1f}ms\n"
                    )
                    sys.stderr.flush()

                    GLib.idle_add(redraw_callback)

                elif job_type == "crop":
                    doc_model, crop_analyzer, page_index, settings, progress_callback, completion_callback = (
                        args
                    )

                    crop_analyzer.scan_page(page_index)

                    if progress_callback:
                        GLib.idle_add(progress_callback, page_index)

                    if all(crop_analyzer.scanned):
                        crop_analyzer.compute_crop_rects(settings)
                        if completion_callback:
                            GLib.idle_add(completion_callback)

                elif job_type == "portal":
                    doc_model, page_index, target_y, target_w, target_h, scale_factor, target_cache, completion_callback = args

                    cached_portal = target_cache.get(page_index, target_y, target_w, target_h)
                    if cached_portal:
                        surface = cached_portal
                    else:
                        page = doc_model.get_page(page_index)
                        page_rect = page.rect
                        matrix_x = target_w / page_rect.width if page_rect.width > 0 else 1.0
                        matrix_y = matrix_x  # Enforce uniform 1:1 aspect ratio scaling

                        crop_h = (target_h / matrix_x) if matrix_x > 0 else 160.0
                        crop_y0 = max(0.0, target_y - (crop_h / 2.0))
                        crop_y1 = min(page_rect.height, crop_y0 + crop_h)

                        crop_rect = fitz.Rect(0.0, crop_y0, page_rect.width, crop_y1)
                        mat = fitz.Matrix(matrix_x, matrix_y)
                        pix = page.get_pixmap(matrix=mat, clip=crop_rect, alpha=False)

                        arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(
                            (pix.height, pix.width, pix.n)
                        )
                        bgra = arr[:, :, [2, 1, 0, 3]].copy() if pix.n == 4 else np.dstack((arr[:, :, [2, 1, 0]], np.full((pix.height, pix.width, 1), 255, dtype=np.uint8))).copy()
                        surface = cairo.ImageSurface.create_for_data(
                            bgra, cairo.FORMAT_ARGB32, pix.width, pix.height, pix.width * 4
                        )
                        surface.set_device_scale(scale_factor, scale_factor)
                        from ..ui.portal import apply_card_decorations
                        apply_card_decorations(surface, scale_factor)
                        buf = bgra
                        target_cache.set(page_index, target_y, target_w, target_h, surface, buf)

                    GLib.idle_add(completion_callback, page_index, target_y, surface)

            except Exception as e:
                print(f"Error in RenderWorker thread: {e}")
            finally:
                self.queue.task_done()
