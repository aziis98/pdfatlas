import fitz

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from .document import DocumentModel
from .settings import CropSettings


class CropAnalyzer:
    """
    Analyzes document pages to detect content bounding boxes by trimming white margins.
    Caches raw content bounding boxes (in points) to avoid re-rendering pages on settings change.
    """

    ANALYSIS_SCALE = 0.2  # 20% scale for fast scan (approx 14.4 dpi)

    def __init__(self, doc_model: DocumentModel):
        self.doc_model = doc_model
        self.page_count = doc_model.page_count
        # Cached raw content bounding box (fitz.Rect) in page coordinates, or None if blank
        self.raw_bboxes = [None] * self.page_count
        # Status of whether each page has been scanned yet
        self.scanned = [False] * self.page_count
        # Computed final crop rects for each page
        self.crop_rects = [None] * self.page_count
        self._doc = None

    def scan_page(self, page_index: int) -> fitz.Rect | None:
        """
        Renders the page at low resolution and detects the content bounding box.
        Saves the result in self.raw_bboxes.
        """
        if self.scanned[page_index]:
            return self.raw_bboxes[page_index]

        # Use private document instance for thread safety during background scan
        if self._doc is None:
            self._doc = fitz.open(self.doc_model.filepath)

        page = self._doc[page_index]
        # Render page at 0.2x scale, without alpha (since we want white background)
        mat = fitz.Matrix(self.ANALYSIS_SCALE, self.ANALYSIS_SCALE)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        width = pix.width
        height = pix.height
        stride = pix.stride
        n = pix.n  # Number of components (usually 3 for RGB)

        bbox_pixels = None  # (min_col, min_row, max_col, max_row)

        if HAS_NUMPY:
            # Fast numpy scanning
            arr = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape((height, width, n))
            # True where pixel is not white (threshold 240)
            non_white = (arr[:, :, 0] <= 240) | (arr[:, :, 1] <= 240) | (arr[:, :, 2] <= 240)

            rows = np.any(non_white, axis=1)
            cols = np.any(non_white, axis=0)

            if np.any(rows) and np.any(cols):
                min_row = int(np.where(rows)[0][0])
                max_row = int(np.where(rows)[0][-1])
                min_col = int(np.where(cols)[0][0])
                max_col = int(np.where(cols)[0][-1])
                bbox_pixels = (min_col, min_row, max_col, max_row)
        else:
            # Fallback pure-Python scanning
            data = pix.samples
            min_row, max_row = None, None

            # Find min_row
            for r in range(height):
                offset = r * stride
                row_has_content = False
                for c in range(width):
                    idx = offset + c * n
                    if data[idx] <= 240 or data[idx + 1] <= 240 or data[idx + 2] <= 240:
                        row_has_content = True
                        break
                if row_has_content:
                    min_row = r
                    break

            if min_row is not None:
                # Find max_row
                for r in range(height - 1, min_row - 1, -1):
                    offset = r * stride
                    row_has_content = False
                    for c in range(width):
                        idx = offset + c * n
                        if data[idx] <= 240 or data[idx + 1] <= 240 or data[idx + 2] <= 240:
                            row_has_content = True
                            break
                    if row_has_content:
                        max_row = r
                        break

                # Find min_col
                min_col = None
                for c in range(width):
                    col_has_content = False
                    for r in range(min_row, max_row + 1):
                        idx = r * stride + c * n
                        if data[idx] <= 240 or data[idx + 1] <= 240 or data[idx + 2] <= 240:
                            col_has_content = True
                            break
                    if col_has_content:
                        min_col = c
                        break

                # Find max_col
                max_col = None
                for c in range(width - 1, min_col - 1, -1):
                    col_has_content = False
                    for r in range(min_row, max_row + 1):
                        idx = r * stride + c * n
                        if data[idx] <= 240 or data[idx + 1] <= 240 or data[idx + 2] <= 240:
                            col_has_content = True
                            break
                    if col_has_content:
                        max_col = c
                        break

                if min_col is not None and max_col is not None:
                    bbox_pixels = (min_col, min_row, max_col, max_row)

        if bbox_pixels is not None:
            min_col, min_row, max_col, max_row = bbox_pixels
            # Convert back to points (divide by ANALYSIS_SCALE)
            raw_box = fitz.Rect(
                min_col / self.ANALYSIS_SCALE,
                min_row / self.ANALYSIS_SCALE,
                (max_col + 1) / self.ANALYSIS_SCALE,
                (max_row + 1) / self.ANALYSIS_SCALE,
            )
            self.raw_bboxes[page_index] = raw_box
        else:
            self.raw_bboxes[page_index] = None

        self.scanned[page_index] = True
        return self.raw_bboxes[page_index]

    def compute_crop_rects(self, settings: CropSettings):
        """
        Computes final crop rectangles based on cached raw bounding boxes and current settings.
        Saves the results in self.crop_rects.
        """
        if not settings.enabled:
            self.crop_rects = [None] * self.page_count
            return

        per_page_rects = [None] * self.page_count
        is_sparse_list = [False] * self.page_count

        # 1. Compute per-page padded rects and identify sparse pages
        for i in range(self.page_count):
            page_rect = self.doc_model.page_rect(i)
            raw_box = self.raw_bboxes[i]

            # If not scanned yet, we use None (which will fall back to full page in main app)
            if not self.scanned[i] or raw_box is None:
                per_page_rects[i] = None
                is_sparse_list[i] = True
                continue

            # Apply margins/padding
            left = max(0.0, raw_box.x0 - settings.min_padding_left)
            right = min(page_rect.x1, raw_box.x1 + settings.min_padding_right)
            top = max(0.0, raw_box.y0 - settings.min_padding_top)
            bottom = min(page_rect.y1, raw_box.y1 + settings.min_padding_bottom)

            if right > left and bottom > top:
                per_page_rect = fitz.Rect(left, top, right, bottom)
                per_page_rects[i] = per_page_rect

                # Check for sparse pages (where content width is less than threshold of page width)
                content_w = raw_box.x1 - raw_box.x0
                is_sparse_list[i] = content_w < (settings.whitespace_threshold * page_rect.width)
            else:
                per_page_rects[i] = None
                is_sparse_list[i] = True

        # 2. Compute document-wide uniform width (left & right) from non-sparse pages
        non_sparse_rects = [
            r for idx, r in enumerate(per_page_rects) if not is_sparse_list[idx] and r is not None
        ]

        if non_sparse_rects:
            uniform_left = min(r.x0 for r in non_sparse_rects)
            uniform_right = max(r.x1 for r in non_sparse_rects)
        else:
            # Fallback if all pages are sparse or none have valid rects
            valid_rects = [r for r in per_page_rects if r is not None]
            if valid_rects:
                uniform_left = min(r.x0 for r in valid_rects)
                uniform_right = max(r.x1 for r in valid_rects)
            else:
                uniform_left = 0.0
                uniform_right = None

        # 3. Assemble final crop rectangles
        new_crop_rects = [None] * self.page_count
        for i in range(self.page_count):
            page_rect = self.doc_model.page_rect(i)
            per_page_rect = per_page_rects[i]
            is_sparse = is_sparse_list[i]

            u_right = uniform_right if uniform_right is not None else page_rect.x1

            if per_page_rect is None:
                # Blank page
                if settings.sparse_strategy in ("use_uniform", "crop_anyway"):
                    new_crop_rects[i] = fitz.Rect(uniform_left, 0.0, u_right, page_rect.y1)
                else:
                    new_crop_rects[i] = None  # Skip crop (full page)
            elif is_sparse:
                if settings.sparse_strategy == "skip":
                    new_crop_rects[i] = None  # Skip crop
                elif settings.sparse_strategy == "use_uniform":
                    new_crop_rects[i] = fitz.Rect(uniform_left, per_page_rect.y0, u_right, per_page_rect.y1)
                else:  # "crop_anyway"
                    new_crop_rects[i] = per_page_rect
            else:
                # Normal page
                if settings.crop_mode == "uniform_width":
                    new_crop_rects[i] = fitz.Rect(uniform_left, per_page_rect.y0, u_right, per_page_rect.y1)
                else:  # "per_page"
                    new_crop_rects[i] = per_page_rect

            # Intersect with the actual page bounds to guarantee validity
            if new_crop_rects[i] is not None:
                new_crop_rects[i] = new_crop_rects[i].intersect(page_rect)

        self.crop_rects = new_crop_rects

    def close(self):
        """Close the private fitz document instance if opened."""
        if self._doc is not None:
            self._doc.close()
            self._doc = None
