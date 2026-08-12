import os
import random
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango

from .gui import box, button, label
from .services.icon_theme import IconThemeManager

if TYPE_CHECKING:
    from ..core.pdf_source import PdfSource, RecentFilesManager
    from .window import MainWindow

#: Number of recent documents shown on the welcome screen.
WELCOME_RECENT_MAX = 5


def _shorten_path(path: str) -> str:
    """Replace the user's home directory prefix with ``~`` for display."""
    home = os.path.abspath(os.path.expanduser("~"))
    abspath = os.path.abspath(path)
    if abspath == home:
        return "~"
    if abspath.startswith(home + os.sep):
        return "~" + abspath[len(home):]
    return path

TIP_MESSAGES: list[str] = [
    "Press W to fit the page width, or F to fit the whole page in the viewport.",
    "Press M to open the minimap grid and jump to any page in one click.",
    "Press C to auto-crop page margins and make the text use more of your screen.",
    "Use j / k to scroll line-by-line and h / l to jump by a full viewport.",
    "Page Up / Page Down scroll by one full viewport height.",
    "Ctrl+scroll zooms in where your cursor is pointed.",
    "+ / - / = zoom in and out; Ctrl+0 resets zoom to 100%.",
    "Ctrl+F focuses the search bar for instant full-text search.",
    "Search terms are highlighted on the portals and the page canvas; the engine uses a trigram tokenizer, so partial word fragments match too.",
    "In search results, click a portal card to jump straight to that spot in the reader.",
    "Pin interesting search portals to keep them handy while you keep reading and searching.",
    "Ctrl+O opens a PDF; your last 10 documents are kept as recent files.",
    "You can open arXiv papers directly from the command line, by ID (pdfatlas 1706.03762) or by URL.",
    "Select any text to reveal the copy and highlight toolbar.",
    "On arXiv papers, Ctrl+C copies the original LaTeX source for your selection.",
    "Ctrl+Shift+C copies the plain PDF text of a selection.",
    "Highlight with H, then pick from 18 highlighter colors.",
    "Highlights and notes are saved per document (by its checksum), so they come right back when you reopen the paper.",
    "Open the tag icon to browse all annotations and notes, grouped by page.",
    "Right-click anywhere on a page and choose Add note here to drop a paperclip note in Markdown.",
    "Notes are written in Markdown and show a rendered preview on hover, with KaTeX math support.",
    "Note editors have Source and Rendered tabs and autosave while you type.",
    "Text indexes are cached on disk, so reopening a paper feels instant.",
    "Gap-less mode stitches pages together for an uninterrupted reading flow.",
    "Zoom and scroll position are restored automatically when you reopen a document, even if the file was renamed or moved.",
    "Escape clears the search, closes the minimap, or deselects text.",
    "Ctrl+Q or q quits PDF Atlas.",
    "In the minimap, your current page and viewport are highlighted for orientation.",
    "A single click on the canvas removes the highlighted search term.",
]


class RecentRow(Gtk.ListBoxRow):
    """ListBoxRow carrying the PdfSource it was built from."""

    def __init__(self, source: "PdfSource"):
        super().__init__()
        self.source = source


