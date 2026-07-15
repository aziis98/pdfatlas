from dataclasses import dataclass

@dataclass
class CropSettings:
    enabled:              bool  = False
    min_padding_left:     float = 16.0   # pts
    min_padding_right:    float = 16.0   # pts
    min_padding_top:      float = 8.0    # pts
    min_padding_bottom:   float = 8.0    # pts
    crop_mode:            str   = "per_page"        # "per_page" or "uniform_width"
    whitespace_threshold: float = 0.15          # float fraction 0..0.50
    sparse_strategy:      str   = "use_uniform"    # "skip", "use_uniform", "crop_anyway"
    page_gaps:            bool  = True
    search_layout:        str   = "grid"           # "list" or "grid"
