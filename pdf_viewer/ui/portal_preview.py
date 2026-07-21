import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk


class LinkPortalPreviewCard(Gtk.Box):
    """
    Minimal floating portal preview snippet card for internal PDF links.
    Uses Gtk.DrawingArea with Cairo surface rendering to guarantee exact layout size requests.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("link-portal-card")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_can_target(False)
        self.set_hexpand(False)
        self.set_vexpand(False)

        self.surface: cairo.ImageSurface | None = None
        self.portal_width = 340
        self.portal_height = 110

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_can_target(False)
        self.drawing_area.set_hexpand(False)
        self.drawing_area.set_vexpand(False)
        self.drawing_area.set_draw_func(self._draw_func)
        self.drawing_area.set_size_request(340, 110)

        self.append(self.drawing_area)

    def set_portal_size(self, width: int, height: int = 110):
        self.portal_width = width
        self.portal_height = height
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

        cr.save()

        # 1. Clip path to rounded rectangle (radius 8.0)
        r = 8.0
        cr.new_sub_path()
        cr.arc(width - r, r, r, -1.5707963, 0)
        cr.arc(width - r, height - r, r, 0, 1.5707963)
        cr.arc(r, height - r, r, 1.5707963, 3.14159265)
        cr.arc(r, r, r, 3.14159265, 4.71238898)
        cr.close_path()

        cr.clip_preserve()

        # 2. Fill white background inside rounded clip
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.fill_preserve()

        # 3. Draw Cairo surface scaled to fill entire card allocation
        if self.surface:
            cr.save()
            self.surface.set_device_scale(1.0, 1.0)
            surf_w = self.surface.get_width()
            surf_h = self.surface.get_height()
            if surf_w > 0 and surf_h > 0:
                scale_x = width / surf_w
                scale_y = height / surf_h
                cr.scale(scale_x, scale_y)
                cr.set_source_surface(self.surface, 0, 0)
                cr.paint()
            cr.restore()

        # 4. Stroke subtle 1px border around rounded edge
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.25)
        cr.set_line_width(1.0)
        cr.stroke()

        cr.restore()
