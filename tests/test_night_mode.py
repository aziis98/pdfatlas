import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from pdfatlas.core.settings import CropSettings
from pdfatlas.ui.canvas import PDFCanvas
from pdfatlas.ui.settings import SettingsWindow
from pdfatlas.ui.window import MainWindow


def test_crop_settings_color_scheme():
    s = CropSettings()
    assert s.color_scheme == "system"
    assert s.night_mode_invert == 0.95
    assert s.night_mode_hue_rotate is True

    s2 = CropSettings(color_scheme="dark", night_mode_invert=0.80, night_mode_hue_rotate=False)
    assert s2.color_scheme == "dark"
    assert s2.night_mode_invert == 0.80
    assert s2.night_mode_hue_rotate is False


def test_canvas_set_night_mode():
    canvas = PDFCanvas()
    assert canvas.night_mode is False
    assert canvas.night_mode_invert == 0.95
    assert canvas.night_mode_hue_rotate is True
    assert canvas.gl_canvas.night_mode is False
    assert canvas.gl_canvas.night_mode_invert == 0.95
    assert canvas.gl_canvas.night_mode_hue_rotate is True

    canvas.set_night_mode(True, invert_amount=0.85, hue_rotate=False)
    assert canvas.night_mode is True
    assert canvas.night_mode_invert == 0.85
    assert canvas.night_mode_hue_rotate is False
    assert canvas.gl_canvas.night_mode is True
    assert canvas.gl_canvas.night_mode_invert == 0.85
    assert canvas.gl_canvas.night_mode_hue_rotate is False

    canvas.set_night_mode(False)
    assert canvas.night_mode is False
    assert canvas.gl_canvas.night_mode is False


def test_main_window_toggle_night_mode():
    app = Adw.Application(application_id="com.example.testnight1")
    win = MainWindow(app)

    # Force light mode initially
    win.settings.color_scheme = "light"
    win._apply_color_scheme()
    assert win.night_mode is False
    assert win.settings.color_scheme == "light"
    assert win.canvas.night_mode is False
    state = win.night_mode_action.get_state()
    assert state is not None and state.get_boolean() is False

    # Toggle to dark
    win.toggle_night_mode()
    assert win.night_mode is True
    assert win.settings.color_scheme == "dark"
    assert win.canvas.night_mode is True
    state = win.night_mode_action.get_state()
    assert state is not None and state.get_boolean() is True
    assert Adw.StyleManager.get_default().get_color_scheme() == Adw.ColorScheme.FORCE_DARK

    # Toggle back to light
    win.toggle_night_mode()
    assert win.night_mode is False
    assert win.settings.color_scheme == "light"
    assert win.canvas.night_mode is False
    state = win.night_mode_action.get_state()
    assert state is not None and state.get_boolean() is False
    assert Adw.StyleManager.get_default().get_color_scheme() == Adw.ColorScheme.FORCE_LIGHT


def test_main_window_system_theme_reset():
    app = Adw.Application(application_id="com.example.testnight_sys")
    win = MainWindow(app)

    win.settings.color_scheme = "system"
    win._apply_color_scheme()
    assert win.settings.color_scheme == "system"

    # Toggling when system theme is active sets it to explicit light or dark
    was_dark = win.is_effective_dark()
    win.toggle_night_mode()
    expected_new_scheme = "light" if was_dark else "dark"
    assert win.settings.color_scheme == expected_new_scheme
    assert win.is_effective_dark() == (not was_dark)


def test_main_window_night_mode_action():
    app = Adw.Application(application_id="com.example.testnight2")
    win = MainWindow(app)

    win.settings.color_scheme = "light"
    win._apply_color_scheme()

    win.night_mode_action.activate(None)
    assert win.night_mode is True
    assert win.settings.color_scheme == "dark"
    assert win.canvas.night_mode is True

    win.night_mode_action.activate(None)
    assert win.night_mode is False
    assert win.settings.color_scheme == "light"
    assert win.canvas.night_mode is False


def test_settings_window_color_scheme():
    settings = CropSettings()
    changed = {"called": False}

    def on_changed():
        changed["called"] = True

    dialog = SettingsWindow(None, settings, on_changed=on_changed, on_reanalyze=lambda: None)

    # Test selecting Dark (index 2)
    dialog.theme_combo.set_selected(2)
    assert settings.color_scheme == "dark"
    assert changed["called"] is True

    # Test selecting Light (index 1)
    changed["called"] = False
    dialog.theme_combo.set_selected(1)
    assert settings.color_scheme == "light"
    assert changed["called"] is True

    # Test selecting System (index 0)
    changed["called"] = False
    dialog.theme_combo.set_selected(0)
    assert settings.color_scheme == "system"
    assert changed["called"] is True

    # Test invert percentage change
    changed["called"] = False
    dialog.invert_adj.set_value(80.0)
    assert round(settings.night_mode_invert, 2) == 0.80
    assert changed["called"] is True

    # Test hue rotate toggle
    changed["called"] = False
    dialog.hue_rotate_switch.set_active(False)
    assert settings.night_mode_hue_rotate is False
    assert changed["called"] is True


def test_settings_persistence(tmp_path):
    settings_file = tmp_path / "settings.json"
    s = CropSettings(color_scheme="dark", night_mode_invert=0.88, night_mode_hue_rotate=False)
    s.save(settings_file)
    assert settings_file.exists()

    loaded = CropSettings.load(settings_file)
    assert loaded.color_scheme == "dark"
    assert loaded.night_mode_invert == 0.88
    assert loaded.night_mode_hue_rotate is False
