from __future__ import annotations

import itertools

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from pdfatlas.ui.window import MainWindow


_APP_IDS = itertools.count(1)


def _make_app() -> Adw.Application:
    app = Adw.Application(application_id=f"com.example.testtabmenu{next(_APP_IDS)}")
    app.register(None)
    return app


def _add_page(win: MainWindow, title: str) -> None:
    page = win.tab_view.append(Gtk.Label(label=title))
    page.props.title = title


def _sections(menu: Gio.MenuModel) -> list[Gio.MenuModel]:
    sections: list[Gio.MenuModel] = []
    for i in range(menu.get_n_items()):
        link = menu.get_item_link(i, Gio.MENU_LINK_SECTION)
        assert link is not None
        sections.append(link)
    return sections


def _labels(menu: Gio.MenuModel) -> list[str]:
    labels: list[str] = []
    for i in range(menu.get_n_items()):
        value = menu.get_item_attribute_value(i, "label", None)
        labels.append(value.get_string() if value is not None else "")
    return labels


def _all_items(menu: Gio.MenuModel) -> list[tuple[Gio.MenuModel, int]]:
    """Flatten a menu model, expanding link sections but not submenus."""
    entries: list[tuple[Gio.MenuModel, int]] = []
    for i in range(menu.get_n_items()):
        section = menu.get_item_link(i, Gio.MENU_LINK_SECTION)
        if section is not None:
            entries.extend((section, j) for j in range(section.get_n_items()))
        else:
            entries.append((menu, i))
    return entries


def test_setup_menu_lists_windows_with_cwd_and_titles():
    app = _make_app()
    win1 = MainWindow(app, cwd="/home/tester/projects/pdfatlas")
    win2 = MainWindow(app, cwd="/home/tester/downloads/papers")

    _add_page(win1, "attention.pdf")
    _add_page(win1, "notes.pdf")
    _add_page(win2, "diffusion.pdf")
    _add_page(win2, "survey.pdf")
    _add_page(win2, "extra.pdf")

    page = win1.tab_view.get_nth_page(0)
    win1.tab_view.emit("setup-menu", page)
    assert win1.tab_controller._context_page is page

    menu = win1.tab_controller._tab_menu
    sections = _sections(menu)
    assert len(sections) == 3

    move_section = sections[1]
    assert _labels(move_section) == ["Move to Window…"]
    submenu = move_section.get_item_link(0, Gio.MENU_LINK_SUBMENU)
    assert submenu is not None

    submenu_sections = _sections(submenu)
    assert len(submenu_sections) == 2

    others = submenu_sections[0]
    assert others.get_n_items() == 1
    other_label = _labels(others)[0]
    assert f"Window {win2.window_uid}" in other_label
    assert "downloads/papers" in other_label
    assert "diffusion.pdf, survey.pdf" in other_label
    assert "+1 more" in other_label
    assert other_label.count("\n") == 1

    markup = others.get_item_attribute_value(0, "use-markup", None)
    assert markup is not None and markup.get_string() == "true"

    action_name = others.get_item_attribute_value(0, "action", None)
    target = others.get_item_attribute_value(0, "target", None)
    assert action_name is not None
    assert action_name.get_string() == "win.move-tab-to-window"
    assert target is not None
    assert target.get_string() == win2.window_uid

    current = submenu_sections[1]
    current_label = _labels(current)[0]
    assert f"Window {win1.window_uid}" in current_label
    assert "projects/pdfatlas" in current_label
    assert "attention.pdf, notes.pdf" in current_label
    assert "(this window)" in current_label
    assert current.get_item_attribute_value(0, "action", None) is None

    win1.tab_view.emit("setup-menu", None)
    assert win1.tab_controller._context_page is None

    win1.close()
    win2.close()


