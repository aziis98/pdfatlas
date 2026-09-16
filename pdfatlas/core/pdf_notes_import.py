"""
PDF embedded annotations extractor.
Extracts annotations (such as highlights and sticky notes) containing comments,
converts rich text / HTML comments to Markdown, and calculates anchor coordinates
at the exact note / highlight position.
"""

from typing import Any
import pymupdf as fitz

from pdfatlas.core.html_to_markdown import html_to_markdown


def _pt_coord(pt: Any) -> tuple[float, float]:
    """Safely extract (x, y) from a point object or tuple/list."""
    if isinstance(pt, (tuple, list)):
        return float(pt[0]), float(pt[1])
    return float(pt.x), float(pt.y)


def extract_embedded_notes_from_doc(doc: fitz.Document) -> list[dict[str, Any]]:
    """
    Extract notes from embedded annotations in a PyMuPDF Document.
    Returns a list of dicts: [{"page": int, "x": float, "y": float, "markdown": str}, ...]
    where 'page' is 0-indexed (matching PDF Atlas layout and database schema).
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

            # Calculate note anchor position
            vertices = getattr(annot, "vertices", None)
            annot_type = annot.type[1] if isinstance(annot.type, tuple) else ""
            if annot_type == "Text":
                # Sticky note: center on icon
                x = float((rect.x0 + rect.x1) / 2.0)
                y = float((rect.y0 + rect.y1) / 2.0)
            elif vertices and len(vertices) >= 4 and len(vertices) % 4 == 0:
                # Text markup (Highlight, Underline, etc.): end of the last highlighted line
                last_ur_x, last_ur_y = _pt_coord(vertices[-3])
                last_lr_x, last_lr_y = _pt_coord(vertices[-1])
                x = max(last_ur_x, last_lr_x)
                y = (last_ur_y + last_lr_y) / 2.0
            else:
                # Default: mid-right edge of annotation bounding box
                x = float(rect.x1)
                y = float((rect.y0 + rect.y1) / 2.0)

            notes.append({
                "page": page_idx,  # 0-indexed
                "x": x,
                "y": y,
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
