from dataclasses import dataclass, field
from typing import Any, cast

from .document import DocumentModel


@dataclass
class CharInfo:
    """A single word or character element with its bounding box in PDF points."""

    char: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    line_y: float
    block_no: int = 0
    line_no: int = 0
    word_no: int = 0


@dataclass
class PageCharIndex:
    """Per-page word/character index built from PyMuPDF's words engine."""

    page_index: int
    chars: list[CharInfo] = field(default_factory=list)


class TextSelection:
    """
    Manages text selection state and word/character-level hit testing for PDF pages.
    Extracts text layout elements from PyMuPDF's native words layout engine.
    """

    def __init__(self, doc_model: DocumentModel):
        self.doc_model = doc_model
        self._page_indices: dict[int, PageCharIndex] = {}

        self.anchor_page: int | None = None
        self.anchor_char_idx: int | None = None
        self.focus_page: int | None = None
        self.focus_char_idx: int | None = None
        self.is_selecting: bool = False

    def get_page_index(self, page_index: int) -> PageCharIndex:
        """Lazily build and return the character/word index for a page."""
        if page_index not in self._page_indices:
            self._build_page_index(page_index)
        return self._page_indices[page_index]

    def _build_page_index(self, page_index: int) -> None:
        """Extract individual character tokens from a page using PyMuPDF's rawdict layout engine."""
        page = self.doc_model.get_page(page_index)
        try:
            rawdict = cast(dict[str, Any], page.get_text("rawdict"))
        except (RuntimeError, ValueError):
            self._page_indices[page_index] = PageCharIndex(page_index=page_index)
            return

        chars: list[CharInfo] = []
        for b_no, block in enumerate(rawdict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for l_no, line in enumerate(block.get("lines", [])):
                bbox_line = line.get("bbox", (0, 0, 0, 0))
                line_y = (float(bbox_line[1]) + float(bbox_line[3])) / 2.0
                for span in line.get("spans", []):
                    for w_no, ch in enumerate(span.get("chars", [])):
                        c_text = str(ch.get("c", ""))
                        c_bbox = ch.get("bbox")
                        if c_bbox and len(c_bbox) == 4:
                            x0, y0, x1, y1 = c_bbox
                            chars.append(
                                CharInfo(
                                    char=c_text,
                                    bbox=(float(x0), float(y0), float(x1), float(y1)),
                                    line_y=line_y,
                                    block_no=b_no,
                                    line_no=l_no,
                                    word_no=w_no,
                                )
                            )

        self._page_indices[page_index] = PageCharIndex(
            page_index=page_index,
            chars=chars,
        )

    def hit_test(self, page_index: int, pt_x: float, pt_y: float) -> int | None:
        """
        Find the character index closest to the given PDF point (pt_x, pt_y) on a page.
        Returns the index into the page's char list, or None if empty.
        """
        pi = self.get_page_index(page_index)
        if not pi.chars:
            return None

        best_idx = None
        best_dist = float("inf")

        for i, c in enumerate(pi.chars):
            x0, y0, x1, y1 = c.bbox

            if y0 <= pt_y <= y1:
                dy = 0.0
            else:
                dy = min(abs(pt_y - y0), abs(pt_y - y1))

            if x0 <= pt_x <= x1:
                dx = 0.0
            else:
                dx = min(abs(pt_x - x0), abs(pt_x - x1))

            dist = dy * 4.0 + dx
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        return best_idx

    def get_word_start_char_idx(self, page_index: int, char_idx: int) -> int:
        """Find the start char index of the word containing char_idx."""
        pi = self.get_page_index(page_index)
        if not pi.chars or char_idx < 0 or char_idx >= len(pi.chars):
            return char_idx
        idx = char_idx
        while idx > 0 and pi.chars[idx - 1].char != " " and abs(pi.chars[idx - 1].line_y - pi.chars[char_idx].line_y) < 3.0:
            idx -= 1
        return idx

    def start_selection(self, page_index: int, char_idx: int) -> None:
        """Begin a new selection at the given element."""
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
        if not self.has_selection():
            return None

        if self.anchor_page == page_index and self.focus_page == page_index:
            a = self.anchor_char_idx or 0
            f = self.focus_char_idx or 0
            return (min(a, f), max(a, f))

        if self.anchor_page is not None and self.focus_page is not None:
            if self.anchor_page <= self.focus_page:
                page_start, page_end = self.anchor_page, self.focus_page
            else:
                page_start, page_end = self.focus_page, self.anchor_page

            if page_index == page_start:
                start_char = self.anchor_char_idx if self.anchor_page == page_index else self.focus_char_idx
                pi = self.get_page_index(page_index)
                return (start_char or 0, len(pi.chars) - 1)
            elif page_index == page_end:
                end_char = self.focus_char_idx if self.focus_page == page_index else self.anchor_char_idx
                return (0, end_char or 0)
            elif page_start < page_index < page_end:
                pi = self.get_page_index(page_index)
                return (0, len(pi.chars) - 1)

        return None

    def get_selection_rects(self, page_index: int) -> list[tuple[float, float, float, float]]:
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

        rects: list[tuple[float, float, float, float]] = []
        line_chars: list[CharInfo] = [selected[0]]

        for c in selected[1:]:
            if (
                abs(c.line_y - line_chars[-1].line_y) < 3.0
                and c.block_no == line_chars[-1].block_no
                and c.line_no == line_chars[-1].line_no
            ):
                line_chars.append(c)
            else:
                rects.append(self._merge_line_rect(line_chars))
                line_chars = [c]

        if line_chars:
            rects.append(self._merge_line_rect(line_chars))

        return rects

    def _merge_line_rect(self, chars: list[CharInfo]) -> tuple[float, float, float, float]:
        x0 = min(c.bbox[0] for c in chars)
        y0 = min(c.bbox[1] for c in chars)
        x1 = max(c.bbox[2] for c in chars)
        y1 = max(c.bbox[3] for c in chars)
        return (x0, y0, x1, y1)

    def get_selected_text(self, page_index: int | None = None) -> str:
        if not self.has_selection() or self.anchor_page is None or self.focus_page is None:
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
                selected = pi.chars[start : end + 1]
                page_text_arr = []
                for idx, c in enumerate(selected):
                    page_text_arr.append(c.char)
                    if idx < len(selected) - 1:
                        next_c = selected[idx + 1]
                        if (
                            c.char != " "
                            and next_c.char != " "
                            and abs(c.line_y - next_c.line_y) < 3.0
                            and next_c.bbox[0] - c.bbox[2] > 2.0
                        ):
                            page_text_arr.append(" ")
                parts.append("".join(page_text_arr))

        return "\n".join(parts)

    def get_forward_char_rects(
        self, page_index: int, start_char_idx: int, word_count: int = 50
    ) -> list[tuple[float, float, float, float]]:
        pi = self.get_page_index(page_index)
        if not pi.chars or start_char_idx < 0 or start_char_idx >= len(pi.chars):
            return []
        end_idx = min(len(pi.chars) - 1, start_char_idx + word_count - 1)
        return [c.bbox for c in pi.chars[start_char_idx : end_idx + 1]]

    def get_word_rects_for_char(
        self, page_index: int, char_idx: int
    ) -> list[tuple[float, float, float, float]]:
        pi = self.get_page_index(page_index)
        if not pi.chars or char_idx < 0 or char_idx >= len(pi.chars):
            return []
        return [pi.chars[char_idx].bbox]

    def get_forward_word_rects(
        self, page_index: int, start_char_idx: int, word_count: int = 50
    ) -> list[tuple[float, float, float, float]]:
        return self.get_forward_char_rects(page_index, start_char_idx, word_count)

    def invalidate_page(self, page_index: int) -> None:
        self._page_indices.pop(page_index, None)
