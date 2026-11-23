import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..core.settings import CropSettings


class SettingsWindow(Adw.PreferencesDialog):
    """
    A modern Adwaita preferences dialog organizing settings into pages and groups.
    It reads/writes settings to a CropSettings instance.
    """

    adj_l: Gtk.Adjustment
    adj_r: Gtk.Adjustment
    adj_t: Gtk.Adjustment
    adj_b: Gtk.Adjustment
    spin_l: Gtk.SpinButton
    spin_r: Gtk.SpinButton
    spin_t: Gtk.SpinButton
    spin_b: Gtk.SpinButton
    check_l: Gtk.CheckButton
    check_r: Gtk.CheckButton
    check_t: Gtk.CheckButton
    check_b: Gtk.CheckButton

    def __init__(self, parent_window, settings: CropSettings, on_changed, on_reanalyze):
        super().__init__()
        self.parent_window = parent_window
        self.settings = settings
        self.on_changed = on_changed
        self.on_reanalyze = on_reanalyze

        self.set_title("Settings")
        self.set_search_enabled(False)

        self._popdown_timeout: int | None = None

        self._build_general_page()
        self._build_crop_page()
        self._build_search_page()
        self._build_zoom_page()
        self._build_debug_page()

    def _add_group(self, page: Adw.PreferencesPage, title: str, rows: list) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title)
        for row in rows:
            group.add(row)
        page.add(group)
        return group

    def _build_general_page(self):
        page = Adw.PreferencesPage(title="General", icon_name="preferences-other-symbolic")

        self.theme_combo = Adw.ComboRow(title="Theme")
        self.theme_combo.set_model(Gtk.StringList.new(["System", "Light", "Dark"]))
        scheme_order = ["system", "light", "dark"]
        curr_scheme = self.settings.color_scheme
        if curr_scheme in scheme_order:
            self.theme_combo.set_selected(scheme_order.index(curr_scheme))
        else:
            self.theme_combo.set_selected(0)
        self.theme_combo.connect("notify::selected", self._on_theme_changed)

        self.invert_adj = Gtk.Adjustment(
            value=int(self.settings.night_mode_invert * 100.0),
            lower=0.0,
            upper=100.0,
            step_increment=1.0,
            page_increment=5.0,
        )
        self.invert_spin = Adw.SpinRow(adjustment=self.invert_adj, title="Invert percentage")
        self.invert_spin.set_digits(0)
        self.invert_adj.connect("value-changed", self._on_invert_changed)

        self.hue_rotate_switch = Adw.SwitchRow(title="Preserve colors (hue rotate)")
        self.hue_rotate_switch.set_active(self.settings.night_mode_hue_rotate)
        self.hue_rotate_switch.connect("notify::active", self._on_hue_rotate_toggled)

        self.shadow_switch = Adw.SwitchRow(title="Page shadows")
        self.shadow_switch.set_active(self.settings.page_shadow)
        self.shadow_switch.connect("notify::active", self._on_shadow_toggled)

        active_doc = (
            self.parent_window.get_active_doc_view()
            if self.parent_window and hasattr(self.parent_window, "get_active_doc_view")
            else None
        )
        initial_enabled = active_doc.settings.enabled if (active_doc and active_doc.settings) else self.settings.enabled
        initial_gaps = active_doc.settings.page_gaps if (active_doc and active_doc.settings) else self.settings.page_gaps

        self.enable_switch = Adw.SwitchRow(title="Enable auto-crop")
        self.enable_switch.set_active(initial_enabled)
        self.enable_switch.connect("notify::active", self._on_enable_toggled)

        self.gaps_switch = Adw.SwitchRow(title="Page gaps")
        self.gaps_switch.set_active(initial_gaps)
        self.gaps_switch.connect("notify::active", self._on_gaps_toggled)

        self._add_group(page, "Appearance", [self.theme_combo, self.shadow_switch, self.invert_spin, self.hue_rotate_switch])
        self._add_group(page, "Cropping", [self.enable_switch, self.gaps_switch])
        self.add(page)

    def _build_crop_page(self):
        page = Adw.PreferencesPage(title="Crop", icon_name="object-select-symbolic")

        # Padding (points) with directional crop enable/disable checkboxes
        padding_configs = [
            ("min_padding_left", "Left", 16.0, "adj_l", "spin_l", "check_l"),
            ("min_padding_right", "Right", 16.0, "adj_r", "spin_r", "check_r"),
            ("min_padding_top", "Top", 8.0, "adj_t", "spin_t", "check_t"),
            ("min_padding_bottom", "Bottom", 8.0, "adj_b", "spin_b", "check_b"),
        ]
        padding_rows = []
        for attr, title, default_val, adj_name, spin_name, check_name in padding_configs:
            raw_val = getattr(self.settings, attr)
            is_enabled = raw_val != "original"
            val = float(raw_val) if isinstance(raw_val, (int, float)) else default_val

            adj = Gtk.Adjustment(
                value=val,
                lower=0.0,
                upper=100.0,
                step_increment=0.5,
                page_increment=5.0,
            )
            spin = Gtk.SpinButton(adjustment=adj, digits=1, valign=Gtk.Align.CENTER)
            spin.set_sensitive(is_enabled)

            row = Adw.ActionRow(title=title)
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            check.set_tooltip_text(f"Crop {title.lower()} edge (uncheck to keep original margin)")
            check.set_active(is_enabled)
            row.add_prefix(check)
            row.add_suffix(spin)
            row.set_activatable_widget(check)

            check.connect("toggled", self._on_padding_check_toggled, attr, adj, spin)
            adj.connect("value-changed", self._on_padding_changed, attr, check)

            setattr(self, adj_name, adj)
            setattr(self, spin_name, spin)
            setattr(self, check_name, check)
            padding_rows.append(row)

        self._add_group(page, "Padding (points)", padding_rows)

        # Crop Mode
        self.mode_combo = Adw.ComboRow(title="Crop mode")
        self.mode_combo.set_model(Gtk.StringList.new(["Per page", "Uniform width"]))
        self.mode_combo.set_selected(0 if self.settings.crop_mode == "per_page" else 1)
        self.mode_combo.connect("notify::selected", self._on_mode_changed)
        self._add_group(page, "Crop Mode", [self.mode_combo])

        # Sparse Pages Strategy
        self.sparse_combo = Adw.ComboRow(title="Strategy for sparse pages")
        self.sparse_combo.set_model(Gtk.StringList.new(["Skip", "Use uniform crop", "Crop anyway"]))
        strategy_order = ["skip", "use_uniform", "crop_anyway"]
        if self.settings.sparse_strategy in strategy_order:
            self.sparse_combo.set_selected(strategy_order.index(self.settings.sparse_strategy))
        else:
            self.sparse_combo.set_selected(0)
        self.sparse_combo.connect("notify::selected", self._on_sparse_changed)
        self._add_group(page, "Sparse Pages", [self.sparse_combo])

        # Whitespace Threshold
        self.threshold_adj = Gtk.Adjustment(
            value=self.settings.whitespace_threshold * 100,
            lower=0.0,
            upper=50.0,
            step_increment=1.0,
            page_increment=5.0,
        )
        self.threshold_spin = Adw.SpinRow(adjustment=self.threshold_adj, title="Whitespace threshold (%)")
        self.threshold_adj.connect("value-changed", self._on_threshold_changed)
        self._add_group(page, "Detection", [self.threshold_spin])

        # Re-analyze
        self.reanalyze_btn = Adw.ButtonRow(title="Re-analyze")
        self.reanalyze_btn.connect("activated", self._on_reanalyze_clicked)
        self._add_group(page, "Apply", [self.reanalyze_btn])

        self.add(page)

    def _build_search_page(self):
        page = Adw.PreferencesPage(title="Search", icon_name="system-search-symbolic")

        self.layout_combo = Adw.ComboRow(title="Result layout")
        self.layout_combo.set_model(Gtk.StringList.new(["List", "Grid"]))
        if self.settings.search_layout == "list":
            self.layout_combo.set_selected(0)
        else:
            self.layout_combo.set_selected(1)
        self.layout_combo.connect("notify::selected", self._on_layout_changed)

        self.search_autotrim_row = Adw.SwitchRow(
            title="Auto-trim white margins",
            subtitle="Crop outer page margins in search cards to enlarge text",
        )
        self.search_autotrim_row.set_active(bool(self.settings.search_autotrim))
        self.search_autotrim_row.connect("notify::active", self._on_search_autotrim_toggled)

        self._add_group(page, "Search", [self.layout_combo, self.search_autotrim_row])

        self.add(page)

    def _build_zoom_page(self):
        page = Adw.PreferencesPage(title="Zoom", icon_name="zoom-in-symbolic")

        # Max texture zoom (empty field = Infinity, no cap). The info icon sits
        # right after the label (prefix box), and the entry is right-aligned.
        self.texture_zoom_row = Adw.ActionRow()
        self.texture_zoom_label = Gtk.Label(label="Max texture zoom")
        self.texture_zoom_label.set_xalign(0)

        self.texture_zoom_info = Gtk.Image(icon_name="help-about-symbolic")
        self.texture_zoom_info.add_css_class("dim-label")
        self.texture_zoom_info.set_valign(Gtk.Align.CENTER)

        zoom_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        zoom_title.append(self.texture_zoom_label)
        zoom_title.append(self.texture_zoom_info)
        self.texture_zoom_row.add_prefix(zoom_title)

        self.texture_zoom_popover = Gtk.Popover()
        self.texture_zoom_popover.set_parent(self.texture_zoom_info)
        pop_label = Gtk.Label(label="Leave empty for Infinity — textures render at true zoom with no cap.")
        pop_label.set_wrap(True)
        pop_label.set_max_width_chars(36)
        pop_label.set_margin_start(12)
        pop_label.set_margin_end(12)
        pop_label.set_margin_top(12)
        pop_label.set_margin_bottom(12)
        self.texture_zoom_popover.set_child(pop_label)

        info_motion = Gtk.EventControllerMotion()
        info_motion.connect("enter", self._on_info_enter)
        info_motion.connect("leave", self._on_info_leave)
        self.texture_zoom_info.add_controller(info_motion)
        pop_motion = Gtk.EventControllerMotion()
        pop_motion.connect("enter", self._on_popover_enter)
        pop_motion.connect("leave", self._on_popover_leave)
        pop_label.add_controller(pop_motion)

        self.texture_zoom_entry = Gtk.Entry()
        self.texture_zoom_entry.set_placeholder_text("Infinity")
        self.texture_zoom_entry.set_width_chars(8)
        self.texture_zoom_entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.texture_zoom_entry.set_valign(Gtk.Align.CENTER)
        if self.settings.max_texture_zoom is not None:
            self.texture_zoom_entry.set_text(str(self.settings.max_texture_zoom))
        self.texture_zoom_entry.connect("changed", self._on_texture_zoom_changed)

        self.texture_zoom_row.add_suffix(self.texture_zoom_entry)
        self.texture_zoom_row.set_activatable_widget(self.texture_zoom_entry)

        # Min zoom
        self.min_zoom_adj = Gtk.Adjustment(
            value=self.settings.min_zoom,
            lower=0.01,
            upper=5.0,
            step_increment=0.05,
            page_increment=0.5,
        )
        self.min_zoom_spin = Adw.SpinRow(adjustment=self.min_zoom_adj, title="Min zoom")
        self.min_zoom_spin.set_digits(2)
        self.min_zoom_adj.connect("value-changed", self._on_min_zoom_changed)

        # Max zoom
        self.max_zoom_adj = Gtk.Adjustment(
            value=self.settings.max_zoom,
            lower=1.0,
            upper=200.0,
            step_increment=0.5,
            page_increment=5.0,
        )
        self.max_zoom_spin = Adw.SpinRow(adjustment=self.max_zoom_adj, title="Max zoom")
        self.max_zoom_spin.set_digits(1)
        self.max_zoom_adj.connect("value-changed", self._on_max_zoom_changed)

        self._add_group(page, "Zoom Limits", [self.texture_zoom_row, self.min_zoom_spin, self.max_zoom_spin])

        self.add(page)

    def _build_debug_page(self):
        page = Adw.PreferencesPage(title="Debug", icon_name="applications-engineering-symbolic")

        self.recalc_notes_btn = Gtk.Button(label="Reload", valign=Gtk.Align.CENTER)
        self.recalc_notes_btn.add_css_class("suggested-action")
        self.recalc_notes_btn.connect("clicked", self._on_recalc_notes_clicked)

        self.recalc_notes_row = Adw.ActionRow(
            title="Reload & Recalculate Notes",
            subtitle="Re-extract embedded PDF annotations and recalculate note positions while preserving user notes",
        )
        self.recalc_notes_row.add_suffix(self.recalc_notes_btn)
        self.recalc_notes_row.set_activatable_widget(self.recalc_notes_btn)

        self._add_group(page, "PDF Annotations", [self.recalc_notes_row])
        self.add(page)

    def _on_info_enter(self, controller, x, y):
        self._cancel_popdown()
        self.texture_zoom_popover.popup()

    def _on_info_leave(self, controller):
        self._schedule_popdown()

    def _on_popover_enter(self, controller, x, y):
        self._cancel_popdown()

    def _on_popover_leave(self, controller):
        self._schedule_popdown()

    def _cancel_popdown(self):
        if self._popdown_timeout is not None:
            GLib.source_remove(self._popdown_timeout)
            self._popdown_timeout = None

    def _schedule_popdown(self):
        self._cancel_popdown()
        self._popdown_timeout = GLib.timeout_add(250, self._do_popdown)

    def _do_popdown(self):
        self._popdown_timeout = None
        self.texture_zoom_popover.popdown()
        return False

    def _on_theme_changed(self, row, pspec):
        scheme_order = ["system", "light", "dark"]
        self.settings.color_scheme = scheme_order[row.get_selected()]
        self.on_changed()

    def _on_shadow_toggled(self, row, pspec):
        self.settings.page_shadow = row.get_active()
        self.on_changed()

    def _on_invert_changed(self, adjustment):
        self.settings.night_mode_invert = adjustment.get_value() / 100.0
        self.on_changed()

    def _on_hue_rotate_toggled(self, row, pspec):
        self.settings.night_mode_hue_rotate = row.get_active()
        self.on_changed()

    def _on_enable_toggled(self, row, pspec):
        val = row.get_active()
        self.settings.enabled = val
        if self.parent_window and hasattr(self.parent_window, "get_active_doc_view"):
            active = self.parent_window.get_active_doc_view()
            if active and active.settings:
                active.settings.enabled = val
        self.on_changed()

    def _on_gaps_toggled(self, row, pspec):
        val = row.get_active()
        self.settings.page_gaps = val
        if self.parent_window and hasattr(self.parent_window, "get_active_doc_view"):
            active = self.parent_window.get_active_doc_view()
            if active and active.settings:
                active.settings.page_gaps = val
        self.on_changed()

    def _on_padding_check_toggled(self, check, attr_name, adj, spin):
        is_active = check.get_active()
        spin.set_sensitive(is_active)
        setattr(self.settings, attr_name, adj.get_value() if is_active else "original")
        self.on_changed()

    def _on_padding_changed(self, adjustment, attr_name, check):
        if check.get_active():
            setattr(self.settings, attr_name, adjustment.get_value())
            self.on_changed()

    def _on_mode_changed(self, row, pspec):
        selected = row.get_selected()
        self.settings.crop_mode = "per_page" if selected == 0 else "uniform_width"
        self.on_changed()

    def _on_sparse_changed(self, row, pspec):
        strategy_order = ["skip", "use_uniform", "crop_anyway"]
        self.settings.sparse_strategy = strategy_order[row.get_selected()]
        self.on_changed()

    def _on_threshold_changed(self, adjustment):
        val = adjustment.get_value()
        self.settings.whitespace_threshold = val / 100.0
        self.on_changed()

    def _on_layout_changed(self, row, pspec):
        selected = row.get_selected()
        self.settings.search_layout = "list" if selected == 0 else "grid"
        self.on_changed()

    def _on_search_autotrim_toggled(self, row, pspec):
        self.settings.search_autotrim = bool(row.get_active())
        self.on_changed()

    def _on_texture_zoom_changed(self, entry):
        text = entry.get_text().strip()
        if text == "":
            self.settings.max_texture_zoom = None
            self.on_changed()
            return
        try:
            value = float(text)
        except ValueError:
            return
        self.settings.max_texture_zoom = value
        self.on_changed()

    def _on_min_zoom_changed(self, adjustment):
        self.settings.min_zoom = adjustment.get_value()
        self.on_changed()

    def _on_max_zoom_changed(self, adjustment):
        self.settings.max_zoom = adjustment.get_value()
        self.on_changed()

    def _on_reanalyze_clicked(self, button):
        self.on_reanalyze()

    def _on_recalc_notes_clicked(self, button: Gtk.Button):
        active_doc = (
            self.parent_window.get_active_doc_view()
            if self.parent_window and hasattr(self.parent_window, "get_active_doc_view")
            else None
        )
        doc_model = (
            active_doc.doc_model
            if (active_doc and active_doc.doc_model)
            else getattr(self.parent_window, "doc_model", None)
        )
        db_service = getattr(self.parent_window, "db_service", None) or (
            active_doc.db_service if active_doc else None
        )

        if not doc_model or not getattr(doc_model, "filepath", None) or not db_service:
            self.add_toast(Adw.Toast.new("No open PDF document to reload notes for"))
            return

        button.set_sensitive(False)
        original_label = button.get_label() or "Reload"
        button.set_label("Reloading…")

        def _on_complete(stats: dict[str, int], notes: list[dict]):
            try:
                button.set_sensitive(True)
                button.set_label(original_label)
            except Exception:
                pass

            if hasattr(self.parent_window, "notes"):
                self.parent_window.notes = notes
            if active_doc is not None:
                active_doc.notes = notes
                if hasattr(active_doc, "notes_layer") and active_doc.notes_layer is not None:
                    active_doc.notes_layer.set_notes(notes)
                if hasattr(active_doc, "on_annotations_changed") and callable(active_doc.on_annotations_changed):
                    active_doc.on_annotations_changed()

            if hasattr(self.parent_window, "notes_layer") and self.parent_window.notes_layer is not None:
                self.parent_window.notes_layer.set_notes(notes)

            if hasattr(self.parent_window, "annotations_controller") and self.parent_window.annotations_controller is not None:
                self.parent_window.annotations_controller.update_annotations_button()

            updated = stats.get("updated", 0)
            inserted = stats.get("inserted", 0)
            kept = stats.get("kept", 0)
            if inserted > 0:
                msg = f"Notes reloaded: {updated} updated, {inserted} added, {kept} user notes kept"
            else:
                msg = f"Notes reloaded: {updated} updated, {kept} user notes kept"

            try:
                self.add_toast(Adw.Toast.new(msg))
            except Exception:
                pass

            if self.parent_window and hasattr(self.parent_window, "_show_toast"):
                self.parent_window._show_toast(msg)

        db_service.reload_embedded_notes(doc_model.filepath, _on_complete)

