import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk


class _FlexiblePortalLayout(Gtk.LayoutManager):
    """Height-for-width LayoutManager that scales card height proportionally to column width."""

    def __init__(self, min_w: int, nat_w: int, height_fn):
        super().__init__()
        self.min_w = min_w
        self.nat_w = nat_w
        self.height_fn = height_fn

    def do_get_request_mode(self, widget: Gtk.Widget) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(
        self, widget: Gtk.Widget, orientation: Gtk.Orientation, for_size: int
    ) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (self.min_w, self.nat_w, -1, -1)
        w = for_size if for_size > 0 else self.nat_w
        h = self.height_fn(w)
        return (h, h, -1, -1)

    def do_allocate(
        self, widget: Gtk.Widget, width: int, height: int, baseline: int
    ) -> None:
        child = widget.get_first_child()
        while child is not None:
            if child.should_layout():
                child.allocate(width, height, baseline, None)
            child = child.get_next_sibling()


class LinkPortalPreviewCard(Gtk.Box):
    """
    Portal preview snippet card for internal PDF links and search results.
    Features:
      - 8px vector rounded corner clipping (both via Cairo and GTK CSS).
      - Proportional aspect-ratio surface scaling (no text stretching).
      - Crisp 1px border stroke and placeholder states.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("link-portal-card")
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.set_can_target(False)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.surface: cairo.ImageSurface | None = None
        self.portal_width = 340
        self.portal_height = 160

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_can_target(False)
        self.drawing_area.set_hexpand(True)
        self.drawing_area.set_vexpand(True)
        self.drawing_area.set_draw_func(self._draw_func)
        self.drawing_area.set_size_request(240, 60)

        self.append(self.drawing_area)

    def set_portal_size(
        self,
        width: int,
        height: int = 160,
        *,
        flexible: bool = False,
        height_fn=None,
    ):
        self.portal_width = width
        self.portal_height = height
        if flexible:
            fn = height_fn or (lambda w: max(48, int(round(height * (w / max(1, width))))))
            self.set_layout_manager(_FlexiblePortalLayout(min_w=220, nat_w=width, height_fn=fn))
        else:
            self.set_size_request(width, height)
            self.drawing_area.set_size_request(width, height)
        self.drawing_area.queue_draw()

    def set_loading(self):
        self.surface = None
        self.drawing_area.queue_draw()

    def set_surface(self, surface: cairo.ImageSurface):
        self.surface = surface
        self.drawing_area.queue_draw()

    def _draw_func(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        if self.surface:
            cr.save()
            surf_w = self.surface.get_width()
            sx, _sy = self.surface.get_device_scale()
            logical_w = surf_w / sx if sx > 0 else float(surf_w)
            if logical_w > 0 and abs(width - logical_w) > 0.5:
                scale = width / logical_w
                cr.scale(scale, scale)
            cr.set_source_surface(self.surface, 0, 0)
            cr.paint()
            cr.restore()
        else:
            cr.save()
            r = 8.0
            cr.new_sub_path()
            cr.arc(width - r, r, r, -1.5707963, 0)
            cr.arc(width - r, height - r, r, 0, 1.5707963)
            cr.arc(r, height - r, r, 1.5707963, 3.14159265)
            cr.arc(r, r, r, 3.14159265, 4.71238898)
            cr.close_path()
            cr.set_source_rgba(0.94, 0.95, 0.97, 1.0)
            cr.fill_preserve()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
            cr.set_line_width(1.0)
            cr.stroke()
            cr.restore()
