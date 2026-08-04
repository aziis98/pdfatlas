from unittest.mock import MagicMock


class DummyProgressManager:
    """Simplified progress manager logic for unit testing progress state tracking."""

    def __init__(self):
        self.progress_bar = MagicMock()
        self.progress_label = MagicMock()
        self.progress_card_box = MagicMock()
        self.link_preview_box = MagicMock()
        self.link_preview_card_box = MagicMock()
        self.link_preview_card_box.get_visible.return_value = False
        self._active_progress_tasks = {}

    def _show_progress(self, task_id: str, description: str, fraction: float):
        pct = int(round(fraction * 100))
        formatted_desc = f"{description} ({pct}%)" if fraction > 0 else description

        self._active_progress_tasks[task_id] = {
            "description": description,
            "formatted": formatted_desc,
            "fraction": fraction,
        }

        max_fraction = max(t["fraction"] for t in self._active_progress_tasks.values())
        self.progress_bar.set_fraction(max_fraction)
        self.progress_bar.set_visible(True)

        latest_task = list(self._active_progress_tasks.values())[-1]
        progress_text = latest_task["formatted"]

        self.progress_label.set_label(progress_text)
        self.progress_card_box.set_visible(True)
        self.link_preview_box.set_visible(True)

    def _hide_progress(self, task_id: str):
        if task_id in self._active_progress_tasks:
            del self._active_progress_tasks[task_id]

        if not self._active_progress_tasks:
            self.progress_bar.set_visible(False)
            self.progress_card_box.set_visible(False)
            if not self.link_preview_card_box.get_visible():
                self.link_preview_box.set_visible(False)
        else:
            latest_task = list(self._active_progress_tasks.values())[-1]
            max_fraction = max(t["fraction"] for t in self._active_progress_tasks.values())
            self.progress_bar.set_fraction(max_fraction)
            self.progress_label.set_label(latest_task["formatted"])


def test_show_and_hide_progress():
    mgr = DummyProgressManager()

    mgr._show_progress("crop", "Scanning page margins...", 0.25)
    mgr.progress_bar.set_visible.assert_called_with(True)
    mgr.progress_bar.set_fraction.assert_called_with(0.25)
    mgr.progress_label.set_label.assert_called_with("Scanning page margins... (25%)")
    mgr.progress_card_box.set_visible.assert_called_with(True)
    mgr.link_preview_box.set_visible.assert_called_with(True)

    mgr._show_progress("crop", "Scanning page margins...", 0.50)
    mgr.progress_bar.set_fraction.assert_called_with(0.50)
    mgr.progress_label.set_label.assert_called_with("Scanning page margins... (50%)")

    mgr._hide_progress("crop")
    mgr.progress_bar.set_visible.assert_called_with(False)
    mgr.progress_card_box.set_visible.assert_called_with(False)
    mgr.link_preview_box.set_visible.assert_called_with(False)
