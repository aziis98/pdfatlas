from typing import Any, Callable
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .base import GtkComponent


class ZoomControlsComponent(GtkComponent):
    """
    Floating bottom-right zoom controls widget.
    """

    def __init__(self, on_zoom_in: Callable[[], None], on_zoom_out: Callable[[], None]):
        self.on_zoom_in = on_zoom_in
        self.on_zoom_out = on_zoom_out
        self.zoom_label = Gtk.Label(label="100%")

    def build_widget(self) -> Gtk.Widget:
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        zoom_box.add_css_class("zoom-floating-box")
        zoom_box.set_halign(Gtk.Align.END)
        zoom_box.set_valign(Gtk.Align.END)
        zoom_box.set_margin_end(20)
        zoom_box.set_margin_bottom(20)

        zoom_in_btn = Gtk.Button()
        zoom_in_btn.set_icon_name("zoom-in-symbolic")
        zoom_in_btn.set_tooltip_text("Zoom In")
        zoom_in_btn.connect("clicked", lambda b: self.on_zoom_in())
        zoom_box.append(zoom_in_btn)

        self.zoom_label.add_css_class("zoom-floating-label")
        zoom_box.append(self.zoom_label)

        zoom_out_btn = Gtk.Button()
        zoom_out_btn.set_icon_name("zoom-out-symbolic")
        zoom_out_btn.set_tooltip_text("Zoom Out")
        zoom_out_btn.connect("clicked", lambda b: self.on_zoom_out())
        zoom_box.append(zoom_out_btn)

        return zoom_box

    def update_state(self, state: dict[str, Any]) -> None:
        if "zoom" in state:
            zoom_val = state["zoom"]
            self.zoom_label.set_label(f"{int(zoom_val * 100)}%")
