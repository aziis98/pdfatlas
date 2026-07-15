import threading
import queue
import heapq
import gi
import sys
gi.require_version('Gtk', '4.0')
from gi.repository import GLib
import fitz
import cairo
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

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

    def queue_render_job(self, priority: int, doc_model, page_index: int, zoom: float, scale_factor: int, crop_rect, is_minimap: bool, target_cache, redraw_callback, screen_physical_dpi: float = 192.0):
        """
        Pushes a new rendering job to the queue.
        """
        with self.lock:
            self.counter += 1
            cnt = self.counter
        self.queue.put((
            priority,
            cnt,
            "render",
            (doc_model, page_index, zoom, scale_factor, screen_physical_dpi, crop_rect, is_minimap, target_cache, redraw_callback)
        ))

    def queue_crop_job(self, doc_model, crop_analyzer, page_index: int, settings: CropSettings, progress_callback, completion_callback):
        """
        Pushes a crop analysis job for a single page.
        """
        with self.lock:
            self.counter += 1
            cnt = self.counter
        self.queue.put((
            4,
            cnt,
            "crop",
            (doc_model, crop_analyzer, page_index, settings, progress_callback, completion_callback)
        ))

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
                    doc_model, page_index, zoom, scale_factor, screen_physical_dpi, crop_rect, is_minimap, target_cache, redraw_callback = args
                    
                    # 1. Retrieve the page from PyMuPDF document
                    page = doc_model.get_page(page_index)
                    
                    # 2. Render to Pixmap using physical zoom (capped at screen's physical DPI)
                    physical_zoom = zoom * scale_factor
                    
                    if not is_minimap:
                        max_physical_zoom = screen_physical_dpi / 72.0
                        if physical_zoom > max_physical_zoom:
                            physical_zoom = max_physical_zoom
                    
                    mat = fitz.Matrix(physical_zoom, physical_zoom)
                    
                    # Calculate output dimensions and resolution beforehand for logging
                    rect_to_render = crop_rect if crop_rect is not None else page.rect
                    out_w = int(rect_to_render.width * physical_zoom)
                    out_h = int(rect_to_render.height * physical_zoom)
                    resolution_dpi = physical_zoom * 72.0
                    
                    sys.stderr.write(
                        f"[RenderWorker] MuPDF render request: page={page_index}, "
                        f"output_size={out_w}x{out_h}, resolution={resolution_dpi:.1f} DPI, "
                        f"zoom={zoom:.4f}, scale_factor={scale_factor}, is_minimap={is_minimap}\n"
                    )
                    sys.stderr.flush()
                    
                    pix = page.get_pixmap(matrix=mat, clip=crop_rect, alpha=True)
                    
                    # 3. Swap R and B channels for Cairo BGRA format
                    if HAS_NUMPY:
                        arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
                        bgra = arr[:, :, [2, 1, 0, 3]].copy()
                        surface = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, pix.width, pix.height, pix.width * 4)
                        buf = bgra
                    else:
                        data = bytearray(pix.samples)
                        r = data[0::4]
                        b = data[2::4]
                        data[0::4] = b
                        data[2::4] = r
                        surface = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, pix.width, pix.height, pix.width * 4)
                        buf = data

                    # Apply dynamic scale factor to Cairo context
                    cairo_scale = physical_zoom / zoom
                    surface.set_device_scale(cairo_scale, cairo_scale)

                    # 4. Cache the resulting surface
                    if is_minimap:
                        target_cache.set(page_index, surface, buf)
                    else:
                        crop_key = (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1) if crop_rect is not None else None
                        target_cache.set(page_index, zoom, scale_factor, crop_key, surface, buf)

                    # 5. Notify main thread to redraw
                    GLib.idle_add(redraw_callback)

                elif job_type == "crop":
                    doc_model, crop_analyzer, page_index, settings, progress_callback, completion_callback = args
                    
                    # Analyze page
                    crop_analyzer.scan_page(page_index)
                    
                    # Report progress
                    if progress_callback:
                        GLib.idle_add(progress_callback, page_index)

                    # Check if all pages are done
                    if all(crop_analyzer.scanned):
                        crop_analyzer.compute_crop_rects(settings)
                        if completion_callback:
                            GLib.idle_add(completion_callback)

            except Exception as e:
                # Prevent background thread from dying if a page rendering fails (e.g. document closed)
                print(f"Error in RenderWorker thread: {e}")
            finally:
                self.queue.task_done()
