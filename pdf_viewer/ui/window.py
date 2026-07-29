import os
import sys
import threading
from pathlib import Path


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango

from ..controllers.navigation import NavigationController
from ..controllers.search import SearchController
from ..core.arxiv_mapper import ArxivDiffMapper, arxiv_id_from_path
from ..core.cache import MiniMapCache, RenderCache
from ..core.crop import CropAnalyzer
from ..core.document import DocumentModel
from ..core.index import get_db_for_pdf, load_doc_state, save_doc_state

from ..core.renderer import RenderWorker
from ..core.settings import CropSettings
from ..core.pdf_source import PdfSource, RecentFilesManager
from .arxiv_dialog import ArxivDialog
from .canvas import PDFCanvas
from .gl_canvas import GLCanvas
from .link_preview import LinkPreviewManager
from .minimap import MinimapWindow
from .settings import SettingsWindow
from .shortcuts import ShortcutsController

DEBOUNCE_MS = 150  # search-as-you-type debounce delay


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

    def __init__(self, app, backend="opengl", state=None, screenshot_path=None, follow_link=None, debug_mode=False):
        super().__init__(application=app)
        self.app = app
        self.set_title("PDF Viewer")
        self.backend = backend
        self.debug_mode = debug_mode
        self.set_default_size(1000, 700)
        self.initial_state = state
        self.screenshot_path = screenshot_path
        self.follow_link = follow_link
        self._deferred_state_query = None

        if self.screenshot_path:
            dir_name = os.path.dirname(self.screenshot_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            GLib.timeout_add(2000, self._take_programmatic_screenshot)

        # Core models
        self.doc_model = None
        self.crop_analyzer = None
        self.settings = CropSettings()
        self.current_source: PdfSource | None = None
        self.recent_files = RecentFilesManager()
        self.arxiv_mapper: ArxivDiffMapper | None = None


        # LRU Caches and background thread pool for canvas rendering
        self.render_cache = RenderCache(20)
        self.minimap_cache = MiniMapCache(1000)
        self.render_worker = RenderWorker()

        # Thread pool for search indexing & result portal rendering
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.index_conn = None
        self._state_save_timer_id: int | None = None
        self.pinned = {}  # id -> {"result": ..., "query_terms": ...}

        self._debounce_source_id = None
        self._last_query = ""

        # UI Zoom state
        self.zoom = 1.0

        # Define window actions for the menu
        gapless_state = not getattr(self.settings, "page_gaps", True)
        self.gapless_action = Gio.SimpleAction.new_stateful(
            "gapless-mode", None, GLib.Variant.new_boolean(gapless_state)
        )
        self.gapless_action.connect("activate", self._on_gapless_action_activated)
        self.add_action(self.gapless_action)

        crop_state = self.settings.enabled
        self.crop_action = Gio.SimpleAction.new_stateful(
            "crop-mode", None, GLib.Variant.new_boolean(crop_state)
        )
        self.crop_action.connect("activate", self._on_crop_action_activated)
        self.add_action(self.crop_action)

        settings_action = Gio.SimpleAction.new("open-settings", None)
        settings_action.connect("activate", lambda act, param: self._on_settings_btn_clicked(None))
        self.add_action(settings_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda act, param: self._on_about_action_activated(None))
        self.add_action(about_action)

        open_file_action = Gio.SimpleAction.new("open-file", None)
        open_file_action.connect("activate", lambda act, param: self._open_file_dialog())
        self.add_action(open_file_action)

        open_arxiv_action = Gio.SimpleAction.new("open-arxiv", None)
        open_arxiv_action.connect("activate", lambda act, param: self._open_arxiv_dialog())
        self.add_action(open_arxiv_action)

        open_recent_action = Gio.SimpleAction.new("open-recent", GLib.VariantType.new("s"))
        open_recent_action.connect("activate", self._on_open_recent)
        self.add_action(open_recent_action)

        # Controllers setup
        self.nav_controller = NavigationController(self)
        self.search_controller = SearchController(self)

        # Build UI layout
        self._build_ui()

        # Setup shortcuts controller
        self.shortcuts_controller = ShortcutsController(self)

    def _is_entry_focused(self) -> bool:
        focus = self.get_focus()
        if focus is not None and isinstance(focus, (Gtk.Editable, Gtk.Entry, Gtk.SearchEntry)):
            return True
        if self.entry.has_focus() or self.page_input.has_focus():
            return True
        return False

    def _setup_system_icons(self):
        display = Gdk.Display.get_default()
        if not display:
            return
        theme = Gtk.IconTheme.get_for_display(display)

        # Register local project assets directory and user hicolor icons for application logos
        assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
        if os.path.exists(assets_dir):
            theme.add_search_path(assets_dir)

        user_icons = os.path.expanduser("~/.local/share/icons")
        if os.path.exists(user_icons):
            theme.add_search_path(user_icons)

        Gtk.Window.set_default_icon_name("com.aziis98.pdfatlas")
        self.set_icon_name("com.aziis98.pdfatlas")

        icon_roots = [
            "/usr/share/icons",
            "/usr/local/share/icons",
            os.path.expanduser("~/.local/share/icons"),
            os.path.expanduser("~/.icons"),
        ]

        added_paths = set()
        target_icons = {"map-symbolic.svg", "image-crop-symbolic.svg", "crop-symbolic.svg"}

        for root in icon_roots:
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if filename in target_icons:
                        if dirpath not in added_paths:
                            theme.add_search_path(dirpath)
                            added_paths.add(dirpath)

    def _build_ui(self):
        self._setup_system_icons()

        # Main vertical container
        main_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_layout)

        # HeaderBar Setup
        header = Adw.HeaderBar()
        main_layout.append(header)

        # Left: Open Button & Filename Label
        left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.open_btn = Adw.SplitButton()
        self.open_btn.set_icon_name("document-open-symbolic")
        self.open_btn.set_tooltip_text("Open PDF [Ctrl+O]")
        self.open_btn.add_css_class("raised")
        self.open_btn.connect("clicked", lambda b: self._open_file_dialog())
        self._rebuild_open_menu()
        left_box.append(self.open_btn)

        self.filename_label = Gtk.Label(label="No document loaded")
        self.filename_label.set_ellipsize(Pango.EllipsizeMode.END)  # End ellipsizing
        self.filename_label.set_max_width_chars(40)
        self.filename_label.set_xalign(0)
        self.filename_label.add_css_class("caption")
        left_box.append(self.filename_label)

        header.pack_start(left_box)

        # Center: Search Entry
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("No document loaded")
        self.entry.set_sensitive(False)
        self.entry.set_hexpand(False)
        self.entry.set_halign(Gtk.Align.CENTER)
        self.entry.set_size_request(300, -1)
        self.entry.set_max_width_chars(45)
        self.entry.connect("search-changed", self.search_controller.on_search_changed_debounced)
        self.entry.connect("activate", self.search_controller.on_activate_immediate)
        header.set_title_widget(self.entry)

        # Right: Page Navigation Entry + Total Pages Label, Menu Button
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right_box.set_margin_end(12)
        header.pack_end(right_box)

        # 4-character wide page text input
        self.page_input = Gtk.Entry()
        self.page_input.add_css_class("page-input")
        self.page_input.set_width_chars(4)
        self.page_input.set_max_width_chars(4)  # Enforce tight 4-character natural size constraint
        self.page_input.set_max_length(5)
        self.page_input.set_alignment(0.5)
        self.page_input.set_sensitive(False)
        self.page_input.set_text("1")
        self.page_input.set_hexpand(False)
        self.page_input.set_halign(Gtk.Align.CENTER)
        self.page_input.connect("activate", self._on_page_input_activate)
        page_input_focus = Gtk.EventControllerFocus.new()
        page_input_focus.connect("leave", lambda ctrl: self._on_scroll_page_changed(self.vadjustment))
        self.page_input.add_controller(page_input_focus)
        right_box.append(self.page_input)

        self.page_total_label = Gtk.Label(label="of 0")
        right_box.append(self.page_total_label)

        # Build native options menu using GMenu Model for checkmarks
        menu = Gio.Menu.new()
        menu.append("Gap-less Mode", "win.gapless-mode")
        menu.append("Auto-crop Mode", "win.crop-mode")

        section = Gio.Menu.new()
        section.append("Open Settings", "win.open-settings")
        section.append("About PDF Atlas", "win.about")
        menu.append_section(None, section)

        # Three-dot Action Menu
        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("view-more-symbolic")
        self.menu_button.set_tooltip_text("Options")
        self.menu_button.set_menu_model(menu)
        right_box.append(self.menu_button)

        # Loading Progress Bar (Crop Analysis)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_visible(False)
        main_layout.append(self.progress_bar)

        # Gtk.Stack for View Switching
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        main_layout.append(self.stack)

        # Initialize CSS styling for canvas background and page margins
        self.css_provider = Gtk.CssProvider()
        self._update_theme_css()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        # Child 1: Document View Setup (always using Gtk.Overlay to overlay floating zoom controls)
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_hexpand(True)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)

        # ScrolledWindow Mouse Event Controllers (100% coverage for hover & link clicks)
        scroll_click = Gtk.GestureClick.new()
        scroll_click.set_button(1)
        scroll_click.connect("pressed", lambda ctrl, n_press, x, y: self._on_scrolled_window_click(n_press, x, y))
        self.scrolled_window.add_controller(scroll_click)

        # Text selection drag gesture on the scrolled_window (avoids conflict with canvas GestureClick)
        scroll_drag = Gtk.GestureDrag.new()
        scroll_drag.set_button(1)
        scroll_drag.connect("drag-begin", lambda g, x, y: self.canvas.on_drag_begin(g, x, y))
        scroll_drag.connect("drag-update", lambda g, ox, oy: self.canvas.on_drag_update(g, ox, oy))
        scroll_drag.connect("drag-end", lambda g, ox, oy: self.canvas.on_drag_end(g, ox, oy))
        self.scrolled_window.add_controller(scroll_drag)

        scroll_motion = Gtk.EventControllerMotion.new()
        scroll_motion.connect("motion", lambda ctrl, x, y: self.canvas._on_motion(ctrl, x, y))
        scroll_motion.connect("leave", lambda ctrl: self.canvas._on_leave(ctrl))
        self.scrolled_window.add_controller(scroll_motion)

        # Inner Canvas Container
        self.canvas = PDFCanvas()
        self.canvas.backend = self.backend
        self.canvas.debug_mode = self.debug_mode
        self.canvas.hadjustment = self.scrolled_window.get_hadjustment()
        self.canvas.vadjustment = self.scrolled_window.get_vadjustment()


        self.canvas.on_link_clicked = self._on_link_clicked
        self.canvas.on_page_hovered = self._on_page_hovered
        self.scrolled_window.set_child(self.canvas)


        # Build floating zoom controls box and link preview box
        self._build_floating_zoom_controls()
        self._build_floating_link_preview()

        # Link Preview Manager setup
        self.link_preview_manager = LinkPreviewManager(self)
        self.canvas.on_link_hovered = self.link_preview_manager.on_link_hovered

        self.overlay = Gtk.Overlay()
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)

        if self.backend == "opengl":
            self.gl_canvas = GLCanvas(canvas_layout_provider=self.canvas)
            self.gl_canvas.set_hexpand(True)
            self.gl_canvas.set_vexpand(True)

            self.overlay.set_child(self.gl_canvas)  # base layer (OpenGL)
            self.overlay.add_overlay(self.scrolled_window)  # middle layer (GTK scroll container)
        else:
            self.gl_canvas = None
            self.overlay.set_child(self.scrolled_window)  # base layer (Cairo scroll container)

        self.overlay.add_overlay(self.zoom_floating_box)  # top layer (Floating zoom controls)
        self.overlay.add_overlay(self.link_preview_box)  # top layer (Floating link preview label)
        self.overlay.add_overlay(self.link_preview_manager.portal_card)  # top layer (Floating link portal card)
        if self.debug_mode:
            self._build_debug_cache_box()
        self.stack.add_named(self.overlay, "document-view")

        # Child 2: Search View Setup
        self.search_scrolled = Gtk.ScrolledWindow()
        self.search_scrolled.set_hexpand(True)
        self.search_scrolled.set_vexpand(True)
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.results_box.set_margin_top(16)
        self.results_box.set_margin_bottom(24)
        self.search_scrolled.set_child(self.results_box)
        self.stack.add_named(self.search_scrolled, "search-view")

        # Adjustments wiring
        self.vadjustment = self.scrolled_window.get_vadjustment()
        self.hadjustment = self.scrolled_window.get_hadjustment()
        self.canvas.set_vadjustment(self.vadjustment)

        if self.backend == "opengl":
            self.canvas.backend = "opengl"
            self.canvas.gl_canvas = self.gl_canvas
            # Repaint the GL background layer on scroll
            def _on_scroll_redraw(adj):
                sys.stderr.write("[MainWindow] scroll -> gl_canvas.queue_draw\n")
                sys.stderr.flush()
                if self.gl_canvas:
                    self.gl_canvas.queue_draw()
            self.vadjustment.connect("value-changed", _on_scroll_redraw)

        # Connect vertical scroll adjustment to track current page
        self.vadjustment.connect("value-changed", self._on_scroll_page_changed)

        # Gestures for canvas zooming/scrolling
        self._setup_canvas_gestures()

    def _setup_canvas_gestures(self):
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self._on_canvas_motion)
        self.canvas.add_controller(motion_controller)

        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_controller.connect("scroll", self._on_canvas_scroll)
        self.canvas.add_controller(scroll_controller)

        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("released", self._on_canvas_clicked)
        self.canvas.add_controller(click_gesture)

        pinch_gesture = Gtk.GestureZoom.new()
        pinch_gesture.connect("begin", self._on_pinch_begin)
        pinch_gesture.connect("scale-changed", self._on_pinch_scale_changed)
        pinch_gesture.connect("end", self._on_pinch_end)
        self.canvas.add_controller(pinch_gesture)

    def _on_pinch_begin(self, gesture, sequence):
        self._pinch_start_zoom = self.zoom
        self.canvas.is_pinching = True

    def _on_pinch_scale_changed(self, gesture, scale):
        new_zoom = self._pinch_start_zoom * gesture.get_scale_delta()
        success, center_x, center_y = gesture.get_bounding_box_center()
        if success:
            self.canvas.pinch_center_x = center_x
            self.canvas.pinch_center_y = center_y
            self.set_zoom_level(new_zoom, center_x=center_x, center_y=center_y)
        else:
            self.set_zoom_level(new_zoom)

    def _on_pinch_end(self, gesture, sequence):
        self.canvas.is_pinching = False
        self.canvas.set_zoom(self.zoom)
        self._queue_canvas_redraw()

    def _on_canvas_clicked(self, gesture, n_press, x, y):
        self.canvas.grab_focus()
        if self.canvas.highlighted_block is not None:
            self.canvas.set_highlighted_block(0, None)
            self.canvas.queue_draw()
            if hasattr(self, "gl_canvas") and self.gl_canvas:
                self.gl_canvas.queue_draw()

    def _on_canvas_motion(self, controller, x, y):
        self.pointer_x = x
        self.pointer_y = y

    def _on_canvas_scroll(self, controller, dx, dy):
        modifiers = controller.get_current_event_state()
        if modifiers & Gdk.ModifierType.CONTROL_MASK:
            factor = 1.2 if dy < 0 else (1.0 / 1.2)
            px = getattr(self, "pointer_x", 0.0)
            py = getattr(self, "pointer_y", 0.0)
            self.set_zoom_level(self.zoom * factor, center_x=px, center_y=py)
            return True
        return False

    # --- Document Loading & Indexing ---

    def open_document(self, source: PdfSource):
        filepath = source.uri
        if not os.path.exists(filepath):
            from ..core.arxiv_mapper import download_arxiv_source, extract_arxiv_id_from_raw

            aid = extract_arxiv_id_from_raw(filepath)
            if aid:
                try:
                    pdf_path, _ = download_arxiv_source(aid)
                    filepath = str(pdf_path)
                    source = PdfSource(
                        source_type="arxiv",
                        uri=filepath,
                        display_name=f"arXiv:{aid}",
                    )
                except Exception as e:
                    self._show_error_dialog(f"Failed to download arXiv paper '{filepath}':\n{e}")
                    return
            else:
                self._show_error_dialog(f"File not found: {filepath}")
                return


        try:
            if self.doc_model:
                self.doc_model.close()

            if self.crop_analyzer:
                self.crop_analyzer.close()

            # Save state and close old search index
            if self.index_conn:
                self._save_current_doc_state()
                self.index_conn.close()
                self.index_conn = None


            self.current_source = source
            self.recent_files.add(source)
            self._rebuild_open_menu()

            self.doc_model = DocumentModel(filepath)
            self.crop_analyzer = CropAnalyzer(self.doc_model)

            self.render_cache.clear()
            self.minimap_cache.clear()
            self.pinned.clear()

            self.zoom = 1.0
            self.zoom_label.set_label("100%")

            # Calculate display DPI scale factors based on monitor properties
            display = Gdk.Display.get_default()
            monitors = display.get_monitors() if display is not None else None
            monitor = (
                monitors.get_item(0)
                if (monitors is not None and monitors.get_n_items() > 0)
                else None
            )

            if monitor:
                geom = monitor.get_geometry()
                w_mm = monitor.get_width_mm()
                scale = monitor.get_scale_factor()
                if w_mm > 0:
                    logical_dpi = (geom.width * 25.4) / w_mm
                    physical_dpi = logical_dpi * scale
                else:
                    logical_dpi = 96.0
                    physical_dpi = 96.0 * scale
            else:
                logical_dpi = 96.0
                physical_dpi = 192.0

            self.canvas.dpi_scale_factor = 1.0
            self.canvas.screen_physical_dpi = physical_dpi

            print(
                f"[MainWindow] Screen logical DPI: {logical_dpi:.1f}, physical DPI: {physical_dpi:.1f}, "
                f"layout scale multiplier: {self.canvas.dpi_scale_factor:.3f}",
                flush=True,
            )

            self.canvas.set_document(
                self.doc_model, self.render_cache, self.render_worker, self.crop_analyzer, self.settings
            )

            self.set_title(f"PDF Viewer — {source.display_name}")
            self.filename_label.set_label(source.display_name)
            self.page_total_label.set_label(f"of {self.doc_model.page_count}")
            self.page_input.set_text("1")
            self.page_input.set_sensitive(True)

            self.arxiv_mapper = None
            if source.is_arxiv:
                aid = arxiv_id_from_path(filepath)
                if aid:
                    self.progress_bar.set_fraction(0.0)
                    self.progress_bar.set_visible(True)
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

            indexing_thread = threading.Thread(target=self._index_worker, args=(filepath,), daemon=True)
            indexing_thread.start()


            # Restore state if passed programmatically
            if self.initial_state:
                try:
                    import json

                    state = json.loads(self.initial_state)

                    if "zoom" in state:
                        self.set_zoom_level(float(state["zoom"]))
                    if "crop" in state:
                        self.settings.enabled = bool(state["crop"])
                    if "page_gaps" in state:
                        self.settings.page_gaps = bool(state["page_gaps"])

                    self._on_crop_settings_updated()

                    # Defer scroll_y and search query application until layout realizes
                    def apply_deferred_state():
                        if "scroll_y" in state:
                            self.vadjustment.set_value(float(state["scroll_y"]))
                        if "query" in state:
                            query = str(state["query"])
                            if self.index_conn:
                                self.entry.set_text(query)
                                self.run_search(query)
                            else:
                                self._deferred_state_query = query
                        if "minimap" in state and state["minimap"]:
                            GLib.timeout_add(500, self.toggle_minimap)
                        if "hover_link" in state:
                            hover_idx = int(state["hover_link"])
                            GLib.timeout_add(400, lambda: self._simulate_link_hover(hover_idx))
                        return False

                    GLib.idle_add(apply_deferred_state)
                except Exception as e:
                    print(f"[MainWindow] Error restoring programmatic state: {e}", flush=True)

            if self.follow_link is not None:
                follow_idx: int = self.follow_link
                GLib.timeout_add(400, lambda: self._follow_link_by_index(follow_idx))

        except Exception as e:
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
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_visible(True)

    def _on_arxiv_diff_complete(self, mapper: ArxivDiffMapper | None):
        self.arxiv_mapper = mapper
        self.progress_bar.set_visible(False)


    def _on_indexing_complete(self, conn):
        self.index_conn = conn
        self.entry.set_sensitive(True)
        self.entry.set_placeholder_text("Search document...")

        # Restore saved zoom & scroll_y state from .db if no CLI state was specified
        if not self.initial_state:
            saved_state = load_doc_state(conn)
            if "zoom" in saved_state:
                self.set_zoom_level(saved_state["zoom"])
            if "scroll_y" in saved_state:
                scroll_y = saved_state["scroll_y"]

                def apply_saved_scroll():
                    self.vadjustment.set_value(scroll_y)
                    return False

                GLib.idle_add(apply_saved_scroll)

        # If there's a deferred query from state restoration, execute it now
        if hasattr(self, "_deferred_state_query") and self._deferred_state_query:
            query = self._deferred_state_query
            self._deferred_state_query = None
            self.entry.set_text(query)
            self.run_search(query)

    def _schedule_state_save(self):
        if hasattr(self, "_state_save_timer_id") and self._state_save_timer_id is not None:
            GLib.source_remove(self._state_save_timer_id)

        def _on_save_timer():
            self._state_save_timer_id = None
            self._save_current_doc_state()
            return False

        self._state_save_timer_id = GLib.timeout_add(1000, _on_save_timer)

    def _save_current_doc_state(self):
        if hasattr(self, "index_conn") and self.index_conn:
            zoom = getattr(self, "zoom", 1.0)
            scroll_y = self.vadjustment.get_value() if hasattr(self, "vadjustment") else 0.0
            save_doc_state(self.index_conn, zoom, scroll_y)


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
            file = dialog.get_file()
            path = file.get_path()
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

        open_file_section = Gio.Menu.new()
        open_file_section.append("Open File\u2026", "win.open-file")
        menu.append_section(None, open_file_section)

        recent = self.recent_files.get_recent(5)
        if recent:
            recent_section = Gio.Menu.new()
            for source in recent:
                recent_section.append(source.display_name, f"win.open-recent::{source.uri}")
            menu.append_section(None, recent_section)

        arxiv_section = Gio.Menu.new()
        arxiv_section.append("Open from arXiv\u2026", "win.open-arxiv")
        menu.append_section(None, arxiv_section)

        self.open_btn.set_menu_model(menu)

    def _on_open_recent(self, action, parameter):
        uri = parameter.get_string()
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
        if hasattr(self, "minimap_dialog") and self.minimap_dialog and self.minimap_dialog.get_visible():
            self.minimap_dialog.destroy()
            self.minimap_dialog = None
            self.canvas.grab_focus()
            return True

        if self.page_input.has_focus():
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
            self.canvas.text_selection.clear_selection()
            self.canvas.queue_draw_overlays("selection-cleared")
            return True

        return False

    def _copy_selection_to_clipboard(self):
        """Copy the currently selected text to the system clipboard (as LaTeX source if arXiv sourcemap is ready)."""
        sel = self.canvas.text_selection
        if sel is None or not sel.has_selection():
            return

        text = ""
        if self.arxiv_mapper and self.arxiv_mapper.is_ready:
            if sel.anchor_page is not None and sel.focus_page is not None:
                if sel.anchor_page <= sel.focus_page:
                    p_start, p_end = sel.anchor_page, sel.focus_page
                else:
                    p_start, p_end = sel.focus_page, sel.anchor_page

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

    def _on_scrolled_window_click(self, n_press: int, x: float, y: float):
        self.set_focus(None)
        if hasattr(self, "canvas") and self.canvas:
            self.canvas._on_click(None, n_press, x, y)

    def _queue_canvas_redraw(self):
        """Redraws the active canvas depending on whether OpenGL or Cairo backend is active."""
        sys.stderr.write(f"[MainWindow] _queue_canvas_redraw backend={self.backend}\n")
        sys.stderr.flush()
        if self.backend == "opengl" and self.gl_canvas:
            self.gl_canvas.queue_draw()
        else:
            self.canvas.queue_draw()

    def page_step(self, forward: bool):
        current_idx = self.get_current_page_index()
        target_idx = current_idx + 1 if forward else current_idx - 1
        self.jump_to_page(target_idx)

    def _on_link_clicked(self, page_index: int, link: dict):
        if not self.doc_model or not self.canvas.page_layout:
            return

        target_page = link.get("page")
        uri = link.get("uri")

        if target_page is None or not isinstance(target_page, int) or target_page < 0 or target_page >= self.doc_model.page_count:
            if uri:
                try:
                    Gtk.show_uri(self, uri, Gdk.CURRENT_TIME)
                except Exception as e:
                    print(f"[MainWindow] Error launching URI {uri}: {e}", flush=True)
            return

        target_rect = self.doc_model.page_rect(target_page)
        to_point = link.get("to")
        if to_point and hasattr(to_point, "y") and to_point.y is not None and to_point.y > 0.0:
            # PyMuPDF to_point coordinates are PDF native bottom-up (0 is page bottom)
            y_offset_in_page = max(0.0, target_rect.height - float(to_point.y))
        else:
            y_offset_in_page = 0.0

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
        )

        self.minimap_dialog = dialog
        dialog.minimap.set_current_page(active_page)
        dialog.present()

    def _on_minimap_page_clicked(self, page_index):
        self.jump_to_page(page_index)

    # --- Toggles & Settings ---

    def toggle_crop(self):
        self.settings.enabled = not self.settings.enabled
        self._on_crop_settings_updated()

    def toggle_gapless(self):
        self.settings.page_gaps = not self.settings.page_gaps
        self.gapless_action.set_state(GLib.Variant.new_boolean(not self.settings.page_gaps))
        self._on_crop_settings_updated()

    def _on_crop_btn_toggled(self, btn):
        self.settings.enabled = btn.get_active()
        self._on_crop_settings_updated()

    def _on_settings_btn_clicked(self, btn):
        dialog = SettingsWindow(
            parent_window=self,
            settings=self.settings,
            on_changed=self._on_settings_changed,
            on_reanalyze=self._on_reanalyze,
        )
        dialog.present()

    def _on_about_action_activated(self, action=None, param=None):
        about = Adw.AboutDialog(
            application_name="PDF Atlas",
            application_icon="logo",
            developer_name="PDF Atlas Team",
            version="1.0.0",
            comments="High-performance PDF document viewer with spatial navigator and FTS5 search.",
        )
        about.present(self)

    def _on_settings_changed(self):
        self._on_crop_settings_updated()
        # Re-run search if a query is active to apply layout changes (list vs grid) in real-time
        if hasattr(self, "_last_query") and self._last_query:
            self.run_search(self._last_query)

    def _on_crop_settings_updated(self):
        # Sync stateful action states
        if hasattr(self, "crop_action") and self.crop_action:
            self.crop_action.set_state(GLib.Variant.new_boolean(self.settings.enabled))
        if hasattr(self, "gapless_action") and self.gapless_action:
            self.gapless_action.set_state(GLib.Variant.new_boolean(getattr(self.settings, "page_gaps", True)))

        # Apply CSS updates dynamically (e.g. for gap-less mode padding/borders)
        self._update_theme_css()

        if self.crop_analyzer:
            self.crop_analyzer.compute_crop_rects(self.settings)

        self.canvas.on_crop_changed()

    # --- Crop Re-analysis ---

    def _on_reanalyze(self):
        self._start_crop_analysis()

    def _start_crop_analysis(self):
        if not self.doc_model or not self.crop_analyzer:
            return

        page_count = self.doc_model.page_count
        self.crop_analyzer.scanned = [False] * page_count
        self.crop_analyzer.raw_bboxes = [None] * page_count

        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_visible(True)
        self.crop_scanned_count = 0

        # Run crop analysis in a separate background thread so it doesn't block RenderWorker renders
        crop_thread = threading.Thread(target=self._crop_analysis_worker, daemon=True)
        crop_thread.start()

    def _crop_analysis_worker(self):
        if not self.doc_model or not self.crop_analyzer:
            return
        page_count = self.doc_model.page_count
        for i in range(page_count):
            if not self.doc_model or not self.crop_analyzer:
                return
            try:
                self.crop_analyzer.scan_page(i)
                GLib.idle_add(self._on_crop_page_scanned, i)
            except Exception as e:
                print(f"Error scanning page {i} for crop analysis: {e}")

        # Compute crop rectangles once scanning completes
        if self.doc_model and self.crop_analyzer:
            self.crop_analyzer.compute_crop_rects(self.settings)
            GLib.idle_add(self._on_crop_analysis_complete)

    def _on_crop_page_scanned(self, page_index):
        self.crop_scanned_count += 1
        total = self.doc_model.page_count if self.doc_model else 1
        self.progress_bar.set_fraction(self.crop_scanned_count / total)

    def _on_crop_analysis_complete(self):
        self.progress_bar.set_visible(False)
        self.canvas.on_crop_changed()

    def close(self):
        # Save state and shutdown executors and close connections cleanly
        self._save_current_doc_state()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.index_conn:
            self.index_conn.close()
            self.index_conn = None

        if self.crop_analyzer:
            self.crop_analyzer.close()
        if self.doc_model:
            self.doc_model.close()
        super().close()

    def _update_theme_css(self):
        gap_size = 12 if getattr(self.settings, "page_gaps", True) else 0

        shared_css = """
            .zoom-floating-box {
                background-color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 10px;
                padding: 4px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            }
            .zoom-floating-label {
                font-size: 10px;
                font-weight: bold;
                color: #2e2e2e;
                margin: 4px 0;
            }
            .zoom-floating-box button {
                min-width: 30px;
                min-height: 30px;
                padding: 0;
                border-radius: 6px;
            }
            headerbar entry.page-input text,
            headerbar entry.page-input > text,
            .page-input text,
            entry.page-input > text {
                padding-top: 2px;
                padding-bottom: 2px;
                padding-left: 2px;
                padding-right: 2px;
                min-width: 0px;
                min-height: 0px;
            }
            headerbar entry.page-input,
            .page-input,
            entry.page-input {
                min-width: 0px;
                min-height: 0px;
                padding: 0;
                margin: 0;
            }
            .link-preview-box {
                background-color: rgba(30, 30, 30, 0.88);
                color: #ffffff;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
                box-shadow: 0px 2px 6px rgba(0, 0, 0, 0.25);
            }
            .debug-info-label {
                font-family: monospace, monospace;
                font-size: 10px;
                color: #ffffff;
                background-color: rgba(30, 30, 30, 0.88);
                border-radius: 6px;
                padding: 4px 10px;
                box-shadow: 0px 2px 6px rgba(0, 0, 0, 0.25);
            }
            .link-portal-card {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 8px;
                padding: 0px;
                box-shadow: 0px 4px 16px rgba(0, 0, 0, 0.12), 0px 1px 3px rgba(0, 0, 0, 0.08);
            }

            .portal-overlay-pin {
                min-width: 28px;
                min-height: 28px;
                padding: 4px;
            }
            .portal-overlay-pill {
                background-color: rgba(60, 60, 60, 0.85);
                color: #ffffff;
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 500;
            }
        """

        if self.backend == "opengl":
            css_data = f"""
                .pdf-canvas {{
                    background-color: transparent;
                    padding: 0px;
                }}
                .page-container {{
                    background-color: transparent;
                    border: none;
                    margin: 0;
                    padding: 0;
                    box-shadow: none;
                }}
                scrolledwindow, viewport {{
                    background-color: transparent;
                }}
                {shared_css}
            """
        else:
            css_data = f"""
                .pdf-canvas {{
                    background-color: #e0e0e0;
                    padding: 0px;
                }}
                .page-container {{
                    background-color: #ffffff;
                    border: {"1px solid #b0b0b0" if gap_size > 0 else "none"};
                    box-shadow: {"0px 3px 6px rgba(0, 0, 0, 0.1)" if gap_size > 0 else "none"};
                }}
                {shared_css}
            """
        self.css_provider.load_from_data(css_data.encode("utf-8"))

    def _build_floating_zoom_controls(self):
        self.zoom_floating_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.zoom_floating_box.add_css_class("zoom-floating-box")
        self.zoom_floating_box.set_halign(Gtk.Align.END)
        self.zoom_floating_box.set_valign(Gtk.Align.END)
        self.zoom_floating_box.set_margin_end(20)
        self.zoom_floating_box.set_margin_bottom(20)

        self.zoom_in_btn = Gtk.Button()
        self.zoom_in_btn.set_icon_name("zoom-in-symbolic")
        self.zoom_in_btn.set_tooltip_text("Zoom In")
        self.zoom_in_btn.connect("clicked", lambda b: self.zoom_in())
        self.zoom_floating_box.append(self.zoom_in_btn)

        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.add_css_class("zoom-floating-label")
        self.zoom_floating_box.append(self.zoom_label)

        self.zoom_out_btn = Gtk.Button()
        self.zoom_out_btn.set_icon_name("zoom-out-symbolic")
        self.zoom_out_btn.set_tooltip_text("Zoom Out")
        self.zoom_out_btn.connect("clicked", lambda b: self.zoom_out())
        self.zoom_floating_box.append(self.zoom_out_btn)

    def _build_floating_link_preview(self):
        self.link_preview_label = Gtk.Label()
        self.link_preview_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.link_preview_label.set_max_width_chars(65)

        self.link_preview_card_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.link_preview_card_box.add_css_class("link-preview-box")
        self.link_preview_card_box.set_halign(Gtk.Align.START)
        self.link_preview_card_box.append(self.link_preview_label)
        self.link_preview_card_box.set_visible(False)

        self.link_preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.link_preview_box.set_halign(Gtk.Align.START)
        self.link_preview_box.set_valign(Gtk.Align.END)
        self.link_preview_box.set_margin_start(8)
        self.link_preview_box.set_margin_bottom(8)
        self.link_preview_box.append(self.link_preview_card_box)

        if self.debug_mode:
            self.debug_info_label = Gtk.Label(xalign=0.0)
            self.debug_info_label.set_halign(Gtk.Align.START)
            self.debug_info_label.set_justify(Gtk.Justification.LEFT)
            self.debug_info_label.add_css_class("debug-info-label")
            self.debug_info_label.set_visible(False)
            self.link_preview_box.append(self.debug_info_label)

            self.debug_arxiv_label = Gtk.Label(xalign=0.0)
            self.debug_arxiv_label.set_halign(Gtk.Align.START)
            self.debug_arxiv_label.set_justify(Gtk.Justification.LEFT)
            self.debug_arxiv_label.add_css_class("debug-info-label")
            self.debug_arxiv_label.set_visible(False)
            self.debug_arxiv_label.set_wrap(True)
            self.debug_arxiv_label.set_max_width_chars(80)
            self.link_preview_box.append(self.debug_arxiv_label)
        else:
            self.debug_info_label = None
            self.debug_arxiv_label = None

        self.link_preview_box.set_visible(False)

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
        if not self.debug_mode or not hasattr(self, "debug_cache_label"):
            return False
        entries = self.render_cache.total_entries()
        cache_mb = self.render_cache.total_bytes() / (1024 * 1024)
        if self.backend == "opengl" and self.gl_canvas:
            tex_mb = self.gl_canvas.texture_bytes() / (1024 * 1024)
            text = f"CACHE:    {entries} entries, {cache_mb:.1f}MB\nTEXTURES: {tex_mb:.1f}MB GPU"
        else:
            text = f"CACHE:    {entries} entries, {cache_mb:.1f}MB"
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
                f"VIEWPORT: zoom={self.zoom:.2f} | scale={self.canvas.dpi_scale_factor:.1f} | scroll_y={scroll_y:.1f}px | backend={self.backend}"
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

    def jump_to_page(self, page_index: int, smooth: bool = True):
        self.nav_controller.jump_to_page(page_index, smooth=smooth)

    def set_zoom_level(
        self,
        new_zoom: float,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        center_x: float | None = None,
        center_y: float | None = None,
    ):
        self.nav_controller.set_zoom_level(
            new_zoom, anchor_x=anchor_x, anchor_y=anchor_y, center_x=center_x, center_y=center_y
        )
        self._schedule_state_save()

    def zoom_in(self):
        self.nav_controller.zoom_in()

    def zoom_out(self):
        self.nav_controller.zoom_out()

    def zoom_reset(self):
        self.nav_controller.zoom_reset()

    def zoom_fit_width(self):
        self.nav_controller.zoom_fit_width()

    def zoom_fit_page(self):
        self.nav_controller.zoom_fit_page()

    def scroll_page(self, forward: bool = True):
        self.nav_controller.scroll_page(forward=forward)

    def scroll_step(self, forward: bool = True):
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

    def _on_scroll_page_changed(self, adj):
        self._schedule_state_save()
        if not self.doc_model or not self.canvas.page_layout:
            return


        y_val = adj.get_value()
        viewport_h = adj.get_page_size()
        y_center = y_val + (viewport_h / 2.0)

        current_idx = 0
        for i, layout in enumerate(self.canvas.page_layout):
            y_offset, dw, dh, crop_rect = layout
            page_y0 = y_offset
            page_y1 = y_offset + dh + self.canvas.page_gap
            if page_y0 <= y_center <= page_y1:
                current_idx = i
                break

        page_num = current_idx + 1
        if hasattr(self, "page_input") and self.page_input and not self.page_input.has_focus():
            self.page_input.set_text(str(page_num))

    def _take_programmatic_screenshot(self):
        print(f"[MainWindow] Taking scheduled screenshot of window to: {self.screenshot_path}", flush=True)
        try:
            self.queue_allocate()
            renderer = self.get_renderer()
            if not renderer:
                print("[Screenshot] Window has no active renderer yet", flush=True)
                return False

            is_minimap = (
                hasattr(self, "minimap_dialog") and self.minimap_dialog and self.minimap_dialog.get_visible()
            )

            if is_minimap and self.minimap_dialog and self.screenshot_path:
                # 1. Snapshot main window content box as base
                base_widget = self.get_content()
                if not base_widget:
                    return False
                bw = base_widget.get_width()
                bh = base_widget.get_height()
                b_rect = Graphene.Rect.alloc()
                b_rect.init(0.0, 0.0, float(bw), float(bh))
                b_snap = Gtk.Snapshot.new()
                bg_color = Gdk.RGBA()
                bg_color.parse("#ffffff")
                b_snap.append_color(bg_color, b_rect)
                Gtk.WidgetPaintable.new(base_widget).snapshot(b_snap, float(bw), float(bh))
                b_texture = renderer.render_texture(b_snap.to_node(), b_rect)

                # 2. Snapshot minimap modal window
                modal_widget = self.minimap_dialog
                mw = modal_widget.get_width()
                mh = modal_widget.get_height()
                m_rect = Graphene.Rect.alloc()
                m_rect.init(0.0, 0.0, float(mw), float(mh))
                m_snap = Gtk.Snapshot.new()
                m_snap.append_color(bg_color, m_rect)
                Gtk.WidgetPaintable.new(modal_widget).snapshot(m_snap, float(mw), float(mh))
                m_texture = renderer.render_texture(m_snap.to_node(), m_rect)

                base_path = self.screenshot_path + ".base.png"
                modal_path = self.screenshot_path + ".modal.png"
                if b_texture and m_texture:
                    b_texture.save_to_png(base_path)
                    m_texture.save_to_png(modal_path)
                    self._composite_minimap_screenshot(base_path, modal_path, self.screenshot_path)
            else:
                content_widget = self.get_content()
                if not content_widget or not self.screenshot_path:
                    print("[Screenshot] Window has no content widget to snapshot", flush=True)
                    return False

                w = content_widget.get_width()
                h = content_widget.get_height()
                rect = Graphene.Rect.alloc()
                rect.init(0.0, 0.0, float(w), float(h))
                snapshot = Gtk.Snapshot.new()
                bg_color = Gdk.RGBA()
                bg_color.parse("#ffffff")
                snapshot.append_color(bg_color, rect)
                Gtk.WidgetPaintable.new(content_widget).snapshot(snapshot, float(w), float(h))

                texture = renderer.render_texture(snapshot.to_node(), rect)
                if texture and self.screenshot_path:
                    texture.save_to_png(self.screenshot_path)
                    print("[Screenshot] Programmatic screenshot saved successfully.", flush=True)
                    self._apply_gnome_shadow(self.screenshot_path)
                else:
                    print("[Screenshot] Failed to render snapshot node to texture.", flush=True)
        except Exception as e:
            print(f"[Screenshot] Error taking screenshot: {e}", flush=True)
        finally:
            self.close()
            if hasattr(self, "app") and self.app:
                self.app.quit()
        return False

    def _composite_minimap_screenshot(self, base_path, modal_path, out_path):
        try:
            import os

            from PIL import Image, ImageDraw, ImageFilter

            base = Image.open(base_path).convert("RGBA")
            modal = Image.open(modal_path).convert("RGBA")

            bw, bh = base.size
            mw, mh = modal.size

            dim = Image.new("RGBA", (bw, bh), (0, 0, 0, 45))
            base_dimmed = Image.alpha_composite(base, dim)

            modal_radius = 12
            modal_mask = Image.new("L", (mw, mh), 0)
            draw_m = ImageDraw.Draw(modal_mask)
            draw_m.rounded_rectangle((0, 0, mw - 1, mh - 1), radius=modal_radius, fill=255)

            rounded_modal = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
            rounded_modal.paste(modal, (0, 0), mask=modal_mask)

            draw_b = ImageDraw.Draw(rounded_modal)
            draw_b.rounded_rectangle(
                (0, 0, mw - 1, mh - 1), radius=modal_radius, outline=(180, 180, 180, 120), width=1
            )

            shadow_blur = 16
            shadow_opacity = 0.25
            offset_y = 6

            shadow_mask = Image.new("L", (bw, bh), 0)
            s_draw = ImageDraw.Draw(shadow_mask)

            x0 = (bw - mw) // 2
            y0 = (bh - mh) // 2

            shadow_box = (x0, y0 + offset_y, x0 + mw - 1, y0 + offset_y + mh - 1)
            s_draw.rounded_rectangle(shadow_box, radius=modal_radius, fill=int(255 * shadow_opacity))
            shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

            dark_fill = Image.new("RGBA", (bw, bh), (10, 10, 16, 255))
            base_dimmed.paste(dark_fill, (0, 0), mask=shadow_mask)
            base_dimmed.paste(rounded_modal, (x0, y0), mask=rounded_modal)

            base_dimmed.save(out_path, format="PNG")

            if os.path.exists(base_path):
                os.remove(base_path)
            if os.path.exists(modal_path):
                os.remove(modal_path)

            self._apply_gnome_shadow(out_path)
            print(
                f"[Screenshot] Composited minimap window over main reader and saved to {out_path}", flush=True
            )
        except Exception as e:
            print(f"[Screenshot] Failed to composite minimap screenshot: {e}", flush=True)

    def _apply_gnome_shadow(self, file_path):
        try:
            from PIL import Image, ImageDraw, ImageFilter

            img = Image.open(file_path).convert("RGBA")
            w, h = img.size

            corner_radius = 12
            shadow_margin = 60
            shadow_blur = 18
            shadow_offset_y = 6
            shadow_opacity = 0.20
            border_color = (180, 180, 180, 100)

            mask = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=corner_radius, fill=255)

            rounded_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            rounded_img.paste(img, (0, 0), mask=mask)

            draw_border = ImageDraw.Draw(rounded_img)
            draw_border.rounded_rectangle(
                (0, 0, w - 1, h - 1), radius=corner_radius, outline=border_color, width=1
            )

            canvas_w = w + shadow_margin * 2
            canvas_h = h + shadow_margin * 2 + shadow_offset_y

            shadow_mask = Image.new("L", (canvas_w, canvas_h), 0)
            shadow_draw = ImageDraw.Draw(shadow_mask)
            shadow_box = (
                shadow_margin,
                shadow_margin + shadow_offset_y,
                shadow_margin + w - 1,
                shadow_margin + shadow_offset_y + h - 1,
            )
            shadow_draw.rounded_rectangle(shadow_box, radius=corner_radius, fill=int(255 * shadow_opacity))
            shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

            canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            dark_fill = Image.new("RGBA", (canvas_w, canvas_h), (12, 16, 24, 255))
            canvas.paste(dark_fill, (0, 0), mask=shadow_mask)
            canvas.paste(rounded_img, (shadow_margin, shadow_margin), mask=rounded_img)

            canvas.save(file_path, format="PNG")
            print(f"[Screenshot] Applied GNOME drop-shadow to {file_path}", flush=True)
        except Exception as e:
            print(f"[Screenshot] Failed to apply GNOME shadow: {e}", flush=True)
