#!/usr/bin/env python3
"""
Generate programmatically styled README screenshots for PDF Atlas.
Saves generated screenshots with Weston floating window desktop shell,
32px background margin, and soft ambient drop-shadows to ./assets/screenshots/
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "main.py"
WAYLAND_SCRIPT = REPO_ROOT / "scripts" / "screenshot_wayland_app.sh"
ATTENTION_PDF = REPO_ROOT / "sandbox.local" / "sample-files" / "attention_is_all_you_need.pdf"
CATEGORY_PDF = REPO_ROOT / "sandbox.local" / "sample-files" / "applied_category_theory.pdf"

OUTPUT_DIR = REPO_ROOT / "assets" / "screenshots"

# Screenshot tasks: (output_filename, pdf_path, state_json)
TASKS = [
    ("attention_hero.png", ATTENTION_PDF, {"scroll_y": 1500}),
    ("attention_portal_search.png", ATTENTION_PDF, {"query": "attention mechanism"}),
    (
        "attention_no_gaps.png",
        ATTENTION_PDF,
        {"crop": True, "page_gaps": False, "scroll_y": 1100},
    ),
    ("category_theory_minimap_view.png", CATEGORY_PDF, {"minimap": True}),
    (
        "attention_text_selection.png",
        "1706.03762",
        {
            "zoom": 1.5,
            "scroll_y": 3950,
            "hide_cursor": False,
            "cursor_x": 760,
            "cursor_y": 215,
            "selection": {
                "page": 3,
                "start_idx": 120,
                "end_idx": 165,
            },
        },
    ),
    (
        "attention_notes_annotations.png",
        ATTENTION_PDF,
        {
            "fit_width": True,
            "scroll_y": 350,
            "annotations_popover": True,
            "open_note_preview": 1,
            "highlights": [
                {
                    "page": 0,
                    "color": "#FFEE55",
                    "text": "computation also forms the foundation of the Extended",
                },
                {
                    "page": 0,
                    "color": "#FF9933",
                    "text": "symbol representations (x1, ..., xn) to a seq",
                },
                {
                    "page": 1,
                    "color": "#FFEE55",
                    "text": "ce transduction models, we use learned embeddings to convert...",
                },
                {
                    "page": 2,
                    "color": "#FFEE55",
                    "text": "M sentences and split tokens into subwords...",
                },
            ],
            "notes": [
                {
                    "id": 1,
                    "page": 0,
                    "x": 60.0,
                    "y": 500.0,
                    "markdown": "### Architecture Summary\n- **Self-attention** mechanism replaces recurrence.\n- **Multi-Head Attention** computes parallel projections.\n- Positional Encodings: $PE_{(pos, 2i)} = \\sin(pos/10000^{2i/d})$",
                }
            ],
        },
    ),
]


def generate(filters: list[str] | None = None):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[Screenshot Generator] Target directory: {OUTPUT_DIR}")

    tasks_to_run = TASKS
    if filters:
        filter_strs = [f.lower() for f in filters]
        tasks_to_run = [
            t for t in TASKS if any(fs in t[0].lower() for fs in filter_strs)
        ]
        if not tasks_to_run:
            print(f"[Warning] No screenshots matched filter(s): {filters}", file=sys.stderr)
            return

    for filename, pdf_target, state in tasks_to_run:
        output_path = OUTPUT_DIR / filename
        if isinstance(pdf_target, Path) and not pdf_target.exists():
            print(f"[Error] PDF not found at {pdf_target}", file=sys.stderr)
            continue

        pdf_target_str = str(pdf_target)
        app_cmd = f"{sys.executable} {MAIN_PY} {shlex.quote(pdf_target_str)}"

        if state:
            serialized = json.dumps(state)
            app_cmd += f" --state {shlex.quote(serialized)}"
        else:
            serialized = None

        state_display = serialized or "(default)"
        target_name = pdf_target.name if isinstance(pdf_target, Path) else str(pdf_target)
        print(f"\n[Generating] {filename} using {target_name} with state: {state_display}...", flush=True)

        env = dict(os.environ)
        env["APP_CMD"] = app_cmd
        env["OUTPUT_PNG"] = str(output_path)

        hide_cursor = state.get("hide_cursor", True) if state else True
        env["PDFATLAS_HIDE_CURSOR"] = "1" if hide_cursor else "0"
        if state and "cursor_x" in state:
            env["CURSOR_X"] = str(state["cursor_x"])
        if state and "cursor_y" in state:
            env["CURSOR_Y"] = str(state["cursor_y"])

        res = subprocess.run(["bash", str(WAYLAND_SCRIPT)], env=env, cwd=str(REPO_ROOT))

        if res.returncode != 0:
            print(f"[Error] Failed to generate {filename} (exit code: {res.returncode})", file=sys.stderr)
        else:
            print(f"[Success] Saved screenshot to {output_path}")

    print("\n[Screenshot Generator] All requested README screenshots generated successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate programmatically styled README screenshots for PDF Atlas."
    )
    parser.add_argument(
        "filter",
        nargs="*",
        help="Optional screenshot name(s) or substring filters (e.g. 'selection', 'reader'). If omitted, runs all.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        dest="only_filter",
        help="Filter screenshots to run by name or substring.",
    )
    cli_args = parser.parse_args()
    active_filters = cli_args.filter or cli_args.only_filter
    generate(active_filters)
