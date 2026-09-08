from __future__ import annotations

import os
import subprocess
from typing import Any
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        commit = ""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=self.root,
            )
            if res.returncode == 0 and res.stdout.strip():
                commit = res.stdout.strip()
        except Exception:
            pass

        target_file = os.path.join(self.root, "pdfatlas", "_build_commit.py")
        content = f'# Generated during package build\nCOMMIT = "{commit}"\n'
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Register as a build artifact so hatchling packages it into the wheel even when gitignored
        build_data.setdefault("artifacts", []).append("pdfatlas/_build_commit.py")

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        target_file = os.path.join(self.root, "pdfatlas", "_build_commit.py")
        if os.path.exists(target_file):
            os.remove(target_file)

    def clean(self, versions: list[str]) -> None:
        target_file = os.path.join(self.root, "pdfatlas", "_build_commit.py")
        if os.path.exists(target_file):
            os.remove(target_file)
