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

    def __init__(self, parent_window, settings: CropSettings, on_changed, on_reanalyze):
        super().__init__()
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

        self.enable_switch = Adw.SwitchRow(title="Enable auto-crop")
        self.enable_switch.set_active(self.settings.enabled)
        self.enable_switch.connect("notify::active", self._on_enable_toggled)

        self.gaps_switch = Adw.SwitchRow(title="Page gaps")
        self.gaps_switch.set_active(self.settings.page_gaps)
        self.gaps_switch.connect("notify::active", self._on_gaps_toggled)

        self._add_group(page, "Appearance", [self.theme_combo, self.invert_spin, self.hue_rotate_switch])
        self._add_group(page, "Cropping", [self.enable_switch, self.gaps_switch])
        self.add(page)

    def _build_crop_page(self):
        page = Adw.PreferencesPage(title="Crop", icon_name="object-select-symbolic")

        # Padding (points)
        self.adj_l = Gtk.Adjustment(
            value=self.settings.min_padding_left,
            lower=0.0,
            upper=100.0,
            step_increment=0.5,
            page_increment=5.0,
        )
        self.adj_r = Gtk.Adjustment(
            value=self.settings.min_padding_right,
            lower=0.0,
            upper=100.0,
            step_increment=0.5,
            page_increment=5.0,
        )
        self.adj_t = Gtk.Adjustment(
            value=self.settings.min_padding_top,
            lower=0.0,
            upper=100.0,
            step_increment=0.5,
            page_increment=5.0,
        )
        self.adj_b = Gtk.Adjustment(
            value=self.settings.min_padding_bottom,
            lower=0.0,
            upper=100.0,
            step_increment=0.5,
            page_increment=5.0,
        )
        self.spin_l = Adw.SpinRow(adjustment=self.adj_l, title="Left")
        self.spin_r = Adw.SpinRow(adjustment=self.adj_r, title="Right")
        self.spin_t = Adw.SpinRow(adjustment=self.adj_t, title="Top")
        self.spin_b = Adw.SpinRow(adjustment=self.adj_b, title="Bottom")
        for spin in (self.spin_l, self.spin_r, self.spin_t, self.spin_b):
            spin.set_digits(1)
        self.adj_l.connect("value-changed", self._on_padding_changed, "min_padding_left")
        self.adj_r.connect("value-changed", self._on_padding_changed, "min_padding_right")
        self.adj_t.connect("value-changed", self._on_padding_changed, "min_padding_top")
        self.adj_b.connect("value-changed", self._on_padding_changed, "min_padding_bottom")
        self._add_group(page, "Padding (points)", [self.spin_l, self.spin_r, self.spin_t, self.spin_b])

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
        self._add_group(page, "Search", [self.layout_combo])

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

    def _on_invert_changed(self, adjustment):
        self.settings.night_mode_invert = adjustment.get_value() / 100.0
        self.on_changed()

    def _on_hue_rotate_toggled(self, row, pspec):
        self.settings.night_mode_hue_rotate = row.get_active()
        self.on_changed()

    def _on_enable_toggled(self, row, pspec):
        self.settings.enabled = row.get_active()
        self.on_changed()

    def _on_gaps_toggled(self, row, pspec):
        self.settings.page_gaps = row.get_active()
        self.on_changed()

    def _on_padding_changed(self, adjustment, attr_name):
        val = adjustment.get_value()
        setattr(self.settings, attr_name, val)
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
