import pytest
from pdfatlas.core.arxiv_mapper import arxiv_id_from_path, extract_arxiv_id_from_raw


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


def test_reconcile_moved_edits():
    from pdfatlas.core.arxiv_mapper import ArxivDiffMapper, SequenceMatcher

    mapper = ArxivDiffMapper()
    mapper.pdf_words = ["Intro", "FooterBlock", "Body"]
    mapper.tex_words = ["Intro", "Body", "FooterBlock"]

    matcher = SequenceMatcher(None, mapper.pdf_words, mapper.tex_words)
    mapper.diff_opcodes = matcher.get_opcodes()

    for tag, i1, i2, j1, j2 in mapper.diff_opcodes:
        if tag in ("equal", "replace"):
            p_len = i2 - i1
            t_len = j2 - j1
            common_len = max(p_len, t_len)
            for k in range(common_len):
                cp = i1 + k if k < p_len else i2 - 1
                ct = j1 + k if k < t_len else j2 - 1
                mapper.pdf_to_tex_map[cp] = ct
                mapper.tex_to_pdf_map[ct] = cp

    mapper.mapped_pdf_indices = set(mapper.tex_to_pdf_map.values())
    moved = mapper._reconcile_moved_edits(min_words=1, threshold=0.9)

    assert len(moved) > 0
    assert 1 in mapper.mapped_pdf_indices
    assert mapper.pdf_to_tex_map[1] == 2
    assert mapper.tex_to_pdf_map[2] == 1


def test_reconcile_moved_table_float_edits():
    from pdfatlas.core.arxiv_mapper import ArxivDiffMapper, SequenceMatcher

    mapper = ArxivDiffMapper()
    # PDF has Table 1 caption and text at top of page before Section 3.5
    mapper.pdf_words = [
        "Section", "3.4", "Overview",
        "Table", "1:", "Maximum", "path", "lengths,", "per-layer", "complexity", "and", "minimum", "number", "of", "sequential", "operations",
        "Section", "3.5", "Positional", "Encoding"
    ]
    # TeX has Table 1 float defined later with TeX macros (\begin{table}, \caption{...}, \hline, &)
    mapper.tex_words = [
        "Section", "3.4", "Overview",
        "Section", "3.5", "Positional", "Encoding",
        "\\begin{table}[t]", "\\caption{Maximum", "path", "lengths,", "per-layer", "complexity", "and", "minimum", "number", "of", "sequential", "operations}", "\\hline", "&"
    ]

    matcher = SequenceMatcher(None, mapper.pdf_words, mapper.tex_words)
    mapper.diff_opcodes = matcher.get_opcodes()

    for tag, i1, i2, j1, j2 in mapper.diff_opcodes:
        if tag in ("equal", "replace"):
            p_len = i2 - i1
            t_len = j2 - j1
            common_len = max(p_len, t_len)
            for k in range(common_len):
                cp = i1 + k if k < p_len else i2 - 1
                ct = j1 + k if k < t_len else j2 - 1
                mapper.pdf_to_tex_map[cp] = ct
                mapper.tex_to_pdf_map[ct] = cp

    mapper.mapped_pdf_indices = set(mapper.tex_to_pdf_map.values())
    moved = mapper._reconcile_moved_edits(min_words=1, threshold=0.45)

    assert len(moved) > 0
    # PDF Table 1 word 'Maximum' (index 5) should be reconciled to TeX 'Maximum' (index 9)
    assert 5 in mapper.mapped_pdf_indices
    assert mapper.pdf_to_tex_map[5] == 8
