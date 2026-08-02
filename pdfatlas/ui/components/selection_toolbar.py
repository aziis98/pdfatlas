from typing import Any, Callable
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk

from .base import GtkComponent


class SelectionToolbarComponent(GtkComponent):
    """
    Floating bottom action bar for text selection & clipboard export.
    """

    def __init__(
        self,
        on_copy_text: Callable[[], None],
        on_copy_tex: Callable[[], None],
    ):
        self.on_copy_text = on_copy_text
        self.on_copy_tex = on_copy_tex
        self.toolbar_box: Gtk.Box | None = None

    def build_widget(self) -> Gtk.Widget:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.add_css_class("selection-toolbar")
        toolbar.set_valign(Gtk.Align.END)
        toolbar.set_halign(Gtk.Align.FILL)
        toolbar.set_visible(False)
        self.toolbar_box = toolbar

        left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        btn_copy_text = Gtk.Button(label="Copy")
        btn_copy_text.set_tooltip_text("Copy selected PDF text [Ctrl+Shift+C]")
        btn_copy_text.connect("clicked", lambda b: self.on_copy_text())
        left_box.append(btn_copy_text)

        btn_copy_tex = Gtk.Button(label="Copy Source TeX")
        btn_copy_tex.set_tooltip_text("Copy source TeX for selection [Ctrl+C]")
        btn_copy_tex.connect("clicked", lambda b: self.on_copy_tex())
        left_box.append(btn_copy_tex)

        toolbar.append(left_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        info_menu_btn = Gtk.MenuButton()
        info_menu_btn.set_icon_name("dialog-information-symbolic")
        info_menu_btn.set_direction(Gtk.ArrowType.UP)
        info_menu_btn.set_tooltip_text("Shortcuts Info")
        info_menu_btn.add_css_class("flat")

        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.TOP)
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        popover_box.set_margin_top(10)
        popover_box.set_margin_bottom(10)
        popover_box.set_margin_start(12)
        popover_box.set_margin_end(12)

        title_label = Gtk.Label(label="Text Selection Shortcuts")
        title_label.add_css_class("heading")
        title_label.set_xalign(0)
        popover_box.append(title_label)

        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(6)

        k1 = Gtk.Label(label="Ctrl+C")
        k1.add_css_class("dim-label")
        k1.set_xalign(0)
        v1 = Gtk.Label(label="Copy source (if available)")
        v1.set_xalign(0)
        grid.attach(k1, 0, 0, 1, 1)
        grid.attach(v1, 1, 0, 1, 1)

        k2 = Gtk.Label(label="Ctrl+Shift+C")
        k2.add_css_class("dim-label")
        k2.set_xalign(0)
        v2 = Gtk.Label(label="Copy PDF text")
        v2.set_xalign(0)
        grid.attach(k2, 0, 1, 1, 1)
        grid.attach(v2, 1, 1, 1, 1)

        popover_box.append(grid)
        popover.set_child(popover_box)
        info_menu_btn.set_popover(popover)

        toolbar.append(info_menu_btn)
        return toolbar

    def update_state(self, state: dict[str, Any]) -> None:
        if "has_selection" in state and self.toolbar_box is not None:
            self.toolbar_box.set_visible(state["has_selection"])
