from pathlib import Path


def get_assets_dir() -> Path:
    """
    Returns absolute Path to assets directory, checking both inside package (pdfatlas/assets)
    and top-level repo directory (repo_root/assets).
    """
    pkg_assets = Path(__file__).resolve().parent.parent / "assets"
    if pkg_assets.exists():
        return pkg_assets

    repo_assets = Path(__file__).resolve().parent.parent.parent / "assets"
    if repo_assets.exists():
        return repo_assets

    return pkg_assets
