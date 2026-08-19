import os
import re
import threading
import time
from bisect import bisect_right
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..controllers.navigation import NavigationController
from ..controllers.search import SearchController
from ..core.arxiv_mapper import ArxivDiffMapper, arxiv_id_from_path, extract_arxiv_id_from_raw
from ..core.cache import MiniMapCache, RenderCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.index import DatabaseService, get_db_for_pdf, load_doc_state
from ..core.installation import ensure_app_installed, is_app_installed
from ..core.pdf_source import PdfSource, RecentFilesManager
from ..core.renderer import create_render_worker
from ..core.settings import CropSettings
from ..core.state import CliState
from .arxiv_dialog import ArxivDialog
from .cairo_utils import hsl_to_hex
from .canvas import PDFCanvas
from .gui import box, button, label, search_entry, spacer
from .link_preview import LinkPreviewManager
from .minimap import MinimapWindow
from .notes import NotesLayer
from .services import IconThemeManager
from .settings import SettingsWindow
from .shortcuts import ShortcutsController
from .theme import load_window_css
from .welcome import WelcomeView

DEBOUNCE_MS = 150  # search-as-you-type debounce delay

#: Max characters of a simplified note preview before GTK ellipsizes it.
MAX_NOTE_PREVIEW_CHARS = 100


def _simplify_md_preview(markdown: str) -> str:
    """One-line markdown preview for the overview list row.

    Strips heading markers line-by-line, drops math delimiters ($ / $$) and
    bold/italic markers (*, **, _, __), joins all lines with spaces, collapses
    whitespace, and truncates around MAX_NOTE_PREVIEW_CHARS; GTK ellipsizes
    the rest.
    """
    lines = []
    for ln in (markdown or "").splitlines():
        ln = re.sub(r"^#{1,6}\s*", "", ln).strip()
        if ln:
            lines.append(ln)
    text = re.sub(r"(\$+|\*+|_+)", "", " ".join(lines))
    text = re.sub(r"\s{2,}", " ", text).strip()[:MAX_NOTE_PREVIEW_CHARS]
    return text or "(Note)"

# Fluorescent highlighter pen colors sorted strictly by hue with increased lightness (74%-82%)
PALETTE_COLS = 6
PALETTE_COLORS = [
    # Red-Orange to Yellow (H: 18° to 54°)
    (18, 100, 80),   # Peach
    (28, 100, 76),   # Orange
    (42, 100, 74),   # Golden Amber
    (54, 100, 75),   # Fluorescent Yellow
    (82, 100, 74),   # Lemon Lime
    (115, 100, 76),  # Neon Green
    # Green to Cyan-Blue (H: 138° to 222°)
    (138, 90, 78),   # Sea Green
    (152, 95, 78),   # Mint Green
    (172, 95, 78),   # Turquoise
    (188, 100, 78),  # Electric Cyan
    (208, 100, 80),  # Sky Blue
    (222, 100, 82),  # Ice Blue
    # Violet to Red-Pink (H: 245° to 350°)
    (245, 95, 82),   # Lavender
    (265, 95, 80),   # Bright Violet
    (282, 90, 80),   # Bright Plum
    (325, 100, 78),  # Hot Pink
    (338, 100, 80),  # Neon Magenta
    (350, 100, 78),  # Bright Coral
]


def clamp(min_val: float, val: float, max_val: float) -> float:
    """Clamps a numeric value within the range [min_val, max_val]."""
    return max(min_val, min(max_val, val))


