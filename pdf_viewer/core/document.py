import fitz


class DocumentModel:
    """
    A read-only model wrapper around fitz.Document.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.doc = fitz.open(filepath)
        self._page_count = len(self.doc)
        # Pre-cache page rectangles to avoid retrieving them repeatedly
        self._page_rects = [self.doc[i].rect for i in range(self._page_count)]

    @property
    def page_count(self) -> int:
        return self._page_count

    def get_page(self, index: int) -> fitz.Page:
        """
        Retrieve a specific page. Note that PyMuPDF Page objects are
        bound to the Document.
        """
        if 0 <= index < self._page_count:
            return self.doc[index]
        raise IndexError(f"Page index {index} out of range (0..{self._page_count - 1})")

    def page_rect(self, index: int) -> fitz.Rect:
        """
        Get the bounding box/rectangle of a page in points.
        """
        if 0 <= index < self._page_count:
            return self._page_rects[index]
        raise IndexError(f"Page rect index {index} out of range (0..{self._page_count - 1})")

    def close(self):
        """
        Close the underlying fitz document.
        """
        self.doc.close()
