from collections import OrderedDict
import threading

import cairo

from .texture import PageTexture

#: A cached entry is either a cairo.ImageSurface (minimap / portal paths, whose
#: backing BGRA numpy buffer is retained via ``buffer``) or a PageTexture (GL
#: canvas path, which owns its raw RGB samples directly).
CacheValue = tuple[cairo.ImageSurface | PageTexture, object | None]


class PageCache:
    """
    Unified LRU Cache storing full rendered PDF page pixels.
    Key: (page_index, round(scale, 2), crop_key)
      - page_index: int
      - scale: float (resolution scale = zoom * scale_factor)
      - crop_key: tuple of float (x0, y0, x1, y1) or None
    Value: CacheValue (cairo.ImageSurface | PageTexture, data_buffer)
    """

    def __init__(self, max_size: int = 50):
        self.max_size = max_size
        self.cache: OrderedDict[tuple, CacheValue] = OrderedDict()
        self._lock = threading.Lock()

    def _make_key(self, page_index: int, scale: float, crop_key: tuple | None = None) -> tuple:
        return (page_index, round(scale, 2), crop_key)

    def get(self, page_index: int, scale: float, crop_key: tuple | None = None) -> cairo.ImageSurface | PageTexture | None:
        key = self._make_key(page_index, scale, crop_key)
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key][0]
        return None

    def get_best(
        self, page_index: int, scale: float, crop_key: tuple | None = None
    ) -> cairo.ImageSurface | PageTexture | None:
        """Return the closest-zoom cached surface for this page+crop, or None."""
        exact = self.get(page_index, scale, crop_key)
        if exact is not None:
            return exact
        best_entry: CacheValue | None = None
        best_diff = float("inf")
        for (p, s, ck), entry in self.cache.items():
            if p == page_index and ck == crop_key:
                diff = abs(s - scale)
                if diff < best_diff:
                    best_diff = diff
                    best_entry = entry
        if best_entry is not None:
            for key, entry in list(self.cache.items()):
                if entry is best_entry:
                    self.cache.move_to_end(key)
                    break
        return best_entry[0] if best_entry is not None else None

    def set(
        self,
        page_index: int,
        scale: float,
        crop_key: tuple | None,
        surface: cairo.ImageSurface | PageTexture,
        data_buffer: object | None,
    ):
        key = self._make_key(page_index, scale, crop_key)
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = (surface, data_buffer)
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def total_entries(self) -> int:
        with self._lock:
            return len(self.cache)

    def total_bytes(self) -> int:
        """Sum of all cached pixel memory in bytes (4 B/px for cairo, channels B/px for PageTexture)."""
        with self._lock:
            total = 0
            for (surface, _buf) in self.cache.values():
                if isinstance(surface, PageTexture):
                    total += surface.byte_size
                else:
                    total += surface.get_width() * surface.get_height() * 4
            return total

    def clear(self):
        with self._lock:
            self.cache.clear()


# Backward-compatibility wrappers mapping to PageCache via composition
class RenderCache:
    """Cache for the GL canvas: stores PageTexture raw RGB pixels."""

    def __init__(self, max_size: int = 20):
        self._page_cache = PageCache(max_size=max_size)

    @staticmethod
    def _normalize_crop_key(crop_rect):
        """Convert fitz.Rect → tuple or keep tuple as-is; pass None through."""
        if crop_rect is None or isinstance(crop_rect, tuple):
            return crop_rect
        return (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)

    def get(
        self, page_index: int, zoom: float, scale_factor: int, crop_rect
    ) -> PageTexture | None:
        scale = zoom * scale_factor
        value = self._page_cache.get(page_index, scale, self._normalize_crop_key(crop_rect))
        return value if isinstance(value, PageTexture) else None

    def set(
        self,
        page_index: int,
        zoom: float,
        scale_factor: int,
        crop_rect,
        surface: PageTexture,
        data_buffer: object | None,
    ):
        scale = zoom * scale_factor
        self._page_cache.set(page_index, scale, self._normalize_crop_key(crop_rect), surface, data_buffer)

    def get_best(
        self, page_index: int, zoom: float, scale_factor: int, crop_rect
    ) -> PageTexture | None:
        scale = zoom * scale_factor
        value = self._page_cache.get_best(page_index, scale, self._normalize_crop_key(crop_rect))
        return value if isinstance(value, PageTexture) else None

    def total_entries(self) -> int:
        return self._page_cache.total_entries()

    def total_bytes(self) -> int:
        return self._page_cache.total_bytes()

    def clear(self):
        self._page_cache.clear()


class MiniMapCache:
    """Legacy alias wrapping PageCache for minimap thumbnails (cairo surfaces)."""

    def __init__(self, max_size: int = 1000):
        self._page_cache = PageCache(max_size=max_size)

    def get(self, page_index: int) -> cairo.ImageSurface | None:
        value = self._page_cache.get(page_index, scale=0.2)
        return value if isinstance(value, cairo.ImageSurface) else None

    def set(self, page_index: int, surface: cairo.ImageSurface, data_buffer: object):
        self._page_cache.set(page_index, scale=0.2, crop_key=None, surface=surface, data_buffer=data_buffer)

    def clear(self):
        self._page_cache.clear()


class LinkPortalCache:
    """Cache for link hover previews keyed by (page_index, round(target_y, 1), target_w, target_h)."""

    def __init__(self, max_size: int = 50):
        self._page_cache = PageCache(max_size=max_size)

    def get(self, page_index: int, target_y: float, target_w: int, target_h: int) -> cairo.ImageSurface | None:
        crop_key = (round(target_y, 1), target_w, target_h)
        value = self._page_cache.get(page_index, scale=1.0, crop_key=crop_key)
        return value if isinstance(value, cairo.ImageSurface) else None

    def set(
        self,
        page_index: int,
        target_y: float,
        target_w: int,
        target_h: int,
        surface: cairo.ImageSurface,
        data_buffer: object,
    ):
        crop_key = (round(target_y, 1), target_w, target_h)
        self._page_cache.set(page_index, scale=1.0, crop_key=crop_key, surface=surface, data_buffer=data_buffer)

    def clear(self):
        self._page_cache.clear()
