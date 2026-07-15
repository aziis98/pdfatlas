from collections import OrderedDict
import cairo

class RenderCache:
    """
    LRU cache for high-resolution rendered page surfaces.
    Key: (page_index, zoom_key, crop_key)
      - page_index: int
      - zoom_key: float (rounded to 2 decimal places)
      - crop_key: tuple of float (x0, y0, x1, y1) or None
    Value: tuple (cairo.ImageSurface, data_buffer)
    """
    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.cache = OrderedDict()

    def get(self, page_index: int, zoom: float, scale_factor: int, crop_rect_tuple: tuple | None) -> cairo.ImageSurface | None:
        key = (page_index, round(zoom, 2), scale_factor, crop_rect_tuple)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key][0]
        return None

    def set(self, page_index: int, zoom: float, scale_factor: int, crop_rect_tuple: tuple | None, surface: cairo.ImageSurface, data_buffer):
        key = (page_index, round(zoom, 2), scale_factor, crop_rect_tuple)
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = (surface, data_buffer)
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def clear(self):
        self.cache.clear()


class MiniMapCache:
    """
    LRU cache for low-resolution minimap thumbnail surfaces.
    Key: page_index (int)
    Value: tuple (cairo.ImageSurface, data_buffer)
    Cleared only when the document changes.
    """
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache = OrderedDict()

    def get(self, page_index: int) -> cairo.ImageSurface | None:
        if page_index in self.cache:
            self.cache.move_to_end(page_index)
            return self.cache[page_index][0]
        return None

    def set(self, page_index: int, surface: cairo.ImageSurface, data_buffer):
        if page_index in self.cache:
            self.cache.move_to_end(page_index)
        self.cache[page_index] = (surface, data_buffer)
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def clear(self):
        self.cache.clear()
