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
            annot_type = annot.type[1] if isinstance(annot.type, tuple) else ""
            if annot_type == "Text":
                # Sticky note: center on icon
                x = float((rect.x0 + rect.x1) / 2.0)
                y = float((rect.y0 + rect.y1) / 2.0)
            else:
                # Highlight / text markup annotation:
                # Compute the whole bbox of the highlight and place the note slightly
                # to the right (+8 pt) of the mid-right border point.
                vertices = getattr(annot, "vertices", None)
                if vertices and len(vertices) >= 4:
                    vx = [_pt_coord(v)[0] for v in vertices]
                    vy = [_pt_coord(v)[1] for v in vertices]
                    bbox_x1 = max(float(rect.x1), max(vx))
                    bbox_y0 = min(float(rect.y0), min(vy))
                    bbox_y1 = max(float(rect.y1), max(vy))
                else:
                    bbox_x1 = float(rect.x1)
                    bbox_y0 = float(rect.y0)
                    bbox_y1 = float(rect.y1)

                offset_x = 8.0  # margin offset to the right of the highlight border
                x = bbox_x1 + offset_x
                y = float((bbox_y0 + bbox_y1) / 2.0)

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