def test_setup_menu_reports_no_other_windows():
    app = _make_app()
    win = MainWindow(app, cwd="/home/tester/documents")
    _add_page(win, "paper.pdf")

    win.tab_view.emit("setup-menu", win.tab_view.get_nth_page(0))
    submenu = _sections(win.tab_controller._tab_menu)[1].get_item_link(
        0, Gio.MENU_LINK_SUBMENU
    )
    assert submenu is not None
    assert submenu.get_n_items() == 1
    assert _labels(submenu) == ["No other windows"]
    assert submenu.get_item_attribute_value(0, "action", None) is None

    win.close()


def test_header_menu_exposes_move_to_window_submenu():
    app = _make_app()
    win1 = MainWindow(app, cwd="/home/tester/one")
    win2 = MainWindow(app, cwd="/home/tester/two")
    _add_page(win1, "a.pdf")
    _add_page(win2, "b.pdf")

    model = win1.menu_button.get_menu_model()
    assert model is not None
    entries = _all_items(model)
    labels: list[str] = []
    for item_model, item_index in entries:
        value = item_model.get_item_attribute_value(item_index, "label", None)
        labels.append(value.get_string() if value is not None else "")
    assert "Move to Window…" in labels

    move_model, move_index = entries[labels.index("Move to Window…")]
    submenu = move_model.get_item_link(move_index, Gio.MENU_LINK_SUBMENU)
    assert submenu is not None
    assert submenu is win1.tab_controller.header_move_menu

    win1.tab_controller.refresh_header_move_submenu()
    other_labels = [
        label for section in _sections(submenu) for label in _labels(section)
    ]
    assert any(f"Window {win2.window_uid}" in label for label in other_labels)

    # The header menu model is refreshed when the options popover opens.
    _add_page(win2, "c.pdf")
    win1.tab_controller.refresh_header_move_submenu()
    refreshed = [label for section in _sections(submenu) for label in _labels(section)]
    assert any("b.pdf, c.pdf" in label for label in refreshed)

    win1.close()
    win2.close()


def test_move_selected_tab_without_context_page():
    app = _make_app()
    win1 = MainWindow(app, cwd="/home/tester/one")
    win2 = MainWindow(app, cwd="/home/tester/two")
    _add_page(win1, "stays.pdf")
    _add_page(win1, "moves.pdf")

    page = win1.tab_view.get_nth_page(1)
    win1.tab_view.set_selected_page(page)
    win1.tab_controller._context_page = None

    win1.activate_action(
        "win.move-tab-to-window", GLib.Variant.new_string(win2.window_uid)
    )

    assert win1.tab_view.get_n_pages() == 1
    assert win2.tab_view.get_n_pages() == 1
    assert win2.tab_view.get_nth_page(0) is page

    win1.close()
    win2.close()


def test_close_other_tabs_keeps_context_page():
    app = _make_app()
    win = MainWindow(app, cwd="/home/tester/docs")
    _add_page(win, "one.pdf")
    _add_page(win, "two.pdf")
    _add_page(win, "three.pdf")

    keep = win.tab_view.get_nth_page(1)
    win.tab_view.emit("setup-menu", keep)
    win.activate_action("win.close-other-tabs", None)

    assert win.tab_view.get_n_pages() == 1
    assert win.tab_view.get_nth_page(0) is keep

    win.close()


def test_move_tab_to_window_transfers_page():
    app = _make_app()
    win1 = MainWindow(app, cwd="/home/tester/one")
    win2 = MainWindow(app, cwd="/home/tester/two")

    _add_page(win1, "stays.pdf")
    _add_page(win1, "moves.pdf")
    assert win1.tab_view.get_n_pages() == 2

    page = win1.tab_view.get_nth_page(1)
    win1.tab_view.emit("setup-menu", page)
    win1.activate_action(
        "win.move-tab-to-window", GLib.Variant.new_string(win2.window_uid)
    )

    assert win1.tab_view.get_n_pages() == 1
    assert win2.tab_view.get_n_pages() == 1
    assert win2.tab_view.get_nth_page(0) is page
    assert win1.tab_controller._context_page is None

    win1.close()
    win2.close()
