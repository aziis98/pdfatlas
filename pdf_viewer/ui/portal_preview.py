import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk


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
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_can_target(False)
        self.set_hexpand(False)
        self.set_vexpand(False)

        self.surface: cairo.ImageSurface | None = None
        self.portal_width = 340
        self.portal_height = 160

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_can_target(False)
        self.drawing_area.set_hexpand(False)
        self.drawing_area.set_vexpand(False)
        self.drawing_area.set_draw_func(self._draw_func)
        self.drawing_area.set_size_request(340, 160)

        self.append(self.drawing_area)

    def set_portal_size(self, width: int, height: int = 160):
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

        # 1. Precise 8px rounded rectangle clip path
        r = 8.0
        cr.new_sub_path()
        cr.arc(width - r, r, r, -1.5707963, 0)
        cr.arc(width - r, height - r, r, 0, 1.5707963)
        cr.arc(r, height - r, r, 1.5707963, 3.14159265)
        cr.arc(r, r, r, 3.14159265, 4.71238898)
        cr.close_path()

        cr.clip_preserve()

        # 2. White background fill
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.fill_preserve()

        # 3. Direct Surface Rendering
        if self.surface:
            cr.save()
            cr.set_source_surface(self.surface, 0, 0)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_rgba(0.94, 0.95, 0.97, 1.0)
            cr.fill_preserve()

        # 4. Subtle 1px border stroke around rounded edge
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
        cr.set_line_width(1.0)
        cr.stroke()

        cr.restore()
