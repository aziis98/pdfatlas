from __future__ import annotations

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import time
from typing import TYPE_CHECKING, Callable
import gi

if TYPE_CHECKING:
    from .document_view import PdfDocumentView

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..controllers.annotations import (
    PALETTE_COLORS,
    PALETTE_COLS,
    AnnotationsController,
    simplify_md_preview,
)
from ..controllers.document_loader import DocumentLoader
from ..controllers.navigation import NavigationController
from ..controllers.search import SearchController
from ..controllers.tabs import TabController
from ..core.arxiv_mapper import ArxivDiffMapper, arxiv_id_from_path, extract_arxiv_id_from_raw
from ..core.cache import MiniMapCache, RenderCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.index import DatabaseService
from ..core.installation import ensure_app_installed, is_app_installed
from ..core.pdf_source import PdfSource, RecentFilesManager
from ..core.renderer import RenderWorker
from ..core.settings import CropSettings
from .arxiv_dialog import ArxivDialog
from .canvas import PDFCanvas
from .components.floating_controls import FloatingControls
from .document_view import PdfDocumentView
from .gui import box, label, search_entry
from .link_preview import LinkPreviewManager
from .minimap import MinimapWindow
from .notes import NotesLayer
from .services import IconThemeManager
from .settings import SettingsWindow
from .shortcuts import ShortcutsController
from .theme import load_window_css
from .welcome import WelcomeView

DEBOUNCE_MS = 150  # search-as-you-type debounce delay
_simplify_md_preview = simplify_md_preview

__all__ = [
    "MainWindow",
    "PALETTE_COLORS",
    "PALETTE_COLS",
    "simplify_md_preview",
    "_simplify_md_preview",
    "clamp",
    "DEBOUNCE_MS",
]


def clamp(min_val: float, val: float, max_val: float) -> float:
    """Clamps a numeric value within the range [min_val, max_val]."""
    return max(min_val, min(max_val, val))


