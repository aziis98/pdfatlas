"""
PDF embedded annotations extractor.
Extracts annotations (such as highlights and sticky notes) containing comments,
converts rich text / HTML comments to Markdown, and calculates anchor coordinates
at the mid-right edge of the annotation bounding box.
"""

from typing import Any
import pymupdf as fitz

from pdfatlas.core.html_to_markdown import html_to_markdown


def extract_embedded_notes_from_doc(doc: fitz.Document) -> list[dict[str, Any]]:
    """
    Extract notes from embedded annotations in a PyMuPDF Document.
    Returns a list of dicts: [{"page": int, "x": float, "y": float, "markdown": str}, ...]
    where 'page' is 1-indexed (matching PDF Atlas SQLite schema).
    """
    notes: list[dict[str, Any]] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        for annot in page.annots():
            rect = annot.rect
            if not rect or rect.is_empty:
                continue

            # Check for rich text in /RC
            content_text = ""
            try:
                xref = annot.xref
                if xref:
                    rc_xml = doc.xref_get_key(xref, "RC")
                    if rc_xml and rc_xml[0] == "string":
                        content_text = rc_xml[1]
            except Exception:
                pass

            # Fallback to info["content"]
            if not content_text:
                info = annot.info
                content_text = (info.get("content") or "").strip()

            if not content_text:
                continue

            markdown = html_to_markdown(content_text)
            if not markdown:
                continue

            # Position note at the mid-right edge of the annotation bbox
            # x = rect.x1, y = (rect.y0 + rect.y1) / 2.0
            mid_right_x = float(rect.x1)
            mid_right_y = float((rect.y0 + rect.y1) / 2.0)

            notes.append({
                "page": page_idx + 1,  # 1-indexed
                "x": mid_right_x,
                "y": mid_right_y,
                "markdown": markdown,
            })

    return notes


def extract_embedded_notes_from_file(pdf_path: str) -> list[dict[str, Any]]:
    """Open a PDF file and extract all embedded annotation notes."""
    doc = fitz.open(pdf_path)
    try:
        return extract_embedded_notes_from_doc(doc)
    finally:
        doc.close()
