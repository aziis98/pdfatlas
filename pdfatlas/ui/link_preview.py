import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..core.cache import LinkPortalCache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

from .portal_preview import LinkPortalPreviewCard


class LinkPreviewManager:
    """
    Manager for link hover previews, popup positioning, and snippet rendering callbacks.
    """

    def __init__(self, main_window: "MainWindow"):
        self.win = main_window
        self.portal_cache = LinkPortalCache(max_size=50)
        self.portal_card = LinkPortalPreviewCard()

        self._active_hover_link: dict | None = None
        self._portal_debounce_id: int | None = None

        # Setup portal card in container
        self.portal_card.set_visible(False)

    def on_link_hovered(self, source_page_index: int | None, link: dict | None):
        """
        Handles mouse hover state over interactive PDF internal links.
        """
        if link:
            link_uri = link.get("uri", "")
            target_page = link.get("page")

            if target_page is not None:
                lbl = f"Go to Page {target_page + 1}"
            elif link_uri:
                lbl = f"Link: {link_uri}"
            else:
                lbl = "Internal Link"

            self.win.link_preview_label.set_text(lbl)
            self.win.link_preview_card_box.set_visible(True)
            self.win.link_preview_box.set_visible(True)

            if target_page is not None:
                if (
                    self._active_hover_link is not None
                    and self._active_hover_link.get("xref") == link.get("xref")
                    and self._active_hover_link.get("from") == link.get("from")
                    and self.portal_card.get_visible()
                ):
                    return

                self._active_hover_link = link
                self.show_link_portal_preview(source_page_index, link)
        else:
            self.win.link_preview_label.set_text("")
            self.win.link_preview_card_box.set_visible(False)
            self._active_hover_link = None
            self.portal_card.set_visible(False)

            if not self.win.debug_info_label or not self.win.debug_info_label.get_visible():
                self.win.link_preview_box.set_visible(False)

    def show_link_portal_preview(self, source_page_index: int | None, link: dict) -> bool:
        """
        Calculates viewport positioning and requests exact 1:1 hardware pixel snippet rendering.
        """
        if not link or not self.win.doc_model:
            self.portal_card.set_visible(False)
            return False

        target_page = link.get("page")
        if target_page is None or not isinstance(target_page, int) or not (0 <= target_page < self.win.doc_model.page_count):
            self.portal_card.set_visible(False)
            return False

        target_rect = self.win.doc_model.page_rect(target_page)
        to_point = link.get("to")
        target_y = (
            max(0.0, target_rect.height - float(to_point.y))
            if (to_point and hasattr(to_point, "y") and to_point.y is not None and to_point.y > 0.0)
            else (target_rect.height / 2.0)
        )

        viewport_w = max(300.0, float(self.win.scrolled_window.get_width()))
        viewport_h = max(300.0, float(self.win.scrolled_window.get_height()))

        scale_factor = float(self.win.canvas.get_scale_factor())
        scale = self.win.zoom * self.win.canvas.dpi_scale_factor
        page_dw = target_rect.width * scale
        raw_portal_w = int(page_dw - 2.0 * self.win.canvas.page_gap)
        portal_w = max(200, min(int(viewport_w - 32.0), raw_portal_w))
        portal_h = max(120, int(viewport_h * 0.25))
        self.portal_card.set_portal_size(portal_w, portal_h)

        render_w = int(portal_w * scale_factor)
        render_h = int(portal_h * scale_factor)

        cached_surface = self.portal_cache.get(target_page, target_y, render_w, render_h)
        if cached_surface:
            self.portal_card.set_surface(cached_surface)
        else:
            self.portal_card.set_loading()
            self.win.render_worker.queue_portal_job(
                self.win.doc_model,
                target_page,
                target_y,
                render_w,
                render_h,
                scale_factor,
                self.portal_cache,
                self._on_portal_render_complete,
            )

        # Center portal horizontally in viewport
        pos_x = max(16, int((viewport_w - portal_w) / 2.0))

        # Vertical positioning relative to link position using link_center_y
        link_rect = (
            self.win.canvas.get_link_screen_rect(source_page_index, link, self.win.overlay)
            if source_page_index is not None
            else None
        )
        if link_rect:
            _link_x, link_y, _link_w, link_h = link_rect
            link_center_y = link_y + (link_h / 2.0)
            gap_offset = 10.0

            if link_center_y < (viewport_h / 2.0):
                pos_y = int(max(8.0, link_y + link_h + gap_offset))
            else:
                pos_y = int(max(8.0, link_y - portal_h - gap_offset))
        else:
            pos_y = max(8, int((viewport_h - portal_h) / 2.0))

        pos_y = max(8, min(int(viewport_h - portal_h - 8), pos_y))

        self.portal_card.set_margin_start(pos_x)
        self.portal_card.set_margin_top(pos_y)
        self.portal_card.set_valign(Gtk.Align.START)
        self.portal_card.set_halign(Gtk.Align.START)
        self.portal_card.set_visible(True)
        return False

    def _on_portal_render_complete(self, page_index: int, target_y: float, surface):
        if self.portal_card.get_visible():
            self.portal_card.set_surface(surface)

    def clear(self):
        """Clears portal card surface cache."""
        self.portal_cache.clear()
        self.portal_card.set_visible(False)
