"""Tests for link target Y resolution and portal rendering in DocumentModel."""

import fitz
from pdfatlas.core.document import DocumentModel


def test_resolve_link_target_y_kind_goto(tmp_path):
    # Create dummy PDF
    pdf_file = tmp_path / "test_goto.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))

    # Kind 1 (LINK_GOTO): to_point.y is top-down (e.g. 180.0 pt from top)
    link_kind_1 = {
        "kind": fitz.LINK_GOTO,
        "page": 1,
        "to": fitz.Point(70.0, 180.0),
    }

    target_y = model.resolve_link_target_y(link_kind_1)
    assert abs(target_y - 180.0) < 1e-3

    model.close()


def test_resolve_link_target_y_kind_named(tmp_path):
    pdf_file = tmp_path / "test_named.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))

    # Kind 4 (LINK_NAMED): to_point.y is PDF native bottom-up (e.g. 640.0 pt from bottom of an 800 pt page -> 160 pt from top)
    link_kind_4 = {
        "kind": fitz.LINK_NAMED,
        "page": 1,
        "to": fitz.Point(70.0, 640.0),
    }

    target_y = model.resolve_link_target_y(link_kind_4)
    assert abs(target_y - 160.0) < 1e-3

    model.close()


def test_render_portal_pixmap(tmp_path):
    pdf_file = tmp_path / "test_portal.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((100, 200), "Hello Portal")
    doc.save(str(pdf_file))
    doc.close()

    model = DocumentModel(str(pdf_file))
    pix = model.render_portal_pixmap(page_index=0, target_y=200.0, target_w=300, target_h=100)

    assert pix is not None
    assert pix.width > 0
    assert pix.height > 0

    model.close()
