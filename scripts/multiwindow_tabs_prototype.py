#!/usr/bin/env python3
"""
multiwindow_tabs_prototype.py — Multi-window tab drag & drop prototype.

Prototype to answer the "single process vs multi process" question for a
browser-like multi-window tabbed PDF viewer.

Chosen model: single process, one Adw.TabView per window.

Adw.TabView already implements all of the hard DnD plumbing natively:

  * tab reordering inside a window,
  * dragging a tab into another window's tab bar (cross-window transfer),
  * dragging a tab out onto the desktop, which emits ::create-window so the
    app only has to build a new window and return its TabView.

A multi-process model would need cross-process DnD (X11 selection / Wayland
data device) plus a transport such as D-Bus to move document state between
processes — lots of fragile plumbing for no benefit, so this prototype is
single-process.

By default the script daemonizes (orphans itself) so the terminal prompt
returns immediately. The app is a unique GApplication: the first invocation
becomes the single window-owning daemon process, and every later invocation
activates it over D-Bus (opening another window in that same process), so tabs
can always be dragged between all open windows. The daemon exits once all of
its windows are closed. Use --foreground to stay attached to the terminal.

Run:
    uv run scripts/multiwindow_tabs_prototype.py
    uv run scripts/multiwindow_tabs_prototype.py --foreground
"""

import argparse
import os
import random
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


def daemonize() -> None:
    """Spawn a detached copy of the script and exit the parent."""
    env = dict(os.environ)
    env["PDFATLAS_TABS_DAEMON"] = "1"
    with open(os.devnull, "w") as devnull:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
            env=env,
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )
    os._exit(0)


class TabWindow(Adw.ApplicationWindow):
    """One window = one Adw.TabView with a two-row headerbar."""

    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.app = app
        self.set_title("Multi-window Tabs")
        self.set_default_size(640, 420)

        self.view = Adw.TabView()
        self.view.connect("create-window", self._on_create_window)
        self.view.connect("page-detached", self._on_page_detached)
        self.view.connect("notify::selected-page", self._on_selection_changed)

        self.title_label = Gtk.Label(label="")
        self.title_label.add_css_class("title-3")

        header = Adw.HeaderBar()
        header.props.title_widget = self.title_label

        new_window_btn = Gtk.Button.new_from_icon_name("window-new-symbolic")
        new_window_btn.set_tooltip_text("New Window")
        new_window_btn.connect("clicked", lambda *_: self._new_window())
        header.pack_start(new_window_btn)

        self.window_count_label = Gtk.Label(label="1")
        self.window_count_label.add_css_class("dim-label")
        self.window_count_label.set_tooltip_text("Open windows")
        header.pack_start(self.window_count_label)

        new_tab_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        new_tab_btn.set_tooltip_text("New Tab")
        new_tab_btn.connect("clicked", lambda *_: self.new_tab())
        header.pack_end(new_tab_btn)

        tab_bar = Adw.TabBar(view=self.view)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(tab_bar)
        toolbar.set_content(self.view)

        self.set_content(toolbar)
        self._add_shortcuts()
        self._on_selection_changed()
        self.app.connect("window-added", self._on_windows_changed)
        self.app.connect("window-removed", self._on_windows_changed)
        self._on_windows_changed()

    # -- public API ---------------------------------------------------------

    def new_tab(self) -> Adw.TabPage:
        tab_id = random.randint(100000, 999999)
        label = Gtk.Label(label=str(tab_id))
        label.add_css_class("title-1")
        label.set_halign(Gtk.Align.CENTER)
        label.set_valign(Gtk.Align.CENTER)
        page = self.view.append(label)
        page.props.title = f"Tab {tab_id}"
        self.view.set_selected_page(page)
        return page

    # -- signal handlers ----------------------------------------------------

    def _on_create_window(self, view: Adw.TabView) -> Adw.TabView:
        """Dragging a tab onto the desktop: build a new window for it."""
        win = TabWindow(app=self.app)
        win.present()
        return win.view

    def _on_page_detached(
        self, view: Adw.TabView, page: Adw.TabPage, position: int
    ) -> None:
        if view.get_n_pages() == 0:
            self.close()

    def _on_selection_changed(self, *args) -> None:
        page = self.view.get_selected_page()
        self.title_label.set_label(page.props.title if page else "")

    def _on_windows_changed(self, *args) -> None:
        self.window_count_label.set_label(str(len(self.app.get_windows())))

    def _new_window(self) -> None:
        win = TabWindow(app=self.app)
        win.present()

    # -- shortcuts ----------------------------------------------------------

    def _add_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.MANAGED)
        controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>t"),
                Gtk.CallbackAction.new(self._shortcut_new_tab),
            )
        )
        controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>n"),
                Gtk.CallbackAction.new(self._shortcut_new_window),
            )
        )
        controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>w"),
                Gtk.CallbackAction.new(self._shortcut_close_tab),
            )
        )
        self.add_controller(controller)

    def _shortcut_new_tab(self) -> bool:
        self.new_tab()
        return True

    def _shortcut_new_window(self) -> bool:
        self._new_window()
        return True

    def _shortcut_close_tab(self) -> bool:
        page = self.view.get_selected_page()
        if page is not None:
            self.view.close_page(page)
        return True


class PrototypeApp(Adw.Application):
    """Unique GApplication so every invocation shares one window-owning daemon.

    The application_id is registered as a D-Bus name: the first invocation
    becomes the primary process that owns all windows, and every later
    invocation is routed to it via the session bus (do_activate runs in the
    primary), keeping tab drag-and-drop possible between *all* windows.
    """

    def __init__(self):
        super().__init__(application_id="com.aziis98.pdfatlas.tabprototype")
        self.connect("window-removed", self._on_window_removed)

    def _on_window_removed(self, app, win) -> None:
        if not app.get_windows():
            self.quit()

    def do_activate(self):
        win = TabWindow(app=self)
        for _ in range(3):
            win.new_tab()
        win.present()


def main():
    parser = argparse.ArgumentParser(description="Multi-window tab detach prototype")
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Keep attached to the terminal instead of daemonizing",
    )
    args = parser.parse_args(sys.argv[1:])

    if not args.foreground and not os.environ.get("PDFATLAS_TABS_DAEMON"):
        daemonize()

    app = PrototypeApp()
    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()
