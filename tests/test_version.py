import subprocess
import sys
from pdfatlas._version import get_version
from hatch_build import CustomBuildHook


def test_get_version_returns_non_empty_string():
    v = get_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_cli_version_flag():
    res = subprocess.run(
        [sys.executable, "-m", "pdfatlas.main", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "pdfatlas " in res.stdout
    assert get_version() in res.stdout


def test_custom_build_hook_generates_commit_file(tmp_path):
    pkg_dir = tmp_path / "pdfatlas"
    pkg_dir.mkdir()
    from typing import Any, cast
    hook = CustomBuildHook(str(tmp_path), {}, cast(Any, {}), cast(Any, {}), "", "wheel")
    hook.initialize("0.1.0", {})
    target = pkg_dir / "_build_commit.py"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "COMMIT =" in content


def test_is_dev_mode_and_app_id():
    from pdfatlas.main import is_dev_mode, PDFViewerApplication

    assert is_dev_mode() is True
    app = PDFViewerApplication()
    assert app.get_application_id() == "com.aziis98.pdfatlas.dev"

    app_custom = PDFViewerApplication(application_id="custom.app.id")
    assert app_custom.get_application_id() == "custom.app.id"

