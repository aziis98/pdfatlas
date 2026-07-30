import pytest
from pdf_viewer.core.arxiv_mapper import arxiv_id_from_path, extract_arxiv_id_from_raw


@pytest.mark.parametrize(
    "raw_input, expected_id",
    [
        # Legacy arXiv URLs
        ("https://arxiv.org/abs/hep-ph/9504271", "hep-ph/9504271"),
        ("https://arxiv.org/pdf/hep-ph/9504271", "hep-ph/9504271"),
        ("https://arxiv.org/pdf/hep-ph/9504271.pdf", "hep-ph/9504271"),
        ("https://arxiv.org/abs/hep-ph/9504271v2", "hep-ph/9504271v2"),
        ("https://arxiv.org/pdf/hep-ph/9504271v2.pdf", "hep-ph/9504271v2"),
        ("https://arxiv.org/abs/math.DG/0101001", "math.DG/0101001"),
        ("https://arxiv.org/abs/cs.CV/0112001", "cs.CV/0112001"),
        ("https://arxiv.org/abs/physics.fluid-dyn/0405001", "physics.fluid-dyn/0405001"),
        # Modern arXiv URLs
        ("https://arxiv.org/abs/2305.12345", "2305.12345"),
        ("https://arxiv.org/pdf/2305.12345.pdf", "2305.12345"),
        ("https://arxiv.org/abs/2305.12345v2", "2305.12345v2"),
        # Raw arXiv IDs and prefixes
        ("arxiv:hep-ph/9504271", "hep-ph/9504271"),
        ("arxiv:2305.12345", "2305.12345"),
        ("hep-ph/9504271", "hep-ph/9504271"),
        ("math.DG/0101001", "math.DG/0101001"),
        ("2305.12345", "2305.12345"),
        ("2305.12345v2", "2305.12345v2"),
    ],
)
def test_extract_arxiv_id_from_raw(raw_input: str, expected_id: str):
    assert extract_arxiv_id_from_raw(raw_input) == expected_id


@pytest.mark.parametrize(
    "raw_input",
    [
        "https://example.com/paper.pdf",
        "/home/user/document.pdf",
        "random_text_without_arxiv_id",
        "",
    ],
)
def test_extract_arxiv_id_from_raw_invalid(raw_input: str):
    assert extract_arxiv_id_from_raw(raw_input) is None


@pytest.mark.parametrize(
    "path_str, expected_id",
    [
        # Cache paths
        ("/home/user/.cache/pdfatlas/source-arxiv/hep-ph/9504271/paper.pdf", "hep-ph/9504271"),
        ("/home/user/.cache/pdfatlas/source-arxiv/2305.12345/paper.pdf", "2305.12345"),
        # File paths with category directories
        ("/downloads/hep-ph/9504271.pdf", "hep-ph/9504271"),
        ("/downloads/math.DG/0101001.pdf", "math.DG/0101001"),
        # File paths with modern IDs
        ("/downloads/2305.12345.pdf", "2305.12345"),
        ("/downloads/2305.12345v2.pdf", "2305.12345v2"),
        # Direct URL passed as path string
        ("https://arxiv.org/abs/hep-ph/9504271", "hep-ph/9504271"),
    ],
)
def test_arxiv_id_from_path(path_str: str, expected_id: str):
    assert arxiv_id_from_path(path_str) == expected_id


@pytest.mark.parametrize(
    "path_str",
    [
        "/home/user/documents/report.pdf",
        "/tmp/random_file.txt",
        "",
    ],
)
def test_arxiv_id_from_path_invalid(path_str: str):
    assert arxiv_id_from_path(path_str) is None
