from unittest.mock import MagicMock
import fitz
from pdfatlas.core.crop import CropAnalyzer
from pdfatlas.core.settings import CropSettings


def test_crop_settings_original_serialization(tmp_path):
    settings = CropSettings(
        min_padding_left="original",
        min_padding_right=20.0,
        min_padding_top="original",
        min_padding_bottom=10.0,
    )
    settings_file = tmp_path / "settings.json"
    settings.save(settings_file)

    loaded = CropSettings.load(settings_file)
    assert loaded.min_padding_left == "original"
    assert loaded.min_padding_right == 20.0
    assert loaded.min_padding_top == "original"
    assert loaded.min_padding_bottom == 10.0


def test_crop_analyzer_respects_original_edges():
    doc_model = MagicMock()
    # Mock single 600x800 page
    doc_model.page_count = 1
    doc_model.page_rect.return_value = fitz.Rect(0, 0, 600, 800)

    analyzer = CropAnalyzer(doc_model)
    analyzer.scanned[0] = True
    # Raw content box: x: 100..500, y: 150..650
    analyzer.raw_bboxes[0] = fitz.Rect(100, 150, 500, 650)

    # 1. Normal cropping on all edges
    settings_all = CropSettings(
        enabled=True,
        min_padding_left=10.0,
        min_padding_right=10.0,
        min_padding_top=10.0,
        min_padding_bottom=10.0,
        crop_mode="per_page",
    )
    analyzer.compute_crop_rects(settings_all)
    rect = analyzer.crop_rects[0]
    assert rect is not None
    assert rect.x0 == 90.0  # 100 - 10
    assert rect.x1 == 510.0  # 500 + 10
    assert rect.y0 == 140.0  # 150 - 10
    assert rect.y1 == 660.0  # 650 + 10

    # 2. Disable left and top crop (set to "original")
    settings_partial = CropSettings(
        enabled=True,
        min_padding_left="original",
        min_padding_right=10.0,
        min_padding_top="original",
        min_padding_bottom=10.0,
        crop_mode="per_page",
    )
    analyzer.compute_crop_rects(settings_partial)
    rect_partial = analyzer.crop_rects[0]
    assert rect_partial is not None
    assert rect_partial.x0 == 0.0  # Kept page_rect.x0
    assert rect_partial.x1 == 510.0  # 500 + 10
    assert rect_partial.y0 == 0.0  # Kept page_rect.y0
    assert rect_partial.y1 == 660.0  # 650 + 10

    # 3. Disable all crop directions (all "original")
    settings_none = CropSettings(
        enabled=True,
        min_padding_left="original",
        min_padding_right="original",
        min_padding_top="original",
        min_padding_bottom="original",
        crop_mode="per_page",
    )
    analyzer.compute_crop_rects(settings_none)
    rect_none = analyzer.crop_rects[0]
    assert rect_none is not None
    assert rect_none == fitz.Rect(0, 0, 600, 800)


def test_settings_dialog_toggle_disables_only_spin():
    from pdfatlas.ui.settings import SettingsWindow

    settings = CropSettings(min_padding_left=16.0)
    on_changed_called = []
    dialog = SettingsWindow(
        None,
        settings,
        on_changed=lambda: on_changed_called.append(True),
        on_reanalyze=lambda: None,
    )

    # Initial state: checkbox active, spin button sensitive
    assert dialog.check_l.get_active() is True
    assert dialog.spin_l.get_sensitive() is True
    assert dialog.check_l.get_sensitive() is True

    # Deselect checkbox: spin button becomes insensitive, checkbox remains sensitive
    dialog.check_l.set_active(False)
    assert dialog.check_l.get_active() is False
    assert dialog.spin_l.get_sensitive() is False
    assert dialog.check_l.get_sensitive() is True  # Can still re-enable!
    assert settings.min_padding_left == "original"

    # Re-enable checkbox: spin button becomes sensitive again
    dialog.check_l.set_active(True)
    assert dialog.check_l.get_active() is True
    assert dialog.spin_l.get_sensitive() is True
    assert settings.min_padding_left == 16.0

