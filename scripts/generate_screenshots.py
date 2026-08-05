#!/usr/bin/env python3
"""
Generate programmatically styled README screenshots for PDF Atlas.
Saves generated screenshots with GNOME Libadwaita window decorations
and soft ambient drop-shadows to ./assets/screenshots/
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "main.py"
ATTENTION_PDF = REPO_ROOT / "assets" / "sample-files" / "attention_is_all_you_need.pdf"
CATEGORY_PDF = REPO_ROOT / "assets" / "sample-files" / "applied_category_theory.pdf"
OUTPUT_DIR = REPO_ROOT / "assets" / "screenshots"

# Screenshot tasks: (output_filename, pdf_path, state_json)
TASKS = [
    ("attention_hero.png", ATTENTION_PDF, {"scroll_y": 1500}),
    ("attention_portal_search.png", ATTENTION_PDF, {"query": "attention mechanism"}),
    (
        "attention_reader_view.png",
        ATTENTION_PDF,
        {"crop": True, "page_gaps": False, "scroll_y": 1100},
    ),
    ("attention_minimap_view.png", CATEGORY_PDF, {"minimap": True}),
    (
        "attention_text_selection.png",
        "1706.03762",
        {
            "zoom": 1.5,
            "scroll_y": 3950,
            "selection": {
                "page": 3,
                "start_idx": 120,
                "end_idx": 165,
            },
        },
    ),
    (
        "attention_annotations_popover.png",
        ATTENTION_PDF,
        {
            "zoom": 2.14,
            "scroll_y": 2750,
            "annotations_popover": True,
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

        cmd = [
            sys.executable,
            str(MAIN_PY),
            str(pdf_target),
            "--screenshot",
            str(output_path),
        ]

        if state:
            serialized = json.dumps(state)
            cmd.extend(["--state", serialized])
        else:
            serialized = None

        state_display = serialized or "(default)"
        target_name = pdf_target.name if isinstance(pdf_target, Path) else str(pdf_target)
        print(f"\n[Generating] {filename} using {target_name} with state: {state_display}...", flush=True)
        res = subprocess.run(cmd, cwd=str(REPO_ROOT))

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
