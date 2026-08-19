from __future__ import annotations

from typing import TYPE_CHECKING, Any
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

if TYPE_CHECKING:
    from ..ui.window import MainWindow


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
        self._active_doc_view_ref: Any | None = None

        self.tab_view.connect("create-window", self.on_create_window)
        self.tab_view.connect("page-attached", self.on_page_attached)
        self.tab_view.connect("page-detached", self.on_page_detached)
        self.tab_view.connect("close-page", self.on_close_page)
        self.tab_view.connect("notify::selected-page", self.on_selected_tab_changed)

    def create_doc_view(self) -> Any:
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
        )
        doc_view.canvas.set_night_mode(
            self.win.is_effective_dark(),
            invert_amount=self.win.settings.night_mode_invert,
            hue_rotate=self.win.settings.night_mode_hue_rotate,
        )
        return doc_view

    def get_active_doc_view(self) -> Any:
        page = self.tab_view.get_selected_page()
        if page is not None:
            return page.get_child()
        return None

    def on_create_window(self, view: Adw.TabView) -> Adw.TabView:
        from ..ui.window import MainWindow

        new_win = MainWindow(
            self.win.app,
            render_mode=self.win.render_mode,
            render_workers=self.win.render_workers,
            use_shm=self.win.use_shm,
        )
        new_win.stack.set_visible_child_name("document-view")
        new_win.settings.color_scheme = self.win.settings.color_scheme
        new_win.settings.night_mode_invert = self.win.settings.night_mode_invert
        new_win.settings.night_mode_hue_rotate = self.win.settings.night_mode_hue_rotate
        new_win._apply_color_scheme()
        new_win.present()
        return new_win.tab_view

    def on_page_attached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        self.win.stack.set_visible_child_name("document-view")
        child = page.get_child()
        canvas = getattr(child, "canvas", None)
        if canvas is not None:
            canvas.set_night_mode(
                self.win.is_effective_dark(),
                invert_amount=self.win.settings.night_mode_invert,
                hue_rotate=self.win.settings.night_mode_hue_rotate,
            )
        self.on_selected_tab_changed(view, None)

    def on_page_detached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
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
        child = page.get_child()
        if hasattr(child, "close"):
            getattr(child, "close")()
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
            if hasattr(prev, "vadjustment") and prev.vadjustment is not None:
                prev.saved_scroll_y = prev.vadjustment.get_value()
            if hasattr(prev, "hadjustment") and prev.hadjustment is not None:
                prev.saved_scroll_x = prev.hadjustment.get_value()

        doc_view = self.get_active_doc_view()
        self._active_doc_view_ref = doc_view if isinstance(doc_view, PdfDocumentView) else None

        if isinstance(doc_view, WelcomeView):
            self.win.stack.set_visible_child_name("document-view")
            doc_view.refresh(self.win.recent_files)
            self.win.filename_label.set_label("PDF Atlas")
            self.win.set_title("PDF Atlas")
            self.win.entry.set_sensitive(False)
            self.win.entry.set_text("")
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

                # Sync night mode to the activated tab
                doc_view.canvas.set_night_mode(
                    self.win.night_mode,
                    invert_amount=self.win.settings.night_mode_invert,
                    hue_rotate=self.win.settings.night_mode_hue_rotate,
                )

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
                self.win.entry.set_sensitive(False)
                self.win.entry.set_text("")
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

    def new_window(self) -> Any:
        """Open a new PDF Atlas window."""
        from ..ui.window import MainWindow

        win = MainWindow(
            self.win.app,
            render_mode=self.win.render_mode,
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
