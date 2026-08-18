"""
Child process that owns the fitz.Document and performs all rasterization.

PyMuPDF re-acquires the GIL in bursts during image decode/scaling, which stutters
the UI thread even when rendering happens in a background thread. Moving every
``page.get_pixmap()`` call into a dedicated child process guarantees the GIL can
never block the UI thread again.

This module deliberately imports only ``fitz`` (plus stdlib) so the child stays
independent of the GTK/cairo stack. It never touches cairo, numpy, or GLib.
"""

import sys
from typing import Literal, NotRequired, TypedDict

import fitz


class ShmInfo(TypedDict):
    name: str
    slot: int
    slot_size: int


class RenderRequest(TypedDict):
    op: Literal["render"]
    seq: int
    filepath: str
    page: int
    scale: float
    clip: tuple[float, float, float, float] | None
    shm: NotRequired[ShmInfo]


class PortalRequest(TypedDict):
    op: Literal["portal"]
    seq: int
    filepath: str
    page: int
    target_y: float
    target_w: int
    target_h: int


class CropRequest(TypedDict):
    op: Literal["crop"]
    seq: int
    filepath: str
    page: int
    scale: float


class OpenRequest(TypedDict):
    op: Literal["open"]
    filepath: str


#: Everything the child can receive on its input queue.
ChildRequest = RenderRequest | PortalRequest | CropRequest | OpenRequest


class RenderResult(TypedDict):
    kind: Literal["render_result"]
    seq: int
    width: int
    height: int
    channels: int
    samples: NotRequired[bytes]
    shm_slot: NotRequired[int]
    length: NotRequired[int]


class OpenResult(TypedDict):
    kind: Literal["open_result"]
    filepath: str


class ErrorResult(TypedDict):
    kind: Literal["error"]
    seq: int | None
    message: str


#: Everything the child can emit on its result queue.
ChildResult = RenderResult | OpenResult | ErrorResult


class _ChildRenderer:
    """Serial renderer owned by the child process, caching open fitz.Document instances by filepath."""

    def __init__(self, max_cached_docs: int = 16):
        self._docs: dict[str, fitz.Document] = {}
        self._max_cached_docs = max_cached_docs
        self._shm_objects = {}

    def _ensure_doc(self, filepath: str):
        if filepath in self._docs:
            return self._docs[filepath]

        if len(self._docs) >= self._max_cached_docs:
            oldest_path, oldest_doc = next(iter(self._docs.items()))
            try:
                oldest_doc.close()
            except Exception:
                pass
            del self._docs[oldest_path]

        doc = fitz.open(filepath)
        self._docs[filepath] = doc
        return doc

    def _ensure_shm(self, name: str):
        if name not in self._shm_objects:
            try:
                from multiprocessing.shared_memory import SharedMemory

                self._shm_objects[name] = SharedMemory(name=name, create=False)
            except Exception:
                return None
        return self._shm_objects.get(name)

    def render(self, req: RenderRequest | PortalRequest | CropRequest) -> ChildResult:
        """Renders one request; returns a picklable result dict."""
        doc = self._ensure_doc(req["filepath"])
        page = doc[req["page"]]

        if req["op"] == "portal":
            page_rect = page.rect
            target_w = req["target_w"]
            target_h = req["target_h"]
            matrix_x = target_w / page_rect.width if page_rect.width > 0 else 1.0
            matrix_y = matrix_x  # Enforce uniform 1:1 aspect ratio scaling
            crop_h = (target_h / matrix_x) if matrix_x > 0 else 160.0
            crop_y0 = max(0.0, req["target_y"] - (crop_h / 2.0))
            crop_y1 = min(page_rect.height, crop_y0 + crop_h)
            clip = fitz.Rect(0.0, crop_y0, page_rect.width, crop_y1)
            mat = fitz.Matrix(matrix_x, matrix_y)
        elif req["op"] == "crop":
            scale = req["scale"]
            mat = fitz.Matrix(scale, scale)
            clip = None
        else:  # render
            scale = req["scale"]
            mat = fitz.Matrix(scale, scale)
            clip = None
            clip_key = req.get("clip")
            if clip_key is not None:
                x0, y0, x1, y1 = clip_key
                clip = fitz.Rect(x0, y0, x1, y1)

        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)

        shm_info = req.get("shm")
        if shm_info:
            shm_name = shm_info["name"]
            slot_idx = shm_info["slot"]
            slot_size = shm_info["slot_size"]
            offset = slot_idx * slot_size
            samples_bytes = pix.samples
            length = len(samples_bytes)

            shm = self._ensure_shm(shm_name)
            if shm and offset + length <= len(shm.buf):
                shm.buf[offset : offset + length] = samples_bytes
                return {
                    "kind": "render_result",
                    "seq": req["seq"],
                    "width": pix.width,
                    "height": pix.height,
                    "channels": pix.n,
                    "shm_slot": slot_idx,
                    "length": length,
                }
            else:
                sys.stderr.write(
                    f"[RenderChild] SHM buffer slot overflow (page frame {pix.width}x{pix.height}x{pix.n} = {length} bytes > "
                    f"slot size {slot_size} bytes); falling back to standard queue IPC for seq={req['seq']}\n"
                )
                sys.stderr.flush()

        return {
            "kind": "render_result",
            "seq": req["seq"],
            "width": pix.width,
            "height": pix.height,
            "channels": pix.n,
            "samples": pix.samples,
        }


def child_main(input_q, result_q):
    """
    Spawn entry point. Reads picklable dict requests from ``input_q`` and pushes
    result dicts onto ``result_q`` until a ``"shutdown"`` sentinel arrives.
    """
    renderer = _ChildRenderer()
    while True:
        req = input_q.get()
        if req == "shutdown":
            break
        try:
            if req.get("op") == "open":
                renderer._ensure_doc(req["filepath"])
                result: ChildResult = {"kind": "open_result", "filepath": req["filepath"]}
                result_q.put(result)
                continue
            result = renderer.render(req)
        except Exception as e:
            result = {"kind": "error", "seq": req.get("seq"), "message": str(e)}
        try:
            result_q.put(result)
        except (BrokenPipeError, EOFError, OSError):
            break
    for doc in renderer._docs.values():
        try:
            doc.close()
        except Exception:
            pass
    for shm in renderer._shm_objects.values():
        try:
            shm.close()
        except Exception:
            pass
