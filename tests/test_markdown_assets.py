"""Guard the vendored markdown renderer assets and the note preview templates.

The note preview switched from marked + KaTeX auto-render to
markdown-it + markdown-it-texmath (math is tokenized before markdown's escape
rule, so LaTeX escapes like \\{ \\} \\, survive into KaTeX). These tests pin
that wiring so the swap can't silently regress.
"""

from pathlib import Path

from pdfatlas.core.resources import get_assets_dir

_MARKDOWN_ASSETS = (
    "markdown-it.min.js",
    "texmath.js",
    "katex.min.js",
    "katex.min.css",
)

_RETIRED_ASSETS = ("marked.min.js", "auto-render.min.js")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_FILES = (
    _REPO_ROOT / "pdfatlas" / "ui" / "notes.py",
    _REPO_ROOT / "scripts" / "markdown_renderer.py",
)


def test_required_markdown_assets_vendored():
    markdown_dir = get_assets_dir() / "markdown"
    for name in _MARKDOWN_ASSETS:
        assert (markdown_dir / name).is_file(), f"missing vendored asset: {name}"


def test_retired_assets_removed():
    markdown_dir = get_assets_dir() / "markdown"
    for name in _RETIRED_ASSETS:
        assert not (markdown_dir / name).exists(), f"retired asset still present: {name}"


def test_templates_use_markdownit_not_marked():
    for path in _TEMPLATE_FILES:
        text = path.read_text(encoding="utf-8")
        assert "markdown-it.min.js" in text or "markdown-it@14.3.0" in text
        assert "texmath" in text
        assert "marked" not in text, f"stale 'marked' reference in {path.name}"
        assert "auto-render" not in text, f"stale 'auto-render' reference in {path.name}"
        assert "renderMathInElement" not in text, f"stale auto-render call in {path.name}"