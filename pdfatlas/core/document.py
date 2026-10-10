import os
import queue
import re
import threading
import time
from typing import Callable, Optional

import pymupdf as fitz
from gi.repository import GLib

ARXIV_TEXT_RE = re.compile(
    r"(?:(?:arXiv|arxiv|ARXIV):\s*|https?://(?:[a-zA-Z0-9\-]+\.)?arxiv\.org/(?:abs|pdf)/|doi:10\.48550/arXiv\.)"
    r"([a-zA-Z\-]+(?:\.[a-zA-Z\-]+)?/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


def detect_text_arxiv_links(page: fitz.Page, existing_links: list[dict]) -> list[dict]:
    """
    Detect plain text occurrences of 'arXiv:<id>' or arXiv URLs on the page that
    lack explicit PDF link annotations, and return synthesized link dictionaries.
    """
    detected: list[dict] = []
    try:
        raw_text = page.get_text("text")
        if not isinstance(raw_text, str) or not raw_text or "arxiv" not in raw_text.lower():
            return detected

        existing_rects = [lnk.get("from") for lnk in existing_links if lnk.get("from") is not None]

        for m in ARXIV_TEXT_RE.finditer(raw_text):
            full_match = m.group(0).strip()
            aid = m.group(1).strip()
            rects = page.search_for(full_match)
            if not rects and " " in full_match:
                rects = page.search_for(full_match.replace(" ", ""))

            for r in rects:
                is_covered = any(
                    r.intersects(er) and (r & er).get_area() > 0.5 * r.get_area()
                    for er in existing_rects
                )
                if not is_covered:
                    detected.append({
                        "kind": fitz.LINK_URI,
                        "from": r,
                        "uri": f"https://arxiv.org/abs/{aid}",
                        "arxiv_id": aid,
                        "auto_detected": True,
                    })
                    existing_rects.append(r)
    except Exception as e:
        print(f"[DocumentModel] Error detecting text arXiv links: {e}", flush=True)
    return detected


def normalize_link_dict(doc: fitz.Document, link: dict, page_count: int) -> dict:
    """
    Normalize link dictionary fields:
    - Ensure 'page' is an integer index (0 <= page < page_count) or None.
    - If 'to' point is missing but 'view' specifies coordinates (e.g. FitH, FitV, XYZ), parse it into 'to'.
    """
    raw_page = link.get("page")
    if isinstance(raw_page, int):
        if not (0 <= raw_page < page_count):
            link["page"] = None
    elif isinstance(raw_page, str):
        target_page: int | None = None
        s = raw_page.strip()
        if s.isdigit():
            p = int(s) - 1
            if 0 <= p < page_count:
                target_page = p
        else:
            try:
                resolved = doc.resolve_link(link)
                if (
                    resolved
                    and isinstance(resolved, (tuple, list))
                    and len(resolved) > 0
                    and isinstance(resolved[0], int)
                    and 0 <= resolved[0] < page_count
                ):
                    target_page = resolved[0]
            except Exception:
                pass
        link["page"] = target_page

    if link.get("to") is None and link.get("view"):
        view = link.get("view")
        if isinstance(view, str):
            parts = [p.strip() for p in view.split(",")]
            if parts[0] == "FitH" and len(parts) > 1:
                try:
                    link["to"] = fitz.Point(0.0, float(parts[1]))
                except ValueError:
                    pass
            elif parts[0] == "FitV" and len(parts) > 1:
                try:
                    link["to"] = fitz.Point(float(parts[1]), 0.0)
                except ValueError:
                    pass
            elif parts[0] == "XYZ" and len(parts) >= 3:
                try:
                    x = float(parts[1]) if parts[1] != "null" else 0.0
                    y = float(parts[2]) if parts[2] != "null" else 0.0
                    link["to"] = fitz.Point(x, y)
                except ValueError:
                    pass
    return link


class DocumentModel:
    """
    A read-only model wrapper around fitz.Document.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.doc = fitz.open(filepath)
        self._page_count = len(self.doc)
        # Pre-cache page rectangles and native PDF links to avoid retrieving them repeatedly during GL render passes
        self._page_rects = [self.doc[i].rect for i in range(self._page_count)]
        self._page_links: list[list[dict]] = []
        self._custom_links: list[dict] = []
        self._custom_links_by_page: dict[int, list[dict]] = {}
        for i in range(self._page_count):
            try:
                page = self.doc[i]
                raw_links = page.get_links()
                normalized_links = [normalize_link_dict(self.doc, lnk, self._page_count) for lnk in raw_links]
                self._page_links.append(normalized_links)
            except (RuntimeError, ValueError):
                self._page_links.append([])

        # Lazy background worker state for plain-text arXiv link detection
        self._auto_links: dict[int, list[dict]] = {}
        self._scanned_pages: set[int] = set()
        self._pending_pages: set[int] = set()
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._closed = False
        self.on_links_updated: Optional[Callable[[int], None]] = None

    def _ensure_arxiv_detection_queued(self, index: int) -> None:
        if self._closed or not (0 <= index < self._page_count):
            return
        with self._lock:
            if index in self._scanned_pages or index in self._pending_pages:
                return
            self._pending_pages.add(index)
            self._queue.put(index)
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = threading.Thread(
                    target=self._arxiv_link_worker,
                    daemon=True,
                    name=f"ArxivLinkWorker-{os.path.basename(self.filepath)}",
                )
                self._worker_thread.start()

    def _arxiv_link_worker(self) -> None:
        try:
            worker_doc = fitz.open(self.filepath)
        except Exception as e:
            print(f"[DocumentModel] Worker failed to open {self.filepath}: {e}", flush=True)
            return

        try:
            while not self._closed:
                try:
                    page_idx = self._queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if page_idx is None or self._closed:
                    break

                try:
                    if 0 <= page_idx < len(worker_doc):
                        page = worker_doc[page_idx]
                        existing = self._page_links[page_idx]
                        detected = detect_text_arxiv_links(page, existing)
                        with self._lock:
                            self._scanned_pages.add(page_idx)
                            self._pending_pages.discard(page_idx)
                            if detected:
                                self._auto_links[page_idx] = detected

                        if detected and self.on_links_updated:
                            cb = self.on_links_updated
                            def _notify(idx: int) -> bool:
                                cb(idx)
                                return False
                            GLib.idle_add(_notify, page_idx)
                except Exception as e:
                    print(f"[DocumentModel] Error detecting arXiv links for page {page_idx}: {e}", flush=True)
                    with self._lock:
                        self._scanned_pages.add(page_idx)
                        self._pending_pages.discard(page_idx)
                finally:
                    self._queue.task_done()
        finally:
            try:
                worker_doc.close()
            except Exception:
                pass

    def wait_for_arxiv_detection(self, index: Optional[int] = None, timeout: float = 3.0) -> None:
        """
        Wait for a page (or all pending pages) to be scanned by the background link worker.
        Primarily used in headless unit tests and scripts.
        """
        if self._closed:
            return
        if index is not None:
            self._ensure_arxiv_detection_queued(index)
        t_end = time.perf_counter() + timeout
        while time.perf_counter() < t_end:
            with self._lock:
                if index is not None:
                    if index in self._scanned_pages:
                        return
                else:
                    if not self._pending_pages:
                        return
            time.sleep(0.01)

    @property
    def page_count(self) -> int:
        return self._page_count

    def set_custom_links(self, links: list[dict]) -> None:
        """Update active custom user links and index them by page."""
        from .layout import coalesce_rects_by_line

        self._custom_links = list(links)
        by_page: dict[int, list[dict]] = {}
        for lnk in links:
            p = lnk.get("page", 0)
            rects = lnk.get("rects", [])
            merged_rects = coalesce_rects_by_line(rects)
            for r in merged_rects:
                if len(r) == 4:
                    from_rect = fitz.Rect(r[0], r[1], r[2], r[3])
                    by_page.setdefault(p, []).append({
                        "kind": fitz.LINK_URI if lnk.get("uri") else fitz.LINK_GOTO,
                        "from": from_rect,
                        "uri": lnk.get("uri"),
                        "file_path": lnk.get("file_path"),
                        "title": lnk.get("title") or lnk.get("text"),
                        "text": lnk.get("text"),
                        "custom": True,
                        "custom_id": lnk.get("id"),
                    })
        self._custom_links_by_page = by_page

    def get_custom_links(self) -> list[dict]:
        return self._custom_links

    def get_page_links(self, index: int, wait: Optional[bool] = None) -> list[dict]:
        """
        Retrieve list of link dictionaries for a page.
        Pre-cached on init to ensure lock-free O(1) access during GL rendering.
        Includes native PDF links, user custom links, and lazy background-detected arXiv links.
        """
        if 0 <= index < self._page_count:
            self._ensure_arxiv_detection_queued(index)

            should_wait = wait if wait is not None else (GLib.main_depth() == 0)
            if should_wait and index not in self._scanned_pages:
                self.wait_for_arxiv_detection(index, timeout=3.0)

            native = self._page_links[index]
            with self._lock:
                auto = self._auto_links.get(index)
            custom = self._custom_links_by_page.get(index)

            res = list(native)
            if auto:
                res.extend(auto)
            if custom:
                res.extend(custom)
            return res
        return []

    def get_page(self, index: int) -> fitz.Page:
        """
        Retrieve a specific page. Note that PyMuPDF Page objects are
        bound to the Document.
        """
        if 0 <= index < self._page_count:
            return self.doc[index]
        raise IndexError(f"Page index {index} out of range (0..{self._page_count - 1})")

    def page_rect(self, index: int) -> fitz.Rect:
        """
        Get the bounding box/rectangle of a page in points.
        """
        if 0 <= index < self._page_count:
            return self._page_rects[index]
        raise IndexError(f"Page rect index {index} out of range (0..{self._page_count - 1})")

    def resolve_link_target_y(self, link: dict) -> float:
        """
        Extract and calculate top-down target Y coordinate in points for a link dictionary.
        Handles coordinate space differences between fitz.LINK_GOTO (top-down)
        and fitz.LINK_NAMED (PDF native bottom-up).
        """
        target_page = link.get("page")
        if target_page is None or not isinstance(target_page, int) or not (0 <= target_page < self._page_count):
            return 0.0
        
        target_rect = self.page_rect(target_page)
        to_point = link.get("to")
        if not isinstance(to_point, fitz.Point) or to_point.y < 0.0:
            return target_rect.height / 2.0
        
        raw_y = float(to_point.y)
        kind = link.get("kind")
        if kind == fitz.LINK_NAMED or (kind == 4):
            return max(0.0, target_rect.height - raw_y)
        return max(0.0, raw_y)

    def render_portal_pixmap(
        self,
        page_index: int,
        target_y: float,
        target_w: int = 600,
        target_h: int = 200,
    ) -> fitz.Pixmap:
        """
        Synchronously rasterize a cropped portal preview pixmap centered around target_y
        on page_index for programmatic library use.
        """
        page = self.get_page(page_index)
        page_rect = page.rect
        matrix_x = target_w / page_rect.width if page_rect.width > 0 else 1.0
        matrix_y = matrix_x
        crop_h = (target_h / matrix_x) if matrix_x > 0 else 160.0
        crop_y0 = max(0.0, target_y - (crop_h / 2.0))
        crop_y1 = min(page_rect.height, crop_y0 + crop_h)
        clip = fitz.Rect(0.0, crop_y0, page_rect.width, crop_y1)
        mat = fitz.Matrix(matrix_x, matrix_y)
        return page.get_pixmap(matrix=mat, clip=clip, alpha=False)

    def close(self):
        """
        Close the underlying fitz document and terminate the background link worker thread.
        """
        self._closed = True
        self._queue.put(None)
        if self.doc and not self.doc.is_closed:
            self.doc.close()

