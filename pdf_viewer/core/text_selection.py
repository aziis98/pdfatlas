from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .document import DocumentModel


@dataclass
class CharInfo:
    """A single character with its bounding box in PDF points."""

    char: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    line_y: float  # vertical center of the line (for grouping)


@dataclass
class PageCharIndex:
    """Per-page character index built from rawdict."""

    page_index: int
    chars: list[CharInfo] = field(default_factory=list)
    line_y_values: list[float] = field(default_factory=list)  # sorted unique line Y centers


class TextSelection:
    """
    Manages text selection state and character-level hit testing for PDF pages.
    Extracts character data from PyMuPDF rawdict and provides selection queries.
    """

    def __init__(self, doc_model: DocumentModel):
        self.doc_model = doc_model
        self._page_indices: dict[int, PageCharIndex] = {}

        # Selection state: anchor is where the drag started, focus is current position
        self.anchor_page: int | None = None
        self.anchor_char_idx: int | None = None
        self.focus_page: int | None = None
        self.focus_char_idx: int | None = None
        self.is_selecting: bool = False

    def get_page_index(self, page_index: int) -> PageCharIndex:
        """Lazily build and return the character index for a page."""
        if page_index not in self._page_indices:
            self._build_page_index(page_index)
        return self._page_indices[page_index]

    def _build_page_index(self, page_index: int) -> None:
        """Extract character-level data from a page using rawdict."""
        page = self.doc_model.get_page(page_index)
        try:
            raw_dict = page.get_text("rawdict")
        except Exception:
            self._page_indices[page_index] = PageCharIndex(page_index=page_index)
            return

        chars: list[CharInfo] = []
        raw_dict_dict: dict = dict(raw_dict)  # type: ignore[arg-type]
        for block in raw_dict_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_bbox = line.get("bbox", (0, 0, 0, 0))
                line_y_center = (line_bbox[1] + line_bbox[3]) / 2.0
                for span in line.get("spans", []):
                    for c_info in span.get("chars", []):
                        c_bbox = c_info.get("bbox", (0, 0, 0, 0))
                        c_text = c_info.get("c", "")
                        if c_text:
                            chars.append(
                                CharInfo(
                                    char=c_text,
                                    bbox=(c_bbox[0], c_bbox[1], c_bbox[2], c_bbox[3]),
                                    line_y=line_y_center,
                                )
                            )

        # Sort by line_y then x0 for consistent ordering
        chars.sort(key=lambda c: (c.line_y, c.bbox[0]))

        # Build sorted unique line_y values for binary search
        line_y_set: set[float] = set()
        for c in chars:
            line_y_set.add(round(c.line_y, 2))
        line_y_values = sorted(line_y_set)

        self._page_indices[page_index] = PageCharIndex(
            page_index=page_index,
            chars=chars,
            line_y_values=line_y_values,
        )

    def hit_test(self, page_index: int, pt_x: float, pt_y: float) -> int | None:
        """
        Find the character index closest to the given PDF point on a page.
        Returns the index into the page's char list, or None if no chars.
        """
        pi = self.get_page_index(page_index)
        if not pi.chars:
            return None

        # First pass: find the closest line_y
        if not pi.line_y_values:
            return None

        target_y = round(pt_y, 2)
        line_idx = bisect.bisect_left(pi.line_y_values, target_y)
        best_line_y = None
        best_line_dist = float("inf")

        # Check the closest 2 line_y values
        for offset in (-1, 0, 1):
            candidate_idx = line_idx + offset
            if 0 <= candidate_idx < len(pi.line_y_values):
                ly = pi.line_y_values[candidate_idx]
                dist = abs(ly - target_y)
                if dist < best_line_dist:
                    best_line_dist = dist
                    best_line_y = ly

        if best_line_y is None:
            return None

        # Find chars on that line and pick the closest horizontally
        best_idx = None
        best_dist = float("inf")
        for i, c in enumerate(pi.chars):
            if round(c.line_y, 2) != best_line_y:
                continue
            cx = (c.bbox[0] + c.bbox[2]) / 2.0
            dist = abs(cx - pt_x)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        return best_idx

    def start_selection(self, page_index: int, char_idx: int) -> None:
        """Begin a new selection at the given character."""
        self.anchor_page = page_index
        self.anchor_char_idx = char_idx
        self.focus_page = page_index
        self.focus_char_idx = char_idx
        self.is_selecting = True

    def update_focus(self, page_index: int, char_idx: int) -> None:
        """Update the focus point of an active selection."""
        if self.is_selecting:
            self.focus_page = page_index
            self.focus_char_idx = char_idx

    def end_selection(self) -> None:
        """Finalize the selection."""
        self.is_selecting = False

    def clear_selection(self) -> None:
        """Clear all selection state."""
        self.anchor_page = None
        self.anchor_char_idx = None
        self.focus_page = None
        self.focus_char_idx = None
        self.is_selecting = False

    def has_selection(self) -> bool:
        """Check if there is an active selection."""
        return (
            self.anchor_page is not None
            and self.anchor_char_idx is not None
            and self.focus_page is not None
            and self.focus_char_idx is not None
        )

    def _selection_range(self, page_index: int) -> tuple[int, int] | None:
        """
        Return (start_idx, end_idx) character range for the given page,
        or None if the selection doesn't involve this page.
        """
        if not self.has_selection():
            return None

        if self.anchor_page == page_index and self.focus_page == page_index:
            # Single-page selection
            a = self.anchor_char_idx or 0
            f = self.focus_char_idx or 0
            return (min(a, f), max(a, f))

        # Multi-page: select all text on pages between anchor and focus
        # Determine page order
        if self.anchor_page is not None and self.focus_page is not None:
            if self.anchor_page <= self.focus_page:
                page_start, page_end = self.anchor_page, self.focus_page
            else:
                page_start, page_end = self.focus_page, self.anchor_page

            if page_index == page_start:
                # From anchor (or focus) to end of page
                start_char = self.anchor_char_idx if self.anchor_page == page_index else self.focus_char_idx
                pi = self.get_page_index(page_index)
                return (start_char or 0, len(pi.chars) - 1)
            elif page_index == page_end:
                # From start of page to focus (or anchor)
                end_char = self.focus_char_idx if self.focus_page == page_index else self.anchor_char_idx
                return (0, end_char or 0)
            elif page_start < page_index < page_end:
                # Full page selection
                pi = self.get_page_index(page_index)
                return (0, len(pi.chars) - 1)

        return None

    def get_selection_rects(self, page_index: int) -> list[tuple[float, float, float, float]]:
        """
        Return a list of bounding rectangles (in PDF points) for the selected text
        on the given page. Merges adjacent characters on the same line into wider rectangles.
        """
        rng = self._selection_range(page_index)
        if rng is None:
            return []

        start, end = rng
        pi = self.get_page_index(page_index)
        if not pi.chars or start >= len(pi.chars):
            return []

        end = min(end, len(pi.chars) - 1)
        selected = pi.chars[start : end + 1]
        if not selected:
            return []

        # Group consecutive chars by line_y (allowing small floating point tolerance)
        rects: list[tuple[float, float, float, float]] = []
        line_chars: list[CharInfo] = [selected[0]]

        for c in selected[1:]:
            if abs(c.line_y - line_chars[-1].line_y) < 1.0:
                line_chars.append(c)
            else:
                rects.append(self._merge_line_rect(line_chars))
                line_chars = [c]

        if line_chars:
            rects.append(self._merge_line_rect(line_chars))

        return rects

    def _merge_line_rect(
        self, chars: list[CharInfo]
    ) -> tuple[float, float, float, float]:
        """Merge a group of characters on the same line into a single bounding rect."""
        x0 = min(c.bbox[0] for c in chars)
        y0 = min(c.bbox[1] for c in chars)
        x1 = max(c.bbox[2] for c in chars)
        y1 = max(c.bbox[3] for c in chars)
        return (x0, y0, x1, y1)

    def get_selected_text(self, page_index: int | None = None) -> str:
        """
        Return the selected text as a string.
        If page_index is given, only return text from that page.
        Otherwise, return all selected text across pages.
        """
        if not self.has_selection():
            return ""

        if self.anchor_page is None or self.focus_page is None:
            return ""

        if self.anchor_page <= self.focus_page:
            page_start, page_end = self.anchor_page, self.focus_page
        else:
            page_start, page_end = self.focus_page, self.anchor_page

        if page_index is not None:
            page_start = page_end = page_index

        parts: list[str] = []
        for pi_idx in range(page_start, page_end + 1):
            rng = self._selection_range(pi_idx)
            if rng is None:
                continue
            start, end = rng
            pi = self.get_page_index(pi_idx)
            end = min(end, len(pi.chars) - 1)
            if start <= end and start < len(pi.chars):
                page_text = "".join(c.char for c in pi.chars[start : end + 1])
                parts.append(page_text)

        return "\n".join(parts)

    def invalidate_page(self, page_index: int) -> None:
        """Remove cached index for a page (e.g. if document changes)."""
        self._page_indices.pop(page_index, None)
