#!/usr/bin/env bash
set -euo pipefail

# Determine repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUBMODULE_DIR="$REPO_ROOT/installers/archlinux/pdfatlas-git"

if [ ! -d "$SUBMODULE_DIR" ]; then
    echo "Error: Submodule directory not found at $SUBMODULE_DIR" >&2
    exit 1
fi

echo "==> Updating AUR package in $SUBMODULE_DIR"
cd "$SUBMODULE_DIR"

echo "==> Running makepkg -s..."
makepkg -s --noconfirm

echo "==> Regenerating .SRCINFO..."
makepkg --printsrcinfo > .SRCINFO

echo "==> Committing changes inside AUR submodule..."
git add PKGBUILD .SRCINFO
git commit -m "chore: update .SRCINFO and PKGBUILD" || echo "No changes to commit inside submodule."

echo "==> AUR package update complete!"
