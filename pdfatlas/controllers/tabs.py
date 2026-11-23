from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib

if TYPE_CHECKING:
    from ..ui.document_view import PdfDocumentView
    from ..ui.window import MainWindow


MOVE_TAB_ACTION = "win.move-tab-to-window"
MAX_DIRNAME_CHARS = 32
MAX_FILE_CHARS = 28


def _truncate_end(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _escape_label(text: str) -> str:
    """Escape Pango markup and neutralize mnemonics (single underscores)."""
    return GLib.markup_escape_text(text).replace("_", "__")


def _source_dirname(source: object) -> str | None:
    source_type = getattr(source, "source_type", None)
    if source_type == "file":
        uri = getattr(source, "uri", "")
        if uri:
            return _path_dirname(str(uri))
    elif source_type == "arxiv" or getattr(source, "is_arxiv", False):
        return "arXiv"
    elif source_type == "url" or getattr(source, "is_url", False):
        return "Web"
    return None


def _path_dirname(path_str: str) -> str | None:
    try:
        p = Path(path_str)
        return p.parent.name or str(p.parent)
    except Exception:
        return None


class TabController:
    """
    Manages Adw.TabView pages, multi-tab navigation, tab state synchronization,
    and cross-window tab detachment / creation.
    """

    def __init__(self, win: MainWindow):
        self.win = win
        self.tab_view: Adw.TabView = Adw.TabView()
        self.tab_bar: Adw.TabBar = Adw.TabBar(view=self.tab_view)
        self.tab_bar.set_autohide(True)
        self._active_doc_view_ref: PdfDocumentView | None = None
        self._context_page: Adw.TabPage | None = None
        self._tab_menu = Gio.Menu()
        self.tab_view.set_menu_model(self._tab_menu)
        # Header-bar options menu dynamic section and submenu.
        self.header_move_section = Gio.Menu()
        self.header_move_menu = Gio.Menu()

        self.tab_view.connect("create-window", self.on_create_window)
        self.tab_view.connect("page-attached", self.on_page_attached)
        self.tab_view.connect("page-detached", self.on_page_detached)
        self.tab_view.connect("close-page", self.on_close_page)
        self.tab_view.connect("notify::selected-page", self.on_selected_tab_changed)
        self.tab_view.connect("setup-menu", self.on_setup_menu)

    def create_doc_view(self) -> PdfDocumentView:
        from ..ui.document_view import PdfDocumentView

        doc_view = PdfDocumentView(
            render_worker=self.win.render_worker,
            settings=self.win.settings,
            db_service=self.win.db_service,
            on_page_changed=self.on_doc_view_page_changed,
            on_zoom_changed=self.on_doc_view_zoom_changed,
            on_link_clicked=self.on_doc_view_link_clicked,
            on_note_create=self.win._on_canvas_note_create,
            on_selection_changed=self.win._update_selection_toolbar,
            on_toast=self.win._show_toast,
            on_state_changed=self.win._schedule_state_save,
            on_annotations_changed=self.win._update_annotations_button,
            on_remove_custom_link=self.win._on_remove_custom_link,
            on_remove_matching_custom_links=self.win._on_remove_matching_custom_links,
            on_highlight_selection=self.win._on_highlight_selection,
            on_add_link_selection=self.win._on_add_link_selection,
        )
        doc_view.canvas.set_night_mode(
            self.win.is_effective_dark(),
            invert_amount=self.win.settings.night_mode_invert,
            hue_rotate=self.win.settings.night_mode_hue_rotate,
        )
        return doc_view

    def get_active_doc_view(self) -> PdfDocumentView | None:
        from ..ui.document_view import PdfDocumentView

        page = self.tab_view.get_selected_page()
        if page is not None:
            child = page.get_child()
            if isinstance(child, PdfDocumentView):
                return child
        return None

    # --- Tab context menu ---

    def _app_windows(self) -> list[MainWindow]:
        from ..ui.window import MainWindow

        app = self.win.app
        if app is not None:
            windows = [w for w in app.get_windows() if isinstance(w, MainWindow)]
            if windows:
                return windows
        return [self.win]

    def on_setup_menu(self, view: Adw.TabView, page: Adw.TabPage | None) -> None:
        """Build the tab context menu for the right-clicked page.

        The menu model is mutated in place: libadwaita captures the model
        pointer before this signal fires, so replacing the model would not
        affect the popover that is about to be shown.
        """
        if page is None:
            self._context_page = None
            return
        self._context_page = page
        self._rebuild_tab_menu()

    def _rebuild_tab_menu(self) -> None:
        menu = self._tab_menu
        menu.remove_all()

        tab_section = Gio.Menu()
        tab_section.append("New Tab", "win.new-tab")
        tab_section.append("New Window", "win.new-window")
        menu.append_section(None, tab_section)

        others = [w for w in self._app_windows() if w is not self.win]
        if others:
            menu.append_section(None, self._build_move_section(others))

        close_section = Gio.Menu()
        close_section.append("Close Tab", "win.close-tab")
        if self.tab_view.get_n_pages() > 1:
            close_section.append("Close Other Tabs", "win.close-other-tabs")
        menu.append_section(None, close_section)

    def _build_move_section(self, others: list[MainWindow] | None = None) -> Gio.Menu:
        section = Gio.Menu()
        move_item = Gio.MenuItem.new("Move to Window…", None)
        move_item.set_submenu(self._build_move_submenu(others))
        section.append_item(move_item)
        return section

    def _build_move_submenu(self, others: list[MainWindow] | None = None) -> Gio.Menu:
        submenu = Gio.Menu()
        self._populate_move_submenu(submenu, others=others)
        return submenu

    def _populate_move_submenu(
        self, submenu: Gio.Menu, others: list[MainWindow] | None = None
    ) -> None:
        submenu.remove_all()
        if others is None:
            others = [w for w in self._app_windows() if w is not self.win]

        if not others:
            return

        other_section = Gio.Menu()
        for win in others:
            other_section.append_item(self._build_window_item(win, current=False))
        submenu.append_section(None, other_section)

        current_section = Gio.Menu()
        current_section.append_item(self._build_window_item(self.win, current=True))
        submenu.append_section(None, current_section)

    def refresh_header_move_submenu(self) -> None:
        """Refresh the header-bar Move to Window… submenu just before it opens."""
        self._context_page = None
        others = [w for w in self._app_windows() if w is not self.win]
        self.header_move_section.remove_all()
        if others:
            self._populate_move_submenu(self.header_move_menu, others=others)
            move_item = Gio.MenuItem.new("Move to Window…", None)
            move_item.set_submenu(self.header_move_menu)
            self.header_move_section.append_item(move_item)
        else:
            self.header_move_menu.remove_all()

    def _build_window_item(self, win: MainWindow, current: bool) -> Gio.MenuItem:
        item = Gio.MenuItem.new(None, None)
        item.set_label(self._window_label(win, current))
        # GtkMenuTrackerItem reads the "use-markup" attribute as a string.
        item.set_attribute_value("use-markup", GLib.Variant.new_string("true"))
        item.set_icon(
            Gio.ThemedIcon.new(
                "object-select-symbolic" if current else "folder-open-symbolic"
            )
        )
        if not current:
            item.set_action_and_target_value(
                MOVE_TAB_ACTION, GLib.Variant.new_string(str(win.window_uid))
            )
        return item

    def _window_dirname(self, win: MainWindow) -> str | None:
        from ..ui.document_view import PdfDocumentView

        pages: list[Adw.TabPage] = []
        selected = win.tab_view.get_selected_page()
        if selected is not None:
            pages.append(selected)
        for i in range(win.tab_view.get_n_pages()):
            page = win.tab_view.get_nth_page(i)
            if page is not None and page not in pages:
                pages.append(page)

        for page in pages:
            child = page.get_child()
            if isinstance(child, PdfDocumentView):
                if child.current_source is not None:
                    name = _source_dirname(child.current_source)
                    if name:
                        return name
                if child.doc_model and child.doc_model.filepath:
                    name = _path_dirname(child.doc_model.filepath)
                    if name:
                        return name

            # Attributes on page or child (mocked in tests or attached directly)
            src = getattr(page, "source", None) or getattr(child, "source", None)
            if src is not None:
                name = _source_dirname(src) if hasattr(src, "source_type") else _path_dirname(str(src))
                if name:
                    return name

            uri = getattr(page, "uri", None) or getattr(child, "uri", None)
            if uri:
                name = _path_dirname(str(uri))
                if name:
                    return name

        if getattr(win, "current_source", None):
            name = _source_dirname(win.current_source)
            if name:
                return name

        return None

    def _window_label(self, win: MainWindow, current: bool) -> str:
        index = getattr(win, "window_uid", "?")
        raw_dirname = self._window_dirname(win)
        dirname = _truncate_end(raw_dirname, MAX_DIRNAME_CHARS) if raw_dirname else None
        files = self._window_files_summary(win)
        marker = "  <i>(this window)</i>" if current else ""
        header = f"Window {index} · {_escape_label(dirname)}" if dirname else f"Window {index}"
        return (
            f'<span line_height="1.35"><b>{header}</b>\n'
            f'<span size="smaller" alpha="60%">{_escape_label(files)}{marker}</span></span>'
        )

    def _window_files_summary(self, win: MainWindow) -> str:
        titles: list[str] = []
        for i in range(win.tab_view.get_n_pages()):
            page = win.tab_view.get_nth_page(i)
            if page is None:
                continue
            title = (page.get_title() or "").strip()
            if title:
                titles.append(_truncate_end(title, MAX_FILE_CHARS))

        if not titles:
            return "(no open documents)"

        summary = ", ".join(titles[:2])
        if len(titles) > 2:
            summary += f", +{len(titles) - 2} more"
        return summary

    def _find_window_by_uid(self, uid: str) -> MainWindow | None:
        for win in self._app_windows():
            if str(getattr(win, "window_uid", "")) == uid:
                return win
        return None

    def _target_page(self) -> Adw.TabPage | None:
        """Page acted on by the context menu or the header options menu."""
        return self._context_page or self.tab_view.get_selected_page()

    def move_page_to_window_by_uid(self, uid: str) -> None:
        page = self._target_page()
        target = self._find_window_by_uid(uid)
        if page is None or target is None:
            return
        self.move_page_to_window(page, target)

    def move_page_to_window(self, page: Adw.TabPage, target_win: MainWindow) -> None:
        """Move *page* into *target_win*, reusing the live tab contents."""
        if target_win is self.win:
            return

        target_view = target_win.tab_view
        target_win.stack.set_visible_child_name("document-view")
        self.tab_view.transfer_page(page, target_view, target_view.get_n_pages())
        target_view.set_selected_page(page)
        target_win.present()
        self._context_page = None

    def close_context_tab(self) -> None:
        page = self._target_page()
        if page is None:
            return
        self._context_page = None
        self.tab_view.close_page(page)

    def close_other_tabs(self) -> None:
        page = self._target_page()
        if page is None:
            return
        pages = [
            self.tab_view.get_nth_page(i)
            for i in range(self.tab_view.get_n_pages())
        ]
        for other in pages:
            if other is not None and other is not page:
                self.tab_view.close_page(other)

    def on_create_window(self, view: Adw.TabView) -> Adw.TabView:
        from ..ui.window import MainWindow

        new_win = MainWindow(
            self.win.app,
            render_workers=self.win.render_workers,
            use_shm=self.win.use_shm,
            show_welcome=False,
            experimental_search_masonry=getattr(self.win, "experimental_search_masonry", False),
        )
        new_win.settings.color_scheme = self.win.settings.color_scheme
        new_win.settings.night_mode_invert = self.win.settings.night_mode_invert
        new_win.settings.night_mode_hue_rotate = self.win.settings.night_mode_hue_rotate
        new_win._apply_color_scheme()
        new_win.present()
        return new_win.tab_view

    def on_page_attached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        from ..ui.document_view import PdfDocumentView

        self.win.stack.set_visible_child_name("document-view")
        child = page.get_child()
        if isinstance(child, PdfDocumentView):
            # A page transferred from another window must use this window's
            # render worker, not the one it was created with.
            child.set_render_worker(self.win.render_worker)
            child.canvas.set_night_mode(
                self.win.is_effective_dark(),
                invert_amount=self.win.settings.night_mode_invert,
                hue_rotate=self.win.settings.night_mode_hue_rotate,
            )
        self.on_selected_tab_changed(view, None)

    def on_page_detached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        if page is self._context_page:
            self._context_page = None
        if view.get_n_pages() == 0:
            self.win.doc_model = None
            self.win.current_source = None
            self._active_doc_view_ref = None
            windows = self.win.app.get_windows() if self.win.app else []
            if len(windows) > 1:
                self.win.close()
            else:
                self.win._show_welcome()

    def on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        from ..ui.document_view import PdfDocumentView

        if page is self._context_page:
            self._context_page = None
        child = page.get_child()
        if isinstance(child, PdfDocumentView):
            child.close()
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            windows = self.win.app.get_windows() if self.win.app else []
            if len(windows) > 1:
                self.win.close()
            else:
                self.win._show_welcome()
        return True

    def on_selected_tab_changed(self, view: Adw.TabView, pspec) -> None:
        from ..ui.welcome import WelcomeView
        from ..ui.document_view import PdfDocumentView

        # Save scroll position of previous active doc_view before switching away
        if self._active_doc_view_ref is not None:
            prev = self._active_doc_view_ref
            if isinstance(prev, PdfDocumentView):
                if prev.vadjustment is not None:
                    prev.saved_scroll_y = prev.vadjustment.get_value()
                if prev.hadjustment is not None:
                    prev.saved_scroll_x = prev.hadjustment.get_value()

        doc_view = self.get_active_doc_view()
        self._active_doc_view_ref = doc_view if isinstance(doc_view, PdfDocumentView) else None

        if isinstance(doc_view, WelcomeView):
            self.win.search_controller.exit_to_document(clear_query=True)
            self.win.stack.set_visible_child_name("document-view")
            if hasattr(self.win, "recent_files") and hasattr(self.win.recent_files, "reload"):
                self.win.recent_files.reload()
            doc_view.refresh(self.win.recent_files)
            self.win.filename_label.set_label("PDF Atlas")
            self.win.set_title("PDF Atlas")
            self.win.entry.set_sensitive(False)
            self.win.entry.set_placeholder_text("No document loaded")
            self.win.page_input.set_text("")
            self.win.page_total_label.set_label("")
            self.win.page_input.set_sensitive(False)
            if self.win.annotations_btn:
                self.win.annotations_btn.set_visible(False)
            if self.win.zoom_label:
                self.win.zoom_label.set_label("100%")
        elif isinstance(doc_view, PdfDocumentView):
            self.win.stack.set_visible_child_name("document-view")
            if doc_view.doc_model is not None:
                self.win.canvas = doc_view.canvas
                self.win.vadjustment = doc_view.vadjustment
                self.win.hadjustment = doc_view.hadjustment
                self.win.doc_model = doc_view.doc_model
                self.win.current_source = doc_view.current_source
                self.win.zoom = doc_view.zoom
                if self.win.zoom_label:
                    self.win.zoom_label.set_label(f"{int(self.win.zoom * 100)}%")
                self.win.arxiv_mapper = doc_view.arxiv_mapper
                self.win.crop_analyzer = doc_view.crop_analyzer
                self.win.notes_layer = doc_view.notes_layer
                self.win.notes = doc_view.notes
                self.win.highlights = doc_view.highlights
                self.win.custom_links = getattr(doc_view, "custom_links", [])

                # Sync night mode to the activated tab
                doc_view.canvas.set_night_mode(
                    self.win.night_mode,
                    invert_amount=self.win.settings.night_mode_invert,
                    hue_rotate=self.win.settings.night_mode_hue_rotate,
                )

                # Sync per-document crop & gapless action states
                if self.win.crop_action and doc_view.settings:
                    self.win.crop_action.set_state(GLib.Variant.new_boolean(doc_view.settings.enabled))
                if self.win.gapless_action and doc_view.settings:
                    self.win.gapless_action.set_state(GLib.Variant.new_boolean(not doc_view.settings.page_gaps))

                if doc_view.settings and doc_view.settings.enabled:
                    self.win._scan_crop_if_needed()

                # Restore scroll position safely
                doc_view.restore_scroll_position()

                curr_page = doc_view.get_current_page_index() + 1
                self.win.page_input.set_text(str(curr_page))
                self.win.page_total_label.set_label(f"of {doc_view.doc_model.page_count}")
                self.win.page_input.set_sensitive(True)
                title = (
                    doc_view.current_source.display_name
                    if doc_view.current_source
                    else "PDF Viewer"
                )
                self.win.set_title(f"PDF Viewer — {title}")
                self.win.filename_label.set_label(title)
                self.win.entry.set_sensitive(True)
                self.win.entry.set_placeholder_text("Search document...")
                if self.win.annotations_btn:
                    self.win.annotations_btn.set_visible(True)
                self.win._update_annotations_button()
                doc_view.canvas._update_visibility()
                doc_view.canvas.gl_canvas.queue_draw()
                doc_view.canvas.queue_draw_overlays("tab-selected")
                self.win.search_controller.exit_to_document(clear_query=False)
            else:
                # Tab is downloading/loading
                self.win.doc_model = None
                self.win.current_source = doc_view.current_source
                title = (
                    doc_view.loading_title.get_label()
                    if doc_view.is_loading
                    else "Loading..."
                )
                self.win.set_title(f"PDF Viewer — {title}")
                self.win.filename_label.set_label(title)
                self.win.search_controller.exit_to_document(clear_query=True)
                self.win.entry.set_sensitive(False)
                self.win.entry.set_placeholder_text("Downloading document...")
                self.win.page_input.set_text("")
                self.win.page_total_label.set_label("")
                self.win.page_input.set_sensitive(False)
                if self.win.annotations_btn:
                    self.win.annotations_btn.set_visible(False)
                if self.win.zoom_label:
                    self.win.zoom_label.set_label("100%")
        elif view.get_n_pages() == 0:
            self.win._show_welcome()

    def on_doc_view_page_changed(self, current: int, total: int):
        self.win.page_input.set_text(str(current))
        self.win.page_total_label.set_label(f"of {total}")

    def on_doc_view_zoom_changed(self, zoom: float):
        self.win.zoom = zoom
        if self.win.zoom_label:
            self.win.zoom_label.set_label(f"{int(zoom * 100)}%")

    def on_doc_view_link_clicked(self, uri: str, link: dict):
        self.win._on_link_clicked(0, link)

    def new_tab(self):
        """Open a new tab with the welcome view."""
        from ..ui.welcome import WelcomeView

        if hasattr(self.win, "recent_files") and hasattr(self.win.recent_files, "reload"):
            self.win.recent_files.reload()
        welcome = WelcomeView(self.win)
        welcome.refresh(self.win.recent_files)
        page = self.tab_view.append(welcome)
        page.props.title = "New Tab"
        self.tab_view.set_selected_page(page)
        self.win.stack.set_visible_child_name("document-view")

    def close_current_tab(self):
        """Close the currently active tab."""
        page = self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)

    def new_window(self) -> MainWindow:
        """Open a new PDF Atlas window."""
        from ..ui.window import MainWindow

        win = MainWindow(
            self.win.app,
            render_workers=self.win.render_workers,
            use_shm=self.win.use_shm,
        )
        win.present()
        return win

    def next_tab(self):
        n = self.tab_view.get_n_pages()
        if n > 1:
            curr_page = self.tab_view.get_selected_page()
            if curr_page is not None:
                idx = self.tab_view.get_page_position(curr_page)
                next_page = self.tab_view.get_nth_page((idx + 1) % n)
                self.tab_view.set_selected_page(next_page)

    def prev_tab(self):
        n = self.tab_view.get_n_pages()
        if n > 1:
            curr_page = self.tab_view.get_selected_page()
            if curr_page is not None:
                idx = self.tab_view.get_page_position(curr_page)
                prev_page = self.tab_view.get_nth_page((idx - 1 + n) % n)
                self.tab_view.set_selected_page(prev_page)

    def select_tab(self, index: int):
        if 0 <= index < self.tab_view.get_n_pages():
            self.tab_view.set_selected_page(self.tab_view.get_nth_page(index))
