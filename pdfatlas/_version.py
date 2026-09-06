from __future__ import annotations

import os
import shutil
import subprocess

try:
    from ._build_commit import COMMIT
except ImportError:
    COMMIT = ""

__version__ = "0.1.0"


def get_version() -> str:
    """Return the current git commit short string or fallback version."""
    # 1. If running from a git repository, query git for live commit
    git = shutil.which("git")
    if git:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            res = subprocess.run(
                [git, "-C", here, "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    # 2. If installed from a built wheel, use the commit embedded at build time
    if COMMIT:
        return COMMIT

    # 3. Fallback to package distribution metadata
    try:
        from importlib.metadata import version

        v = version("pdfatlas")
        if v:
            return v
    except Exception:
        pass

    return __version__