class MainWindow(Adw.ApplicationWindow):
    """
    Main Adwaita application window.
    Features:
      - HeaderBar with centered fuzzy SearchEntry and crop/minimap/settings buttons.
      - TabView and TabBar for multi-tab document viewing and window detachment.
      - Gtk.Stack holding the PDF Canvas view, fuzzy search portal view, and welcome view.
      - Delegated controllers for tab management, document loading, annotations, navigation, and search.
    """

    def __init__(
        self,
        app,
        state=None,
        follow_link=None,
        debug_mode=False,
        debug_note_rect=False,
        render_workers=2,
        use_shm=True,
    ):
        super().__init__(application=app)
        self.app = app
        self.set_title("PDF Viewer")
        self.set_default_size(1000, 700)

        # 1. Window & CLI execution parameters
        self.initial_state = state
        self.follow_link = follow_link
        self.debug_mode = debug_mode
        self.debug_note_rect = debug_note_rect
        self.render_workers = render_workers
        self.use_shm = use_shm
        self._deferred_state_query: str | None = None

        # 2. Core models & persistent configuration
        self.doc_model: DocumentModel | None = None
        self.crop_analyzer: CropAnalyzer | None = None
        self.settings = CropSettings.load()
        self.current_source: PdfSource | None = None
        self.recent_files = RecentFilesManager()
        self.arxiv_mapper: ArxivDiffMapper | None = None

        # 3. LRU Caches and background rendering worker
        self.render_cache = RenderCache(20)
        self.minimap_cache = MiniMapCache(1000)
        self.render_worker = RenderWorker(
            num_workers=render_workers, use_shm=use_shm
        )

        print(f"[PDFAtlas] render workers: {render_workers}", flush=True)
        if use_shm:
            print("[PDFAtlas] Zero-copy SHM IPC enabled", flush=True)

        # 4. Search indexing & database persistence
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.db_service = DatabaseService()
        self.index_conn: sqlite3.Connection | None = None
        self.pinned = {}  # id -> {"result": ..., "query_terms": ...}
        self._debounce_source_id = None
        self._last_query = ""

        # 5. UI, viewport, annotations & interaction state
        self.zoom = 1.0
        self.pointer_x: float = 0.0
        self.pointer_y: float = 0.0
        self.highlights: list[dict] = []
        self.notes: list[dict] = []
        self._active_progress_tasks: dict[str, dict] = {}
        self.night_mode = self.is_effective_dark()
        self._last_link_click_time: float = 0.0
        self.crop_scanned_count: int = 0
        self.notes_layer: NotesLayer | None = None
        self.on_annotations_changed: Callable[[], None] | None = None
        self.on_page_changed: Callable[[int, int], None] | None = None

        # Optional / on-demand widgets
        self.minimap_dialog: MinimapWindow | None = None

        # 6. Feature controllers
        self.floating_controls = FloatingControls(self)
        self.annotations_controller = AnnotationsController(self)
        self.tab_controller = TabController(self)
        self.document_loader = DocumentLoader(self)
        self.nav_controller = NavigationController(self)
        self.search_controller = SearchController(self)

        # 7. Window actions and menu signals
        self._setup_actions()

        # 8. Build UI hierarchy & widgets
        self._build_ui()

        # 9. Keyboard shortcuts and window event listeners
        self.shortcuts_controller = ShortcutsController(self)
        self.connect("realize", self._on_window_realized)

        # Apply saved color scheme and synchronize night mode on launch
        self._apply_color_scheme()

    # --- Property Forwarders for 100% Backward Compatibility ---

    @property
    def tab_view(self) -> Adw.TabView:
        return self.tab_controller.tab_view

    @property
    def tab_bar(self) -> Adw.TabBar:
        return self.tab_controller.tab_bar

    @property
    def annotations_btn(self) -> Gtk.MenuButton:
        return self.annotations_controller.annotations_btn  # type: ignore

    @annotations_btn.setter
    def annotations_btn(self, val: Gtk.MenuButton):
        self.annotations_controller.annotations_btn = val

    @property
    def annotations_popover(self) -> Gtk.Popover:
        return self.annotations_controller.annotations_popover  # type: ignore

    @annotations_popover.setter
    def annotations_popover(self, val: Gtk.Popover):
        self.annotations_controller.annotations_popover = val

    @property
    def annotations_count_label(self) -> Gtk.Label:
        return self.annotations_controller.annotations_count_label  # type: ignore

    @annotations_count_label.setter
    def annotations_count_label(self, val: Gtk.Label):
        self.annotations_controller.annotations_count_label = val

    @property
    def annotations_list(self) -> Gtk.Box:
        return self.annotations_controller.annotations_list  # type: ignore

    @annotations_list.setter
    def annotations_list(self, val: Gtk.Box):
        self.annotations_controller.annotations_list = val

    @property
    def selection_toolbar(self) -> Gtk.Box:
        return self.annotations_controller.selection_toolbar  # type: ignore

    @selection_toolbar.setter
    def selection_toolbar(self, val: Gtk.Box):
        self.annotations_controller.selection_toolbar = val

    @property
    def btn_copy_text(self) -> Gtk.Button:
        return self.annotations_controller.btn_copy_text  # type: ignore

    @btn_copy_text.setter
    def btn_copy_text(self, val: Gtk.Button):
        self.annotations_controller.btn_copy_text = val

    @property
    def btn_copy_tex(self) -> Gtk.Button:
        return self.annotations_controller.btn_copy_tex  # type: ignore

    @btn_copy_tex.setter
    def btn_copy_tex(self, val: Gtk.Button):
        self.annotations_controller.btn_copy_tex = val

    @property
    def btn_highlight(self) -> Adw.SplitButton:
        return self.annotations_controller.btn_highlight  # type: ignore

    @btn_highlight.setter
    def btn_highlight(self, val: Adw.SplitButton):
        self.annotations_controller.btn_highlight = val

    @property
    def btn_remove_hl(self) -> Gtk.Button:
        return self.annotations_controller.btn_remove_hl  # type: ignore

    @btn_remove_hl.setter
    def btn_remove_hl(self, val: Gtk.Button):
        self.annotations_controller.btn_remove_hl = val

    @property
    def active_highlight_color(self) -> str:
        return self.annotations_controller.active_highlight_color

    @active_highlight_color.setter
    def active_highlight_color(self, val: str):
        self.annotations_controller.active_highlight_color = val

    @property
    def zoom_label(self) -> Gtk.Label:
        return self.floating_controls.zoom_label  # type: ignore

    @zoom_label.setter
    def zoom_label(self, val: Gtk.Label):
        self.floating_controls.zoom_label = val

    @property
    def zoom_floating_box(self) -> Gtk.Box:
        return self.floating_controls.zoom_floating_box

    @zoom_floating_box.setter
    def zoom_floating_box(self, val: Gtk.Box):
        self.floating_controls.zoom_floating_box = val

    @property
    def link_preview_label(self) -> Gtk.Label:
        return self.floating_controls.link_preview_label

    @link_preview_label.setter
    def link_preview_label(self, val: Gtk.Label):
        self.floating_controls.link_preview_label = val

    @property
    def link_preview_card_box(self) -> Gtk.Box:
        return self.floating_controls.link_preview_card_box

    @link_preview_card_box.setter
    def link_preview_card_box(self, val: Gtk.Box):
        self.floating_controls.link_preview_card_box = val

    @property
    def progress_label(self) -> Gtk.Label:
        return self.floating_controls.progress_label

    @progress_label.setter
    def progress_label(self, val: Gtk.Label):
        self.floating_controls.progress_label = val

    @property
    def progress_card_box(self) -> Gtk.Box:
        return self.floating_controls.progress_card_box

    @progress_card_box.setter
    def progress_card_box(self, val: Gtk.Box):
        self.floating_controls.progress_card_box = val

    @property
    def link_preview_box(self) -> Gtk.Box:
        return self.floating_controls.link_preview_box

    @link_preview_box.setter
    def link_preview_box(self, val: Gtk.Box):
        self.floating_controls.link_preview_box = val

    @property
    def debug_info_label(self) -> Gtk.Label | None:
        return self.floating_controls.debug_info_label

    @debug_info_label.setter
    def debug_info_label(self, val: Gtk.Label | None):
        self.floating_controls.debug_info_label = val

    @property
    def debug_arxiv_label(self) -> Gtk.Label | None:
        return self.floating_controls.debug_arxiv_label

    @debug_arxiv_label.setter
    def debug_arxiv_label(self, val: Gtk.Label | None):
        self.floating_controls.debug_arxiv_label = val

    @property
    def debug_cache_label(self) -> Gtk.Label | None:
        return self.floating_controls.debug_cache_label

    @debug_cache_label.setter
    def debug_cache_label(self, val: Gtk.Label | None):
        self.floating_controls.debug_cache_label = val

    @property
    def portal_cache(self):
        return self.link_preview_manager.portal_cache

    # --- Actions Setup & Entry Focus ---

    def _setup_actions(self):
        """Register application and window Gio actions."""
        # Night mode stateful toggle
        self.night_mode_action = Gio.SimpleAction.new_stateful(
            "night-mode", None, GLib.Variant.new_boolean(self.night_mode)
        )
        self.night_mode_action.connect("activate", self._on_night_mode_action_activated)
        self.add_action(self.night_mode_action)

        Adw.StyleManager.get_default().connect("notify::dark", self._on_style_manager_dark_changed)

        # Gapless mode stateful toggle
        self.gapless_action = Gio.SimpleAction.new_stateful(
            "gapless-mode", None, GLib.Variant.new_boolean(not self.settings.page_gaps)
        )
        self.gapless_action.connect("activate", self._on_gapless_action_activated)
        self.add_action(self.gapless_action)

        # Crop mode stateful toggle
        self.crop_action = Gio.SimpleAction.new_stateful(
            "crop-mode", None, GLib.Variant.new_boolean(self.settings.enabled)
        )
        self.crop_action.connect("activate", self._on_crop_action_activated)
        self.add_action(self.crop_action)

        # Stateless command actions
        actions = [
            ("open-settings", lambda act, param: self._on_settings_btn_clicked(None)),
            ("about", lambda act, param: self._on_about_action_activated(None)),
            ("install-app", lambda act, param: self._on_install_app_action_activated()),
            ("open-file", lambda act, param: self._open_file_dialog()),
            ("open-arxiv", lambda act, param: self._open_arxiv_dialog()),
            ("new-tab", lambda act, param: self.new_tab()),
            ("new-window", lambda act, param: self.new_window()),
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        open_recent_action = Gio.SimpleAction.new("open-recent", GLib.VariantType.new("s"))
        open_recent_action.connect("activate", self._on_open_recent)
        self.add_action(open_recent_action)

    def _is_entry_focused(self) -> bool:
        focus = self.get_focus()
        if focus is not None and isinstance(focus, (Gtk.Editable, Gtk.Entry, Gtk.SearchEntry)):
            return True
        if self.entry.has_focus() or (self.page_input and self.page_input.has_focus()):
            return True
        return False

    def _setup_system_icons(self):
        IconThemeManager.setup_system_icons(self)

    # --- UI Layout Construction ---

    def _build_ui(self):
        self._setup_system_icons()

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        header = Adw.HeaderBar()

        # Left: Open Button & Filename Label
        self.open_btn = Adw.SplitButton()
        self.open_btn.set_icon_name("document-open-symbolic")
        self.open_btn.set_tooltip_text("Open PDF [Ctrl+O]")
        self.open_btn.add_css_class("raised")
        self.open_btn.connect("clicked", lambda b: self._open_file_dialog())
        self._rebuild_open_menu()

        self.filename_label = label(
            text="No document loaded",
            css_class="caption",
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=40,
            xalign=0,
        )
        left_box = box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            children=[self.open_btn, self.filename_label],
        )
        header.pack_start(left_box)

        # Center: Search Entry
        self.entry = search_entry(placeholder="No document loaded", sensitive=False)
        self.entry.connect("search-changed", self.search_controller.on_search_changed_debounced)
        self.entry.connect("activate", self.search_controller.on_activate_immediate)
        header.set_title_widget(self.entry)

        # Right: Page Navigation Entry + Total Pages Label, Menu Button
        right_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_end=12)
        header.pack_end(right_box)

        # Tag icon button for annotations (placed on the left of page_input)
        self.annotations_btn.set_icon_name("tag-symbolic")
        self.annotations_btn.set_tooltip_text("Annotations & Highlights")
        self.annotations_btn.set_visible(False)
        self.annotations_btn.set_popover(self.annotations_controller.annotations_popover)
        right_box.append(self.annotations_btn)

        self.page_input = Gtk.Entry()
        self.page_input.set_width_chars(4)
        self.page_input.set_max_width_chars(4)
        self.page_input.set_max_length(5)
        self.page_input.set_alignment(0.5)
        self.page_input.set_sensitive(False)
        self.page_input.set_text("1")
        self.page_input.set_hexpand(False)
        self.page_input.set_halign(Gtk.Align.CENTER)
        self.page_input.add_css_class("page-input")
        self.page_input.connect("activate", self._on_page_input_activate)
        page_input_focus = Gtk.EventControllerFocus.new()
        page_input_focus.connect("leave", lambda ctrl: self._on_scroll_page_changed(self.vadjustment))
        self.page_input.add_controller(page_input_focus)
        right_box.append(self.page_input)

        self.page_total_label = label(text="of 0")
        right_box.append(self.page_total_label)

        # GMenu Model
        menu = Gio.Menu.new()
        menu.append("Night Mode", "win.night-mode")
        menu.append("Gap-less Mode", "win.gapless-mode")
        menu.append("Auto-crop Mode", "win.crop-mode")
        section = Gio.Menu.new()
        section.append("Open Settings", "win.open-settings")
        section.append("About PDF Atlas", "win.about")
        menu.append_section(None, section)
        install_section = Gio.Menu.new()
        install_section.append("Install Desktop Application", "win.install-app")
        menu.append_section(None, install_section)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("view-more-symbolic")
        self.menu_button.set_tooltip_text("Options")
        self.menu_button.set_menu_model(menu)
        self.menu_button_overlay = Gtk.Overlay()
        self.menu_button_overlay.set_child(self.menu_button)

        self.menu_badge_dot = Gtk.Box()
        self.menu_badge_dot.add_css_class("menu-badge-dot")
        self.menu_badge_dot.set_halign(Gtk.Align.END)
        self.menu_badge_dot.set_valign(Gtk.Align.START)
        self.menu_badge_dot.set_margin_top(4)
        self.menu_badge_dot.set_margin_end(4)
        self.menu_badge_dot.set_can_target(False)
        self.menu_button_overlay.add_overlay(self.menu_badge_dot)

        right_box.append(self.menu_button_overlay)
        self._update_installation_badge_status()

        # Content Overlay + Stack
        self.content_overlay = Gtk.Overlay()
        self.content_overlay.set_hexpand(True)
        self.content_overlay.set_vexpand(True)

        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(header)
        self.toolbar_view.add_top_bar(self.tab_bar)
        self.toolbar_view.set_content(self.content_overlay)
        self.toast_overlay.set_child(self.toolbar_view)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.content_overlay.set_child(self.stack)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.add_css_class("osd")
        self.progress_bar.set_valign(Gtk.Align.START)
        self.progress_bar.set_halign(Gtk.Align.FILL)
        self.progress_bar.set_visible(False)
        self.content_overlay.add_overlay(self.progress_bar)

        self.css_provider = load_window_css()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        # Fallback Document Canvas
        self.canvas = PDFCanvas()
        self.canvas.win = self
        self.canvas.debug_mode = self.debug_mode
        self.canvas.on_link_clicked = self._on_link_clicked
        self.canvas.on_page_hovered = self._on_page_hovered
        self.canvas.on_selection_changed = self._update_selection_toolbar
        self._apply_color_scheme()

        self._build_floating_zoom_controls()
        self._build_floating_link_preview()
        self._build_selection_toolbar()

        self.link_preview_manager = LinkPreviewManager(self)
        self.canvas.on_link_hovered = self.link_preview_manager.on_link_hovered

        self.notes_layer = NotesLayer(self)
        self.canvas.on_note_create = self._on_canvas_note_create
        self.canvas.notes_layer = self.notes_layer

        self.canvas.add_overlay(self.zoom_floating_box)
        self.canvas.add_overlay(self.link_preview_box)
        self.canvas.add_overlay(self.link_preview_manager.portal_card)
        if self.debug_mode:
            self._build_debug_cache_box()

        self.stack.add_named(self.tab_view, "document-view")

        # Search View
        from .components.search_results_view import SearchResultsView

        self.search_results_view = SearchResultsView(
            on_row_clicked=self.search_controller.on_row_clicked,
            on_toggle_pin=self.search_controller.on_toggle_pin,
        )
        self.search_scrolled = self.search_results_view.scrolled
        self.results_box = self.search_results_view.results_box
        self.stack.add_named(self.search_results_view, "search-view")

        # Welcome View (empty-window landing screen)
        self.welcome_view = WelcomeView(self)
        self.stack.add_named(self.welcome_view, "welcome-view")

        # Centered Loading View (for fetching remote papers)
        self.loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        self.loading_box.set_size_request(480, -1)

        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_size_request(48, 48)
        self.loading_spinner.set_halign(Gtk.Align.CENTER)
        self.loading_spinner.start()
        self.loading_box.append(self.loading_spinner)

        self.loading_title = Gtk.Label()
        self.loading_title.add_css_class("title-2")
        self.loading_title.set_justify(Gtk.Justification.CENTER)
        self.loading_title.set_halign(Gtk.Align.CENTER)
        self.loading_title.set_hexpand(True)
        self.loading_title.set_label("Downloading Paper...")
        self.loading_box.append(self.loading_title)

        self.loading_subtitle = Gtk.Label()
        self.loading_subtitle.add_css_class("dim-label")
        self.loading_subtitle.set_justify(Gtk.Justification.CENTER)
        self.loading_subtitle.set_halign(Gtk.Align.CENTER)
        self.loading_subtitle.set_hexpand(True)
        self.loading_subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.loading_subtitle.set_label("Connecting to arXiv...")
        self.loading_box.append(self.loading_subtitle)

        self.loading_progress_bar = Gtk.ProgressBar()
        self.loading_progress_bar.set_size_request(360, 6)
        self.loading_progress_bar.set_halign(Gtk.Align.CENTER)
        self.loading_box.append(self.loading_progress_bar)

        self.stack.add_named(self.loading_box, "loading-view")
        self.stack.connect("notify::visible-child-name", self._on_stack_visible_child_changed)

        # Adjustments wiring (owned by the canvas)
        self.vadjustment = self.canvas.vadjustment
        self.hadjustment = self.canvas.hadjustment
        self.vadjustment.connect("value-changed", self._on_scroll_page_changed)
        self.hadjustment.connect("value-changed", self._on_horizontal_scroll_changed)

        self._show_welcome()

    def _show_welcome(self):
        """Show the empty-window welcome screen with fresh recents and a tip."""
        self.welcome_view.refresh(self.recent_files)
        self.stack.set_visible_child_name("welcome-view")

    def _on_stack_visible_child_changed(self, stack, param):
        if stack.get_visible_child_name() == "document-view" and self.doc_model:
            self.canvas.update_layout()
            self.canvas._update_visibility()
            self.canvas.gl_canvas.queue_draw()
            self.canvas.queue_draw_overlays("stack-shown")

    def _on_window_realized(self, widget):
        if os.environ.get("PDFATLAS_HIDE_CURSOR") == "1":
            blank_cursor = Gdk.Cursor.new_from_name("none", None)
            self.set_cursor(blank_cursor)
            surface = self.get_surface()
            if surface:
                surface.set_cursor(blank_cursor)

    def _on_canvas_note_create(self, page: int, x: float, y: float):
        if self.notes_layer is not None:
            self.notes_layer.create_note(page, x, y)

    def _show_toast(self, message: str):
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # --- Multi-Tab Management Delegations ---

    def _create_doc_view(self) -> PdfDocumentView:
        return self.tab_controller.create_doc_view()

    def get_active_doc_view(self) -> PdfDocumentView | None:
        return self.tab_controller.get_active_doc_view()

    def _on_create_window(self, view: Adw.TabView) -> Adw.TabView:
        return self.tab_controller.on_create_window(view)

    def _on_page_attached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        self.tab_controller.on_page_attached(view, page, position)

    def _on_page_detached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        self.tab_controller.on_page_detached(view, page, position)

    def _on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        return self.tab_controller.on_close_page(view, page)

    def _on_selected_tab_changed(self, view: Adw.TabView, pspec) -> None:
        self.tab_controller.on_selected_tab_changed(view, pspec)

    def _on_doc_view_page_changed(self, current: int, total: int):
        self.tab_controller.on_doc_view_page_changed(current, total)

    def _on_doc_view_zoom_changed(self, zoom: float):
        self.tab_controller.on_doc_view_zoom_changed(zoom)

    def _on_doc_view_link_clicked(self, uri: str, link: dict):
        self.tab_controller.on_doc_view_link_clicked(uri, link)

    def new_tab(self):
        """Open a new tab with the welcome view."""
        self.tab_controller.new_tab()

    def close_current_tab(self):
        """Close the currently active tab."""
        self.tab_controller.close_current_tab()

    def new_window(self):
        """Open a new PDF Atlas window."""
        return self.tab_controller.new_window()

    def next_tab(self):
        self.tab_controller.next_tab()

    def prev_tab(self):
        self.tab_controller.prev_tab()

    def select_tab(self, index: int):
        self.tab_controller.select_tab(index)

    # --- Document Loading & Indexing Delegations ---

    def open_document(self, source: PdfSource, new_tab: bool = False):
        self.document_loader.open_document(source, new_tab=new_tab)

    def _index_worker(self, filepath):
        self.document_loader._index_worker(filepath)

    def _arxiv_diff_worker(self, arxiv_id: str, filepath: str):
        self.document_loader._arxiv_diff_worker(arxiv_id, filepath)

    def _on_arxiv_diff_progress(self, fraction: float):
        self.document_loader._on_arxiv_diff_progress(fraction)

    def _on_arxiv_diff_complete(self, mapper: ArxivDiffMapper | None):
        self.document_loader._on_arxiv_diff_complete(mapper)

    def _on_indexing_complete(self, conn):
        self.document_loader._on_indexing_complete(conn)

    def _schedule_state_save(self):
        self.document_loader.schedule_state_save()

    def _save_current_doc_state(self):
        self.document_loader.save_current_doc_state()

    # --- Annotations & Selection Toolbar Delegations ---

    def _build_annotations_popover(self):
        return self.annotations_controller.build_annotations_popover()

    def _update_annotations_button(self):
        self.annotations_controller.update_annotations_button()

    def _activate_annotation(self, hl: dict):
        self.annotations_controller.activate_annotation(hl)

    def _activate_note(self, note: dict):
        self.annotations_controller.activate_note(note)

    def _delete_annotation(self, hl: dict):
        self.annotations_controller.delete_annotation(hl)

    def _on_highlights_loaded(self, highlights: list[dict]):
        self.annotations_controller.on_highlights_loaded(highlights)

    def _on_notes_loaded(self, notes: list[dict]):
        self.annotations_controller.on_notes_loaded(notes)

    def _build_selection_toolbar(self):
        tb = self.annotations_controller.build_selection_toolbar()
        self.content_overlay.add_overlay(tb)
        return tb

    def _update_highlight_split_button_label(self):
        self.annotations_controller.update_highlight_split_button_label()

    def _select_highlight_color(self, hex_color: str, popover: Gtk.Popover):
        self.annotations_controller.select_highlight_color(hex_color, popover)

    def _apply_highlight_to_selection(self):
        self.annotations_controller.apply_highlight_to_selection()

    def _remove_highlights_in_selection(self, popover: Gtk.Popover | None = None):
        self.annotations_controller.remove_highlights_in_selection(popover)

    def _remove_matching_highlights(self):
        self.annotations_controller.remove_matching_highlights()

    def _selection_matching_highlights(self) -> list[dict]:
        return self.annotations_controller.selection_matching_highlights()

    def _copy_pdf_text_to_clipboard(self):
        self.annotations_controller.copy_pdf_text_to_clipboard()

    def _copy_tex_to_clipboard(self):
        self.annotations_controller.copy_tex_to_clipboard()

    def _copy_selection_to_clipboard(self):
        self.annotations_controller.copy_selection_to_clipboard()

    def _update_selection_toolbar(self, has_selection: bool | None = None):
        self.annotations_controller.update_selection_toolbar(has_selection)

    # --- Floating Controls Delegations ---

    def _build_floating_zoom_controls(self):
        return self.floating_controls.build_floating_zoom_controls()

    def _build_floating_link_preview(self):
        return self.floating_controls.build_floating_link_preview()

    def _build_debug_cache_box(self):
        return self.floating_controls.build_debug_cache_box()

    def _refresh_debug_cache(self) -> bool:
        return self.floating_controls.refresh_debug_cache()

    def _on_page_hovered(self, page_index: int | None, x: float, y: float):
        self.floating_controls.on_page_hovered(page_index, x, y)

    # --- File Dialog & Recent Files Menu ---

    def _on_horizontal_scroll_changed(self, adj):
        if self.notes_layer is not None:
            self.notes_layer.hide_preview()
        self._schedule_state_save()

    def _open_file_dialog(self):
        dialog = Gtk.FileChooserNative.new(
            "Open PDF File", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel"
        )
        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF Files")
        filter_pdf.add_mime_type("application/pdf")
        filter_pdf.add_pattern("*.pdf")
        dialog.add_filter(filter_pdf)
        dialog.connect("response", self._on_open_response)
        dialog.show()

    def _on_open_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            if path:
                existing = self.recent_files.get_by_uri(path)
                aid = arxiv_id_from_path(path)
                if not existing and aid:
                    existing = self.recent_files.get_by_arxiv_id(aid)

                if existing:
                    source = existing
                elif aid:
                    source = PdfSource(source_type="arxiv", uri=path, display_name=f"arXiv:{aid}")
                else:
                    source = PdfSource(source_type="file", uri=path, display_name=os.path.basename(path))
                self.open_document(source)
        dialog.destroy()

    def _show_error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show()

    def _rebuild_open_menu(self):
        menu = Gio.Menu.new()

        tab_section = Gio.Menu.new()
        tab_section.append("New Tab", "win.new-tab")
        tab_section.append("New Window", "win.new-window")
        menu.append_section(None, tab_section)

        open_file_section = Gio.Menu.new()
        open_file_section.append("Open File\u2026", "win.open-file")
        menu.append_section(None, open_file_section)

        arxiv_section = Gio.Menu.new()
        arxiv_section.append("Open from arXiv\u2026", "win.open-arxiv")
        menu.append_section(None, arxiv_section)

        recent = self.recent_files.get_recent(5)
        if recent:
            recent_section = Gio.Menu.new()
            for source in recent:
                display_name = source.display_name
                if len(display_name) > 36:
                    display_name = display_name[:34] + "\u2026"
                recent_section.append(
                    display_name.replace("_", "__"), f"win.open-recent::{source.uri}"
                )
            menu.append_section(None, recent_section)

        self.open_btn.set_menu_model(menu)

    def _on_open_recent(self, action, parameter):
        uri = parameter.get_string()
        existing = self.recent_files.get_by_uri(uri)
        if existing:
            source = existing
        else:
            aid = arxiv_id_from_path(uri)
            if aid:
                source = PdfSource(source_type="arxiv", uri=uri, display_name=f"arXiv:{aid}")
            else:
                source = PdfSource(source_type="file", uri=uri, display_name=os.path.basename(uri))
        self.open_document(source)

    def _open_arxiv_dialog(self):
        ArxivDialog(
            parent_window=self,
            on_source=self._on_arxiv_source,
            recent_files=self.recent_files,
        ).present()

    def _on_arxiv_source(self, source: PdfSource):
        self.open_document(source)

    def _update_installation_badge_status(self):
        if is_app_installed():
            self.menu_badge_dot.set_visible(False)
        else:
            self.menu_badge_dot.set_visible(True)

    def _on_install_app_action_activated(self):
        success = ensure_app_installed(force=True)
        if success:
            self._show_toast("PDF Atlas desktop entry installed successfully!")
            self._update_installation_badge_status()
        else:
            self._show_error_dialog("Failed to install desktop application.")

    # --- Search Engine Wiring ---

    def run_search(self, query: str):
        self.search_controller.run_search(query)

    def _on_escape(self):
        """Clears search input or closes minimap modal and returns focus to reader view."""
        if self.minimap_dialog and self.minimap_dialog.get_visible():
            self.minimap_dialog.close()
            self.minimap_dialog = None
            if self.canvas:
                self.canvas.grab_focus()
            return True

        if self.page_input and self.page_input.has_focus():
            self._on_scroll_page_changed(self.vadjustment)
            if self.canvas:
                self.canvas.grab_focus()
            return True

        if self.stack.get_visible_child_name() == "search-view" or self.entry.has_focus():
            self.entry.set_text("")
            self.stack.set_visible_child_name("document-view")
            if self.canvas:
                self.canvas.grab_focus()
            return True

        # Clear text selection on Escape
        if (
            self.canvas
            and self.canvas.text_selection is not None
            and self.canvas.text_selection.has_selection()
        ):
            self.canvas.clear_selection()
            return True

        return False

    # --- Zoom & Navigation Operations ---

    def get_current_page_index(self) -> int:
        if not self.doc_model or not self.canvas or not self.canvas.page_layout:
            return 0

        y_val = self.vadjustment.get_value() if self.vadjustment else 0.0
        viewport_h = self.vadjustment.get_page_size() if self.vadjustment else 700.0
        y_center = y_val + (viewport_h / 2.0)

        for i, layout in enumerate(self.canvas.page_layout):
            y_offset, dw, dh, crop_rect = layout
            page_y0 = y_offset
            page_y1 = y_offset + dh + self.canvas.page_gap
            if page_y0 <= y_center <= page_y1:
                return i
        return 0

    def _queue_canvas_redraw(self):
        """Redraws the OpenGL background canvas."""
        if self.canvas:
            self.canvas.queue_draw_overlays("redraw")

    def page_step(self, forward: bool):
        current_idx = self.get_current_page_index()
        target_idx = current_idx + 1 if forward else current_idx - 1
        self.jump_to_page(target_idx)

    def _open_new_instance_for_source(self, target: str):
        from ..core.process_utils import launch_pdfatlas_process

        try:
            return launch_pdfatlas_process(target)
        except Exception as e:
            print(f"[MainWindow] Error launching new PDF Atlas instance for {target}: {e}", flush=True)
            return None

    def _on_link_clicked(self, page_index: int, link: dict):
        if not self.doc_model or not self.canvas or not self.canvas.page_layout:
            return

        target_page = link.get("page")

        if (
            target_page is None
            or not isinstance(target_page, int)
            or target_page < 0
            or target_page >= self.doc_model.page_count
        ):
            if uri := link.get("uri"):
                aid = extract_arxiv_id_from_raw(uri) or arxiv_id_from_path(uri)
                if aid:
                    now = time.monotonic()
                    if self._last_link_click_time + 0.5 > now:
                        return
                    self._last_link_click_time = now
                    source = PdfSource(
                        source_type="arxiv",
                        uri=f"arxiv:{aid}",
                        display_name=f"arXiv:{aid}",
                    )
                    self.open_document(source, new_tab=True)
                    return

                try:
                    Gtk.show_uri(self, uri, Gdk.CURRENT_TIME)
                except Exception as e:
                    print(f"[MainWindow] Error launching URI {uri}: {e}", flush=True)
            return

        y_offset_in_page = self.doc_model.resolve_link_target_y(link)

        y_offset, dw, dh, crop_rect = self.canvas.page_layout[target_page]
        crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
        pt_y = max(0.0, y_offset_in_page - crop_off_y)
        scaled_y = pt_y * self.zoom * self.canvas.dpi_scale_factor

        viewport_h = self.vadjustment.get_page_size() if self.vadjustment else 700.0
        if viewport_h <= 1.0:
            viewport_h = 700.0

        lower = self.vadjustment.get_lower() if self.vadjustment else 0.0
        upper = self.vadjustment.get_upper() if self.vadjustment else 2000.0
        max_y = max(lower, upper - viewport_h)
        target_y = clamp(lower, (y_offset + scaled_y) - (viewport_h / 2.0), max_y)

        if self.vadjustment:
            self.vadjustment.set_value(target_y)
            self._on_scroll_page_changed(self.vadjustment)
        self._queue_canvas_redraw()

    def _follow_link_by_index(self, link_index: int) -> bool:
        if not self.doc_model:
            return False

        current_count = 0
        for page_idx in range(self.doc_model.page_count):
            links = self.doc_model.get_page_links(page_idx)
            for link in links:
                if current_count == link_index:
                    print(
                        f"[MainWindow] Following link #{link_index} on page {page_idx + 1}: {link}",
                        flush=True,
                    )
                    self._on_link_clicked(page_idx, link)
                    return False
                current_count += 1
        print(f"[MainWindow] Link #{link_index} not found (total links: {current_count})", flush=True)
        return False

    def toggle_minimap(self):
        if not self.doc_model or not self.vadjustment or not self.canvas:
            return

        if self.minimap_dialog and self.minimap_dialog.get_visible():
            self.minimap_dialog.close()
            self.minimap_dialog = None
            return

        val = self.vadjustment.get_value()
        y_center = val + self.vadjustment.get_page_size() / 2

        active_page = 0
        for i, (y_offset, dw, dh, crop_rect) in enumerate(self.canvas.page_layout):
            if y_offset <= y_center <= y_offset + dh + self.canvas.page_gap:
                active_page = i
                break

        dialog = MinimapWindow(
            parent_window=self,
            doc_model=self.doc_model,
            cache=self.minimap_cache,
            render_worker=self.render_worker,
            crop_analyzer=self.crop_analyzer,
            settings=self.settings,
            main_vadjustment=self.vadjustment,
            main_zoom=self.zoom,
            on_page_selected=self._on_minimap_page_clicked,
            page_layout=self.canvas.page_layout if self.canvas else None,
            page_gap=self.canvas.page_gap if self.canvas else 12,
        )

        self.minimap_dialog = dialog
        dialog.connect("close-request", lambda w: setattr(self, "minimap_dialog", None))
        dialog.minimap.set_current_page(active_page)
        dialog.present()

    def _on_minimap_page_clicked(self, page_index):
        self.minimap_dialog = None
        self.jump_to_page(page_index)

    # --- Toggles & Crop Analyzer ---

    def toggle_crop(self):
        self.settings.enabled = not self.settings.enabled
        self._on_crop_settings_updated()
        if self.settings.enabled:
            self._scan_crop_if_needed()

    def _scan_crop_if_needed(self):
        if self.settings.enabled and self.crop_analyzer and not all(self.crop_analyzer.scanned):
            self._start_crop_analysis()

    def toggle_gapless(self):
        self.settings.page_gaps = not self.settings.page_gaps
        self.gapless_action.set_state(GLib.Variant.new_boolean(not self.settings.page_gaps))
        self._on_crop_settings_updated()

    def _on_crop_btn_toggled(self, btn):
        self.settings.enabled = btn.get_active()
        self._on_crop_settings_updated()
        if self.settings.enabled:
            self._scan_crop_if_needed()

    def _on_settings_btn_clicked(self, btn):
        SettingsWindow(
            parent_window=self,
            settings=self.settings,
            on_changed=self._on_settings_changed,
            on_reanalyze=self._on_reanalyze,
        ).present(self)

    def _on_about_action_activated(self, action=None, param=None):
        Adw.AboutDialog(
            application_name="PDF Atlas",
            application_icon=IconThemeManager.get_app_icon_name(),
            developer_name="PDF Atlas Team",
            version="1.0.0",
            comments="High-performance PDF document viewer with spatial navigator and FTS5 search.",
        ).present(self)

    def _on_settings_changed(self):
        self._on_crop_settings_updated()
        min_zoom = self.settings.min_zoom
        max_zoom = self.settings.max_zoom
        if self.zoom < min_zoom or self.zoom > max_zoom:
            self.set_zoom_level(self.zoom)
        if self._last_query:
            self.run_search(self._last_query)

    def _on_crop_settings_updated(self):
        if self.crop_action:
            self.crop_action.set_state(GLib.Variant.new_boolean(self.settings.enabled))
        if self.gapless_action:
            self.gapless_action.set_state(GLib.Variant.new_boolean(self.settings.page_gaps))
        self._apply_color_scheme()
        self.settings.save()

        if self.crop_analyzer:
            self.crop_analyzer.compute_crop_rects(self.settings)

        if self.canvas:
            self.canvas.on_crop_changed()

    def _on_reanalyze(self):
        self._start_crop_analysis(force=True)

    def _start_crop_analysis(self, force: bool = False):
        if not self.doc_model or not self.crop_analyzer:
            return
        if not force and not self.settings.enabled:
            return

        page_count = self.doc_model.page_count

        if not force and self.db_service:
            def _on_loaded(cached_bboxes):
                if not self.crop_analyzer or not self.doc_model:
                    return
                if cached_bboxes is not None and len(cached_bboxes) == page_count:
                    self.crop_analyzer.raw_bboxes = cached_bboxes
                    self.crop_analyzer.scanned = [True] * page_count
                    self.crop_analyzer.compute_crop_rects(self.settings)
                    if self.canvas:
                        self.canvas.on_crop_changed()
                    return
                self._run_crop_scan()

            self.db_service.load_crop_bboxes(page_count, _on_loaded)
        else:
            self._run_crop_scan()

    def _run_crop_scan(self):
        if not self.doc_model or not self.crop_analyzer:
            return
        page_count = self.doc_model.page_count
        self.crop_analyzer.scanned = [False] * page_count
        self.crop_analyzer.raw_bboxes = [None] * page_count

        self.crop_scanned_count = 0
        self._show_progress("crop_analysis", "Scanning page margins for auto-crop...", 0.0)

        for i in range(page_count):
            if self.render_worker:
                self.render_worker.queue_crop_job(
                    self.doc_model,
                    self.crop_analyzer,
                    i,
                    self.settings,
                    self._on_crop_page_scanned,
                    self._on_crop_analysis_complete,
                )

    def _on_crop_page_scanned(self, page_index):
        self.crop_scanned_count += 1
        total = self.doc_model.page_count if self.doc_model else 1
        self._show_progress(
            "crop_analysis",
            "Scanning page margins for auto-crop...",
            self.crop_scanned_count / total,
        )

    def _on_crop_analysis_complete(self):
        self._hide_progress("crop_analysis")
        if self.db_service and self.crop_analyzer:
            self.db_service.save_crop_bboxes(self.crop_analyzer.raw_bboxes)
        if self.canvas:
            self.canvas.on_crop_changed()

    def _show_progress(self, task_id: str, description: str, fraction: float):
        pct = int(round(fraction * 100))
        formatted_desc = f"{description} ({pct}%)" if fraction > 0 else description

        self._active_progress_tasks[task_id] = {
            "description": description,
            "formatted": formatted_desc,
            "fraction": fraction,
        }

        max_fraction = max(t["fraction"] for t in self._active_progress_tasks.values())
        self.progress_bar.set_fraction(max_fraction)
        self.progress_bar.set_visible(True)

        latest_task = list(self._active_progress_tasks.values())[-1]
        progress_text = latest_task["formatted"]

        self.progress_label.set_label(progress_text)
        self.progress_card_box.set_visible(True)
        self.link_preview_box.set_visible(True)

    def _hide_progress(self, task_id: str):
        if task_id in self._active_progress_tasks:
            del self._active_progress_tasks[task_id]

        if not self._active_progress_tasks:
            self.progress_bar.set_visible(False)
            self.progress_card_box.set_visible(False)
            if not self.link_preview_card_box.get_visible():
                self.link_preview_box.set_visible(False)
        else:
            max_fraction = max(t["fraction"] for t in self._active_progress_tasks.values())
            self.progress_bar.set_fraction(max_fraction)
            latest_task = list(self._active_progress_tasks.values())[-1]
            progress_text = latest_task["formatted"]
            self.progress_label.set_label(progress_text)

    # --- Navigation & Zoom Operations ---

    def _simulate_link_hover(self, link_index: int) -> bool:
        if not self.doc_model:
            return False

        current_count = 0
        for page_idx in range(self.doc_model.page_count):
            links = self.doc_model.get_page_links(page_idx)
            for link in links:
                if current_count == link_index:
                    print(
                        f"[MainWindow] Simulating hover on link #{link_index} (page {page_idx + 1}): {link}",
                        flush=True,
                    )
                    self.link_preview_manager.on_link_hovered(page_idx, link)
                    return False
                current_count += 1
        print(f"[MainWindow] Link #{link_index} not found (total links: {current_count})", flush=True)
        return False

    def _run_scroll_benchmark(self, config: dict[str, object] | str) -> bool:
        try:
            if isinstance(config, dict):
                y_start = float(str(config.get("y_start", 0.0)))
                default_y_end = self.vadjustment.get_upper() if self.vadjustment else 2000.0
                y_end = float(str(config.get("y_end", default_y_end)))
                interval_ms = int(str(config.get("interval_ms", 16)))
                steps = int(str(config.get("steps", 60)))
                repeat_count = int(str(config.get("repeat_count", 1)))
                auto_quit = bool(config.get("auto_quit", False))
            else:
                parts = [p.strip() for p in config.split(",") if p.strip()]
                y_start = float(parts[0]) if len(parts) > 0 else 0.0
                y_end = (
                    float(parts[1])
                    if len(parts) > 1
                    else (self.vadjustment.get_upper() if self.vadjustment else 2000.0)
                )
                interval_ms = int(parts[2]) if len(parts) > 2 else 16
                steps = int(parts[3]) if len(parts) > 3 else 60
                repeat_count = int(parts[4]) if len(parts) > 4 else 1
                auto_quit = bool(int(parts[5])) if len(parts) > 5 else False
        except Exception as e:
            print(f"[ScrollBenchmark] Invalid config format '{config}': {e}", flush=True)
            return False

        print(
            f"[ScrollBenchmark] Starting benchmark: y_start={y_start}, y_end={y_end}, interval={interval_ms}ms, steps={steps}, repeats={repeat_count}",
            flush=True,
        )

        current_step = 0
        current_repeat = 0
        direction = 1

        def _step_callback():
            nonlocal current_step, current_repeat, direction
            current_step += 1
            t = current_step / steps
            if direction == 1:
                target_y = y_start + t * (y_end - y_start)
            else:
                target_y = y_end + t * (y_start - y_end)

            if self.vadjustment:
                self.vadjustment.set_value(target_y)

            if current_step >= steps:
                current_step = 0
                if direction == 1:
                    direction = -1
                else:
                    direction = 1
                    current_repeat += 1

                if current_repeat >= repeat_count:
                    print("[ScrollBenchmark] Benchmark complete.", flush=True)
                    if auto_quit:
                        GLib.timeout_add(500, lambda: self.app.quit())
                    return False

            return True

        GLib.timeout_add(interval_ms, _step_callback)
        return False

    def jump_to_page(self, page_index: int, smooth: bool = True):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.jump_to_page(page_index)
        else:
            self.nav_controller.jump_to_page(page_index, smooth=smooth)

    def set_zoom_level(
        self,
        new_zoom: float,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        center_x: float | None = None,
        center_y: float | None = None,
    ):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.set_zoom_level(
                new_zoom,
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                center_x=center_x,
                center_y=center_y,
            )
        else:
            self.nav_controller.set_zoom_level(
                new_zoom,
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                center_x=center_x,
                center_y=center_y,
            )
        self._schedule_state_save()

    def zoom_in(self):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.zoom_in()
        else:
            self.nav_controller.zoom_in()

    def zoom_out(self):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.zoom_out()
        else:
            self.nav_controller.zoom_out()

    def zoom_reset(self):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.set_zoom_level(1.0)
        else:
            self.nav_controller.zoom_reset()

    def zoom_fit_width(self):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.zoom_fit_width()
        else:
            self.nav_controller.zoom_fit_width()

    def zoom_fit_page(self):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.zoom_fit_page()
        else:
            self.nav_controller.zoom_fit_page()

    def scroll_page(self, forward: bool = True):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.page_step(1 if forward else -1)
        else:
            self.nav_controller.scroll_page(forward=forward)

    def scroll_step(self, forward: bool = True):
        doc_view = self.get_active_doc_view()
        if doc_view is not None:
            doc_view.scroll_step(1 if forward else -1)
        else:
            self.nav_controller.scroll_step(forward=forward)

    def _on_page_input_activate(self, entry):
        if not self.doc_model or not self.canvas or not self.canvas.page_layout:
            return

        text = entry.get_text().strip()
        try:
            page_num = int(text)
            page_idx = page_num - 1
            if 0 <= page_idx < self.doc_model.page_count:
                self.jump_to_page(page_idx)
                self.canvas.grab_focus()
        except ValueError:
            self._on_scroll_page_changed(self.vadjustment)

    def _on_gapless_action_activated(self, action, parameter):
        old_state = action.get_state().get_boolean()
        new_state = not old_state
        action.set_state(GLib.Variant.new_boolean(new_state))

        self.settings.page_gaps = not new_state
        self._on_crop_settings_updated()

    def _on_crop_action_activated(self, action, parameter):
        old_state = action.get_state().get_boolean()
        new_state = not old_state
        action.set_state(GLib.Variant.new_boolean(new_state))

        self.settings.enabled = new_state
        self._on_crop_settings_updated()

    def is_effective_dark(self) -> bool:
        scheme = self.settings.color_scheme
        if scheme == "dark":
            return True
        elif scheme == "light":
            return False
        else:  # "system"
            return Adw.StyleManager.get_default().get_dark()

    def _apply_color_scheme(self):
        scheme = self.settings.color_scheme
        style_mgr = Adw.StyleManager.get_default()
        if scheme == "dark":
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif scheme == "light":
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:  # "system"
            style_mgr.set_color_scheme(Adw.ColorScheme.DEFAULT)

        # Broadcast color scheme and sync night mode across all open windows
        windows = self.app.get_windows() if self.app else [self]
        for win in windows:
            if isinstance(win, MainWindow):
                win.settings.color_scheme = scheme
                win.settings.night_mode_invert = self.settings.night_mode_invert
                win.settings.night_mode_hue_rotate = self.settings.night_mode_hue_rotate
                win._sync_effective_theme()

    def _sync_effective_theme(self):
        self.night_mode = self.is_effective_dark()
        if self.night_mode_action:
            self.night_mode_action.set_state(GLib.Variant.new_boolean(self.night_mode))
        if self.tab_view is not None:
            for i in range(self.tab_view.get_n_pages()):
                page = self.tab_view.get_nth_page(i)
                child = page.get_child()
                if isinstance(child, PdfDocumentView):
                    child.canvas.set_night_mode(
                        self.night_mode,
                        invert_amount=self.settings.night_mode_invert,
                        hue_rotate=self.settings.night_mode_hue_rotate,
                    )
        if self.canvas:
            self.canvas.set_night_mode(
                self.night_mode,
                invert_amount=self.settings.night_mode_invert,
                hue_rotate=self.settings.night_mode_hue_rotate,
            )

    def _on_style_manager_dark_changed(self, style_mgr, pspec):
        self._sync_effective_theme()

    def toggle_night_mode(self):
        current_dark = self.is_effective_dark()
        self.settings.color_scheme = "light" if current_dark else "dark"
        self._apply_color_scheme()
        self.settings.save()

    def _on_night_mode_action_activated(self, action, parameter):
        self.toggle_night_mode()

    def _on_scroll_page_changed(self, adj):
        if self.notes_layer is not None:
            self.notes_layer.hide_preview()
        if not self.doc_model or not self.canvas or not self.canvas.page_layout:
            return

        y_val = adj.get_value()
        viewport_h = adj.get_page_size()
        y_center = y_val + (viewport_h / 2.0)

        page_layout = self.canvas.page_layout
        offsets = [layout[0] for layout in page_layout]
        idx = bisect_right(offsets, y_center) - 1
        current_idx = max(0, min(idx, len(page_layout) - 1))

        page_num_str = str(current_idx + 1)
        if self.page_input and not self.page_input.has_focus():
            if self.page_input.get_text() != page_num_str:
                self.page_input.set_text(page_num_str)
