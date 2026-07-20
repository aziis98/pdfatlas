import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..core.settings import CropSettings


class SettingsWindow(Gtk.Window):
    """
    A GTK4 modal Window containing crop controls and settings.
    It reads/writes settings to a CropSettings instance.
    """

    def __init__(self, parent_window, settings: CropSettings, on_changed, on_reanalyze):
        super().__init__(
            title="Crop Settings", transient_for=parent_window, modal=True, destroy_with_parent=True
        )
        self.settings = settings
        self.on_changed = on_changed
        self.on_reanalyze = on_reanalyze

        self.set_default_size(340, 480)

        # Set titlebar using Adw.HeaderBar for native Adwaita look
        header = Adw.HeaderBar()
        self.set_titlebar(header)

        # Main vertical layout container
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.main_box.set_margin_start(16)
        self.main_box.set_margin_end(16)
        self.main_box.set_margin_top(16)
        self.main_box.set_margin_bottom(16)
        self.set_child(self.main_box)

        # 1. Enable Switch
        self.switch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.switch_label = Gtk.Label(label="Enable auto-crop")
        self.switch_label.set_hexpand(True)
        self.switch_label.set_xalign(0)
        self.enable_switch = Gtk.Switch()
        self.enable_switch.set_active(self.settings.enabled)
        self.enable_switch.connect("state-set", self._on_enable_toggled)
        self.switch_box.append(self.switch_label)
        self.switch_box.append(self.enable_switch)
        self.main_box.append(self.switch_box)

        # 1b. Page Gaps Switch
        self.gaps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.gaps_label = Gtk.Label(label="Page gaps")
        self.gaps_label.set_hexpand(True)
        self.gaps_label.set_xalign(0)
        self.gaps_switch = Gtk.Switch()
        self.gaps_switch.set_active(getattr(self.settings, "page_gaps", True))
        self.gaps_switch.connect("state-set", self._on_gaps_toggled)
        self.gaps_box.append(self.gaps_label)
        self.gaps_box.append(self.gaps_switch)
        self.main_box.append(self.gaps_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Padding Fields (Gtk.Grid)
        self.padding_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.padding_title = Gtk.Label(label="Padding (points)")
        self.padding_title.set_xalign(0)
        self.padding_title.set_markup("<b>Padding (points)</b>")
        self.padding_box.append(self.padding_title)

        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(10)
        self.grid.set_row_spacing(6)

        # Labels
        self.lbl_l = Gtk.Label(label="Left", xalign=0)
        self.lbl_r = Gtk.Label(label="Right", xalign=0)
        self.lbl_t = Gtk.Label(label="Top", xalign=0)
        self.lbl_b = Gtk.Label(label="Bottom", xalign=0)

        # Spin adjustments (lower, upper, step)
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

        self.spin_l = Gtk.SpinButton(adjustment=self.adj_l, digits=1)
        self.spin_r = Gtk.SpinButton(adjustment=self.adj_r, digits=1)
        self.spin_t = Gtk.SpinButton(adjustment=self.adj_t, digits=1)
        self.spin_b = Gtk.SpinButton(adjustment=self.adj_b, digits=1)

        self.spin_l.connect("value-changed", self._on_padding_changed, "min_padding_left")
        self.spin_r.connect("value-changed", self._on_padding_changed, "min_padding_right")
        self.spin_t.connect("value-changed", self._on_padding_changed, "min_padding_top")
        self.spin_b.connect("value-changed", self._on_padding_changed, "min_padding_bottom")

        # Grid placement: Left, Top (0, 0), Right, Top (2, 0), Left, Bottom (0, 1), etc.
        self.grid.attach(self.lbl_l, 0, 0, 1, 1)
        self.grid.attach(self.spin_l, 1, 0, 1, 1)
        self.grid.attach(self.lbl_r, 2, 0, 1, 1)
        self.grid.attach(self.spin_r, 3, 0, 1, 1)
        self.grid.attach(self.lbl_t, 0, 1, 1, 1)
        self.grid.attach(self.spin_t, 1, 1, 1, 1)
        self.grid.attach(self.lbl_b, 2, 1, 1, 1)
        self.grid.attach(self.spin_b, 3, 1, 1, 1)

        self.padding_box.append(self.grid)
        self.main_box.append(self.padding_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 3. Crop Mode (Radio-like CheckButtons)
        self.mode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.mode_title = Gtk.Label(xalign=0)
        self.mode_title.set_markup("<b>Crop Mode</b>")
        self.mode_box.append(self.mode_title)

        self.btn_per_page = Gtk.CheckButton(label="Per page")
        self.btn_uniform = Gtk.CheckButton(label="Uniform width")
        self.btn_uniform.set_group(self.btn_per_page)

        if self.settings.crop_mode == "per_page":
            self.btn_per_page.set_active(True)
        else:
            self.btn_uniform.set_active(True)

        self.btn_per_page.connect("toggled", self._on_mode_toggled, "per_page")
        self.btn_uniform.connect("toggled", self._on_mode_toggled, "uniform_width")

        self.mode_box.append(self.btn_per_page)
        self.mode_box.append(self.btn_uniform)
        self.main_box.append(self.mode_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 4. Sparse Pages Strategy
        self.sparse_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sparse_title = Gtk.Label(xalign=0)
        self.sparse_title.set_markup("<b>Sparse Pages (&lt; 15% content)</b>")
        self.sparse_box.append(self.sparse_title)

        self.btn_skip = Gtk.CheckButton(label="Skip")
        self.btn_use_uniform = Gtk.CheckButton(label="Use uniform crop")
        self.btn_crop_anyway = Gtk.CheckButton(label="Crop anyway")
        self.btn_use_uniform.set_group(self.btn_skip)
        self.btn_crop_anyway.set_group(self.btn_skip)

        if self.settings.sparse_strategy == "skip":
            self.btn_skip.set_active(True)
        elif self.settings.sparse_strategy == "use_uniform":
            self.btn_use_uniform.set_active(True)
        else:
            self.btn_crop_anyway.set_active(True)

        self.btn_skip.connect("toggled", self._on_sparse_toggled, "skip")
        self.btn_use_uniform.connect("toggled", self._on_sparse_toggled, "use_uniform")
        self.btn_crop_anyway.connect("toggled", self._on_sparse_toggled, "crop_anyway")

        self.sparse_box.append(self.btn_skip)
        self.sparse_box.append(self.btn_use_uniform)
        self.sparse_box.append(self.btn_crop_anyway)
        self.main_box.append(self.sparse_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 5. Whitespace Threshold
        self.threshold_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.threshold_label = Gtk.Label(label="Whitespace threshold", xalign=0)
        self.threshold_label.set_hexpand(True)
        self.threshold_adj = Gtk.Adjustment(
            value=self.settings.whitespace_threshold * 100,
            lower=0.0,
            upper=50.0,
            step_increment=1.0,
            page_increment=5.0,
        )
        self.threshold_spin = Gtk.SpinButton(adjustment=self.threshold_adj, digits=0)
        self.threshold_spin.connect("value-changed", self._on_threshold_changed)
        self.threshold_suffix = Gtk.Label(label="%")

        self.threshold_box.append(self.threshold_label)
        self.threshold_box.append(self.threshold_spin)
        self.threshold_box.append(self.threshold_suffix)
        self.main_box.append(self.threshold_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 5b. Search Result Layout Dropdown
        self.layout_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.layout_label = Gtk.Label(label="Search result layout", xalign=0)
        self.layout_label.set_hexpand(True)

        self.layout_dropdown = Gtk.DropDown.new_from_strings(["List", "Grid"])
        if getattr(self.settings, "search_layout", "grid") == "list":
            self.layout_dropdown.set_selected(0)
        else:
            self.layout_dropdown.set_selected(1)
        self.layout_dropdown.connect("notify::selected", self._on_layout_changed)

        self.layout_box.append(self.layout_label)
        self.layout_box.append(self.layout_dropdown)
        self.main_box.append(self.layout_box)

        self.main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 6. Re-analyze Button
        self.reanalyze_btn = Gtk.Button(label="Re-analyze")
        self.reanalyze_btn.connect("clicked", self._on_reanalyze_clicked)
        self.main_box.append(self.reanalyze_btn)

    def _on_enable_toggled(self, switch, state):
        self.settings.enabled = state
        self.on_changed()
        return False

    def _on_gaps_toggled(self, switch, state):
        self.settings.page_gaps = state
        self.on_changed()
        return False

    def _on_padding_changed(self, spin, attr_name):
        val = spin.get_value()
        setattr(self.settings, attr_name, val)
        self.on_changed()

    def _on_mode_toggled(self, button, mode_val):
        if button.get_active():
            self.settings.crop_mode = mode_val
            self.on_changed()

    def _on_sparse_toggled(self, button, strategy_val):
        if button.get_active():
            self.settings.sparse_strategy = strategy_val
            self.on_changed()

    def _on_threshold_changed(self, spin):
        val = spin.get_value()
        self.settings.whitespace_threshold = val / 100.0
        self.on_changed()

    def _on_layout_changed(self, dropdown, pspec):
        selected = dropdown.get_selected()
        if selected == 0:
            self.settings.search_layout = "list"
        else:
            self.settings.search_layout = "grid"
        self.on_changed()

    def _on_reanalyze_clicked(self, button):
        self.on_reanalyze()
