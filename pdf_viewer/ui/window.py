import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, Gdk, GLib, Gio, Adw, Graphene
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from ..core.document import DocumentModel
from ..core.cache import RenderCache, MiniMapCache
from ..core.renderer import RenderWorker
from ..core.crop import CropAnalyzer
from ..core.settings import CropSettings
from ..core.index import get_db_for_pdf, search as fts_search

from .canvas import PDFCanvas
from .minimap import MinimapWindow
from .settings import SettingsWindow
from .portal import ResultRow
from .gl_canvas import GLCanvas

DEBOUNCE_MS = 300  # search-as-you-type debounce delay

class MainWindow(Adw.ApplicationWindow):
    """
    Main Adwaita application window.
    Features:
      - HeaderBar with centered fuzzy SearchEntry and crop/minimap/settings buttons.
      - Gtk.Stack holding the PDF Canvas view and the fuzzy search portal view.
      - Background FTS5 database builder to prevent UI freeze during text indexing.
      - Click-to-navigate search portal coordinates mapping.
    """
    def __init__(self, app, backend="cairo", state=None, screenshot_path=None):
        super().__init__(application=app)
        self.app = app
        self.set_title("PDF Viewer")
        self.backend = backend
        self.set_default_size(900, 700)
        self.initial_state = state
        self.screenshot_path = screenshot_path
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
        
        # LRU Caches and background thread pool for canvas rendering
        self.render_cache = RenderCache(20)
        self.minimap_cache = MiniMapCache(1000)
        self.render_worker = RenderWorker()
        
        # Thread pool for search indexing & result portal rendering
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")
        self.index_conn = None
        self.pinned = {}  # id -> {"result": ..., "query_terms": ...}
        self._debounce_source_id = None
        self._last_query = ""

        # UI Zoom state
        self.zoom = 1.0

        # Define window actions for the menu
        gapless_state = getattr(self.settings, 'page_gaps', True)
        self.gapless_action = Gio.SimpleAction.new_stateful(
            "gapless-mode",
            None,
            GLib.Variant.new_boolean(gapless_state)
        )
        self.gapless_action.connect("activate", self._on_gapless_action_activated)
        self.add_action(self.gapless_action)

        crop_state = self.settings.enabled
        self.crop_action = Gio.SimpleAction.new_stateful(
            "crop-mode",
            None,
            GLib.Variant.new_boolean(crop_state)
        )
        self.crop_action.connect("activate", self._on_crop_action_activated)
        self.add_action(self.crop_action)

        settings_action = Gio.SimpleAction.new("open-settings", None)
        settings_action.connect("activate", lambda act, param: self._on_settings_btn_clicked(None))
        self.add_action(settings_action)

        # Build UI layout
        self._build_ui()

        # Setup shortcut controller
        self.shortcut_controller = Gtk.ShortcutController.new()
        self.add_controller(self.shortcut_controller)
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        # File operations
        self._add_shortcut("<Control>o", self._open_file_dialog)
        self._add_shortcut("<Control>q", self.close)
        self._add_shortcut("q", self.close)

        # Focus search bar
        self._add_shortcut("<Control>l", self.entry.grab_focus)

        # Zoom keys
        self._add_shortcut("plus", self.zoom_in)
        self._add_shortcut("<Shift>plus", self.zoom_in)
        self._add_shortcut("equal", self.zoom_in)
        self._add_shortcut("<Shift>equal", self.zoom_in)
        self._add_shortcut("KP_Add", self.zoom_in)
        self._add_shortcut("minus", self.zoom_out)
        self._add_shortcut("KP_Subtract", self.zoom_out)
        self._add_shortcut("<Control>0", self.zoom_reset)

        # Modal window triggers
        self._add_shortcut("m", self.toggle_minimap)
        self._add_shortcut("c", self.toggle_crop)

        # Scrolling
        self._add_shortcut("Page_Up", lambda: self.scroll_page(forward=False))
        self._add_shortcut("Page_Down", lambda: self.scroll_page(forward=True))
        self._add_shortcut("Up", lambda: self.scroll_step(forward=False))
        self._add_shortcut("Down", lambda: self.scroll_step(forward=True))

        # Close/clear search
        self._add_shortcut("Escape", self._on_escape)

    def _add_shortcut(self, trigger_str, callback):
        trigger = Gtk.ShortcutTrigger.parse_string(trigger_str)
        action = Gtk.CallbackAction.new(lambda w, a: (callback(), True)[1])
        shortcut = Gtk.Shortcut.new(trigger, action)
        self.shortcut_controller.add_shortcut(shortcut)

    def _setup_system_icons(self):
        display = Gdk.Display.get_default()
        if not display:
            return
        theme = Gtk.IconTheme.get_for_display(display)
        
        icon_roots = [
            "/usr/share/icons",
            "/usr/local/share/icons",
            os.path.expanduser("~/.local/share/icons"),
            os.path.expanduser("~/.icons")
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
        left_box.set_margin_start(12)
        
        self.open_btn = Gtk.Button()
        self.open_btn.set_icon_name("document-open-symbolic")
        self.open_btn.set_tooltip_text("Open PDF [Ctrl+O]")
        self.open_btn.connect("clicked", lambda b: self._open_file_dialog())
        left_box.append(self.open_btn)

        self.filename_label = Gtk.Label(label="No document loaded")
        self.filename_label.set_ellipsize(3)  # End ellipsizing
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
        self.entry.connect("search-changed", self._on_search_changed_debounced)
        self.entry.connect("activate", self._on_activate_immediate)
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
        right_box.append(self.page_input)

        self.page_total_label = Gtk.Label(label="of 0")
        right_box.append(self.page_total_label)

        # Build native options menu using GMenu Model for checkmarks
        menu = Gio.Menu.new()
        menu.append("Gap-less Mode", "win.gapless-mode")
        menu.append("Auto-crop Mode", "win.crop-mode")
        
        section = Gio.Menu.new()
        section.append("Open Settings", "win.open-settings")
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
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Child 1: Document View Setup (always using Gtk.Overlay to overlay floating zoom controls)
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_hexpand(True)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        
        self.canvas = PDFCanvas()
        self.scrolled_window.set_child(self.canvas)
        
        # Build floating zoom controls box
        self._build_floating_zoom_controls()
        
        self.overlay = Gtk.Overlay()
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)
        
        if self.backend == "opengl":
            self.gl_canvas = GLCanvas(self.canvas)
            self.gl_canvas.set_hexpand(True)
            self.gl_canvas.set_vexpand(True)
            
            self.overlay.set_child(self.gl_canvas)         # base layer (OpenGL)
            self.overlay.add_overlay(self.scrolled_window)  # middle layer (GTK scroll container)
        else:
            self.gl_canvas = None
            self.overlay.set_child(self.scrolled_window)   # base layer (Cairo scroll container)
            
        self.overlay.add_overlay(self.zoom_floating_box)    # top layer (Floating zoom controls)
        self.stack.add_named(self.overlay, "document-view")

        # Child 2: Search View
        self.search_scrolled = Gtk.ScrolledWindow()
        self.search_scrolled.set_hexpand(True)
        self.search_scrolled.set_vexpand(True)
        self.search_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
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
            self.vadjustment.connect("value-changed", lambda adj: self.gl_canvas.queue_draw())
            
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

    def _on_canvas_motion(self, controller, x, y):
        self.pointer_x = x
        self.pointer_y = y

    def _on_canvas_scroll(self, controller, dx, dy):
        modifiers = controller.get_current_event_state()
        if modifiers & Gdk.ModifierType.CONTROL_MASK:
            factor = 1.2 if dy < 0 else (1.0 / 1.2)
            px = getattr(self, 'pointer_x', 0.0)
            py = getattr(self, 'pointer_y', 0.0)
            self.set_zoom_level(self.zoom * factor, center_x=px, center_y=py)
            return True
        return False

    # --- Document Loading & Indexing ---

    def open_document(self, filepath: str):
        if not os.path.exists(filepath):
            self._show_error_dialog(f"File not found: {filepath}")
            return

        try:
            if self.doc_model:
                self.doc_model.close()

            if self.crop_analyzer:
                self.crop_analyzer.close()

            # Close old search index
            if self.index_conn:
                self.index_conn.close()
                self.index_conn = None

            self.doc_model = DocumentModel(filepath)
            self.crop_analyzer = CropAnalyzer(self.doc_model)

            self.render_cache.clear()
            self.minimap_cache.clear()
            self.pinned.clear()

            self.zoom = 1.0
            self.zoom_label.set_label("100%")

            # Calculate display DPI scale factors based on monitor properties
            display = Gdk.Display.get_default()
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if (monitors and monitors.get_n_items() > 0) else None
            
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
            
            print(f"[MainWindow] Screen logical DPI: {logical_dpi:.1f}, physical DPI: {physical_dpi:.1f}, "
                  f"layout scale multiplier: {self.canvas.dpi_scale_factor:.3f}", flush=True)

            self.canvas.set_document(self.doc_model, self.render_cache, self.render_worker, self.crop_analyzer, self.settings)

            filename = os.path.basename(filepath)
            self.set_title(f"PDF Viewer — {filename}")
            self.filename_label.set_label(filename)
            self.page_total_label.set_label(f"of {self.doc_model.page_count}")
            self.page_input.set_text("1")
            self.page_input.set_sensitive(True)

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
                        return False
                    GLib.idle_add(apply_deferred_state)
                except Exception as e:
                    print(f"[MainWindow] Error restoring programmatic state: {e}", flush=True)

        except Exception as e:
            self._show_error_dialog(f"Failed to open PDF document:\n{e}")

    def _index_worker(self, filepath):
        try:
            conn = get_db_for_pdf(filepath)
            GLib.idle_add(self._on_indexing_complete, conn)
        except Exception as e:
            GLib.idle_add(self._show_error_dialog, f"Search indexing failed:\n{e}")

    def _on_indexing_complete(self, conn):
        self.index_conn = conn
        self.entry.set_sensitive(True)
        self.entry.set_placeholder_text("Search document...")
        
        # If there's a deferred query from state restoration, execute it now
        if hasattr(self, "_deferred_state_query") and self._deferred_state_query:
            query = self._deferred_state_query
            self._deferred_state_query = None
            self.entry.set_text(query)
            self.run_search(query)

    def _open_file_dialog(self):
        dialog = Gtk.FileChooserNative.new(
            "Open PDF File",
            self,
            Gtk.FileChooserAction.OPEN,
            "Open",
            "Cancel"
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
            self.open_document(file.get_path())
        dialog.destroy()

    def _show_error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show()

    # --- Search Engine Wiring ---

    def _on_search_changed_debounced(self, entry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
        self._debounce_source_id = GLib.timeout_add(DEBOUNCE_MS, self._debounced_fire)

    def _debounced_fire(self):
        self._debounce_source_id = None
        self.run_search(self.entry.get_text())
        return False

    def _on_activate_immediate(self, entry):
        if self._debounce_source_id is not None:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = None
        self.run_search(entry.get_text())

    def _clear_results_box(self):
        child = self.results_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.results_box.remove(child)
            child = nxt

    def _on_toggle_pin(self, result, query_terms, pinned):
        if pinned:
            self.pinned[result["id"]] = {"result": result, "query_terms": query_terms}
        else:
            self.pinned.pop(result["id"], None)
        self.run_search(self.entry.get_text())

    def _on_row_clicked(self, result, query_terms):
        """Scrolls the document canvas to center the selected matched block."""
        if not self.doc_model:
            return

        page_no = result["page"]
        page_idx = page_no - 1

        if page_idx < 0 or page_idx >= len(self.canvas.page_layout):
            return

        # Set visual outline highlight on main canvas
        self.canvas.set_highlighted_block(page_idx, (result["x0"], result["y0"], result["x1"], result["y1"]))

        # Switch view back to reader mode
        self.stack.set_visible_child_name("document-view")

        # Defer coordinate calculation and scrolling until the stack widget transition/layout has finished
        def scroll_to_target():
            if not self.doc_model or page_idx >= len(self.canvas.page_layout):
                return False
                
            y_offset, dw, dh, crop_rect = self.canvas.page_layout[page_idx]
            crop_y0 = crop_rect.y0 if crop_rect is not None else 0.0
            
            # Calculate midpoint of the match block relative to cropped Y top boundary
            block_rel_y0 = max(0.0, result["y0"] - crop_y0)
            block_rel_y1 = max(0.0, result["y1"] - crop_y0)
            block_rel_mid = block_rel_y0 + (block_rel_y1 - block_rel_y0) / 2.0
            
            # Convert points to layout pixels (taking zoom and dpi scale multiplier into account)
            scale = self.zoom * self.canvas.dpi_scale_factor
            block_pixel_y = block_rel_mid * scale
            
            # Absolute target Y including the page gap offset
            block_absolute_y = y_offset + self.canvas.page_gap + block_pixel_y

            viewport_h = self.vadjustment.get_page_size()
            if viewport_h <= 1.0:
                viewport_h = 700.0  # Fallback layout height
                
            target_y = block_absolute_y - (viewport_h / 2.0)

            lower = self.vadjustment.get_lower()
            upper = self.vadjustment.get_upper()
            max_y = upper - viewport_h
            target_y = max(lower, min(max_y, target_y))

            self.canvas.grab_focus()
            self.vadjustment.set_value(target_y)
            return False

        GLib.idle_add(scroll_to_target)

    def run_search(self, query):
        query = query or ""
        if not query.strip():
            self._clear_results_box()
            self._last_query = ""
            if self.stack.get_visible_child_name() == "search-view":
                self.stack.set_visible_child_name("document-view")
                self.canvas.grab_focus()
            return

        # Cancel any previous/pending search result renderings by shutting down and recreating the thread pool.
        # This prevents CPU saturation and typing lags when the user types rapidly.
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-portal")

        self._last_query = query
        self._clear_results_box()
        self.stack.set_visible_child_name("search-view")

        # Normalize query words
        import string
        query_terms = {t.strip(string.punctuation).lower() for t in query.strip().split() if t}

        live_results = fts_search(self.index_conn, query, limit=30) if self.index_conn else []

        # 1. Pinned Excerpts Header + Portals
        if self.pinned:
            pinned_label = Gtk.Label(label="📌 Pinned Portals", xalign=0)
            pinned_label.add_css_class("heading")
            pinned_label.set_margin_top(14)
            pinned_label.set_margin_start(16)
            pinned_label.set_margin_bottom(6)
            self.results_box.append(pinned_label)

            for entry in self.pinned.values():
                row = ResultRow(
                    self.doc_model.filepath,
                    self.executor,
                    entry["result"],
                    entry["query_terms"],
                    pinned=True,
                    on_toggle_pin=self._on_toggle_pin,
                    on_row_clicked=self._on_row_clicked
                )
                self.results_box.append(row)
                self.results_box.append(Gtk.Separator())

            if live_results:
                live_label = Gtk.Label(label="Search Results", xalign=0)
                live_label.add_css_class("heading")
                live_label.set_margin_top(10)
                live_label.set_margin_start(16)
                live_label.set_margin_bottom(6)
                self.results_box.append(live_label)

        # 2. Main Search Results List
        if not live_results:
            placeholder = Gtk.Label(label="No matches found.", margin_top=32)
            placeholder.add_css_class("dim-label")
            self.results_box.append(placeholder)
            return

        for i, result in enumerate(live_results):
            already_pinned = result["id"] in self.pinned
            row = ResultRow(
                self.doc_model.filepath,
                self.executor,
                result,
                query_terms,
                pinned=already_pinned,
                on_toggle_pin=self._on_toggle_pin,
                on_row_clicked=self._on_row_clicked
            )
            self.results_box.append(row)
            if i < len(live_results) - 1:
                self.results_box.append(Gtk.Separator())

    def _on_escape(self):
        """Clears the search input and returns to reader view."""
        if self.stack.get_visible_child_name() == "search-view" or self.entry.has_focus():
            self.entry.set_text("")
            self.stack.set_visible_child_name("document-view")
            self.canvas.grab_focus()
            return True
        return False

    # --- Zoom Operations ---

    def set_zoom_level(self, zoom: float, center_x=None, center_y=None):
        if not self.doc_model:
            return
        
        old_zoom = self.zoom
        new_zoom = max(0.25, min(8.0, zoom))
        if old_zoom == new_zoom:
            return

        # Save old scroll positions
        val_h = self.hadjustment.get_value()
        val_v = self.vadjustment.get_value()

        # Default center to viewport midpoint
        if center_x is None or center_y is None:
            viewport_w = self.hadjustment.get_page_size()
            viewport_h = self.vadjustment.get_page_size()
            center_x = val_h + viewport_w / 2
            center_y = val_v + viewport_h / 2

        ratio = new_zoom / old_zoom

        self.zoom = new_zoom
        self.zoom_label.set_label(f"{int(new_zoom * 100)}%")
        
        # Apply to canvas (recomputes layout / bounds)
        self.canvas.set_zoom(new_zoom)

        # Defer updating scroll positions until after the GTK layout pass
        # has updated the adjustment bounds.
        def apply_scroll_deferred():
            lh = self.hadjustment.get_lower()
            uh = self.hadjustment.get_upper()
            ph = self.hadjustment.get_page_size()
            new_val_h_clamped = max(lh, min(uh - ph, val_h + center_x * (ratio - 1)))

            lv = self.vadjustment.get_lower()
            uv = self.vadjustment.get_upper()
            pv = self.vadjustment.get_page_size()
            new_val_v_clamped = max(lv, min(uv - pv, val_v + center_y * (ratio - 1)))

            self.hadjustment.set_value(new_val_h_clamped)
            self.vadjustment.set_value(new_val_v_clamped)
            return False

        GLib.idle_add(apply_scroll_deferred)

    def zoom_in(self):
        self.set_zoom_level(self.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom_level(self.zoom / 1.2)

    def zoom_reset(self):
        self.set_zoom_level(1.0)

    def zoom_fit_width(self):
        if not self.doc_model:
            return

        viewport_w = self.scrolled_window.get_width()
        
        max_w = 0.0
        for i in range(self.doc_model.page_count):
            rect = None
            if self.settings.enabled and self.crop_analyzer:
                rect = self.crop_analyzer.crop_rects[i]
            if rect is None:
                rect = self.doc_model.page_rect(i)
            if rect.width > max_w:
                max_w = rect.width

        if max_w > 0:
            target_zoom = (viewport_w - 24.0) / max_w
            self.set_zoom_level(target_zoom)

    def zoom_fit_page(self):
        if not self.doc_model:
            return

        viewport_w = self.scrolled_window.get_width()
        viewport_h = self.scrolled_window.get_height()

        max_w = 0.0
        max_h = 0.0
        for i in range(self.doc_model.page_count):
            rect = None
            if self.settings.enabled and self.crop_analyzer:
                rect = self.crop_analyzer.crop_rects[i]
            if rect is None:
                rect = self.doc_model.page_rect(i)
            if rect.width > max_w:
                max_w = rect.width
            if rect.height > max_h:
                max_h = rect.height

        if max_w > 0 and max_h > 0:
            target_zoom_w = (viewport_w - 24.0) / max_w
            target_zoom_h = (viewport_h - 24.0) / max_h
            target_zoom = min(target_zoom_w, target_zoom_h)
            self.set_zoom_level(target_zoom)

    # --- Scrolling ---

    def scroll_page(self, forward: bool):
        val = self.vadjustment.get_value()
        page_size = self.vadjustment.get_page_size()
        step = page_size * 0.9
        
        new_val = val + step if forward else val - step

        lower = self.vadjustment.get_lower()
        upper = self.vadjustment.get_upper()
        max_y = upper - page_size
        new_val = max(lower, min(max_y, new_val))
        self.vadjustment.set_value(new_val)

    def scroll_step(self, forward: bool):
        if not self.vadjustment:
            return
        val = self.vadjustment.get_value()
        step = self.vadjustment.get_step_increment()
        if step <= 0:
            step = 40.0
            
        new_val = val + step if forward else val - step
        lower = self.vadjustment.get_lower()
        upper = self.vadjustment.get_upper()
        page_size = self.vadjustment.get_page_size()
        max_y = upper - page_size
        self.vadjustment.set_value(max(lower, min(max_y, new_val)))

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
            on_page_selected=self._on_minimap_page_clicked
        )
        
        dialog.minimap.set_current_page(active_page)
        dialog.present()

    def _on_minimap_page_clicked(self, page_index):
        if not self.doc_model or page_index < 0 or page_index >= self.doc_model.page_count:
            return

        if page_index < len(self.canvas.page_layout):
            y_offset, dw, dh, crop_rect = self.canvas.page_layout[page_index]
            viewport_h = self.vadjustment.get_page_size()
            
            target_y = y_offset + dh / 2 - viewport_h / 2
            
            lower = self.vadjustment.get_lower()
            upper = self.vadjustment.get_upper()
            max_y = upper - viewport_h
            target_y = max(lower, min(max_y, target_y))
            
            self.vadjustment.set_value(target_y)

    # --- Toggles & Settings ---

    def toggle_crop(self):
        self.settings.enabled = not self.settings.enabled
        self._on_crop_settings_updated()

    def _on_crop_btn_toggled(self, btn):
        self.settings.enabled = btn.get_active()
        self._on_crop_settings_updated()

    def _on_settings_btn_clicked(self, btn):
        dialog = SettingsWindow(
            parent_window=self,
            settings=self.settings,
            on_changed=self._on_settings_changed,
            on_reanalyze=self._on_reanalyze
        )
        dialog.present()

    def _on_settings_changed(self):
        self._on_crop_settings_updated()

    def _on_crop_settings_updated(self):
        # Sync stateful action states
        if hasattr(self, "crop_action") and self.crop_action:
            self.crop_action.set_state(GLib.Variant.new_boolean(self.settings.enabled))
        if hasattr(self, "gapless_action") and self.gapless_action:
            self.gapless_action.set_state(GLib.Variant.new_boolean(getattr(self.settings, 'page_gaps', True)))
            
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
        if not self.doc_model:
            return
        page_count = self.doc_model.page_count
        for i in range(page_count):
            if not self.doc_model:
                return
            try:
                self.crop_analyzer.scan_page(i)
                GLib.idle_add(self._on_crop_page_scanned, i)
            except Exception as e:
                print(f"Error scanning page {i} for crop analysis: {e}")

        # Compute crop rectangles once scanning completes
        if self.doc_model:
            self.crop_analyzer.compute_crop_rects(self.settings)
            GLib.idle_add(self._on_crop_analysis_complete)

    def _on_crop_page_scanned(self, page_index):
        self.crop_scanned_count += 1
        total = self.doc_model.page_count
        self.progress_bar.set_fraction(self.crop_scanned_count / total)

    def _on_crop_analysis_complete(self):
        self.progress_bar.set_visible(False)
        self.canvas.on_crop_changed()

    def close(self):
        # Shutdown executors and close connections cleanly
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
        gap_size = 12 if getattr(self.settings, 'page_gaps', True) else 0
        
        shared_css = f"""
            .zoom-floating-box {{
                background-color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 10px;
                padding: 4px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            }}
            .zoom-floating-label {{
                font-size: 10px;
                font-weight: bold;
                color: #2e2e2e;
                margin: 4px 0;
            }}
            .zoom-floating-box button {{
                min-width: 30px;
                min-height: 30px;
                padding: 0;
                border-radius: 6px;
            }}
            headerbar entry.page-input text,
            headerbar entry.page-input > text,
            .page-input text,
            entry.page-input > text {{
                padding-top: 2px;
                padding-bottom: 2px;
                padding-left: 2px;
                padding-right: 2px;
                min-width: 0px;
                min-height: 0px;
            }}
            headerbar entry.page-input,
            .page-input,
            entry.page-input {{
                min-width: 0px;
                min-height: 0px;
                padding: 0;
                margin: 0;
            }}
        """

        if self.backend == "opengl":
            css_data = f"""
                .pdf-canvas {{
                    background-color: transparent;
                    padding: {gap_size}px 0;
                }}
                .page-container {{
                    background-color: transparent;
                    border: {"1px dashed rgba(0, 0, 0, 0.08)" if gap_size > 0 else "none"};
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
                    padding: {gap_size}px 0;
                }}
                .page-container {{
                    background-color: #ffffff;
                    border: {"1px solid #b0b0b0" if gap_size > 0 else "none"};
                    box-shadow: {"0px 3px 6px rgba(0, 0, 0, 0.1)" if gap_size > 0 else "none"};
                }}
                {shared_css}
            """
        self.css_provider.load_from_data(css_data.encode('utf-8'))

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

    def _on_page_input_activate(self, entry):
        if not self.doc_model or not self.canvas.page_layout:
            return
            
        text = entry.get_text().strip()
        try:
            page_num = int(text)
            page_idx = page_num - 1
            if 0 <= page_idx < len(self.canvas.page_layout):
                y_offset = self.canvas.page_layout[page_idx][0]
                target_y = y_offset + self.canvas.page_gap

                lower = self.vadjustment.get_lower()
                upper = self.vadjustment.get_upper()
                viewport_h = self.vadjustment.get_page_size()
                if viewport_h <= 1.0:
                    viewport_h = 700.0
                max_y = upper - viewport_h
                target_y = max(lower, min(max_y, target_y))

                self.vadjustment.set_value(target_y)
                self.canvas.grab_focus()
        except ValueError:
            # Revert to actual page index on parse failure
            self._on_scroll_page_changed(self.vadjustment)

    def _on_gapless_action_activated(self, action, parameter):
        old_state = action.get_state().get_boolean()
        new_state = not old_state
        action.set_state(GLib.Variant.new_boolean(new_state))
        
        self.settings.page_gaps = new_state
        self._on_crop_settings_updated()

    def _on_crop_action_activated(self, action, parameter):
        old_state = action.get_state().get_boolean()
        new_state = not old_state
        action.set_state(GLib.Variant.new_boolean(new_state))
        
        self.settings.enabled = new_state
        self._on_crop_settings_updated()

    def _on_scroll_page_changed(self, adj):
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
            
            # Use WidgetPaintable to capture snapshot of the window content widget
            content_widget = self.get_content()
            if not content_widget:
                print("[Screenshot] Window has no content widget to snapshot", flush=True)
                return False
                
            paintable = Gtk.WidgetPaintable.new(content_widget)
            w = content_widget.get_width()
            h = content_widget.get_height()
            
            rect = Graphene.Rect.alloc()
            rect.init(0.0, 0.0, float(w), float(h))
            
            snapshot = Gtk.Snapshot.new()
            
            # Draw solid opaque white background color to eliminate alpha transparency
            bg_color = Gdk.RGBA()
            bg_color.parse("#ffffff")
            snapshot.append_color(bg_color, rect)
            
            paintable.snapshot(snapshot, float(w), float(h))
            node = snapshot.to_node()
            
            if not node:
                print("[Screenshot] Snapshot yielded empty render node", flush=True)
                return False
                
            renderer = self.get_renderer()
            if not renderer:
                print("[Screenshot] Window has no active renderer yet", flush=True)
                return False
                
            rect = Graphene.Rect.alloc()
            rect.init(0.0, 0.0, float(w), float(h))
            
            texture = renderer.render_texture(node, rect)
            if texture:
                texture.save_to_png(self.screenshot_path)
                print(f"[Screenshot] Programmatic screenshot saved successfully.", flush=True)
            else:
                print("[Screenshot] Failed to render snapshot node to texture.", flush=True)
        except Exception as e:
            print(f"[Screenshot] Error taking screenshot: {e}", flush=True)
        finally:
            self.close()
            if hasattr(self, "app") and self.app:
                self.app.quit()
        return False
