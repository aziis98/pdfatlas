import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def get_logo_path() -> str | None:
    """Return absolute path to assets/logo.png if it exists."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logo_path = os.path.join(base_dir, "assets", "logo.png")
    return logo_path if os.path.exists(logo_path) else None


def get_base_dir() -> str:
    """Return base directory of the project repository."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_app_installed() -> bool:
    """
    Check if application desktop entry, shortcut, system binary, or app bundle is installed.
    """
    logger.debug("Checking application installation status...")

    # Check if running directly from an installed system binary (e.g. /usr/bin/pdfatlas)
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        base_name = os.path.basename(argv0)
        logger.debug("Checking sys.argv[0]: '%s' (basename: '%s')", argv0, base_name)
        if base_name in ("pdfatlas", "pdfatlas-git"):
            logger.info("Detected app running from system binary name '%s'", base_name)
            return True
        if argv0.startswith(("/usr/bin/", "/usr/local/bin/", "/opt/homebrew/", "/usr/lib/")):
            logger.info("Detected app running from system path '%s'", argv0)
            return True

    user_home = os.path.expanduser("~")

    if sys.platform == "darwin":
        user_app = os.path.join(user_home, "Applications", "PDF Atlas.app")
        sys_app = "/Applications/PDF Atlas.app"
        user_exists = os.path.exists(user_app)
        sys_exists = os.path.exists(sys_app)
        logger.debug("macOS app bundle check: user_app='%s' (exists=%s), sys_app='%s' (exists=%s)", user_app, user_exists, sys_app, sys_exists)
        if user_exists or sys_exists:
            logger.info("Detected macOS app bundle")
            return True
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.join(user_home, "AppData", "Roaming"))
        lnk_path = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "PDF Atlas.lnk")
        lnk_exists = os.path.exists(lnk_path)
        logger.debug("Windows shortcut check: '%s' (exists=%s)", lnk_path, lnk_exists)
        if lnk_exists:
            logger.info("Detected Windows Start Menu shortcut")
            return True
    else:
        desktop_candidates = [
            os.path.join(user_home, ".local", "share", "applications", "com.aziis98.pdfatlas.desktop"),
            "/usr/share/applications/com.aziis98.pdfatlas.desktop",
            "/usr/local/share/applications/com.aziis98.pdfatlas.desktop",
            "/var/lib/flatpak/exports/share/applications/com.aziis98.pdfatlas.desktop",
            "/var/lib/snapd/desktop/applications/com.aziis98.pdfatlas.desktop",
        ]
        for p in desktop_candidates:
            exists = os.path.exists(p)
            logger.debug("Desktop file check: '%s' (exists=%s)", p, exists)
            if exists:
                logger.info("Detected desktop file at '%s'", p)
                return True

    logger.info("Application is not detected as installed")
    return False




def _install_linux(base_dir: str, user_home: str, logo_path: str | None) -> bool:
    """Install .desktop entry and icon theme symlinks for Linux desktop environments."""
    if logo_path:
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
                    except Exception:
                        pass
                try:
                    os.symlink(logo_path, target_symlink)
                except Exception:
                    pass

        # Remove any legacy PNG from scalable/apps (scalable is reserved for SVG only in Freedesktop spec)
        for legacy_name in ["com-aziis98-pdfatlas.png", "com.aziis98.pdfatlas.png"]:
            legacy_scalable = os.path.join(user_home, ".local", "share", "icons", "hicolor", "scalable", "apps", legacy_name)
            if os.path.exists(legacy_scalable) or os.path.islink(legacy_scalable):
                try:
                    os.remove(legacy_scalable)
                except Exception:
                    pass

        # Also place pixmaps fallback symlink
        pixmaps_dir = os.path.join(user_home, ".local", "share", "pixmaps")

        os.makedirs(pixmaps_dir, exist_ok=True)
        for pixmap_name in ["com-aziis98-pdfatlas.png", "com.aziis98.pdfatlas.png"]:
            pixmap_target = os.path.join(pixmaps_dir, pixmap_name)
            if not (os.path.islink(pixmap_target) and os.readlink(pixmap_target) == logo_path):
                try:
                    if os.path.exists(pixmap_target) or os.path.islink(pixmap_target):
                        os.remove(pixmap_target)
                    os.symlink(logo_path, pixmap_target)
                except Exception:
                    pass


    desktop_dir = os.path.join(user_home, ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_file = os.path.join(desktop_dir, "com.aziis98.pdfatlas.desktop")
    desktop_contents = f"""[Desktop Entry]
Name=PDF Atlas
Comment=PDF Viewer with Portals & FTS5 Search
Exec=env PYTHONPATH={base_dir} {sys.executable} -m pdfatlas.main %f
Path={base_dir}
Icon=com-aziis98-pdfatlas
Terminal=false
Type=Application
Categories=Office;Viewer;
MimeType=application/pdf;
StartupWMClass=com.aziis98.pdfatlas
"""
    should_write = True
    if os.path.exists(desktop_file):
        try:
            with open(desktop_file, "r", encoding="utf-8") as f:
                if f.read() == desktop_contents:
                    should_write = False
        except Exception:
            pass

    if should_write:
        with open(desktop_file, "w", encoding="utf-8") as f:
            f.write(desktop_contents)

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", desktop_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("gtk-update-icon-cache"):
        hicolor_dir = os.path.join(user_home, ".local", "share", "icons", "hicolor")
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", hicolor_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
    if logo_path and os.path.exists(logo_path):
        ps_script += f'\n$Shortcut.IconLocation = "{logo_path.replace("\\", "\\\\")}"'

    ps_script += "\n$Shortcut.Save()\n"

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
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
    """Check if application is installed system-wide via package manager or system binary."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        base_name = os.path.basename(argv0)
        if base_name in ("pdfatlas", "pdfatlas-git"):
            return True
        if argv0.startswith(("/usr/bin/", "/usr/local/bin/", "/opt/homebrew/", "/usr/lib/")):
            return True

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
    if is_system_installed():
        logger.info("System-wide installation detected. Skipping local user folder copying.")
        return True

    if not force and is_app_installed():
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
    except Exception:
        return False

