import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


from .resources import get_assets_dir


def get_logo_path() -> str | None:
    """Return absolute path to assets/logo.png if it exists."""
    logo_path = get_assets_dir() / "logo.png"
    return str(logo_path) if logo_path.exists() else None


def get_base_dir() -> str:
    """Return base directory of the project repository."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_installation_mode_info() -> tuple[str, str]:
    """
    Determines installation mode and returns a tuple: (mode_name, reason_description).
    Modes:
      - "system-wide": Installed globally via system package manager (e.g. /usr/bin/pdfatlas)
      - "user": Installed in user space (e.g. uv tool, pip --user, ~/.local/bin/pdfatlas)
      - "development": Running directly from repository source or local virtualenv
    """
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv else ""
    user_home = os.path.expanduser("~")

    # Check 1: System-wide binary paths
    if argv0.startswith(("/usr/bin/", "/usr/local/bin/", "/opt/", "/var/lib/", "/snap/")):
        return ("system-wide", f"Executable is located in system binary path '{argv0}'")

    # Check 2: User-space binary paths (uv tool, pip --user, ~/.local/bin)
    if argv0.startswith(os.path.join(user_home, ".local")) or argv0.startswith(os.path.join(user_home, ".cargo")):
        if ".venv" in argv0:
            return ("development", f"Executable is in local repository virtualenv '{argv0}'")
        return ("user", f"Executable is located in user local tool directory '{argv0}'")

    # Check 3: Desktop launchers
    user_desktop = os.path.join(user_home, ".local", "share", "applications", "com.aziis98.pdfatlas.desktop")
    if os.path.exists(user_desktop):
        return ("user", f"User desktop launcher exists at '{user_desktop}'")

    sys_desktops = ["/usr/share/applications/com.aziis98.pdfatlas.desktop", "/usr/local/share/applications/com.aziis98.pdfatlas.desktop"]
    for p in sys_desktops:
        if os.path.exists(p):
            return ("system-wide", f"System desktop launcher exists at '{p}'")

    if ".venv" in argv0 or argv0.endswith("main.py"):
        return ("development", f"Running directly from source/virtualenv '{argv0}'")

    base_name = os.path.basename(argv0)
    if base_name in ("pdfatlas", "pdfatlas-git"):
        return ("user", f"Running from installed binary name '{argv0}'")

    return ("development", f"Running in development mode from '{argv0}'")


def is_desktop_launcher_installed() -> bool:
    """
    Check if .desktop application entry exists in user or system applications directory.
    """
    user_home = os.path.expanduser("~")
    desktop_candidates = [
        os.path.join(user_home, ".local", "share", "applications", "com.aziis98.pdfatlas.desktop"),
        "/usr/share/applications/com.aziis98.pdfatlas.desktop",
        "/usr/local/share/applications/com.aziis98.pdfatlas.desktop",
        "/var/lib/flatpak/exports/share/applications/com.aziis98.pdfatlas.desktop",
        "/var/lib/snapd/desktop/applications/com.aziis98.pdfatlas.desktop",
    ]
    return any(os.path.exists(p) for p in desktop_candidates)


def is_app_installed() -> bool:
    """
    Check if application desktop entry launcher (.desktop) is installed.
    """
    mode, reason = get_installation_mode_info()
    launcher_installed = is_desktop_launcher_installed()
    logger.info("Installation check: mode='%s' (%s), launcher_installed=%s", mode, reason, launcher_installed)
    return launcher_installed


def _install_linux(base_dir: str, user_home: str, logo_path: str | None) -> bool:
    """Install .desktop entry and icon theme symlinks for Linux desktop environments."""
    logger.info("Starting Linux desktop application launcher installation...")
    print("[PDFAtlas] Installing desktop entry and icons to ~/.local/share/...", flush=True)

    if logo_path:
        logger.info("Installing icon symlinks using logo at '%s'", logo_path)
        icon_names = ["com-aziis98-pdfatlas.png", "com.aziis98.pdfatlas.png"]
        sizes = ["512x512", "256x256", "128x128", "48x48"]
        for size in sizes:
            apps_dir = os.path.join(user_home, ".local", "share", "icons", "hicolor", size, "apps")
            os.makedirs(apps_dir, exist_ok=True)
            for icon_name in icon_names:
                target_symlink = os.path.join(apps_dir, icon_name)
                if os.path.islink(target_symlink) or os.path.exists(target_symlink):
                    try:
                        if os.path.islink(target_symlink) and os.readlink(target_symlink) == logo_path:
                            continue
                        os.remove(target_symlink)
                    except Exception as e:
                        logger.debug("Failed to remove old icon symlink '%s': %s", target_symlink, e)
                try:
                    os.symlink(logo_path, target_symlink)
                except Exception as e:
                    logger.debug("Failed to create icon symlink '%s': %s", target_symlink, e)

        pixmaps_dir = os.path.join(user_home, ".local", "share", "pixmaps")
        os.makedirs(pixmaps_dir, exist_ok=True)
        for pixmap_name in ["com-aziis98-pdfatlas.png", "com.aziis98.pdfatlas.png"]:
            pixmap_target = os.path.join(pixmaps_dir, pixmap_name)
            if not (os.path.islink(pixmap_target) and os.readlink(pixmap_target) == logo_path):
                try:
                    if os.path.exists(pixmap_target) or os.path.islink(pixmap_target):
                        os.remove(pixmap_target)
                    os.symlink(logo_path, pixmap_target)
                except Exception as e:
                    logger.debug("Failed to create pixmap symlink '%s': %s", pixmap_target, e)

    # Determine Exec command for desktop entry
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.isabs(argv0) and os.path.exists(argv0) and not argv0.endswith(".py"):
        exec_cmd = f"{argv0} %f"
    elif shutil.which("pdfatlas"):
        exec_cmd = f"{shutil.which('pdfatlas')} %f"
    else:
        exec_cmd = f"env PYTHONPATH={base_dir} {sys.executable} -m pdfatlas.main %f"

    desktop_dir = os.path.join(user_home, ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_file = os.path.join(desktop_dir, "com.aziis98.pdfatlas.desktop")
    desktop_contents = f"""[Desktop Entry]
