import os
import pymupdf as fitz
import pytest

from pdfatlas.core.pdf_notes_import import (
    extract_embedded_notes_from_file,
)
from pdfatlas.core.index import get_db_for_pdf, load_notes_from_db


def test_extract_notes_from_synthetic_pdf(tmp_path):
    pdf_path = str(tmp_path / "test_annot.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=800)

    # Add a highlight annotation with comment
    rect = fitz.Rect(100, 200, 300, 250)
    annot = page.add_highlight_annot(rect)
    annot.set_info(content="This is a test note from a highlight")
    annot.update()

    # Add a sticky text annotation
    rect2 = fitz.Rect(50, 60, 70, 80)
    annot2 = page.add_text_annot(rect2.tl, "Sticky note content")
    annot2.update()

    doc.save(pdf_path)
    doc.close()

    notes = extract_embedded_notes_from_file(pdf_path)
    assert len(notes) == 2

    # Verify highlight note
    h_note = [n for n in notes if "highlight" in n["markdown"]][0]
    assert h_note["page"] == 1
    # mid-right of rect(100, 200, 300, 250): x = 300, y = 225
    assert h_note["x"] == pytest.approx(300.0, 0.1)
    assert h_note["y"] == pytest.approx(225.0, 0.1)
    assert h_note["markdown"] == "This is a test note from a highlight"

    # Verify sticky note
    s_note = [n for n in notes if "Sticky" in n["markdown"]][0]
    assert s_note["page"] == 1
    assert s_note["markdown"] == "Sticky note content"


def test_extract_notes_from_sample_student_report():
    sample_path = "/home/aziis98/Documents/Academia/GCM/previous-student-report.pdf"
    if not os.path.exists(sample_path):
        pytest.skip("Sample PDF not found")

    notes = extract_embedded_notes_from_file(sample_path)
    assert len(notes) >= 2

    # Check page 6 (0-indexed page 5) note
    note_p6 = [n for n in notes if n["page"] == 6][0]
    assert "a bit overselling" in note_p6["markdown"]
    assert note_p6["x"] == pytest.approx(482.28, 0.1)
    assert note_p6["y"] == pytest.approx(324.12, 0.1)

    # Check page 8 (0-indexed page 7) note
    note_p8 = [n for n in notes if n["page"] == 8][0]
    assert "inconsistency with next notations" in note_p8["markdown"]
    assert note_p8["x"] == pytest.approx(349.41, 0.1)
    assert note_p8["y"] == pytest.approx(667.57, 0.1)


def test_import_notes_into_db(tmp_path):
    pdf_path = str(tmp_path / "test_db_notes.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=800)
    rect = fitz.Rect(100, 200, 300, 250)
    annot = page.add_highlight_annot(rect)
    annot.set_info(content="Persistent note")
    annot.update()
    doc.save(pdf_path)
    doc.close()

    conn = get_db_for_pdf(pdf_path)
    try:
        notes = load_notes_from_db(conn)
        assert len(notes) == 1
        assert notes[0]["markdown"] == "Persistent note"
        assert notes[0]["x"] == pytest.approx(300.0, 0.1)
        assert notes[0]["y"] == pytest.approx(225.0, 0.1)
    finally:
        conn.close()
