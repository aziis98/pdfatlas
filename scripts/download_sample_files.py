#!/usr/bin/env python3
"""
Script to download sample PDF documents into sandbox.local/sample-files/
using curl or urllib.
"""

import subprocess
from pathlib import Path

SAMPLE_FILES = {
    "applied_category_theory.pdf": "https://arxiv.org/pdf/1803.05316",
    "attention_is_all_you_need.pdf": "https://arxiv.org/pdf/1706.03762",
    "how_do_transformers_perform_in_context_learning.pdf": "https://arxiv.org/pdf/2402.05787",
}

def main():
    target_dir = Path("sandbox.local/sample-files")
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Checking sample files in {target_dir}...")

    for filename, url in SAMPLE_FILES.items():
        dest_path = target_dir / filename
        if dest_path.exists() and dest_path.stat().st_size > 0:
            print(f"  [OK] {filename} already exists.")
            continue

        print(f"  [Downloading] {filename} from {url}...")
        try:
            # Try curl first
            subprocess.run(
                ["curl", "-sSL", "-o", str(dest_path), url],
                check=True,
            )
            print(f"  [Done] Saved to {dest_path}")
        except Exception as e:
            print(f"  [Error] Failed to download {filename}: {e}")


    # Check local-only sample files
    local_only = ["deepseek-cordis.local.pdf"]
    for filename in local_only:
        dest_path = target_dir / filename
        if dest_path.exists():
            print(f"  [OK] {filename} is present locally.")
        else:
            print(f"  [Warning] {filename} missing from {target_dir}")

if __name__ == "__main__":
    main()
