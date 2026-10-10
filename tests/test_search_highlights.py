from __future__ import annotations

from unittest.mock import MagicMock
import fitz
from pdfatlas.controllers.search import SearchController
from pdfatlas.ui.gl_renderer import QuadRenderer


def test_extract_search_highlights_single_and_multi_word():
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 100), "Convolutional neural networks are effective.")
    p2 = doc.new_page()
    p2.insert_text((50, 100), "Another page with neural and graph methods.")

    mock_win = MagicMock()
    mock_doc_model = MagicMock()
    mock_doc_model.doc = doc
    mock_win.doc_model = mock_doc_model
    mock_win.canvas.page_layout = [(0, 600, 800, None), (800, 600, 800, None)]

    controller = SearchController(mock_win)

    # 1. Single word search "neural" across both pages
    hl_single = controller._extract_search_highlights("neural", {"neural"})
    assert 0 in hl_single
    assert 1 in hl_single
    assert len(hl_single[0]) == 1
    assert len(hl_single[1]) == 1

    # 2. Phrase search "neural networks" - only matches on page 0 as a phrase
    hl_phrase = controller._extract_search_highlights("neural networks", {"neural", "networks"})
    assert 0 in hl_phrase
    # On page 1, "neural networks" is not a phrase, but "neural" is present
    assert 1 in hl_phrase
    assert len(hl_phrase[0]) == 1

    # 3. Non-matching search
    hl_none = controller._extract_search_highlights("transformer", {"transformer"})
    assert len(hl_none) == 0


def test_search_controller_on_row_clicked():
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 100), "Attention is all you need.")

    mock_win = MagicMock()
    mock_doc_model = MagicMock()
    mock_doc_model.doc = doc
    mock_win.doc_model = mock_doc_model
    mock_win.canvas.page_layout = [(0, 600, 800, None)]
    mock_win.entry.get_text.return_value = "Attention"

    controller = SearchController(mock_win)

    result = {
        "id": 1,
        "page": 1,
        "x0": 50.0,
        "y0": 90.0,
        "x1": 150.0,
        "y1": 110.0,
    }

    controller.on_row_clicked(result, {"attention"})

    mock_win.canvas.set_search_highlights.assert_called_once()
    highlights_arg = mock_win.canvas.set_search_highlights.call_args[0][0]
    assert 0 in highlights_arg
    assert len(highlights_arg[0]) == 1
    mock_win.stack.set_visible_child_name.assert_called_once_with("document-view")


def test_quad_renderer_stroke_rects():
    renderer = QuadRenderer()
    renderer.fill_rects = MagicMock()

    rects = [(10.0, 20.0, 100.0, 50.0)]
    color = (1.0, 0.55, 0.0, 1.0)
    renderer.stroke_rects(rects, color, line_width=2.0)

    renderer.fill_rects.assert_called_once()
    quads, col = renderer.fill_rects.call_args[0]
    assert col == color
    # 4 border quads generated
    assert len(quads) == 4
    # Top: (x, y, w, lw)
    assert quads[0] == (10.0, 20.0, 100.0, 2.0)
    # Bottom: (x, y + h - lw, w, lw)
    assert quads[1] == (10.0, 68.0, 100.0, 2.0)
    # Left: (x, y + lw, lw, h - 2*lw)
    assert quads[2] == (10.0, 22.0, 2.0, 46.0)
    # Right: (x + w - lw, y + lw, lw, h - 2*lw)
    assert quads[3] == (108.0, 22.0, 2.0, 46.0)


def test_canvas_search_highlights_state():
    from pdfatlas.ui.canvas import PDFCanvas

    canvas = MagicMock(spec=PDFCanvas)
    canvas.search_highlights = {}
    canvas.highlighted_block = None
    canvas.gl_canvas = MagicMock()

    canvas.set_search_highlights = PDFCanvas.set_search_highlights.__get__(canvas, PDFCanvas)
    canvas.clear_search_highlights = PDFCanvas.clear_search_highlights.__get__(canvas, PDFCanvas)
    canvas.has_search_highlights = PDFCanvas.has_search_highlights.__get__(canvas, PDFCanvas)
    canvas.set_highlighted_block = PDFCanvas.set_highlighted_block.__get__(canvas, PDFCanvas)

    canvas.set_search_highlights({0: [(10.0, 20.0, 30.0, 40.0)]})
    assert canvas.has_search_highlights() is True
    assert 0 in canvas.search_highlights
    canvas.gl_canvas.queue_draw.assert_called()

    canvas.clear_search_highlights()
    assert canvas.has_search_highlights() is False
    assert canvas.search_highlights == {}

    # Test legacy set_highlighted_block
    canvas.set_highlighted_block(1, (5.0, 10.0, 15.0, 20.0))
    assert canvas.has_search_highlights() is True
    assert canvas.search_highlights == {1: [(5.0, 10.0, 15.0, 20.0)]}

    canvas.set_highlighted_block(1, None)
    assert canvas.has_search_highlights() is False
    assert canvas.search_highlights == {}

