import gi
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk


class ShortcutsController:
    """
    Controller handling keyboard shortcuts, keybindings, and gesture triggers.
    """

    def __init__(self, main_window: "MainWindow"):
        self.win = main_window
        self.shortcut_controller = Gtk.ShortcutController.new()
        self.shortcut_controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        self.win.add_controller(self.shortcut_controller)

        self._setup_shortcuts()
        self._setup_nav_key_capture()

    def _setup_shortcuts(self):
        # File operations
        self._add_shortcut("<Control>o", self.win._open_file_dialog)
        self._add_shortcut("<Control>q", self.win.close)
        self._add_nav_shortcut("q", self.win.close)

        # Focus search bar [Ctrl+F]
        self._add_shortcut("<Control>f", self.win.entry.grab_focus)
        self._add_shortcut("<Control>F", self.win.entry.grab_focus)

        # Zoom keys
        self._add_shortcut("plus", self.win.zoom_in)
        self._add_shortcut("<Shift>plus", self.win.zoom_in)
        self._add_shortcut("equal", self.win.zoom_in)
        self._add_shortcut("<Shift>equal", self.win.zoom_in)
        self._add_shortcut("KP_Add", self.win.zoom_in)
        self._add_shortcut("minus", self.win.zoom_out)
        self._add_shortcut("KP_Subtract", self.win.zoom_out)
        self._add_shortcut("<Control>0", self.win.zoom_reset)

        # Modal window / mode / zoom fitting triggers
        self._add_nav_shortcut("m", self.win.toggle_minimap)
        self._add_nav_shortcut("c", self.win.toggle_crop)
        self._add_nav_shortcut("n", self.win.toggle_night_mode)
        self._add_nav_shortcut("w", self.win.zoom_fit_width)
        self._add_nav_shortcut("f", self.win.zoom_fit_page)
        self._add_nav_shortcut("g", self.win.toggle_gapless)

        # Scrolling - Arrow / Page keys are handled at the capture phase in
        # `_setup_nav_key_capture` because GTK consumes them for focus navigation
        # before the shortcut controller sees them.

        # Scrolling - Vim Keys (h & l: viewport height; j & k: step scroll)
        self._add_nav_shortcut("h", lambda: self.win.scroll_page(forward=False))
        self._add_nav_shortcut("j", lambda: self.win.scroll_step(forward=True))
        self._add_nav_shortcut("k", lambda: self.win.scroll_step(forward=False))
        self._add_nav_shortcut("l", lambda: self.win.scroll_page(forward=True))

        # Close/clear search or exit minimap
        self._add_shortcut("Escape", self.win._on_escape)

        # Copy selected text (only when canvas has focus, not entry fields)
        self._add_nav_shortcut("<Control>c", self.win._copy_tex_to_clipboard)
        self._add_nav_shortcut("<Control><Shift>c", self.win._copy_pdf_text_to_clipboard)
        self._add_nav_shortcut("<Control>C", self.win._copy_pdf_text_to_clipboard)

    def _add_shortcut(self, trigger_str, callback):
        trigger = Gtk.ShortcutTrigger.parse_string(trigger_str)
        action = Gtk.CallbackAction.new(lambda w, a: (callback(), True)[1])
        shortcut = Gtk.Shortcut.new(trigger, action)
        self.shortcut_controller.add_shortcut(shortcut)

    def _add_nav_shortcut(self, trigger_str, callback):
        trigger = Gtk.ShortcutTrigger.parse_string(trigger_str)

        def _handler(w, a):
            if self.win._is_entry_focused():
                return False
            callback()
            return True

        action = Gtk.CallbackAction.new(_handler)
        shortcut = Gtk.Shortcut.new(trigger, action)
        self.shortcut_controller.add_shortcut(shortcut)

    def _setup_nav_key_capture(self):
        """
        Arrow and page keys are consumed by GTK's default focus navigation before
        the GLOBAL shortcut controller gets a chance to handle them (which is why
        pressing Up moves focus to the search entry instead of scrolling). Intercept
        them at the capture phase so they scroll the document unless an entry field
        has focus, in which case they keep their normal editing behavior.
        """
        controller = Gtk.EventControllerKey.new()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_nav_key_pressed)
        self.win.add_controller(controller)

    def _on_nav_key_pressed(self, controller, keyval, keycode, state):
        if self.win._is_entry_focused():
            return False

        if keyval == Gdk.KEY_Up:
            self.win.scroll_step(forward=False)
        elif keyval == Gdk.KEY_Down:
            self.win.scroll_step(forward=True)
        elif keyval == Gdk.KEY_Left:
            self.win.scroll_page(forward=False)
        elif keyval == Gdk.KEY_Right:
            self.win.scroll_page(forward=True)
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            self.win.scroll_page(forward=False)
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
            self.win.scroll_page(forward=True)
        else:
            return False

        return True
