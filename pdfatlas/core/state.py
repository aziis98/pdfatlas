"""
Pydantic model for validating and parsing initial application state passed via --state CLI flag.
"""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class SelectionState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int = 0
    start_idx: int | None = None
    end_idx: int | None = None
    start: str | None = None
    end: str | None = None


class HighlightState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    page: int = 0
    color: str = "#FFEE55"
    text: str | None = None
    rects: list[list[float]] = Field(default_factory=list)


class NoteState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    page: int = 0
    x: float = 0.0
    y: float = 0.0
    markdown: str = ""


class CliState(BaseModel):
    """
    Typed and validated schema for application state passed via --state.
    """

    model_config = ConfigDict(extra="ignore")

    zoom: float | None = None
    crop: bool | None = None
    page_gaps: bool | None = None
    color_scheme: str | None = None
    night_mode: bool | None = None
    dark_mode: bool | None = None
    night_mode_invert: float | None = None
    night_mode_hue_rotate: bool | None = None
    fit_width: bool = False
    scroll_y: float | None = None
    query: str | None = None
    minimap: bool = False
    hover_link: int | None = None
    scroll_benchmark: dict[str, Any] | None = None
    selection: SelectionState | None = None
    highlights: list[dict[str, Any]] | None = None
    notes: list[dict[str, Any]] | None = None
    annotations_popover: bool = False
    open_note_preview: int | None = None
    page: int | None = None
    hide_cursor: bool = True
    cursor_x: float | None = None
    cursor_y: float | None = None

    @classmethod
    def from_json(cls, json_str: str) -> "CliState":
        return cls.model_validate_json(json_str)
