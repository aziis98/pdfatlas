from __future__ import annotations

from typing import Callable
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


def normalize_url(raw_url: str) -> str:
    raw = raw_url.strip()
    if not raw:
        return ""
    if raw.startswith("10."):
        return f"https://doi.org/{raw}"
    elif not raw.startswith(("http://", "https://", "ftp://")):
        return f"https://{raw}"
    return raw


class UrlInputDialog(Gtk.Window):
    """
    Modal dialog for entering a web URL or DOI to attach to selected text.
    """

    def __init__(
        self,
        parent_window: Gtk.Window | None,
        initial_url: str = "",
        initial_title: str = "",
        on_confirm: Callable[[str, str], None] | None = None,
    ):
        super().__init__(
            title="Add Web Link",
            transient_for=parent_window,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(440, -1)
        self.set_resizable(False)
        self._on_confirm = on_confirm

        header = Adw.HeaderBar()
        self.set_titlebar(header)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda b: self.destroy())
        header.pack_start(btn_cancel)

        self.btn_save = Gtk.Button(label="Add Link")
        self.btn_save.add_css_class("suggested-action")
        self.btn_save.connect("clicked", lambda b: self._submit())
        header.pack_end(self.btn_save)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(20)
        self.set_child(vbox)

        url_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl_url = Gtk.Label(label="URL or DOI")
        lbl_url.set_halign(Gtk.Align.START)
        lbl_url.add_css_class("heading")
        lbl_url.add_css_class("caption")
        url_group.append(lbl_url)

        self.entry_url = Gtk.Entry()
        self.entry_url.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "globe-symbolic")
        self.entry_url.set_placeholder_text("https://example.com or 10.1145/...")
        self.entry_url.set_text(initial_url)
        self.entry_url.connect("activate", lambda e: self._submit())
        url_group.append(self.entry_url)
        vbox.append(url_group)

        title_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl_title = Gtk.Label(label="Link Title (Optional)")
        lbl_title.set_halign(Gtk.Align.START)
        lbl_title.add_css_class("heading")
        lbl_title.add_css_class("caption")
        title_group.append(lbl_title)

        self.entry_title = Gtk.Entry()
        self.entry_title.set_placeholder_text("Display label for link")
        self.entry_title.set_text(initial_title)
        self.entry_title.connect("activate", lambda e: self._submit())
        title_group.append(self.entry_title)
        vbox.append(title_group)

    def _submit(self):
        raw_url = self.entry_url.get_text().strip()
        if not raw_url:
            return

        url = normalize_url(raw_url)
        title = self.entry_title.get_text().strip()
        if self._on_confirm:
            self._on_confirm(url, title)
        self.destroy()