Name=PDF Atlas
Comment=PDF Viewer with Portals & FTS5 Search
Exec={exec_cmd}
Path={base_dir}
Icon=com-aziis98-pdfatlas
Terminal=false
Type=Application
Categories=Office;Viewer;
MimeType=application/pdf;
StartupWMClass=com.aziis98.pdfatlas
"""
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(desktop_contents)
    logger.info("Wrote desktop launcher to '%s' (Exec='%s')", desktop_file, exec_cmd)

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", desktop_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("gtk-update-icon-cache"):
        hicolor_dir = os.path.join(user_home, ".local", "share", "icons", "hicolor")
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", hicolor_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"[PDFAtlas] Successfully installed desktop launcher to '{desktop_file}'", flush=True)
    return True


def _install_windows(base_dir: str, user_home: str, logo_path: str | None) -> bool:
    """Create Start Menu shortcut (.lnk) on Windows via PowerShell WScript.Shell."""
    appdata = os.environ.get("APPDATA", os.path.join(user_home, "AppData", "Roaming"))
    programs_dir = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")
    os.makedirs(programs_dir, exist_ok=True)
    lnk_path = os.path.join(programs_dir, "PDF Atlas.lnk")

    ps_script = f"""
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{lnk_path.replace('\\', '\\\\')}")
$Shortcut.TargetPath = "{sys.executable.replace('\\', '\\\\')}"
$Shortcut.Arguments = "-m pdfatlas.main"
$Shortcut.WorkingDirectory = "{base_dir.replace('\\', '\\\\')}"
$Shortcut.Description = "PDF Viewer with Portals & FTS5 Search"
"""
    if logo_path:
        ps_script += f'$Shortcut.IconLocation = "{logo_path.replace("\\", "\\\\")}"\n'
    ps_script += "$Shortcut.Save()\n"

    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Created Windows Start Menu shortcut at '%s'", lnk_path)
        print(f"[PDFAtlas] Successfully installed Windows shortcut to '{lnk_path}'", flush=True)
        return True
    except Exception as e:
        logger.error("Failed to create Windows shortcut: %s", e)
        return False


def _install_macos(base_dir: str, user_home: str, logo_path: str | None) -> bool:
    """Create ~/Applications/PDF Atlas.app bundle with Info.plist and executable wrapper."""
    apps_dir = os.path.join(user_home, "Applications")
    bundle_dir = os.path.join(apps_dir, "PDF Atlas.app")
    contents_dir = os.path.join(bundle_dir, "Contents")
    macos_dir = os.path.join(contents_dir, "MacOS")
    resources_dir = os.path.join(contents_dir, "Resources")

    os.makedirs(macos_dir, exist_ok=True)
    os.makedirs(resources_dir, exist_ok=True)

    plist_contents = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>pdfatlas</string>
    <key>CFBundleIconFile</key>
    <string>logo.png</string>
    <key>CFBundleIdentifier</key>
    <string>com.aziis98.pdfatlas</string>
    <key>CFBundleName</key>
    <string>PDF Atlas</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key>
            <string>PDF Document</string>
            <key>CFBundleTypeRole</key>
            <string>Viewer</string>
            <key>LSItemContentTypes</key>
            <array>
                <string>com.adobe.pdf</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""
    plist_path = os.path.join(contents_dir, "Info.plist")
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(plist_contents)

    if logo_path and os.path.exists(logo_path):
        target_icon = os.path.join(resources_dir, "logo.png")
        try:
            shutil.copy2(logo_path, target_icon)
        except Exception:
            pass

    exec_path = os.path.join(macos_dir, "pdfatlas")
    exec_contents = f"""#!/bin/bash
