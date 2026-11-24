from __future__ import annotations

from unittest.mock import MagicMock

from pdfatlas.core.text_selection import CharInfo, PageCharIndex, TextSelection


def _make_selection(pages_text: list[str]) -> TextSelection:
    doc = MagicMock()
    doc.page_count = len(pages_text)
    sel = TextSelection(doc)
    for p_idx, text in enumerate(pages_text):
        chars: list[CharInfo] = []
        for i, ch in enumerate(text):
            x0 = float(i * 10)
            chars.append(
                CharInfo(
                    char=ch,
                    bbox=(x0, 100.0, x0 + 9.0, 112.0),
                    line_y=106.0,
                    block_no=0,
                    line_no=0,
                    word_no=i,
                )
            )
        sel._page_indices[p_idx] = PageCharIndex(page_index=p_idx, chars=chars)
    return sel


def test_word_drag_forward_and_backward_keeps_anchor_word():
    # "alpha beta gamma delta"
    #  0..4  6..9 11..15 17..21
    sel = _make_selection(["alpha beta gamma delta"])

    # Double-click inside "gamma" (idx 13)
    sel.start_word_selection(0, 13)
    assert sel.get_selected_text() == "gamma"

    # Drag forward into "delta" (idx 18) -> "gamma delta"
    sel.update_word_focus(0, 18)
    assert sel.get_selected_text() == "gamma delta"

    # Drag backward into "beta" (idx 8) -> entire anchor word "gamma" must remain selected
    sel.update_word_focus(0, 8)
    assert sel.get_selected_text() == "beta gamma"

    # Drag further backward to first word "alpha" (idx 2, word starts at char 0)
    sel.update_word_focus(0, 2)
    assert sel.get_selected_text() == "alpha beta gamma"

    # Drag back to anchor word "gamma" (idx 12) -> just "gamma"
    sel.update_word_focus(0, 12)
    assert sel.get_selected_text() == "gamma"

    # Drag forward again into "delta" (idx 20) -> "gamma delta"
    sel.update_word_focus(0, 20)
    assert sel.get_selected_text() == "gamma delta"

    sel.end_selection()
    assert sel.is_selecting is False
    assert sel.get_selected_text() == "gamma delta"


def test_word_drag_starting_at_char_zero_and_cross_page():
    # Page 0: "one two", Page 1: "three four"
    sel = _make_selection(["one two", "three four"])

    # Start on page 0, word 0 ("one", starts at index 0) -> drag forward to page 1 ("three")
    sel.start_word_selection(0, 0)
    assert sel.get_selected_text() == "one"
    sel.update_word_focus(1, 2)
    assert sel.get_selected_text() == "one two\nthree"

    # Start on page 1, "four" -> drag backward onto page 0, "two" (page_idx=0 must not be treated as falsy)
    sel.start_word_selection(1, 7)
    assert sel.get_selected_text() == "four"
    sel.update_word_focus(0, 5)
    assert sel.get_selected_text() == "two\nthree four"
