import multiprocessing
import queue
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
import cairo
import numpy as np
from gi.repository import GLib

from .render_child import ChildRequest, ErrorResult, RenderResult, child_main
from .settings import CropSettings
from .texture import PageTexture


class RenderWorker:
    """
    Background rendering coordinator.

    All PyMuPDF rasterization runs in a dedicated child process (see
    ``render_child``), so GIL bursts during image decode can never block the UI
    thread. The parent keeps a priority queue, a dispatcher thread that forwards
    jobs to the child, and a pump thread that rebuilds cairo surfaces from the
    raw pixels returned by the child and stores them in the existing caches.

    The public API (``queue_render_job``, ``queue_crop_job``,
    ``queue_portal_job``, ``clear_canvas_render_jobs``) is unchanged so canvas,
    minimap, and link-preview callers are untouched.

    Priorities:
      Priority 0: Visible canvas pages
      Priority 1: Canvas pages ±1
      Priority 2: Canvas pages ±2 / portal previews
      Priority 3: Minimap thumbnails
      Priority 4: Crop analysis scans
    """

    def __init__(self):
        self.queue = queue.PriorityQueue()
        self.counter = 0
        self.lock = threading.Lock()
        self._generation = 0
        self._active_filepath = None
        self._respawn_count = 0
        self._pending: dict[int, dict] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()

        self._mp_ctx = multiprocessing.get_context("spawn")
        self._jobs_q = self._mp_ctx.Queue(maxsize=8)
        self._results_q = self._mp_ctx.Queue(maxsize=2)
        self._child = None
        self._accepting = True
        self._spawn_child()

        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
        self._dispatch_thread.start()
        self._pump_thread.start()

    # --- Public API (unchanged signatures) ---

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
        filepath = doc_model.filepath
        crop_key = (
            (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)
            if crop_rect is not None
            else None
        )
        with self.lock:
            self.counter += 1
            cnt = self.counter
            gen = self._generation
        seq = cnt
        with self._pending_lock:
            self._pending[seq] = {
                "kind": "render",
                "in_flight": False,
                "gen": gen,
                "gen_sensitive": not is_minimap,
                "filepath": filepath,
                "page_index": page_index,
                "zoom": zoom,
                "scale_factor": scale_factor,
                "crop_key": crop_key,
                "is_minimap": is_minimap,
                "target_cache": target_cache,
                "redraw_callback": redraw_callback,
                "dispatched_at": time.perf_counter(),
            }
        self.queue.put(
            (
                priority,
                cnt,
                "render",
                (seq, filepath, page_index, zoom * scale_factor, crop_key, is_minimap),
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
        filepath = doc_model.filepath
        with self.lock:
            self.counter += 1
            cnt = self.counter
            gen = self._generation
        seq = cnt
        with self._pending_lock:
            self._pending[seq] = {
                "kind": "crop",
                "in_flight": False,
                "gen": gen,
                "gen_sensitive": False,
                "filepath": filepath,
                "page_index": page_index,
                "scale": crop_analyzer.ANALYSIS_SCALE,
                "crop_analyzer": crop_analyzer,
                "settings": settings,
                "progress_callback": progress_callback,
                "completion_callback": completion_callback,
            }
        self.queue.put((4, cnt, "crop", (seq, filepath, page_index)))

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
        filepath = doc_model.filepath
        with self.lock:
            self.counter += 1
            cnt = self.counter
            gen = self._generation
        seq = cnt
        with self._pending_lock:
            self._pending[seq] = {
                "kind": "portal",
                "in_flight": False,
                "gen": gen,
                "gen_sensitive": False,
                "filepath": filepath,
                "page_index": page_index,
                "target_y": target_y,
                "target_w": target_w,
                "target_h": target_h,
                "scale_factor": scale_factor,
                "target_cache": target_cache,
                "completion_callback": completion_callback,
            }
        self.queue.put(
            (2, cnt, "portal", (seq, filepath, page_index, target_y, target_w, target_h, scale_factor))
        )

    def clear_canvas_render_jobs(self):
        """Removes queued page renders (keeps minimap and crop scans), bumping the
        generation so stale in-flight results are discarded."""
        with self.lock:
            temp_list = []
            dropped_seqs = []
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] >= 3:
                    temp_list.append(item)
                else:
                    dropped_seqs.append(item[3][0])
            for item in temp_list:
                self.queue.put(item)
            self._generation += 1
        if dropped_seqs:
            with self._pending_lock:
                for seq in dropped_seqs:
                    entry = self._pending.get(seq)
                    if entry is not None and not entry["in_flight"]:
                        del self._pending[seq]

    def set_document(self, filepath: str):
        """Switches the active document: drops queued jobs and pending results so
        no stale render from the previous document can pollute the new one."""
        with self.lock:
            self._generation += 1
            self._active_filepath = filepath
            self._respawn_count = 0
        with self._pending_lock:
            self._pending.clear()
        with self.lock:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
        try:
            self._jobs_q.put({"op": "open", "filepath": filepath}, timeout=1.0)
        except Exception:
            pass

    def shutdown(self):
        """Stops the dispatcher/pump threads and terminates the child process."""
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._jobs_q.put("shutdown", timeout=1.0)
        except Exception:
            pass
        self._dispatch_thread.join(timeout=1.0)
        self._pump_thread.join(timeout=1.0)
        if self._child is not None and self._child.is_alive():
            self._child.join(timeout=2.0)
        if self._child is not None and self._child.is_alive():
            self._child.terminate()
        try:
            self._jobs_q.close()
            self._results_q.close()
        except Exception:
            pass

    # --- Child process lifecycle ---

    def _spawn_child(self):
        self._child = self._mp_ctx.Process(
            target=child_main, args=(self._jobs_q, self._results_q), daemon=True
        )
        self._child.start()

    def _respawn_child(self, reason: str):
        if self._stop.is_set():
            return
        with self.lock:
            self._accepting = False
            self._respawn_count += 1
            if self._respawn_count > 3:
                print(f"[RenderWorker] child process keeps dying ({reason}); giving up")
                return
            try:
                self._jobs_q.close()
                self._results_q.close()
            except Exception:
                pass
            self._jobs_q = self._mp_ctx.Queue(maxsize=8)
            self._results_q = self._mp_ctx.Queue(maxsize=2)
            self._spawn_child()
            self._accepting = True
            if self._active_filepath:
                try:
                    self._jobs_q.put({"op": "open", "filepath": self._active_filepath})
                except Exception:
                    pass
        print(f"[RenderWorker] respawned child process ({reason})")

    # --- Parent threads ---

    def _dispatch_loop(self):
        while not self._stop.is_set():
            try:
                priority, cnt, job_type, args = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            seq = args[0]
            with self._pending_lock:
                entry = self._pending.get(seq)
                if entry is None:
                    self.queue.task_done()
                    continue
                entry["in_flight"] = True
            try:
                req: ChildRequest
                if job_type == "render":
                    _, filepath, page_index, scale, crop_key, _is_minimap = args
                    req = {
                        "op": "render",
                        "seq": seq,
                        "filepath": filepath,
                        "page": page_index,
                        "scale": scale,
                        "clip": crop_key,
                    }
                elif job_type == "portal":
                    _, filepath, page_index, target_y, target_w, target_h, _scale_factor = args
                    req = {
                        "op": "portal",
                        "seq": seq,
                        "filepath": filepath,
                        "page": page_index,
                        "target_y": target_y,
                        "target_w": target_w,
                        "target_h": target_h,
                    }
                elif job_type == "crop":
                    _, filepath, page_index = args
                    req = {
                        "op": "crop",
                        "seq": seq,
                        "filepath": filepath,
                        "page": page_index,
                        "scale": entry["scale"],
                    }
                else:
                    self.queue.task_done()
                    continue
            except (ValueError, IndexError):
                self.queue.task_done()
                continue

            with self.lock:
                accepting = self._accepting
                jobs_q = self._jobs_q
            if not accepting:
                with self._pending_lock:
                    self._pending.pop(seq, None)
                self._abandon(entry)
                self.queue.task_done()
                continue
            try:
                jobs_q.put(req, timeout=1.0)
            except Exception:
                with self._pending_lock:
                    self._pending.pop(seq, None)
                self._abandon(entry)
            self.queue.task_done()

    def _pump_loop(self):
        while not self._stop.is_set():
            with self.lock:
                results_q = self._results_q
            try:
                msg = results_q.get(timeout=0.2)
            except queue.Empty:
                self._check_child_alive()
                continue
            except (EOFError, OSError):
                self._check_child_alive()
                continue

            if msg is None:
                continue
            kind = msg.get("kind")
            if kind == "open_result":
                continue
            if kind == "error":
                self._handle_error(msg)
                continue
            with self._pending_lock:
                entry = self._pending.pop(msg.get("seq"), None)
            if entry is None:
                continue
            try:
                if entry["kind"] == "render":
                    self._deliver_render(msg, entry)
                elif entry["kind"] == "portal":
                    self._deliver_portal(msg, entry)
                elif entry["kind"] == "crop":
                    self._deliver_crop(msg, entry)
            except Exception as e:
                print(f"Error processing render result in pump thread: {e}")
                self._abandon(entry)

    # --- Result delivery ---

    @staticmethod
    def _bgra_buffer(data, w: int, h: int) -> np.ndarray:
        """Builds a contiguous, writable BGRA buffer from raw RGB bytes (single allocation)."""
        arr = np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3))
        bgra = np.empty((h, w, 4), dtype=np.uint8)
        bgra[:, :, :3] = arr[:, :, ::-1]
        bgra[:, :, 3] = 255
        return bgra

    def _is_stale(self, entry: dict) -> bool:
        if entry["filepath"] != self._active_filepath:
            return True
        if entry["gen_sensitive"] and entry["gen"] != self._generation:
            return True
        return False

    def _deliver_render(self, msg: RenderResult, entry: dict):
        w = msg["width"]
        h = msg["height"]

        if not self._is_stale(entry):
            if entry["is_minimap"]:
                bgra = self._bgra_buffer(msg["samples"], w, h)
                surface = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_RGB24, w, h, w * 4)
                surface.set_device_scale(entry["scale_factor"], entry["scale_factor"])
                entry["target_cache"].set(entry["page_index"], surface, bgra)
            else:
                texture = PageTexture(w, h, msg["channels"], msg["samples"])
                entry["target_cache"].set(
                    entry["page_index"], entry["zoom"], entry["scale_factor"], entry["crop_key"], texture, None
                )

        elapsed_ms = (time.perf_counter() - entry["dispatched_at"]) * 1000.0
        sys.stderr.write(
            f"[RenderWorker] Render END page={entry['page_index']}, "
            f"zoom={entry['zoom']:.2f}, time={elapsed_ms:.1f}ms\n"
        )
        sys.stderr.flush()

        GLib.idle_add(entry["redraw_callback"])

    def _deliver_portal(self, msg: RenderResult, entry: dict):
        w = msg["width"]
        h = msg["height"]
        bgra = self._bgra_buffer(msg["samples"], w, h)
        surface = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, w, h, w * 4)
        surface.set_device_scale(entry["scale_factor"], entry["scale_factor"])
        from ..ui.portal import apply_card_decorations

        apply_card_decorations(surface, entry["scale_factor"])

        if not self._is_stale(entry):
            entry["target_cache"].set(
                entry["page_index"], entry["target_y"], entry["target_w"], entry["target_h"], surface, bgra
            )
            GLib.idle_add(entry["completion_callback"], entry["page_index"], entry["target_y"], surface)

    def _deliver_crop(self, msg: RenderResult, entry: dict):
        analyzer = entry["crop_analyzer"]
        bbox = analyzer.analyze_pixmap(
            msg["width"], msg["height"], msg["channels"], msg["samples"], scale=entry["scale"]
        )
        if self._is_stale(entry):
            return
        self._finalize_crop(entry, bbox)

    def _finalize_crop(self, entry: dict, bbox) -> None:
        """Shared tail for crop delivery: records the page result and fires the
        scan progress; once every page is scanned, computes crop rects and fires
        the completion callback. ``bbox`` is None for failed/abandoned pages."""
        analyzer = entry["crop_analyzer"]
        analyzer.raw_bboxes[entry["page_index"]] = bbox
        analyzer.scanned[entry["page_index"]] = True
        GLib.idle_add(entry["progress_callback"], entry["page_index"])
        if all(analyzer.scanned):
            analyzer.compute_crop_rects(entry["settings"])
            GLib.idle_add(entry["completion_callback"])

    def _abandon(self, entry: dict) -> None:
        """Completes the visible contract for a job whose result can never arrive
        (child death, failed dispatch, or delivery error) so nothing stays wedged:
        renders re-request via redraw, crop scans are finalized with the page
        treated as blank, portal jobs are simply dropped."""
        kind = entry["kind"]
        if kind == "render":
            GLib.idle_add(entry["redraw_callback"])
        elif kind == "crop":
            self._finalize_crop(entry, None)

    def _handle_error(self, msg: ErrorResult):
        seq = msg.get("seq")
        if not isinstance(seq, int):
            return
        with self._pending_lock:
            entry = self._pending.pop(seq, None)
        if entry is None:
            return
        print(f"RenderWorker: child error for seq {seq}: {msg.get('message')}")
        self._abandon(entry)

    def _check_child_alive(self):
        if self._stop.is_set() or self._child is None or self._child.is_alive():
            return
        with self._pending_lock:
            dead = list(self._pending.values())
            self._pending.clear()
        for entry in dead:
            self._abandon(entry)
        self._respawn_child("unexpected death")
