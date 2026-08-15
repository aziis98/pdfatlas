import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
SETTINGS_FILE = XDG_CONFIG_HOME / "pdfatlas" / "settings.json"


@dataclass
class CropSettings:
    enabled: bool = False
    min_padding_left: float = 16.0  # pts
    min_padding_right: float = 16.0  # pts
    min_padding_top: float = 8.0  # pts
    min_padding_bottom: float = 8.0  # pts
    crop_mode: str = "per_page"  # "per_page" or "uniform_width"
    whitespace_threshold: float = 0.15  # float fraction 0..0.50
    sparse_strategy: str = "use_uniform"  # "skip", "use_uniform", "crop_anyway"
    page_gaps: bool = True
    search_layout: str = "grid"  # "list" or "grid"
    max_texture_zoom: float | None = 2.5  # None = Infinity (no texture-zoom cap)
    min_zoom: float = 0.25
    max_zoom: float = 50.0
    color_scheme: str = "system"  # "system", "light", or "dark"
    night_mode_invert: float = 0.95  # 0.0 .. 1.0 (95% default)
    night_mode_hue_rotate: bool = True

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "CropSettings":
        settings = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                valid_keys = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in valid_keys:
                        setattr(settings, k, v)
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                pass
        return settings

    def save(self, path: Path = SETTINGS_FILE):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
