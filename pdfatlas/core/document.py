import re
import fitz

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


class DocumentModel:
    """
    A read-only model wrapper around fitz.Document.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.doc = fitz.open(filepath)
        self._page_count = len(self.doc)
        # Pre-cache page rectangles and links to avoid retrieving them repeatedly during GL render passes
        self._page_rects = [self.doc[i].rect for i in range(self._page_count)]
        self._page_links: list[list[dict]] = []
        for i in range(self._page_count):
            try:
                page = self.doc[i]
                links = page.get_links()
                auto_links = detect_text_arxiv_links(page, links)
                self._page_links.append(links + auto_links)
            except (RuntimeError, ValueError):
                self._page_links.append([])

    @property
    def page_count(self) -> int:
        return self._page_count

    def get_page_links(self, index: int) -> list[dict]:
        """
        Retrieve list of link dictionaries for a page.
        Pre-cached on init to ensure lock-free O(1) access during GL rendering.
        """
        if 0 <= index < self._page_count:
            return self._page_links[index]
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
        Close the underlying fitz document if not already closed.
        """
        if self.doc and not getattr(self.doc, "is_closed", False):
            self.doc.close()

