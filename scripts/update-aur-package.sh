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

if [ ! -f "$SUBMODULE_DIR/PKGBUILD" ]; then
    echo "==> Initializing AUR submodule..."
    git submodule update --init --checkout installers/archlinux/pdfatlas-git
fi


cd "$REPO_ROOT"

# Step 1: Prompt to push root repository at the start
echo "==> Root repository: $REPO_ROOT"
response=""
if [ -t 0 ]; then
    read -r -p "Do you want to push the root repository to origin first? [y/N] " response
else
    read -r -p "Do you want to push the root repository to origin first? [y/N] " response < /dev/tty || response="n"
fi

if [[ "$response" =~ ^[Yy]$ ]]; then
    CURRENT_BRANCH="$(git branch --show-current)"
    echo "==> Pushing root repository ($CURRENT_BRANCH) to origin..."
    git push origin "$CURRENT_BRANCH"
else
    echo "==> Initial root repository push skipped."
fi

# Step 2: Update AUR package submodule
echo ""
echo "==> Updating AUR package in $SUBMODULE_DIR"
cd "$SUBMODULE_DIR"

echo "==> Running makepkg -s..."
makepkg -f -s --noconfirm

echo "==> Regenerating .SRCINFO..."
makepkg --printsrcinfo > .SRCINFO

if [ -z "$(git status --porcelain PKGBUILD .SRCINFO)" ]; then
    echo "==> No changes detected in PKGBUILD or .SRCINFO."
else
    PKGVER="$(grep -m 1 'pkgver =' .SRCINFO | awk '{print $3}')"
    PKGREL="$(grep -m 1 'pkgrel =' .SRCINFO | awk '{print $3}')"
    COMMIT_MSG="chore: update to ${PKGVER}-${PKGREL}"

    echo "==> Committing changes inside AUR submodule ($COMMIT_MSG)..."
    git add PKGBUILD .SRCINFO
    git commit -m "$COMMIT_MSG"

    echo ""
    aur_response=""
    if [ -t 0 ]; then
        read -r -p "Do you want to push the AUR package to origin/master? [y/N] " aur_response
    else
        read -r -p "Do you want to push the AUR package to origin/master? [y/N] " aur_response < /dev/tty || aur_response="n"
    fi

    if [[ "$aur_response" =~ ^[Yy]$ ]]; then
        echo "==> Pushing AUR submodule to master..."
        git push origin master
        echo "==> AUR package update complete!"
    else
        echo "==> AUR push skipped. Changes committed locally inside AUR submodule."
    fi
fi

# Step 3: Final commit on the root repo for submodule pointer update
cd "$REPO_ROOT"
if [ -n "$(git status --porcelain "$SUBMODULE_DIR")" ]; then
    echo ""
    echo "==> Committing updated submodule pointer in root repository..."
    git add "$SUBMODULE_DIR"
    git commit -m "chore: update AUR package submodule pointer"
    echo "==> Submodule pointer committed in root repository."
fi
