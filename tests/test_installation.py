import os
import sys
import tempfile

from pdf_viewer.core.installation import (
    _install_linux,
    _install_macos,
    _install_windows,
    get_base_dir,
    get_logo_path,
    is_app_installed,
)


def test_get_base_dir_and_logo():
    base_dir = get_base_dir()
    assert os.path.exists(base_dir)
    assert os.path.isdir(base_dir)

    logo_path = get_logo_path()
    if logo_path is not None:
        assert os.path.exists(logo_path)
        assert logo_path.endswith("logo.png")


def test_is_app_installed_returns_bool():
    installed = is_app_installed()
    assert isinstance(installed, bool)


def test_install_linux_temp_directory():
    with tempfile.TemporaryDirectory() as tmp_home:
        base_dir = get_base_dir()
        result = _install_linux(base_dir, tmp_home, None)
        assert result is True

        desktop_file = os.path.join(tmp_home, ".local", "share", "applications", "com.aziis98.pdfatlas.desktop")
        assert os.path.exists(desktop_file)

        with open(desktop_file, "r", encoding="utf-8") as f:
            content = f.read()

        assert "[Desktop Entry]" in content
        assert "Name=PDF Atlas" in content
        assert "MimeType=application/pdf;" in content
        assert "StartupWMClass=com.aziis98.pdfatlas" in content
        assert sys.executable in content


def test_install_macos_temp_directory():
    with tempfile.TemporaryDirectory() as tmp_home:
        base_dir = get_base_dir()
        result = _install_macos(base_dir, tmp_home, None)
        assert result is True

        bundle_dir = os.path.join(tmp_home, "Applications", "PDF Atlas.app")
        plist_path = os.path.join(bundle_dir, "Contents", "Info.plist")
        exec_path = os.path.join(bundle_dir, "Contents", "MacOS", "pdfatlas")

        assert os.path.exists(bundle_dir)
        assert os.path.exists(plist_path)
        assert os.path.exists(exec_path)

        with open(plist_path, "r", encoding="utf-8") as f:
            plist_content = f.read()
        assert "com.aziis98.pdfatlas" in plist_content
        assert "<key>CFBundleExecutable</key>" in plist_content

        with open(exec_path, "r", encoding="utf-8") as f:
            exec_content = f.read()
        assert base_dir in exec_content
        assert sys.executable in exec_content


def test_install_windows_temp_directory(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_home:
        monkeypatch.setenv("APPDATA", tmp_home)
        base_dir = get_base_dir()
        # Stub powershell call to avoid executing on non-Windows test runners
        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)

        result = _install_windows(base_dir, tmp_home, None)
        assert result is True
