import os
import re
import tarfile
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..core.pdf_source import PdfSource


ARXIV_CACHE_ROOT = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "pdfatlas" / "source-arxiv"

ARXIV_PDF_URL = "https://arxiv.org/pdf/{}.pdf"
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{}"

ARXIV_ID_RE = re.compile(
    r"(?:https?://arxiv\.org/(?:abs|pdf)/([\w.-]+)(?:v\d+)?(?:\.pdf)?|([\w.-]+))"
)


def _extract_arxiv_id(raw: str) -> str | None:
    m = ARXIV_ID_RE.fullmatch(raw.strip())
    if m:
        return m.group(1) or m.group(2)
    return None


ARXIV_API_URL = "http://export.arxiv.org/api/query?id_list={}"


def _fetch_arxiv_title(arxiv_id: str) -> str | None:
    try:
        url = ARXIV_API_URL.format(arxiv_id)
        resp = urllib.request.urlopen(url, timeout=10)
        data = resp.read()
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        title_el = root.find("atom:entry/atom:title", ns)
        if title_el is not None and title_el.text:
            return " ".join(title_el.text.split())
    except Exception:
        pass
    return None


def _arxiv_id_from_path(pdf_path: str) -> str | None:
    parts = Path(pdf_path).parts
    for part in parts:
        if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", part):
            return part
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

        self._cache_dir: Path | None = None

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

        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        vbox.append(self.spinner)

        self.fetch_btn = Gtk.Button(label="Fetch & Open")
        self.fetch_btn.add_css_class("suggested-action")
        self.fetch_btn.connect("clicked", lambda b: self._on_fetch())
        vbox.append(self.fetch_btn)

        recent = recent_files.get_recent_by_type("arxiv", 5)
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

        self.fetch_btn.set_sensitive(False)
        self.entry.set_sensitive(False)
        self.spinner.set_visible(True)
        self.spinner.start()
        self._set_status("Downloading...")

        thread = threading.Thread(target=self._download_worker, args=(arxiv_id,), daemon=True)
        thread.start()

    def _make_recent_handler(self, source):
        def handler(row):
            self._on_source(source)
            self.destroy()
        return handler

    def _download_worker(self, arxiv_id: str):
        try:
            cache_dir = ARXIV_CACHE_ROOT / arxiv_id
            cache_dir.mkdir(parents=True, exist_ok=True)

            pdf_path = cache_dir / "paper.pdf"
            if not pdf_path.exists():
                self._idle_status("Downloading PDF...")
                pdf_url = ARXIV_PDF_URL.format(arxiv_id)
                urllib.request.urlretrieve(pdf_url, pdf_path)

            eprint_path = cache_dir / "source.tar.gz"
            if not eprint_path.exists():
                self._idle_status("Downloading source tarball...")
                eprint_url = ARXIV_EPRINT_URL.format(arxiv_id)
                urllib.request.urlretrieve(eprint_url, eprint_path)

            self._idle_status("Extracting source files...")
            with tarfile.open(eprint_path, "r:gz") as tar:
                tar.extractall(path=cache_dir)
            eprint_path.unlink()

            self._idle_status("Fetching paper metadata...")
            title = _fetch_arxiv_title(arxiv_id) or f"arXiv:{arxiv_id}"
            self._idle_title(title)

            pdf_source = PdfSource(
                source_type="arxiv",
                uri=str(cache_dir / "paper.pdf"),
                display_name=title,
            )
            self._idle_done(pdf_source)
        except urllib.error.HTTPError as e:
            self._idle_error(f"HTTP error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            self._idle_error(f"Network error: {e.reason}")
        except Exception as e:
            self._idle_error(f"Error: {e}")

    def _idle_status(self, msg: str):
        GLib.idle_add(self._set_status, msg)

    def _set_status(self, msg: str):
        self.status_label.set_label(msg)
        self.status_label.set_visible(bool(msg))

    def _idle_error(self, msg: str):
        GLib.idle_add(self._on_error, msg)

    def _on_error(self, msg: str):
        self._set_status(msg)
        self.fetch_btn.set_sensitive(True)
        self.entry.set_sensitive(True)
        self.spinner.stop()
        self.spinner.set_visible(False)

    def _idle_done(self, source: PdfSource):
        GLib.idle_add(self._on_done, source)

    def _on_done(self, source: PdfSource):
        self._on_source(source)
        self.destroy()

    def _idle_title(self, title: str):
        GLib.idle_add(self._set_status, title)