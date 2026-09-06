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
        # Only overwrite if we found a commit or if the file does not exist yet
        if commit or not os.path.exists(target_file):
            content = f'# Generated during package build\nCOMMIT = "{commit}"\n'
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)
