#!/usr/bin/env python3
"""
Generate programmatically styled README screenshots for PDF Atlas.
Saves generated screenshots with GNOME Libadwaita window decorations
and soft ambient drop-shadows to ./assets/screenshots/
"""

import sys
import subprocess
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "main.py"
ATTENTION_PDF = REPO_ROOT / "assets" / "sample-files" / "attention_is_all_you_need.pdf"
CATEGORY_PDF = REPO_ROOT / "assets" / "sample-files" / "applied_category_theory.pdf"
OUTPUT_DIR = REPO_ROOT / "assets" / "screenshots"

# Screenshot tasks: (output_filename, pdf_path, state_json)
TASKS = [
    ("attention_hero.png", ATTENTION_PDF, None),
    ("attention_portal_search.png", ATTENTION_PDF, '{"query": "attention mechanism"}'),
    ("attention_reader_view.png", ATTENTION_PDF, '{"crop": true, "page_gaps": false, "scroll_y": 1100}'),
    ("attention_minimap_view.png", CATEGORY_PDF, '{"minimap": true}'),
]

def generate_all():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[Screenshot Generator] Target directory: {OUTPUT_DIR}")

    for filename, pdf_path, state in TASKS:
        output_path = OUTPUT_DIR / filename
        if not pdf_path.exists():
            print(f"[Error] PDF not found at {pdf_path}", file=sys.stderr)
            continue

        cmd = [
            sys.executable,
            str(MAIN_PY),
            str(pdf_path),
            "--screenshot",
            str(output_path),
        ]
        
        if state:
            cmd.extend(["--state", state])
            
        print(f"\n[Generating] {filename} using {pdf_path.name} with state: {state or '(default)'}...", flush=True)
        res = subprocess.run(cmd, cwd=str(REPO_ROOT))
        
        if res.returncode != 0:
            print(f"[Error] Failed to generate {filename} (exit code: {res.returncode})", file=sys.stderr)
        else:
            print(f"[Success] Saved screenshot to {output_path}")

    print("\n[Screenshot Generator] All README screenshots generated successfully!")

if __name__ == "__main__":
    generate_all()
