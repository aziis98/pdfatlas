import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=False, vexpand=False,
        halign=None, valign=None, css_class=None, margin_end=None, margin_start=None,
        margin_top=None, margin_bottom=None, children=None) -> Gtk.Box:
    b = Gtk.Box(orientation=orientation, spacing=spacing)
    if hexpand:
        b.set_hexpand(True)
    if vexpand:
        b.set_vexpand(True)
    if halign is not None:
        b.set_halign(halign)
    if valign is not None:
        b.set_valign(valign)
    if css_class is not None:
        b.add_css_class(css_class)
    if margin_end is not None:
        b.set_margin_end(margin_end)
    if margin_start is not None:
        b.set_margin_start(margin_start)
    if margin_top is not None:
        b.set_margin_top(margin_top)
    if margin_bottom is not None:
        b.set_margin_bottom(margin_bottom)
    if children:
        for child in children:
            b.append(child)
    return b


def button(icon_name=None, label=None, tooltip=None, css_class=None, on_clicked=None) -> Gtk.Button:
    b = Gtk.Button()
    if icon_name:
        b.set_icon_name(icon_name)
    if label:
        b.set_label(label)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if css_class:
        b.add_css_class(css_class)
    if on_clicked is not None:
        b.connect("clicked", on_clicked)
    return b


def label(text="", css_class=None, xalign=None, ellipsize=None, max_width_chars=None,
          halign=None, valign=None, justify=None, wrap=False) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    if css_class:
        lbl.add_css_class(css_class)
    if xalign is not None:
        lbl.set_xalign(xalign)
    if ellipsize is not None:
        lbl.set_ellipsize(ellipsize)
    if max_width_chars is not None:
        lbl.set_max_width_chars(max_width_chars)
    if halign is not None:
        lbl.set_halign(halign)
    if valign is not None:
        lbl.set_valign(valign)
    if justify is not None:
        lbl.set_justify(justify)
    if wrap:
        lbl.set_wrap(True)
    return lbl


def search_entry(placeholder=None, sensitive=True) -> Gtk.SearchEntry:
    e = Gtk.SearchEntry()
    if placeholder:
        e.set_placeholder_text(placeholder)
    e.set_sensitive(sensitive)
    e.set_hexpand(False)
    e.set_halign(Gtk.Align.CENTER)
    e.set_size_request(300, -1)
    e.set_max_width_chars(45)
    return e


def scrolled_window(hexpand=True, vexpand=True) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    if hexpand:
        sw.set_hexpand(True)
    if vexpand:
        sw.set_vexpand(True)
    return sw


def spacer() -> Gtk.Box:
    b = Gtk.Box()
    b.set_hexpand(True)
    return b
