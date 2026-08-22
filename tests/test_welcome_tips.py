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


def test_welcome_view_shows_all_recents_with_scroller():
    import tempfile
    from pathlib import Path
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from unittest.mock import MagicMock
    from gi.repository import Adw, Gtk
    from pdfatlas.core.pdf_source import PdfSource, RecentFilesManager
    from pdfatlas.ui.welcome import WelcomeView

    Adw.init()
    with tempfile.TemporaryDirectory() as tmpdir:
        recent_file = Path(tmpdir) / "recent.json"
        recents = RecentFilesManager(path=recent_file)
        for i in range(8):
            recents.add(PdfSource(source_type="file", uri=f"/home/user/documents/doc_{i}.pdf", display_name=f"Test Document {i}"))

        win_mock = MagicMock()
        welcome = WelcomeView(win_mock)
        welcome.refresh(recents)

        # Scrolled window configuration
        assert welcome.recent_scrolled.get_visible() is True
        assert welcome.recent_empty_label.get_visible() is False
        assert welcome.recent_scrolled.get_propagate_natural_height() is True
        assert welcome.recent_scrolled.get_max_content_height() == 265
        assert welcome.recent_scrolled.get_hexpand() is True
        h_policy, v_policy = welcome.recent_scrolled.get_policy()
        assert h_policy == Gtk.PolicyType.NEVER
        assert v_policy == Gtk.PolicyType.AUTOMATIC

        # All 8 items are rendered in recent_list
        row_count = 0
        child = welcome.recent_list.get_first_child()
        while child is not None:
            row_count += 1
            child = child.get_next_sibling()
        assert row_count == 8


def test_recents_ignores_pytest_test_paths():
    import tempfile
    from pathlib import Path
    from pdfatlas.core.pdf_source import PdfSource, RecentFilesManager, is_test_path

    assert is_test_path("/tmp/pytest-of-user/pytest-123/sample.pdf") is True
    assert is_test_path("/home/user/papers/attention.pdf") is False

    with tempfile.TemporaryDirectory() as tmpdir:
        recent_file = Path(tmpdir) / "recent.json"
        recents = RecentFilesManager(path=recent_file)
        recents.add(PdfSource("file", "/tmp/pytest-of-user/pytest-1/test.pdf", "Test Doc"))
        recents.add(PdfSource("file", "/home/user/papers/real.pdf", "Real Paper"))

        entries = recents.get_recent()
        assert len(entries) == 1
        assert entries[0].display_name == "Real Paper"


def test_recents_unlimited_by_default():
    import tempfile
    from pathlib import Path
    from pdfatlas.core.pdf_source import PdfSource, RecentFilesManager

    with tempfile.TemporaryDirectory() as tmpdir:
        recent_file = Path(tmpdir) / "recent.json"
        recents = RecentFilesManager(path=recent_file)
        for i in range(25):
            recents.add(PdfSource("file", f"/home/user/papers/paper_{i}.pdf", f"Paper {i}"))

        entries = recents.get_recent()
        assert len(entries) == 25
        assert entries[0].display_name == "Paper 24"
        assert entries[-1].display_name == "Paper 0"