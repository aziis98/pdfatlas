import gi
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


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

    def _setup_shortcuts(self):
        # File operations
        self._add_shortcut("<Control>o", self.win._open_file_dialog)
        self._add_shortcut("<Control>q", self.win.close)
        self._add_nav_shortcut("q", self.win.close)

        # Focus search bar
        self._add_shortcut("<Control>l", self.win.entry.grab_focus)

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
        self._add_nav_shortcut("w", self.win.zoom_fit_width)
        self._add_nav_shortcut("f", self.win.zoom_fit_page)
        self._add_nav_shortcut("g", self.win.toggle_gapless)

        # Scrolling - Page and Arrow keys
        self._add_shortcut("Page_Up", lambda: self.win.scroll_page(forward=False))
        self._add_shortcut("Page_Down", lambda: self.win.scroll_page(forward=True))
        self._add_shortcut("Up", lambda: self.win.scroll_step(forward=False))
        self._add_shortcut("Down", lambda: self.win.scroll_step(forward=True))
        self._add_shortcut("Left", lambda: self.win.scroll_page(forward=False))
        self._add_shortcut("Right", lambda: self.win.scroll_page(forward=True))

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