class MainWindow(Adw.ApplicationWindow):
    """
    Main Adwaita application window.
    Features:
      - HeaderBar with centered fuzzy SearchEntry and crop/minimap/settings buttons.
      - Gtk.Stack holding the PDF Canvas view and the fuzzy search portal view.
      - Background FTS5 database builder to prevent UI freeze during text indexing.
      - Click-to-navigate search portal coordinates mapping.
    """

    def __init__(
        self,
        app,
        state=None,
        follow_link=None,
        debug_mode=False,
        debug_note_rect=False,
        render_mode="mp",
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
        self.render_mode = render_mode
        self.render_workers = render_workers
        self.use_shm = use_shm
        self._deferred_state_query = None

        # 2. Core models & persistent configuration
        self.doc_model = None
        self.crop_analyzer = None
        self.settings = CropSettings.load()
        self.current_source: PdfSource | None = None
        self.recent_files = RecentFilesManager()
        self.arxiv_mapper: ArxivDiffMapper | None = None

        # 3. LRU Caches and background rendering worker
        self.render_cache = RenderCache(20)
        self.minimap_cache = MiniMapCache(1000)
        self.render_worker = create_render_worker(render_mode, num_workers=render_workers, use_shm=use_shm)

        print(f"[PDFAtlas] render backend: {render_mode} x{render_workers}", flush=True)
        if use_shm:
            print("[PDFAtlas] Zero-copy SHM IPC enabled", flush=True)

        # 4. Search indexing & database persistence
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.db_service = DatabaseService()
        self.index_conn = None
        self._state_save_timer_id: int | None = None
        self.pinned = {}  # id -> {"result": ..., "query_terms": ...}
        self._debounce_source_id = None
        self._last_query = ""

        # 5. UI, viewport, annotations & interaction state
        self.zoom = 1.0
        self.pointer_x: float = 0.0
        self.pointer_y: float = 0.0
        self.highlights: list[dict] = []
        self.notes: list[dict] = []
        self.active_highlight_color: str = "#FFF49C"
        self._active_progress_tasks: dict[str, dict] = {}
        self.night_mode = self.is_effective_dark()

        # Optional / on-demand widgets and debug labels
        self.minimap_dialog: MinimapWindow | None = None
        self.debug_cache_label: Gtk.Label | None = None
        self.debug_info_label: Gtk.Label | None = None

        # 6. Window actions and menu signals
        self._setup_actions()

        # 7. Feature controllers
        self.nav_controller = NavigationController(self)
        self.search_controller = SearchController(self)

        # 8. Build UI hierarchy & widgets
        self._build_ui()

        # 9. Keyboard shortcuts and window event listeners
        self.shortcuts_controller = ShortcutsController(self)
        self.connect("realize", self._on_window_realized)

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
        if self.entry.has_focus() or self.page_input.has_focus():
            return True
        return False

    def _setup_system_icons(self):
        IconThemeManager.setup_system_icons(self)

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

        self.filename_label = label(text="No document loaded", css_class="caption",
                                    ellipsize=Pango.EllipsizeMode.END, max_width_chars=40, xalign=0)
        left_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                       children=[self.open_btn, self.filename_label])
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
        self.annotations_btn = Gtk.MenuButton()
        self.annotations_btn.set_icon_name("tag-symbolic")
        self.annotations_btn.set_tooltip_text("Annotations & Highlights")
        self.annotations_btn.set_visible(False)
        right_box.append(self.annotations_btn)

        self._build_annotations_popover()

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

        # Tab View & Tab Bar
        self.tab_view = Adw.TabView()
        self.tab_view.connect("create-window", self._on_create_window)
        self.tab_view.connect("page-attached", self._on_page_attached)
        self.tab_view.connect("page-detached", self._on_page_detached)
        self.tab_view.connect("close-page", self._on_close_page)
        self.tab_view.connect("notify::selected-page", self._on_selected_tab_changed)

        self.tab_bar = Adw.TabBar(view=self.tab_view)
        self.tab_bar.set_autohide(True)

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
        # Screenshot/debug-only: hide the cursor on a headless capture. When
        # PDFATLAS_HIDE_CURSOR is not "1" (normal use), leave cursor handling
        # entirely to the canvas hover logic -- do not force any cursor here.
        if os.environ.get("PDFATLAS_HIDE_CURSOR") == "1":
            blank_cursor = Gdk.Cursor.new_from_name("none", None)
            self.set_cursor(blank_cursor)
            surface = self.get_surface()
            if surface:
                surface.set_cursor(blank_cursor)

    def _on_canvas_note_create(self, page: int, x: float, y: float):
        self.notes_layer.create_note(page, x, y)

    def _show_toast(self, message: str):
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # --- Multi-Tab Management ---

    def _create_doc_view(self) -> Any:
        from .document_view import PdfDocumentView
        doc_view = PdfDocumentView(
            render_worker=self.render_worker,
            settings=self.settings,
            db_service=self.db_service,
            on_page_changed=self._on_doc_view_page_changed,
            on_zoom_changed=self._on_doc_view_zoom_changed,
            on_link_clicked=self._on_doc_view_link_clicked,
            on_note_create=self._on_canvas_note_create,
            on_selection_changed=self._update_selection_toolbar,
            on_toast=self._show_toast,
            on_state_changed=self._schedule_state_save,
            on_annotations_changed=self._update_annotations_button,
        )
        doc_view.canvas.set_night_mode(
            self.is_effective_dark(),
            invert_amount=self.settings.night_mode_invert,
            hue_rotate=self.settings.night_mode_hue_rotate,
        )
        return doc_view

    def get_active_doc_view(self) -> Any:
        page = self.tab_view.get_selected_page()
        if page is not None:
            child = page.get_child()
            return child
        return None

    def _on_create_window(self, view: Adw.TabView) -> Adw.TabView:
        new_win = MainWindow(
            self.app,
            render_mode=self.render_mode,
            render_workers=self.render_workers,
            use_shm=self.use_shm,
        )
        new_win.stack.set_visible_child_name("document-view")
        new_win.present()
        return new_win.tab_view

    def _on_page_attached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        self.stack.set_visible_child_name("document-view")
        self._on_selected_tab_changed(view, None)

    def _on_page_detached(self, view: Adw.TabView, page: Adw.TabPage, position: int) -> None:
        if view.get_n_pages() == 0:
            self.doc_model = None
            self.current_source = None
            self._active_doc_view_ref = None
            windows = self.app.get_windows() if self.app else []
            if len(windows) > 1:
                self.close()
            else:
                self._show_welcome()

    def _on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        child = page.get_child()
        if hasattr(child, "close"):
            getattr(child, "close")()
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            windows = self.app.get_windows() if self.app else []
            if len(windows) > 1:
                self.close()
            else:
                self._show_welcome()
        return True

    def _on_selected_tab_changed(self, view: Adw.TabView, pspec) -> None:
        from .welcome import WelcomeView
        from .document_view import PdfDocumentView

        # Save scroll position of previous active doc_view before switching away
        if hasattr(self, "_active_doc_view_ref") and self._active_doc_view_ref is not None:
            prev = self._active_doc_view_ref
            if hasattr(prev, "vadjustment") and prev.vadjustment is not None:
                prev.saved_scroll_y = prev.vadjustment.get_value()
            if hasattr(prev, "hadjustment") and prev.hadjustment is not None:
                prev.saved_scroll_x = prev.hadjustment.get_value()

        doc_view = self.get_active_doc_view()
        self._active_doc_view_ref = doc_view if isinstance(doc_view, PdfDocumentView) else None

        if isinstance(doc_view, WelcomeView):
            self.stack.set_visible_child_name("document-view")
            doc_view.refresh(self.recent_files)
            self.filename_label.set_label("PDF Atlas")
            self.set_title("PDF Atlas")
            self.entry.set_sensitive(False)
            self.entry.set_text("")
            self.entry.set_placeholder_text("No document loaded")
            self.page_input.set_text("")
            self.page_total_label.set_label("")
            self.page_input.set_sensitive(False)
            self.annotations_btn.set_visible(False)
            self.zoom_label.set_label("100%")
        elif isinstance(doc_view, PdfDocumentView):
            self.stack.set_visible_child_name("document-view")
            if doc_view.doc_model is not None:
                self.canvas = doc_view.canvas
                self.vadjustment = doc_view.vadjustment
                self.hadjustment = doc_view.hadjustment
                self.doc_model = doc_view.doc_model
                self.current_source = doc_view.current_source
                self.zoom = doc_view.zoom
                self.zoom_label.set_label(f"{int(self.zoom * 100)}%")
                self.arxiv_mapper = doc_view.arxiv_mapper
                self.crop_analyzer = doc_view.crop_analyzer
                self.notes_layer = doc_view.notes_layer
                self.notes = doc_view.notes
                self.highlights = doc_view.highlights

                # Sync night mode to the activated tab
                doc_view.canvas.set_night_mode(
                    self.night_mode,
                    invert_amount=self.settings.night_mode_invert,
                    hue_rotate=self.settings.night_mode_hue_rotate,
                )

                # Restore scroll position safely
                doc_view.restore_scroll_position()

                curr_page = doc_view.get_current_page_index() + 1
                self.page_input.set_text(str(curr_page))
                self.page_total_label.set_label(f"of {doc_view.doc_model.page_count}")
                self.page_input.set_sensitive(True)
                title = doc_view.current_source.display_name if doc_view.current_source else "PDF Viewer"
                self.set_title(f"PDF Viewer — {title}")
                self.filename_label.set_label(title)
                self.entry.set_sensitive(True)
                self.entry.set_placeholder_text("Search document...")
                self.annotations_btn.set_visible(True)
                self._update_annotations_button()
                doc_view.canvas._update_visibility()
                doc_view.canvas.gl_canvas.queue_draw()
                doc_view.canvas.queue_draw_overlays("tab-selected")
            else:
                # Tab is downloading/loading
                self.doc_model = None
                self.current_source = doc_view.current_source
                title = doc_view.loading_title.get_label() if doc_view.is_loading else "Loading..."
                self.set_title(f"PDF Viewer — {title}")
                self.filename_label.set_label(title)
                self.entry.set_sensitive(False)
                self.entry.set_text("")
                self.entry.set_placeholder_text("Downloading document...")
                self.page_input.set_text("")
                self.page_total_label.set_label("")
                self.page_input.set_sensitive(False)
                self.annotations_btn.set_visible(False)
                self.zoom_label.set_label("100%")
        elif view.get_n_pages() == 0:
            self._show_welcome()

    def _on_doc_view_page_changed(self, current: int, total: int):
        self.page_input.set_text(str(current))
        self.page_total_label.set_label(f"of {total}")

    def _on_doc_view_zoom_changed(self, zoom: float):
        self.zoom = zoom
        self.zoom_label.set_label(f"{int(zoom * 100)}%")

    def _on_doc_view_link_clicked(self, uri: str, link: dict):
        self._on_link_clicked(0, link)

    def new_tab(self):
        """Open a new tab with the welcome view."""
        from .welcome import WelcomeView
        welcome = WelcomeView(self)
        welcome.refresh(self.recent_files)
        page = self.tab_view.append(welcome)
        page.props.title = "New Tab"
        self.tab_view.set_selected_page(page)
        self.stack.set_visible_child_name("document-view")

    def close_current_tab(self):
        """Close the currently active tab."""
        page = self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)

    def new_window(self):
        """Open a new PDF Atlas window."""
        win = MainWindow(
            self.app,
            render_mode=self.render_mode,
            render_workers=self.render_workers,
            use_shm=self.use_shm,
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

    # --- Document Loading & Indexing ---

    def open_document(self, source: PdfSource, new_tab: bool = True):
        raw_path = os.path.expanduser(source.uri)
        try:
            filepath = os.path.abspath(raw_path) if os.path.exists(raw_path) else raw_path
        except OSError:
            filepath = raw_path

        aid = arxiv_id_from_path(filepath)

        # If a local file exists, open it directly without requiring internet.
        # If the file does not exist locally (or is an arXiv source whose local path is gone),
        # check the local arXiv cache first, and then attempt to download from arXiv if needed.
        if aid and not os.path.exists(filepath):
            from ..core.arxiv_mapper import ARXIV_CACHE_ROOT, download_arxiv_source
            from .document_view import PdfDocumentView
            from .welcome import WelcomeView

            cached_pdf = ARXIV_CACHE_ROOT / aid / "paper.pdf"
            if cached_pdf.exists():
                filepath = str(cached_pdf)
                source = PdfSource(
                    source_type="arxiv",
                    uri=filepath,
                    display_name=source.display_name or f"arXiv:{aid}",
                )
            else:
                # Open or allocate the tab to host the in-tab loading view
                selected = self.tab_view.get_selected_page()
                current_child = selected.get_child() if selected else None
                is_empty_tab = isinstance(current_child, WelcomeView)

                display_title = source.display_name or f"arXiv:{aid}"

                if is_empty_tab and selected is not None:
                    pos = self.tab_view.get_page_position(selected)
                    doc_view = self._create_doc_view()
                    page = self.tab_view.insert(doc_view, pos)
                    self.tab_view.close_page(selected)
                elif self.tab_view.get_n_pages() == 0 or not new_tab:
                    if self.tab_view.get_n_pages() == 0:
                        doc_view = self._create_doc_view()
                        page = self.tab_view.append(doc_view)
                    else:
                        page = selected if selected else self.tab_view.get_nth_page(0)
                        child = page.get_child()
                        if isinstance(child, PdfDocumentView):
                            doc_view = child
                        else:
                            pos = self.tab_view.get_page_position(page)
                            doc_view = self._create_doc_view()
                            page = self.tab_view.insert(doc_view, pos)
                            self.tab_view.close_page(selected if selected else page)
                else:
                    doc_view = self._create_doc_view()
                    page = self.tab_view.append(doc_view)

                page.props.title = display_title
                self.tab_view.set_selected_page(page)
                self.stack.set_visible_child_name("document-view")

                doc_view.show_loading(
                    title=f"Downloading {display_title}",
                    subtitle="Connecting to arXiv...",
                )

                def _download_worker():
                    def _on_progress(fraction: float, message: str):
                        def _update():
                            doc_view.set_loading_progress(fraction, message)
                            return False
                        GLib.idle_add(_update)

                    try:
                        download_arxiv_source(
                            aid,
                            download_pdf=True,
                            download_source=False,
                            progress_callback=_on_progress,
                        )
                        new_source = PdfSource(
                            source_type="arxiv",
                            uri=str(cached_pdf),
                            display_name=source.display_name or f"arXiv:{aid}",
                        )

                        def _on_success():
                            doc_model = DocumentModel(str(cached_pdf))
                            if self.render_worker:
                                self.render_worker.set_document(str(cached_pdf))
                            meta_title = (doc_model.doc.metadata or {}).get("title")
                            if meta_title and isinstance(meta_title, str):
                                cleaned_meta = meta_title.strip()
                                if cleaned_meta and cleaned_meta.lower() not in ("paper.pdf", "untitled", "none"):
                                    new_source.display_name = cleaned_meta

                            page.props.title = new_source.display_name
                            doc_view.set_document(doc_model, new_source, self.render_worker)
                            self.recent_files.add(new_source)
                            self._rebuild_open_menu()

                            if self.tab_view.get_selected_page() == page:
                                self._on_selected_tab_changed(self.tab_view, None)
                            return False

                        GLib.idle_add(_on_success)
                    except Exception as e:
                        err_msg = str(e)

                        def _on_fail():
                            if self.tab_view.get_page_position(page) >= 0:
                                self.tab_view.close_page(page)
                            self._show_error_dialog(f"Failed to download arXiv paper '{source.uri}':\n{err_msg}")
                            return False

                        GLib.idle_add(_on_fail)

                threading.Thread(target=_download_worker, daemon=True).start()
                return

        if not os.path.exists(filepath):
            self._show_error_dialog(f"File not found: {filepath}")
            return


        aid = arxiv_id_from_path(filepath)
        existing = self.recent_files.get_by_uri(filepath)
        if not existing and aid:
            existing = self.recent_files.get_by_arxiv_id(aid)

        if existing and existing.display_name and existing.display_name != "paper.pdf":
            source = PdfSource(
                source_type=existing.source_type,
                uri=filepath,
                display_name=existing.display_name,
            )

        try:
            # Save state for previous document
            if self.db_service and self.doc_model and self.current_source:
                self._save_current_doc_state()

            self.doc_model = DocumentModel(filepath)
            self.crop_analyzer = CropAnalyzer(self.doc_model)
            self.render_worker.set_document(filepath)

            # Try extracting PDF metadata title for local files if display name is just basename/generic
            if source.display_name in (os.path.basename(filepath), "paper.pdf") or source.display_name.startswith("arXiv:"):
                meta_title = (self.doc_model.doc.metadata or {}).get("title")
                if meta_title and isinstance(meta_title, str):
                    cleaned_meta = meta_title.strip()
                    if cleaned_meta and cleaned_meta.lower() not in ("paper.pdf", "untitled", "none"):
                        source.display_name = cleaned_meta

            self.current_source = source
            self.recent_files.add(source)
            self._rebuild_open_menu()

            self._active_progress_tasks.clear()
            if self.progress_card_box:
                self.progress_card_box.set_visible(False)
            if self.progress_bar:
                self.progress_bar.set_visible(False)

            # Create or reuse TabPage with PdfDocumentView
            from .document_view import PdfDocumentView
            from .welcome import WelcomeView

            selected = self.tab_view.get_selected_page()
            current_child = selected.get_child() if selected else None
            is_empty_tab = isinstance(current_child, WelcomeView)

            if is_empty_tab and selected is not None:
                pos = self.tab_view.get_page_position(selected)
                doc_view = self._create_doc_view()
                doc_view.set_document(self.doc_model, source, self.render_worker)
                page = self.tab_view.insert(doc_view, pos)
                self.tab_view.close_page(selected)
            elif self.tab_view.get_n_pages() == 0 or not new_tab:
                if self.tab_view.get_n_pages() == 0:
                    doc_view = self._create_doc_view()
                    doc_view.set_document(self.doc_model, source, self.render_worker)
                    page = self.tab_view.append(doc_view)
                else:
                    page = selected if selected else self.tab_view.get_nth_page(0)
                    child = page.get_child()
                    if isinstance(child, PdfDocumentView):
                        doc_view = child
                        doc_view.set_document(self.doc_model, source, self.render_worker)
                    else:
                        pos = self.tab_view.get_page_position(page)
                        doc_view = self._create_doc_view()
                        doc_view.set_document(self.doc_model, source, self.render_worker)
                        page = self.tab_view.insert(doc_view, pos)
                        self.tab_view.close_page(selected if selected else page)
            else:
                doc_view = self._create_doc_view()
                doc_view.set_document(self.doc_model, source, self.render_worker)
                page = self.tab_view.append(doc_view)

            page.props.title = source.display_name
            self.tab_view.set_selected_page(page)
            self.stack.set_visible_child_name("document-view")

            self.canvas = doc_view.canvas
            self.vadjustment = doc_view.vadjustment
            self.hadjustment = doc_view.hadjustment
            self.zoom = doc_view.zoom
            self.notes_layer = doc_view.notes_layer
            self.notes = doc_view.notes
            self.highlights = doc_view.highlights

            self.set_title(f"PDF Viewer — {source.display_name}")
            self.filename_label.set_label(source.display_name)
            if self.doc_model:
                self.page_total_label.set_label(f"of {self.doc_model.page_count}")
            self.page_input.set_text("1")
            self.page_input.set_sensitive(True)

            if source.is_arxiv and aid and source.display_name in ("paper.pdf", f"arXiv:{aid}"):
                def _bg_fetch(paper_aid: str, paper_uri: str, target_page: Any):
                    from .arxiv_dialog import _fetch_arxiv_title
                    title = _fetch_arxiv_title(paper_aid)
                    if title:
                        def _update():
                            target_page.props.title = title
                            if self.current_source and self.current_source.uri == paper_uri:
                                self.current_source.display_name = title
                                self.current_source.source_type = "arxiv"
                                self.recent_files.add(self.current_source)
                                self._rebuild_open_menu()
                                self.set_title(f"PDF Viewer — {title}")
                                self.filename_label.set_label(title)
                        GLib.idle_add(_update)
                threading.Thread(target=_bg_fetch, args=(aid, filepath, page), daemon=True).start()

            self.arxiv_mapper = None
            if source.is_arxiv:
                aid = arxiv_id_from_path(filepath)
                if aid:
                    self._show_progress("arxiv_diff", "Analyzing arXiv TeX sources...", 0.0)
                    arxiv_thread = threading.Thread(
                        target=self._arxiv_diff_worker, args=(aid, filepath), daemon=True
                    )
                    arxiv_thread.start()

            # Start crop analysis
            self._start_crop_analysis()

            # Trigger background indexing
            self.entry.set_text("")
            self.entry.set_placeholder_text("Indexing text index...")
            self.entry.set_sensitive(False)
            self.stack.set_visible_child_name("document-view")

            self._show_progress("indexing", "Indexing document text for search...", 0.0)
            self.db_service.open_db(filepath, self._on_indexing_complete)


            # Restore state if passed programmatically
            if self.initial_state:
                try:
                    state = CliState.from_json(self.initial_state)

                    if state.zoom is not None:
                        self.set_zoom_level(state.zoom)
                    if state.crop is not None:
                        self.settings.enabled = state.crop
                    if state.page_gaps is not None:
                        self.settings.page_gaps = state.page_gaps
                    if state.color_scheme is not None:
                        self.settings.color_scheme = state.color_scheme
                    elif state.night_mode is not None:
                        self.settings.color_scheme = "dark" if state.night_mode else "light"
                    elif state.dark_mode is not None:
                        self.settings.color_scheme = "dark" if state.dark_mode else "light"

                    if state.night_mode_invert is not None:
                        self.settings.night_mode_invert = state.night_mode_invert
                    if state.night_mode_hue_rotate is not None:
                        self.settings.night_mode_hue_rotate = state.night_mode_hue_rotate

                    self._on_crop_settings_updated()

                    # Defer scroll_y, fit_width, and search query application until layout realizes
                    def apply_deferred_state():
                        if state.fit_width:
                            self.zoom_fit_width()
                        if state.scroll_y is not None:
                            self.vadjustment.set_value(state.scroll_y)
                        if state.query:
                            if self.index_conn:
                                self.entry.set_text(state.query)
                                self.run_search(state.query)
                            else:
                                self._deferred_state_query = state.query
                        if state.minimap:
                            GLib.timeout_add(500, self.toggle_minimap)
                        if state.hover_link is not None:
                            hover_idx = state.hover_link
                            GLib.timeout_add(400, lambda: self._simulate_link_hover(hover_idx))
                        if state.scroll_benchmark is not None:
                            bench_info = state.scroll_benchmark
                            GLib.timeout_add(300, lambda: self._run_scroll_benchmark(bench_info))
                        if state.selection is not None:
                            sel_info = state.selection
                            page_idx = sel_info.page
                            if self.canvas.text_selection:
                                pi = self.canvas.text_selection.get_page_index(page_idx)
                                start_idx = sel_info.start_idx
                                end_idx = sel_info.end_idx

                                if start_idx is None or end_idx is None:
                                    if pi and pi.chars and sel_info.start:
                                        s_text = sel_info.start
                                        e_text = sel_info.end or s_text
                                        for idx, c in enumerate(pi.chars):
                                            if start_idx is None and s_text in c.char:
                                                start_idx = idx
                                            if e_text in c.char:
                                                end_idx = idx

                                if start_idx is not None and end_idx is not None:
                                    self.canvas.text_selection.start_selection(page_idx, start_idx)
                                    self.canvas.text_selection.update_focus(page_idx, end_idx)
                                    self.canvas.text_selection.end_selection()
                                    self.canvas.queue_draw_overlays("selection-update")
                                    self._update_selection_toolbar(True)
                        return False

                    GLib.idle_add(apply_deferred_state)

                    if state.highlights is not None:
                        sample_hls = state.highlights
                        for idx, h in enumerate(sample_hls):
                            if "id" not in h:
                                h["id"] = idx + 1
                            if "rects" not in h:
                                h["rects"] = []
                        self.highlights = sample_hls
                        self.canvas.set_highlights(sample_hls)
                        self._update_annotations_button()

                    if state.notes is not None:
                        sample_notes = state.notes
                        for idx, n in enumerate(sample_notes):
                            if "id" not in n:
                                n["id"] = idx + 1
                            if "markdown" not in n:
                                n["markdown"] = ""
                        self.notes = sample_notes
                        self.notes_layer.set_notes(sample_notes)
                        self._update_annotations_button()

                    if state.annotations_popover:
                        def open_popover():
                            if self.annotations_btn.get_visible():
                                if os.environ.get("PDFATLAS_HIDE_CURSOR") == "1":
                                    blank = Gdk.Cursor.new_from_name("none", None)
                                    self.annotations_popover.set_cursor(blank)
                                self.annotations_popover.popup()
                            return False
                        GLib.timeout_add(400, open_popover)

                    if state.open_note_preview is not None:
                        def open_note():
                            nid = state.open_note_preview
                            note = next((n for n in self.notes if n.get("id") == nid), self.notes[0] if self.notes else None)
                            if note and self.notes_layer:
                                self.notes_layer.prepare()
                                self.notes_layer._on_preview_show(note)
                                rect = self.notes_layer._preview_anchor_rect(note)
                                if rect:
                                    # Window-relative coordinates (Headerbar height = 46px, icon center offset +12)
                                    exact_x = rect.x + 12
                                    exact_y = 46 + rect.y + 12
                                    print(f"[PDFAtlas] NOTE_ICON_EXACT_COORDS: {exact_x},{exact_y}", flush=True)
                            return False
                        GLib.timeout_add(600, open_note)

                    # If page is specified, navigate to it after layout
                    if state.page is not None:
                        target_page = state.page - 1
                        GLib.idle_add(lambda: self.jump_to_page(target_page))
                except Exception as e:
                    print(f"Failed to restore initial CLI state: {e}")

            if self.follow_link is not None:
                follow_idx: int = self.follow_link
                GLib.timeout_add(400, lambda: self._follow_link_by_index(follow_idx))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._show_error_dialog(f"Failed to open PDF document:\n{e}")

    def _index_worker(self, filepath):
        try:
            conn = get_db_for_pdf(filepath)
            GLib.idle_add(self._on_indexing_complete, conn)
        except Exception as e:
            GLib.idle_add(self._show_error_dialog, f"Search indexing failed:\n{e}")

    def _arxiv_diff_worker(self, arxiv_id: str, filepath: str):
        try:
            def progress_cb(f: float) -> None:
                GLib.idle_add(self._on_arxiv_diff_progress, f)

            mapper = ArxivDiffMapper()
            mapper.process(
                arxiv_id,
                Path(filepath),
                progress_callback=progress_cb,
            )
            GLib.idle_add(self._on_arxiv_diff_complete, mapper)
        except Exception as e:
            print(f"[MainWindow] Arxiv diff calculation failed: {e}", flush=True)
            GLib.idle_add(self._on_arxiv_diff_complete, None)


    def _on_arxiv_diff_progress(self, fraction: float):
        self._show_progress("arxiv_diff", "Analyzing arXiv TeX sources...", fraction)

    def _on_arxiv_diff_complete(self, mapper: ArxivDiffMapper | None):
        self.arxiv_mapper = mapper
        self._hide_progress("arxiv_diff")
        if self.selection_toolbar and self.selection_toolbar.get_visible():
            self._update_selection_toolbar(True)


    def _on_indexing_complete(self, conn):
        self._hide_progress("indexing")
        self.index_conn = conn
        self.entry.set_sensitive(True)
        self.entry.set_placeholder_text("Search document...")
        if not self.initial_state:
            self.db_service.load_highlights(self._on_highlights_loaded)
            self.db_service.load_notes(self._on_notes_loaded)

        if self._deferred_state_query:
            query = self._deferred_state_query
            self._deferred_state_query = None
            self.entry.set_text(query)
            self.run_search(query)

        # Restore saved zoom & scroll_x/scroll_y state from .db if no CLI state was specified
        if not self.initial_state and conn is not None:
            saved_state = load_doc_state(conn)
            if "zoom" in saved_state:
                self.set_zoom_level(saved_state["zoom"])
            if "scroll_x" in saved_state or "scroll_y" in saved_state:
                scroll_x = saved_state.get("scroll_x", 0.0)
                scroll_y = saved_state.get("scroll_y", 0.0)

                def apply_saved_scroll():
                    if "scroll_x" in saved_state:
                        self.hadjustment.set_value(scroll_x)
                    if "scroll_y" in saved_state:
                        self.vadjustment.set_value(scroll_y)

                GLib.idle_add(apply_saved_scroll)

    def _build_annotations_popover(self):
        self.annotations_popover = Gtk.Popover()
        self.annotations_btn.set_popover(self.annotations_popover)

        popover_box = box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        # Vertical 3:4 (portrait ~4:3) size request (280px width x 370px height)
        popover_box.set_size_request(280, 370)

        # Header Title
        title_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_start=4, margin_top=2, margin_bottom=2)
        self.annotations_count_label = label(text="Annotations (0)", css_class="bold")
        self.annotations_count_label.set_hexpand(True)
        self.annotations_count_label.set_halign(Gtk.Align.START)
        title_box.append(self.annotations_count_label)
        popover_box.append(title_box)

        popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Scrollable Annotations List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.annotations_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.annotations_list.add_css_class("annotation-list")
        scrolled.set_child(self.annotations_list)
        popover_box.append(scrolled)

        self.annotations_popover.set_child(popover_box)
        # Regenerate the list (especially note preview text) each time the
        # popover opens so labels reflect the latest note content.
        self.annotations_popover.connect(
            "notify::visible", self._on_annotations_popover_visibility
        )

    def _on_annotations_popover_visibility(self, popover, pspec):
        if popover.get_visible():
            self._update_annotations_button()

    def _update_annotations_button(self):
        count = len(self.highlights) + len(self.notes)
        self.annotations_btn.set_visible(count > 0)
        if self.annotations_count_label:
            self.annotations_count_label.set_text(f"Annotations ({count})")

        if not self.annotations_list:
            return

        child = self.annotations_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.annotations_list.remove(child)
            child = nxt

        # One flat list grouped by page, highlights and notes interleaved:
        # notes use the same row layout as highlights, with the note icon in
        # place of the color circle swatch.
        items: list[tuple[int, int, float, dict, str]] = []
        for hl in self.highlights:
            items.append((hl.get("page", 0), 0, hl.get("char_start", 0), hl, "highlight"))
        for note in self.notes:
            items.append((note.get("page", 0), 1, note.get("y", 0.0), note, "note"))
        items.sort(key=lambda it: (it[0], it[1], it[2]))

        last_page: int | None = None
        for page_idx, _rank, _pos, item, kind in items:
            if page_idx != last_page:
                hdr_box = box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_start=4, margin_top=4, margin_bottom=1)
                lbl = label(text=f"PAGE {page_idx + 1}", css_class="dim-label")
                lbl.add_css_class("caption")
                lbl.add_css_class("bold")
                lbl.set_halign(Gtk.Align.START)
                hdr_box.append(lbl)
                self.annotations_list.append(hdr_box)
                last_page = page_idx

            item_box = box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_start=4, margin_end=4, margin_top=2, margin_bottom=2)

            if kind == "highlight":
                # Prominent color circle swatch (14px) — note rows use the
                # icon instead, keeping the same row structure.
                color_swatch = Gtk.Box()
                color_swatch.set_size_request(14, 14)
                color_swatch.set_valign(Gtk.Align.CENTER)
                color_swatch.add_css_class("highlight-circle-swatch")
                bg_color = item.get("color", "#FFEE55")
                provider = Gtk.CssProvider()
                provider.load_from_string(f".highlight-circle-swatch {{ background-color: {bg_color}; border-radius: 9999px; min-width: 14px; min-height: 14px; }}")
                color_swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                item_box.append(color_swatch)
                txt = (item.get("text", "") or "").strip() or "(Highlight)"
            else:
                note_icon = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
                note_icon.set_pixel_size(14)
                note_icon.set_valign(Gtk.Align.CENTER)
                item_box.append(note_icon)
                md_text = item.get("markdown", "") or ""
                txt = _simplify_md_preview(md_text)

            txt_lbl = label(text=txt)
            txt_lbl.set_single_line_mode(True)
            txt_lbl.set_lines(1)
            txt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            txt_lbl.set_halign(Gtk.Align.START)
            txt_lbl.set_xalign(0.0)
            txt_lbl.set_hexpand(True)
            item_box.append(txt_lbl)

            linked_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            linked_box.add_css_class("linked")
            linked_box.set_hexpand(True)

            main_btn = Gtk.Button()
            main_btn.set_hexpand(True)
            main_btn.set_child(item_box)
            if kind == "highlight":
                main_btn.set_tooltip_text("Go to annotation")
                main_btn.connect("clicked", lambda b, h=item: self._activate_annotation(h))
            else:
                main_btn.set_tooltip_text("Go to note")
                main_btn.connect("clicked", lambda b, n=item: self._activate_note(n))
            linked_box.append(main_btn)

            btn_delete = Gtk.Button(icon_name="user-trash-symbolic")
            if kind == "highlight":
                btn_delete.set_tooltip_text("Delete annotation")
                btn_delete.connect("clicked", lambda b, h=item: self._delete_annotation(h))
            else:
                btn_delete.set_tooltip_text("Delete note")
                btn_delete.connect("clicked", lambda b, n=item: self.notes_layer.delete_note(n))
            linked_box.append(btn_delete)

            self.annotations_list.append(linked_box)

    def _activate_annotation(self, hl: dict):
        self.nav_controller.jump_to_annotation(hl)
        self.annotations_popover.popdown()

    def _activate_note(self, note: dict):
        self.nav_controller.jump_to_note(note)
        self.annotations_popover.popdown()

    def _delete_annotation(self, hl: dict):
        if hl in self.highlights:
            self.highlights.remove(hl)
        self.db_service.delete_highlight(hl["id"])
        self.canvas.set_highlights(self.highlights)
        self.canvas.queue_draw()
        self._update_annotations_button()

    def _on_highlights_loaded(self, highlights: list[dict]):
        if self.initial_state:
            return
        self.highlights = highlights
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "highlights"):
            doc_view.highlights = highlights
        self.canvas.set_highlights(highlights)
        self.canvas.queue_draw()
        self._update_annotations_button()

        # If there's a deferred query from state restoration, execute it now
        if self._deferred_state_query:
            query = self._deferred_state_query
            self._deferred_state_query = None
            self.entry.set_text(query)
            self.run_search(query)

    def _on_notes_loaded(self, notes: list[dict]):
        if self.initial_state:
            return
        self.notes = notes
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "notes"):
            doc_view.notes = notes
        self.notes_layer.set_notes(notes)
        self._update_annotations_button()

    def _schedule_state_save(self):
        if self._state_save_timer_id is not None:
            GLib.source_remove(self._state_save_timer_id)

        def _on_save_timer():
            self._state_save_timer_id = None
            self._save_current_doc_state()
            return False

        self._state_save_timer_id = GLib.timeout_add(1000, _on_save_timer)

    def _save_current_doc_state(self):
        if self.db_service:
            zoom = self.zoom
            scroll_y = self.vadjustment.get_value() if self.vadjustment else 0.0
            scroll_x = self.hadjustment.get_value() if self.hadjustment else 0.0
            self.db_service.save_state(zoom, scroll_y, scroll_x)

    def _on_horizontal_scroll_changed(self, adj):
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
                recent_section.append(display_name.replace("_", "__"), f"win.open-recent::{source.uri}")
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
        ArxivDialog(parent_window=self, on_source=self._on_arxiv_source, recent_files=self.recent_files).present()

    def _on_arxiv_source(self, source: PdfSource):
        self.open_document(source)

    # --- Search Engine Wiring ---

    def run_search(self, query: str):
        self.search_controller.run_search(query)

    def _on_escape(self):
        """Clears search input or closes minimap modal and returns focus to reader view."""
        if self.minimap_dialog and self.minimap_dialog.get_visible():
            self.minimap_dialog.close()
            self.minimap_dialog = None
            self.canvas.grab_focus()
            return True

        if self.page_input and self.page_input.has_focus():
            self._on_scroll_page_changed(self.vadjustment)
            self.canvas.grab_focus()
            return True

        if self.stack.get_visible_child_name() == "search-view" or self.entry.has_focus():
            self.entry.set_text("")
            self.stack.set_visible_child_name("document-view")
            self.canvas.grab_focus()
            return True

        # Clear text selection on Escape
        if self.canvas.text_selection is not None and self.canvas.text_selection.has_selection():
            self.canvas.clear_selection()
            return True

        return False

    def _selection_matching_highlights(self) -> list[dict]:
        """Return highlights whose page and char range exactly match the current selection."""
        sel = self.canvas.text_selection if self.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return []
        if sel.anchor_page != sel.focus_page:
            return []
        c_start = min(sel.anchor_char_idx or 0, sel.focus_char_idx or 0)
        c_end = max(sel.anchor_char_idx or 0, sel.focus_char_idx or 0)
        return [
            hl
            for hl in self.highlights
            if hl.get("page") == sel.anchor_page
            and hl.get("char_start") == c_start
            and hl.get("char_end") == c_end
        ]

    def _update_selection_toolbar(self, has_selection: bool):
        if self.selection_toolbar:
            if has_selection and self.canvas.text_selection and self.canvas.text_selection.has_selection():
                is_tex_available = bool(self.arxiv_mapper and self.arxiv_mapper.is_ready)
                if self.btn_copy_tex:
                    self.btn_copy_tex.set_visible(is_tex_available)
                    self.btn_copy_tex.set_sensitive(is_tex_available)
                    if is_tex_available:
                        self.btn_copy_tex.set_tooltip_text("Copy source TeX for selection [Ctrl+C]")
                if self.btn_remove_hl:
                    self.btn_remove_hl.set_visible(bool(self._selection_matching_highlights()))
                self.selection_toolbar.set_visible(True)
            else:
                self.selection_toolbar.set_visible(False)

    def _copy_pdf_text_to_clipboard(self):
        """Copy selected PDF plain text to the system clipboard [Ctrl+Shift+C]."""
        sel = self.canvas.text_selection
        if sel is None or not sel.has_selection():
            return
        text = sel.get_selected_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def _copy_tex_to_clipboard(self):
        """Copy selected text as LaTeX source TeX if available, otherwise plain PDF text [Ctrl+C]."""
        sel = self.canvas.text_selection
        if sel is None or not sel.has_selection():
            return

        text = ""
        if self.arxiv_mapper and self.arxiv_mapper.is_ready:
            if sel.anchor_page is not None and sel.focus_page is not None:
                p_start = min(sel.anchor_page, sel.focus_page)
                p_end = max(sel.anchor_page, sel.focus_page)

                latex_parts = []
                for pi in range(p_start, p_end + 1):
                    rng = sel._selection_range(pi)
                    if rng:
                        s_char, e_char = rng
                        tex_snippet = self.arxiv_mapper.get_latex_for_pdf_range(pi, s_char, e_char)
                        if tex_snippet:
                            latex_parts.append(tex_snippet)

                if latex_parts:
                    text = "\n".join(latex_parts)

        if not text:
            text = sel.get_selected_text()

        if not text:
            return

        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def _copy_selection_to_clipboard(self):
        """Default selection copy handler."""
        self._copy_tex_to_clipboard()


    # --- Zoom Operations ---

    def get_current_page_index(self) -> int:
        if not self.doc_model or not self.canvas.page_layout:
            return 0

        y_val = self.vadjustment.get_value()
        viewport_h = self.vadjustment.get_page_size()
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
        if not self.doc_model or not self.canvas.page_layout:
            return

        target_page = link.get("page")

        if target_page is None or not isinstance(target_page, int) or target_page < 0 or target_page >= self.doc_model.page_count:
            if uri := link.get("uri"):
                aid = extract_arxiv_id_from_raw(uri) or arxiv_id_from_path(uri)
                if aid:
                    now = time.monotonic()
                    if getattr(self, "_last_link_click_time", 0.0) + 0.5 > now:
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

        viewport_h = self.vadjustment.get_page_size()
        if viewport_h <= 1.0:
            viewport_h = 700.0

        lower = self.vadjustment.get_lower()
        upper = self.vadjustment.get_upper()
        max_y = max(lower, upper - viewport_h)
        target_y = clamp(lower, (y_offset + scaled_y) - (viewport_h / 2.0), max_y)

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

    # --- Pages Minimap Window ---

    def toggle_minimap(self):
        if not self.doc_model:
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
        dialog.minimap.set_current_page(active_page)
        dialog.present(self)

    def _on_minimap_page_clicked(self, page_index):
        self.jump_to_page(page_index)

    # --- Toggles & Settings ---

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
        # Re-clamp current zoom if the min/max zoom limits changed
        min_zoom = self.settings.min_zoom
        max_zoom = self.settings.max_zoom
        if self.zoom < min_zoom or self.zoom > max_zoom:
            self.set_zoom_level(self.zoom)
        # Re-run search if a query is active to apply layout changes (list vs grid) in real-time
        if self._last_query:
            self.run_search(self._last_query)

    def _on_crop_settings_updated(self):
        # Sync stateful action states
        if self.crop_action:
            self.crop_action.set_state(GLib.Variant.new_boolean(self.settings.enabled))
        if self.gapless_action:
            self.gapless_action.set_state(GLib.Variant.new_boolean(self.settings.page_gaps))
        self._apply_color_scheme()
        self.settings.save()

        if self.crop_analyzer:
            self.crop_analyzer.compute_crop_rects(self.settings)

        self.canvas.on_crop_changed()

    # --- Crop Re-analysis ---

    def _on_reanalyze(self):
        self._start_crop_analysis(force=True)

    def _start_crop_analysis(self, force: bool = False):
        if not self.doc_model or not self.crop_analyzer:
            return
        if not force and not self.settings.enabled:
            return

        page_count = self.doc_model.page_count
        self.crop_analyzer.scanned = [False] * page_count
        self.crop_analyzer.raw_bboxes = [None] * page_count

        self.crop_scanned_count = 0
        self._show_progress("crop_analysis", "Scanning page margins for auto-crop...", 0.0)

        # Route per-page scans through the renderer child process so the low-res
        # rasterization cannot stall the UI thread.
        for i in range(page_count):
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
        self._show_progress("crop_analysis", "Scanning page margins for auto-crop...", self.crop_scanned_count / total)

    def _on_crop_analysis_complete(self):
        self._hide_progress("crop_analysis")
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
        if self.progress_bar:
            self.progress_bar.set_fraction(max_fraction)
            self.progress_bar.set_visible(True)

        latest_task = list(self._active_progress_tasks.values())[-1]
        progress_text = latest_task["formatted"]

        if self.progress_label:
            self.progress_label.set_label(progress_text)
        if self.progress_card_box:
            self.progress_card_box.set_visible(True)
        if self.link_preview_box:
            self.link_preview_box.set_visible(True)

    def _hide_progress(self, task_id: str):
        if task_id in self._active_progress_tasks:
            del self._active_progress_tasks[task_id]

        if not self._active_progress_tasks:
            if self.progress_bar:
                self.progress_bar.set_visible(False)
            if self.progress_card_box:
                self.progress_card_box.set_visible(False)
            if self.link_preview_card_box and not self.link_preview_card_box.get_visible() and self.link_preview_box:
                self.link_preview_box.set_visible(False)
        else:
            latest_task = list(self._active_progress_tasks.values())[-1]
            max_fraction = max(t["fraction"] for t in self._active_progress_tasks.values())
            if self.progress_bar:
                self.progress_bar.set_fraction(max_fraction)
            if self.progress_label:
                self.progress_label.set_label(latest_task["formatted"])

    def add_toast(self, toast: Adw.Toast):
        self.toast_overlay.add_toast(toast)

    def _update_installation_badge_status(self):
        installed = is_app_installed()
        show_badge = (not installed) or self.debug_mode
        self.menu_badge_dot.set_visible(show_badge)

        menu = Gio.Menu.new()
        menu.append("Night Mode", "win.night-mode")
        menu.append("Gap-less Mode", "win.gapless-mode")
        menu.append("Auto-crop Mode", "win.crop-mode")

        section = Gio.Menu.new()
        section.append("Open Settings", "win.open-settings")
        section.append("About PDF Atlas", "win.about")
        menu.append_section(None, section)

        if show_badge:
            install_section = Gio.Menu.new()
            install_section.append("Install Desktop Application", "win.install-app")
            menu.append_section(None, install_section)

        self.menu_button.set_menu_model(menu)


    def _on_install_app_action_activated(self):
        if ensure_app_installed(force=True):
            self.add_toast(Adw.Toast.new("Desktop application launcher installed!"))
            self._update_installation_badge_status()
        else:
            self.add_toast(Adw.Toast.new("Installation failed."))


    def close(self):

        # Save state and shutdown executors and close connections cleanly
        self._save_current_doc_state()
        self.notes_layer.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.render_worker.shutdown()
        if self.index_conn:
            self.index_conn.close()
            self.index_conn = None

        if self.doc_model:
            self.doc_model.close()
        super().close()

    def _build_floating_zoom_controls(self):
        self.zoom_label = label(text="100%", css_class="zoom-floating-label")
        self.zoom_floating_box = box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4, css_class="zoom-floating-box",
            halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=20, margin_bottom=20,
            children=[
                button(icon_name="zoom-in-symbolic", tooltip="Zoom In", css_class="flat", on_clicked=lambda b: self.zoom_in()),
                self.zoom_label,
                button(icon_name="zoom-out-symbolic", tooltip="Zoom Out", css_class="flat", on_clicked=lambda b: self.zoom_out()),
            ],
        )

    def _build_floating_link_preview(self):
        self.link_preview_label = label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=65)

        self.link_preview_card_box = box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            css_class="link-preview-box", halign=Gtk.Align.START,
            children=[self.link_preview_label],
        )
        self.link_preview_card_box.set_visible(False)

        self.progress_label = label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=65)
        self.progress_card_box = box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            css_class="link-preview-box", halign=Gtk.Align.START,
            children=[self.progress_label],
        )
        self.progress_card_box.set_visible(False)

        self.link_preview_box = box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=8, margin_bottom=8,
            children=[self.progress_card_box, self.link_preview_card_box],
        )

        if self.debug_mode:
            self.debug_info_label = label(xalign=0.0, halign=Gtk.Align.START,
                                          justify=Gtk.Justification.LEFT, css_class="debug-info-label")
            self.debug_info_label.set_visible(False)
            self.link_preview_box.append(self.debug_info_label)

            self.debug_arxiv_label = label(xalign=0.0, halign=Gtk.Align.START,
                                           justify=Gtk.Justification.LEFT, css_class="debug-info-label",
                                           wrap=True, max_width_chars=80)
            self.debug_arxiv_label.set_visible(False)
            self.link_preview_box.append(self.debug_arxiv_label)
        else:
            self.debug_info_label = None
            self.debug_arxiv_label = None

        self.link_preview_box.set_visible(False)

    def _build_selection_toolbar(self):
        self.btn_copy_text = button(label="Copy", tooltip="Copy selected PDF text [Ctrl+Shift+C]",
                                    on_clicked=lambda b: self._copy_pdf_text_to_clipboard())
        self.btn_copy_tex = button(label="Copy Source TeX", tooltip="Copy source TeX for selection [Ctrl+C]",
                                   on_clicked=lambda b: self._copy_tex_to_clipboard())

        # Highlight SplitButton
        self.btn_highlight = Adw.SplitButton()
        self.btn_highlight.set_tooltip_text("Highlight selected text [Ctrl+H]")
        self.btn_highlight.connect("clicked", lambda b: self._apply_highlight_to_selection())
        self._update_highlight_split_button_label()

        self.btn_remove_hl = button(label="Remove", tooltip="Remove the selected highlight",
                                    on_clicked=lambda b: self._remove_matching_highlights())

        popover_palette = Gtk.Popover()
        popover_palette.set_position(Gtk.PositionType.TOP)

        grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        grid.set_margin_start(6)
        grid.set_margin_end(6)

        for idx, hsl in enumerate(PALETTE_COLORS):
            row = idx // PALETTE_COLS
            col = idx % PALETTE_COLS
            hex_color = hsl_to_hex(*hsl)
            color_btn = Gtk.Button()
            color_btn.set_size_request(24, 24)
            color_btn.set_tooltip_text(hex_color)

            provider = Gtk.CssProvider()
            provider.load_from_data(
                f"button {{ background-color: {hex_color}; background-image: none; border-radius: 4px; border: 1px solid rgba(0,0,0,0.2); min-width: 24px; min-height: 24px; padding: 0; margin: 0; }} button:hover {{ outline: 2px solid #ffffff; outline-offset: -2px; }}".encode("utf-8")
            )
            color_btn.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            def make_color_cb(c: str):
                return lambda b: self._select_highlight_color(c, popover_palette)

            color_btn.connect("clicked", make_color_cb(hex_color))
            grid.attach(color_btn, col, row, 1, 1)

        popover_box = box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6,
            children=[
                label(text="Highlight Color", css_class="heading", xalign=0),
                grid,
            ],
        )

        btn_clear_hl = button(label="Remove Highlights", tooltip="Remove highlights in selection",
                              on_clicked=lambda b: self._remove_highlights_in_selection(popover_palette))
        popover_box.append(btn_clear_hl)

        popover_palette.set_child(popover_box)
        self.btn_highlight.set_popover(popover_palette)

        self.selection_toolbar = box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            css_class="selection-toolbar", valign=Gtk.Align.END, halign=Gtk.Align.FILL,
            children=[
                box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                    children=[self.btn_highlight, self.btn_remove_hl, self.btn_copy_text, self.btn_copy_tex]),
                spacer(),
            ],
        )
        self.selection_toolbar.set_visible(False)

        self.info_menu_btn = Gtk.MenuButton()
        self.info_menu_btn.set_icon_name("dialog-information-symbolic")
        self.info_menu_btn.set_direction(Gtk.ArrowType.UP)
        self.info_menu_btn.set_tooltip_text("Shortcuts Info")
        self.info_menu_btn.add_css_class("flat")

        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.TOP)
        popover_grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        popover.set_child(box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12,
            children=[
                label(text="Text Selection Shortcuts", css_class="heading", xalign=0),
                popover_grid,
            ],
        ))
        popover_grid.attach(label(text="Ctrl+H", css_class="dim-label", xalign=0), 0, 0, 1, 1)
        popover_grid.attach(label(text="Highlight selection", xalign=0), 1, 0, 1, 1)
        popover_grid.attach(label(text="Ctrl+C", css_class="dim-label", xalign=0), 0, 1, 1, 1)
        popover_grid.attach(label(text="Copy source (if available)", xalign=0), 1, 1, 1, 1)
        popover_grid.attach(label(text="Ctrl+Shift+C", css_class="dim-label", xalign=0), 0, 2, 1, 1)
        popover_grid.attach(label(text="Copy PDF text", xalign=0), 1, 2, 1, 1)
        self.info_menu_btn.set_popover(popover)

        self.selection_toolbar.append(self.info_menu_btn)
        self.content_overlay.add_overlay(self.selection_toolbar)

    def _update_highlight_split_button_label(self):
        circle_swatch = box(css_class="highlight-circle-swatch")
        provider = Gtk.CssProvider()
        provider.load_from_data(
            f".highlight-circle-swatch {{ min-width: 18px; min-height: 18px; background-color: {self.active_highlight_color}; border-radius: 50%; border: 1px solid rgba(0,0,0,0.3); }}".encode("utf-8")
        )
        circle_swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.btn_highlight.set_child(circle_swatch)

    def _select_highlight_color(self, hex_color: str, popover: Gtk.Popover):
        self.active_highlight_color = hex_color
        self._update_highlight_split_button_label()
        popover.popdown()
        self._apply_highlight_to_selection()

    def _apply_highlight_to_selection(self):
        sel = self.canvas.text_selection if self.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return

        if sel.anchor_page <= sel.focus_page:
            p_start, p_end = sel.anchor_page, sel.focus_page
        else:
            p_start, p_end = sel.focus_page, sel.anchor_page

        color = self.active_highlight_color

        for page_idx in range(p_start, p_end + 1):
            rng = sel._selection_range(page_idx)
            if rng is None:
                continue
            char_start, char_end = rng
            rects = sel.get_selection_rects(page_idx)
            text = sel.get_selected_text(page_idx)
            if not rects:
                continue

            def make_on_saved(p_i, c_s, c_e, col, rcts, txt):
                def _on_saved(hid: int):
                    hl_obj = {
                        "id": hid,
                        "page": p_i,
                        "char_start": c_s,
                        "char_end": c_e,
                        "color": col,
                        "rects": rcts,
                        "text": txt,
                    }
                    self.highlights.append(hl_obj)
                    self.canvas.set_highlights(self.highlights)
                    self.canvas.queue_draw()
                    self._update_annotations_button()
                return _on_saved

            self.db_service.save_highlight(
                page_idx, char_start, char_end, color, rects, text,
                make_on_saved(page_idx, char_start, char_end, color, rects, text)
            )

        sel.clear_selection()
        self._update_selection_toolbar(False)
        self.canvas.queue_draw()

    def _remove_highlights_in_selection(self, popover: Gtk.Popover | None = None):
        if popover:
            popover.popdown()

        sel = self.canvas.text_selection if self.canvas else None
        if not sel or not sel.has_selection() or sel.anchor_page is None or sel.focus_page is None:
            return

        if sel.anchor_page <= sel.focus_page:
            p_start, p_end = sel.anchor_page, sel.focus_page
        else:
            p_start, p_end = sel.focus_page, sel.anchor_page

        to_remove = []
        for hl in self.highlights:
            if p_start <= hl["page"] <= p_end:
                to_remove.append(hl)

        for hl in to_remove:
            self.db_service.delete_highlight(hl["id"])
            if hl in self.highlights:
                self.highlights.remove(hl)

        self.canvas.set_highlights(self.highlights)
        self.canvas.queue_draw()
        self._update_annotations_button()
        sel.clear_selection()
        self._update_selection_toolbar(False)

    def _remove_matching_highlights(self):
        to_remove = self._selection_matching_highlights()
        if not to_remove:
            return
        for hl in to_remove:
            self.db_service.delete_highlight(hl["id"])
            if hl in self.highlights:
                self.highlights.remove(hl)
        self.canvas.set_highlights(self.highlights)
        self.canvas.queue_draw()
        self._update_annotations_button()
        sel = self.canvas.text_selection if self.canvas else None
        if sel:
            sel.clear_selection()
        self._update_selection_toolbar(False)

    def _build_debug_cache_box(self):
        self.debug_cache_label = Gtk.Label(xalign=0.0)
        self.debug_cache_label.set_halign(Gtk.Align.START)
        self.debug_cache_label.set_justify(Gtk.Justification.LEFT)
        self.debug_cache_label.add_css_class("debug-info-label")
        self.debug_cache_label.set_visible(True)

        self.link_preview_box.append(self.debug_cache_label)

        self._refresh_debug_cache()
        GLib.timeout_add(1000, self._refresh_debug_cache)

    def _refresh_debug_cache(self) -> bool:
        if not self.debug_mode or not self.debug_cache_label:
            return False
        entries = self.render_cache.total_entries()
        cache_mb = self.render_cache.total_bytes() / (1024 * 1024)
        tex_mb = self.canvas.texture_bytes() / (1024 * 1024)
        text = f"CACHE:    {entries} entries, {cache_mb:.1f}MB\nTEXTURES: {tex_mb:.1f}MB GPU"
        self.debug_cache_label.set_text(text)
        return True

    def _on_page_hovered(self, page_index: int | None, x: float, y: float):
        if not self.debug_mode or not self.debug_info_label:
            return

        if page_index is not None and self.canvas.page_layout and 0 <= page_index < len(self.canvas.page_layout):
            y_offset, dw, dh, crop_rect = self.canvas.page_layout[page_index]
            crop_str = (
                f"({crop_rect.x0:.1f}, {crop_rect.y0:.1f}, {crop_rect.x1:.1f}, {crop_rect.y1:.1f})"
                if crop_rect is not None
                else "uncropped"
            )
            scroll_y = self.vadjustment.get_value()
            total_pages = self.doc_model.page_count if self.doc_model else "?"
            debug_txt = (
                f"PAGE:     {page_index + 1} / {total_pages} (index {page_index})\n"
                f"LAYOUT:   y_off={y_offset:.1f}px | width={dw:.1f}px | height={dh:.1f}px\n"
                f"CROP:     {crop_str}\n"
                f"VIEWPORT: zoom={self.zoom:.2f} | scale={self.canvas.dpi_scale_factor:.1f} | scroll_y={scroll_y:.1f}px"
            )
            self.debug_info_label.set_text(debug_txt)
            self.debug_info_label.set_visible(True)

            if (
                self.debug_arxiv_label
                and self.arxiv_mapper
                and self.arxiv_mapper.is_ready
                and self.canvas.text_selection
            ):
                pt = self.canvas._screen_to_pdf_point(x, y, page_index)
                if pt is not None:
                    char_idx = self.canvas.text_selection.hit_test(page_index, pt[0], pt[1])
                    if char_idx is not None:
                        w_start = self.canvas.text_selection.get_word_start_char_idx(page_index, char_idx)
                        pdf_frag, tex_frag = self.arxiv_mapper.get_cursor_fragment(
                            page_index, w_start, window_words=50
                        )
                        pi = self.canvas.text_selection.get_page_index(page_index)
                        curr_c_rect = pi.chars[char_idx].bbox if 0 <= char_idx < len(pi.chars) else None
                        curr_w_rects = self.canvas.text_selection.get_word_rects_for_char(page_index, char_idx)
                        fwd_c_rects = self.canvas.text_selection.get_forward_char_rects(
                            page_index, w_start, word_count=50
                        )

                        new_data = {
                            "page_index": page_index,
                            "curr_word_rects": curr_w_rects,
                            "curr_char_rect": curr_c_rect,
                            "forward_char_rects": fwd_c_rects,
                        }

                        if self.canvas.debug_arxiv_data != new_data:
                            self.canvas.debug_arxiv_data = new_data
                            self.canvas.queue_draw_overlays("debug-arxiv-data")

                        if pdf_frag or tex_frag:
                            arxiv_txt = (
                                "ARXIV CURSOR FRAGMENT (~50 words forward):\n\n"
                                f"PDF:  {pdf_frag}\n\n"
                                f"TEX:  {tex_frag}"
                            )
                            self.debug_arxiv_label.set_text(arxiv_txt)
                            self.debug_arxiv_label.set_visible(True)
                        else:
                            self.debug_arxiv_label.set_visible(False)
                    else:
                        self.debug_arxiv_label.set_visible(False)
                        if self.canvas.debug_arxiv_data is not None:
                            self.canvas.debug_arxiv_data = None
                            self.canvas.queue_draw_overlays("debug-arxiv-clear")
                else:
                    self.debug_arxiv_label.set_visible(False)
                    if self.canvas.debug_arxiv_data is not None:
                        self.canvas.debug_arxiv_data = None
                        self.canvas.queue_draw_overlays("debug-arxiv-clear")
            self.link_preview_box.set_visible(True)
        else:
            self.debug_info_label.set_text("")
            self.debug_info_label.set_visible(False)
            if self.debug_arxiv_label:
                self.debug_arxiv_label.set_text("")
                self.debug_arxiv_label.set_visible(False)
            if self.canvas.debug_arxiv_data is not None:
                self.canvas.debug_arxiv_data = None
                self.canvas.queue_draw_overlays("debug-arxiv-clear")
            if not self.link_preview_card_box.get_visible():
                self.link_preview_box.set_visible(False)



    @property
    def portal_cache(self):
        return self.link_preview_manager.portal_cache

    def _simulate_link_hover(self, link_index: int) -> bool:
        if not self.doc_model:
            return False
        current_count = 0
        for page_idx in range(self.doc_model.page_count):
            links = self.doc_model.get_page_links(page_idx)
            for link in links:
                if current_count == link_index:
                    self.link_preview_manager.on_link_hovered(page_idx, link)
                    return False
                current_count += 1
        return False

    def _run_scroll_benchmark(self, bench_info: dict) -> bool:
        """
        Executes a frame-by-frame programmatic scroll benchmark between specified pages,
        animating vadjustment smoothly and triggering auto-quit if configured.
        """
        if not self.doc_model or not self.canvas.page_layout:
            return False

        from_page_num = int(bench_info.get("from_page", 8))
        to_page_num = int(bench_info.get("to_page", 9))
        steps = max(1, int(bench_info.get("steps", 40)))
        interval_ms = max(1, int(bench_info.get("interval_ms", 16)))
        repeat_count = max(1, int(bench_info.get("repeat", 3)))
        auto_quit = bool(bench_info.get("auto_quit", True))

        from_idx = max(0, min(self.doc_model.page_count - 1, from_page_num - 1))
        to_idx = max(0, min(self.doc_model.page_count - 1, to_page_num - 1))

        page_layout = self.canvas.page_layout
        if not page_layout or from_idx >= len(page_layout) or to_idx >= len(page_layout):
            return False

        page_gap = self.canvas.page_gap
        y_start = max(0.0, page_layout[from_idx][0] - (page_gap / 2.0))
        y_end = max(0.0, page_layout[to_idx][0] - (page_gap / 2.0))

        self.vadjustment.set_value(y_start)

        current_repeat = 0
        current_step = 0
        direction = 1

        print(
            f"[ScrollBenchmark] Starting benchmark: Page {from_page_num} -> {to_page_num} "
            f"({y_start:.1f}px -> {y_end:.1f}px) across {steps} steps x {repeat_count} repeats",
            flush=True,
        )

        def _step_callback():
            nonlocal current_step, current_repeat, direction
            current_step += 1
            t = current_step / steps
            if direction == 1:
                target_y = y_start + t * (y_end - y_start)
            else:
                target_y = y_end + t * (y_start - y_end)

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
        if doc_view and hasattr(doc_view, "jump_to_page"):
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
        if doc_view and hasattr(doc_view, "set_zoom_level"):
            doc_view.set_zoom_level(
                new_zoom, anchor_x=anchor_x, anchor_y=anchor_y, center_x=center_x, center_y=center_y
            )
        else:
            self.nav_controller.set_zoom_level(
                new_zoom, anchor_x=anchor_x, anchor_y=anchor_y, center_x=center_x, center_y=center_y
            )
        self._schedule_state_save()

    def zoom_in(self):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "zoom_in"):
            doc_view.zoom_in()
        else:
            self.nav_controller.zoom_in()

    def zoom_out(self):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "zoom_out"):
            doc_view.zoom_out()
        else:
            self.nav_controller.zoom_out()

    def zoom_reset(self):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "set_zoom_level"):
            doc_view.set_zoom_level(1.0)
        else:
            self.nav_controller.zoom_reset()

    def zoom_fit_width(self):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "zoom_fit_width"):
            doc_view.zoom_fit_width()
        else:
            self.nav_controller.zoom_fit_width()

    def zoom_fit_page(self):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "zoom_fit_page"):
            doc_view.zoom_fit_page()
        elif doc_view and hasattr(doc_view, "zoom_fit_height"):
            doc_view.zoom_fit_height()
        else:
            self.nav_controller.zoom_fit_page()

    def scroll_page(self, forward: bool = True):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "page_step"):
            doc_view.page_step(1 if forward else -1)
        else:
            self.nav_controller.scroll_page(forward=forward)

    def scroll_step(self, forward: bool = True):
        doc_view = self.get_active_doc_view()
        if doc_view and hasattr(doc_view, "scroll_step"):
            doc_view.scroll_step(1 if forward else -1)
        else:
            self.nav_controller.scroll_step(forward=forward)

    def _on_page_input_activate(self, entry):
        if not self.doc_model or not self.canvas.page_layout:
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

        self._sync_effective_theme()

    def _sync_effective_theme(self):
        self.night_mode = self.is_effective_dark()
        if self.night_mode_action:
            self.night_mode_action.set_state(GLib.Variant.new_boolean(self.night_mode))
        if hasattr(self, "tab_view") and self.tab_view is not None:
            for i in range(self.tab_view.get_n_pages()):
                page = self.tab_view.get_nth_page(i)
                child = page.get_child()
                canvas = getattr(child, "canvas", None)
                if canvas is not None:
                    canvas.set_night_mode(
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
        scheme = self.settings.color_scheme
        if scheme == "system":
            self._sync_effective_theme()

    def toggle_night_mode(self):
        current_dark = self.is_effective_dark()
        self.settings.color_scheme = "light" if current_dark else "dark"
        self._apply_color_scheme()
        self.settings.save()

    def _on_night_mode_action_activated(self, action, parameter):
        self.toggle_night_mode()

    def _on_scroll_page_changed(self, adj):
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