export PYTHONPATH="{base_dir}"
exec "{sys.executable}" -m pdfatlas.main "$@"
"""
    with open(exec_path, "w", encoding="utf-8") as f:
        f.write(exec_contents)
    os.chmod(exec_path, 0o755)

    if shutil.which("codesign"):
        subprocess.run(["codesign", "-s", "-", "--force", bundle_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return True


def is_system_installed() -> bool:
    """Check if application desktop entry is installed system-wide via package manager."""
    system_desktop_candidates = [
        "/usr/share/applications/com.aziis98.pdfatlas.desktop",
        "/usr/local/share/applications/com.aziis98.pdfatlas.desktop",
        "/var/lib/flatpak/exports/share/applications/com.aziis98.pdfatlas.desktop",
        "/var/lib/snapd/desktop/applications/com.aziis98.pdfatlas.desktop",
        "/Applications/PDF Atlas.app",
    ]
    return any(os.path.exists(p) for p in system_desktop_candidates)


def ensure_app_installed(force: bool = False) -> bool:
    """
    Ensures application desktop entries, shortcuts, or app bundles are installed.
    Returns True if installation succeeded or was already up to date.
    """
    mode, reason = get_installation_mode_info()
    logger.info("ensure_app_installed called: mode='%s' (%s), force=%s", mode, reason, force)

    if not force and is_system_installed():
        logger.info("System-wide installation detected. Skipping user desktop launcher creation.")
        return True

    if not force and is_desktop_launcher_installed():
        logger.info("Desktop launcher already installed. Skipping.")
        return True

    base_dir = get_base_dir()
    user_home = os.path.expanduser("~")
    logo_path = get_logo_path()

    try:
        if sys.platform == "darwin":
            return _install_macos(base_dir, user_home, logo_path)
        elif sys.platform == "win32":
            return _install_windows(base_dir, user_home, logo_path)
        else:
            return _install_linux(base_dir, user_home, logo_path)
    except Exception as e:
        logger.error("Failed to install desktop application launcher: %s", e, exc_info=True)
        print(f"[PDFAtlas] Desktop launcher installation failed: {e}", flush=True)
        return False

