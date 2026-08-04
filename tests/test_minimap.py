from pdfatlas.ui.minimap import compute_grid


def test_compute_grid():
    n_cols, n_rows, scale, thumb_w, thumb_h, cell_w, cell_h = compute_grid(
        page_count=10,
        alloc_w=1000.0,
        alloc_h=800.0,
        first_page_w=100.0,
        first_page_h=150.0,
    )
    assert n_cols * n_rows >= 10
    assert scale > 0
    assert thumb_w > 0
    assert thumb_h > 0
    assert cell_w > 0
    assert cell_h > 0
