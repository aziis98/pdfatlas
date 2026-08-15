"""
Tests for CliState parsing, defaults, and validation.
"""

from pdfatlas.core.state import CliState


def test_cli_state_defaults():
    state = CliState()
    assert state.zoom is None
    assert state.crop is None
    assert state.fit_width is False
    assert state.scroll_y is None
    assert state.query is None
    assert state.minimap is False
    assert state.highlights is None
    assert state.notes is None


def test_cli_state_from_json():
    json_str = """
    {
        "zoom": 1.5,
        "crop": true,
        "page_gaps": false,
        "night_mode": true,
        "scroll_y": 1500.0,
        "query": "transformer",
        "selection": {
            "page": 2,
            "start_idx": 10,
            "end_idx": 50
        },
        "highlights": [
            {"page": 0, "color": "#FFEE55", "text": "attention"}
        ],
        "notes": [
            {"id": 1, "page": 0, "x": 10.0, "y": 20.0, "markdown": "Hello"}
        ]
    }
    """
    state = CliState.from_json(json_str)
    assert state.zoom == 1.5
    assert state.crop is True
    assert state.page_gaps is False
    assert state.night_mode is True
    assert state.scroll_y == 1500.0
    assert state.query == "transformer"
    assert state.selection is not None
    assert state.selection.page == 2
    assert state.selection.start_idx == 10
    assert state.selection.end_idx == 50
    assert state.highlights is not None and len(state.highlights) == 1
    assert state.notes is not None and len(state.notes) == 1


def test_cli_state_ignores_unknown_fields():
    json_str = '{"unknown_future_field": 123, "zoom": 2.0}'
    state = CliState.from_json(json_str)
    assert state.zoom == 2.0
