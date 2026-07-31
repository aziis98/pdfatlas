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
makepkg -f -s --noconfirm

echo "==> Regenerating .SRCINFO..."
makepkg --printsrcinfo > .SRCINFO

if [ -z "$(git status --porcelain PKGBUILD .SRCINFO)" ]; then
    echo "==> No changes detected in PKGBUILD or .SRCINFO. Exiting."
    exit 0
fi

PKGVER="$(grep -m 1 'pkgver =' .SRCINFO | awk '{print $3}')"
PKGREL="$(grep -m 1 'pkgrel =' .SRCINFO | awk '{print $3}')"
COMMIT_MSG="chore: update to ${PKGVER}-${PKGREL}"

echo "==> Committing changes inside AUR submodule ($COMMIT_MSG)..."
git add PKGBUILD .SRCINFO
git commit -m "$COMMIT_MSG"

echo ""
response=""
if [ -t 0 ]; then
    read -r -p "Do you want to push the AUR package to origin/master? [y/N] " response
else
    read -r -p "Do you want to push the AUR package to origin/master? [y/N] " response < /dev/tty || response="n"
fi

if [[ "$response" =~ ^[Yy]$ ]]; then
    echo "==> Pushing AUR submodule to master..."
    git push origin master
    echo "==> AUR package update complete!"
else
    echo "==> Push skipped. Changes committed locally inside AUR submodule."
fi
