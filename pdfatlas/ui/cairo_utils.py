import math

import cairo


def rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def paint_surface_scaled(cr: cairo.Context, surface: cairo.ImageSurface,
                         x: float, y: float, w: float, h: float, scale_factor: float = 1.0):
    sw = surface.get_width()
    sh = surface.get_height()
    cr.save()
    cr.translate(x, y)
    cr.scale(w / (sw / scale_factor), h / (sh / scale_factor))
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    cr.restore()


def fill_rect(cr: cairo.Context, x: float, y: float, w: float, h: float,
              r: float, g: float, b: float, a: float = 1.0):
    cr.save()
    cr.set_source_rgba(r, g, b, a)
    cr.rectangle(x, y, w, h)
    cr.fill()
    cr.restore()


def stroke_rect(cr: cairo.Context, x: float, y: float, w: float, h: float,
                line_width: float, r: float, g: float, b: float, a: float = 1.0):
    cr.save()
    cr.set_source_rgba(r, g, b, a)
    cr.set_line_width(line_width)
    cr.rectangle(x, y, w, h)
    cr.stroke()
    cr.restore()


def hex_to_rgba(hex_str: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        try:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            return (r, g, b, alpha)
        except ValueError:
            pass
    return (1.0, 0.933, 0.333, alpha)