class WelcomeView(Gtk.Box):
    """
    Empty-state welcome screen shown when no document is loaded.

    Layout: a centered Adw.StatusPage holding the recent-documents list and
    open actions, with a rotating usage tip pinned to the bottom.
    """

    def __init__(self, win: "MainWindow"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win = win
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.status_page = Adw.StatusPage()
        self.status_page.set_vexpand(True)
        self.append(self.status_page)
        self._build_status_content()

        self._tip_index = 0
        self.tip_label = label(
            text="", wrap=True, halign=Gtk.Align.CENTER,
        )
        self.tip_label.set_justify(Gtk.Justification.CENTER)
        tip_content = box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            children=[
                label(text="\U0001f4a1", css_class="welcome-tip-icon", valign=Gtk.Align.START),
                self.tip_label,
            ],
        )
        self.tip_button = Gtk.Button()
        self.tip_button.add_css_class("flat")
        self.tip_button.add_css_class("welcome-tip")
        self.tip_button.set_tooltip_text("Click for another tip")
        self.tip_button.set_child(tip_content)
        self.tip_button.set_halign(Gtk.Align.CENTER)
        self.tip_button.set_valign(Gtk.Align.END)
        self.tip_button.set_margin_bottom(18)
        self.tip_button.connect("clicked", self._on_tip_clicked)
        self.append(self.tip_button)

    def _build_status_content(self):
        content = box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=8, halign=Gtk.Align.CENTER)
        content.set_size_request(520, -1)
        self.status_page.set_child(content)

        logo = Gtk.Image.new_from_icon_name(IconThemeManager.get_app_icon_name())
        logo.set_pixel_size(80)
        title = label(text="PDF Atlas", wrap=False, halign=Gtk.Align.START)
        title.add_css_class("title-1")
        title.set_hexpand(True)
        header = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                     halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                     children=[logo, title])
        content.append(header)

        actions = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                      halign=Gtk.Align.CENTER, css_class="linked")
        actions.append(button(label="Open File", tooltip="Open PDF [Ctrl+O]",
                              css_class="suggested-action",
                              on_clicked=lambda b: self.win._open_file_dialog()))
        actions.append(button(label="Open from arXiv", tooltip="Open an arXiv paper",
                              on_clicked=lambda b: self.win._open_arxiv_dialog()))
        content.append(actions)

        recents_header = box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        recents_title = label(text="Recent Documents", css_class="heading", halign=Gtk.Align.START)
        recents_header.append(recents_title)
        content.append(recents_header)

        self.recent_list = Gtk.ListBox()
        self.recent_list.add_css_class("welcome-recent-list")
        self.recent_list.add_css_class("card")
        self.recent_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_list.set_hexpand(False)
        self.recent_list.set_halign(Gtk.Align.CENTER)
        self.recent_list.set_size_request(260, -1)
        self.recent_list.connect("row-activated", self._on_recent_activated)
        content.append(self.recent_list)

        self.recent_empty_label = label(
            text="No recent documents yet, open a PDF to get started.",
            css_class="dim-label", halign=Gtk.Align.CENTER,
        )
        self.recent_empty_label.set_visible(False)
        content.append(self.recent_empty_label)

    def refresh(self, recents: "RecentFilesManager"):
        while (child := self.recent_list.get_first_child()) is not None:
            self.recent_list.remove(child)

        recent = recents.get_recent(WELCOME_RECENT_MAX)
        if recent:
            self.recent_empty_label.set_visible(False)
            self.recent_list.set_visible(True)
            for source in recent:
                row = RecentRow(source)
                row_box = box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                              margin_top=6, margin_bottom=6,
                              margin_start=12, margin_end=12)
                name = label(text=source.display_name or source.uri, wrap=False,
                             halign=Gtk.Align.START)
                name.set_single_line_mode(True)
                name.set_ellipsize(Pango.EllipsizeMode.END)
                name.add_css_class("welcome-recent-name")
                row_box.append(name)

                if source.is_arxiv:
                    from ..core.arxiv_mapper import arxiv_id_from_path
                    aid = arxiv_id_from_path(source.uri)
                    secondary = f"arXiv:{aid}" if aid else _shorten_path(source.uri)
                else:
                    secondary = _shorten_path(source.uri)
                path = label(text=secondary, css_class="caption dim-label",
                             halign=Gtk.Align.START, wrap=False)
                path.set_single_line_mode(True)
                path.set_ellipsize(Pango.EllipsizeMode.END)
                row_box.append(path)

                row.set_child(row_box)
                self.recent_list.append(row)
        else:
            self.recent_empty_label.set_visible(True)
            self.recent_list.set_visible(False)

        random_start = random.randrange(len(TIP_MESSAGES))
        self._tip_index = random_start
        self.tip_label.set_label(TIP_MESSAGES[self._tip_index])

    def _on_tip_clicked(self, button):
        if not TIP_MESSAGES:
            return
        self._tip_index = (self._tip_index + 1) % len(TIP_MESSAGES)
        self.tip_label.set_label(TIP_MESSAGES[self._tip_index])

    def _on_recent_activated(self, listbox, row):
        if isinstance(row, RecentRow) and row.source is not None:
            self.win.open_document(row.source)