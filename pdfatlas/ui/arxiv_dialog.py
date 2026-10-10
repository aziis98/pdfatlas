import functools
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..core.arxiv_mapper import (
    arxiv_id_from_path as _arxiv_id_from_path,
    extract_arxiv_id_from_raw as _extract_arxiv_id,
)
from ..core.pdf_source import PdfSource


ARXIV_CACHE_ROOT = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "pdfatlas" / "source-arxiv"

ARXIV_PDF_URL = "https://arxiv.org/pdf/{}.pdf"
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{}"
ARXIV_API_URL = "http://export.arxiv.org/api/query?id_list={}"


def clean_arxiv_title(raw_title: str) -> str:
    if not raw_title:
        return ""
    text = raw_title.replace("\u00ad", "-")
    text = text.replace(r"\-", "-")
    text = text.replace("---", "—").replace("--", "–")
    text = re.sub(r"\\(?:textit|textbf|mathrm|mbox)\{([^}]+)\}", r"\1", text)
    text = text.replace("$", "")
    words = text.split()
    cleaned = " ".join(words)
    cleaned = re.sub(r"(\w+)\s*-\s*(\w+)", r"\1-\2", cleaned)
    return cleaned


@functools.lru_cache(maxsize=128)
def _fetch_arxiv_title(arxiv_id: str) -> str | None:
    try:
        url = ARXIV_API_URL.format(arxiv_id)
        resp = urllib.request.urlopen(url, timeout=10)
        data = resp.read()
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        title_el = root.find("atom:entry/atom:title", ns)
        if title_el is not None and title_el.text:
            return clean_arxiv_title(title_el.text)
    except Exception:
        pass
    return None


class ArxivDialog(Gtk.Window):
    def __init__(self, parent_window, on_source, recent_files):
        super().__init__(
            title="Open from arXiv",
            transient_for=parent_window,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(420, -1)
        self._on_source = on_source
        self.recent_files = recent_files

        header = Adw.HeaderBar()
        self.set_titlebar(header)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        self.set_child(vbox)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("arXiv URL or ID (e.g. 2305.12345)")
        self.entry.connect("activate", lambda e: self._on_fetch())
        vbox.append(self.entry)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_visible(False)
        vbox.append(self.status_label)

        self.fetch_btn = Gtk.Button(label="Fetch & Open")
        self.fetch_btn.add_css_class("suggested-action")
        self.fetch_btn.connect("clicked", lambda b: self._on_fetch())
        vbox.append(self.fetch_btn)

        recent = recent_files.get_recent_by_type("arxiv", 5) if recent_files else []
        if recent:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            vbox.append(sep)

            recent_label = Gtk.Label(label="Recent")
            recent_label.set_halign(Gtk.Align.START)
            recent_label.add_css_class("caption")
            recent_label.add_css_class("dim-label")
            vbox.append(recent_label)

            list_box = Gtk.ListBox()
            list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            list_box.add_css_class("boxed-list")

            seen = set()
            for source in recent:
                if source.uri in seen:
                    continue
                seen.add(source.uri)
                arxiv_id = _arxiv_id_from_path(source.uri)
                row = Adw.ActionRow()
                row.set_title(source.display_name)
                row.set_subtitle(f"arXiv:{arxiv_id}" if arxiv_id else source.uri)
                row.set_activatable(True)
                row.set_subtitle_lines(1)
                row.connect("activated", self._make_recent_handler(source))
                list_box.append(row)

            vbox.append(list_box)

        shortcut_controller = Gtk.ShortcutController.new()
        trigger = Gtk.ShortcutTrigger.parse_string("Escape")
        action = Gtk.CallbackAction.new(lambda w, a: (self.destroy(), True)[1])
        shortcut_controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        self.add_controller(shortcut_controller)

    def _on_fetch(self):
        raw = self.entry.get_text().strip()
        arxiv_id = _extract_arxiv_id(raw)
        if not arxiv_id:
            self._set_status("Invalid arXiv ID or URL")
            return

        cache_dir = ARXIV_CACHE_ROOT / arxiv_id
        pdf_path = cache_dir / "paper.pdf"

        existing = self.recent_files.get_by_arxiv_id(arxiv_id) if self.recent_files else None
        title = existing.display_name if (existing and existing.display_name) else f"arXiv:{arxiv_id}"

        pdf_source = PdfSource(
            source_type="arxiv",
            uri=str(pdf_path),
            display_name=title,
        )
        self._on_source(pdf_source)
        self.destroy()

    def _make_recent_handler(self, source):
        def handler(row):
            self._on_source(source)
            self.destroy()
        return handler

    def _set_status(self, msg: str):
        self.status_label.set_label(msg)
        self.status_label.set_visible(bool(msg))