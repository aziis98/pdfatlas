"""Unit tests for the welcome screen tip messages."""


def _load_tips():
    from pdfatlas.ui.welcome import TIP_MESSAGES

    return TIP_MESSAGES


def test_tip_count():
    assert len(_load_tips()) == 29


def test_tips_are_unique_nonempty_strings():
    tips = _load_tips()
    assert all(isinstance(t, str) and t.strip() for t in tips)
    assert len(set(tips)) == len(tips)


def test_tips_have_no_em_dashes():
    tips = _load_tips()
    assert all("\u2014" not in t for t in tips)


def test_tips_are_concise():
    tips = _load_tips()
    assert all(len(t) <= 160 for t in tips)


def test_empty_window_shows_welcome_view():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from pdfatlas.ui.window import MainWindow

    app = Adw.Application(application_id="com.example.testwelcome")
    app.register(None)
    win = None
    try:
        win = MainWindow(app)
        assert win.stack.get_visible_child_name() == "welcome-view"
        assert win.welcome_view.tip_label.get_text() in _load_tips()
    finally:
        if win is not None:
            win.close()