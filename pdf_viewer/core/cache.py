from collections import OrderedDict
import threading

import cairo


class PageCache:
    """
    Unified LRU Cache storing full rendered PDF page surfaces.
    Key: (page_index, round(scale, 2), crop_key)
      - page_index: int
      - scale: float (resolution scale = zoom * scale_factor)
      - crop_key: tuple of float (x0, y0, x1, y1) or None
    Value: tuple (cairo.ImageSurface, data_buffer)
    """

    def __init__(self, max_size: int = 50):
        self.max_size = max_size
        self.cache: OrderedDict[tuple, tuple[cairo.ImageSurface, object]] = OrderedDict()
        self._lock = threading.Lock()

    def _make_key(self, page_index: int, scale: float, crop_key: tuple | None = None) -> tuple:
        return (page_index, round(scale, 2), crop_key)

    def get(self, page_index: int, scale: float, crop_key: tuple | None = None) -> cairo.ImageSurface | None:
        key = self._make_key(page_index, scale, crop_key)
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key][0]
            # Fallback: if requesting uncropped base page surface, reuse any cached base page surface for this page
            if crop_key is None:
                for (p_idx, s_val, c_key), (surf, _buf) in reversed(self.cache.items()):
                    if p_idx == page_index and c_key is None:
                        return surf
        return None

    def set(
        self,
        page_index: int,
        scale: float,
        crop_key: tuple | None,
        surface: cairo.ImageSurface,
        data_buffer: object,
    ):
        key = self._make_key(page_index, scale, crop_key)
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = (surface, data_buffer)
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def get_sub_surface(
        self, page_index: int, scale: float, clip_y0: float, clip_y1: float
    ) -> cairo.ImageSurface | None:
        """
        Derives a sub-surface snippet from a cached base page surface at requested scale.
        """
        base_surface = self.get(page_index, scale)
        if base_surface is None:
            # Look for any base page surface for this page if scale matches closely
            with self._lock:
                for (p_idx, s_val, c_key), (surf, _buf) in reversed(self.cache.items()):
                    if p_idx == page_index and c_key is None:
                        base_surface = surf
                        scale = s_val
                        break

        if base_surface is None:
            return None

        surf_w = base_surface.get_width()
        surf_h = base_surface.get_height()
        if surf_w <= 0 or surf_h <= 0:
            return None

        # Convert document point clip_y0/clip_y1 to pixel y-offsets
        py0 = max(0, int(clip_y0 * scale))
        py1 = min(surf_h, int(clip_y1 * scale))
        clip_h = max(1, py1 - py0)

        sub_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, surf_w, clip_h)
        ctx = cairo.Context(sub_surface)
        ctx.set_source_surface(base_surface, 0, -py0)
        ctx.paint()
        return sub_surface

    def clear(self):
        with self._lock:
            self.cache.clear()


# Backward-compatibility wrappers mapping to PageCache via composition
class RenderCache:
    """Legacy alias wrapping PageCache for canvas rendering."""

    def __init__(self, max_size: int = 20):
        self._page_cache = PageCache(max_size=max_size)

    def get(
        self, page_index: int, zoom: float, scale_factor: int, crop_rect_tuple: tuple | None
    ) -> cairo.ImageSurface | None:
        scale = zoom * scale_factor
        return self._page_cache.get(page_index, scale, crop_rect_tuple)

    def set(
        self,
        page_index: int,
        zoom: float,
        scale_factor: int,
        crop_rect_tuple: tuple | None,
        surface: cairo.ImageSurface,
        data_buffer: object,
    ):
        scale = zoom * scale_factor
        self._page_cache.set(page_index, scale, crop_rect_tuple, surface, data_buffer)

    def get_sub_surface(
        self, page_index: int, scale: float, clip_y0: float, clip_y1: float
    ) -> cairo.ImageSurface | None:
        return self._page_cache.get_sub_surface(page_index, scale, clip_y0, clip_y1)

    def clear(self):
        self._page_cache.clear()


class MiniMapCache:
    """Legacy alias wrapping PageCache for minimap thumbnails."""

    def __init__(self, max_size: int = 1000):
        self._page_cache = PageCache(max_size=max_size)

    def get(self, page_index: int) -> cairo.ImageSurface | None:
        return self._page_cache.get(page_index, scale=0.2)

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
        return self._page_cache.get(page_index, scale=1.0, crop_key=crop_key)

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
