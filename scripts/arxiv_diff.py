#!/usr/bin/env python3
"""
arxiv_diff.py — Compare PDF-extracted text vs raw TeX source from an arXiv paper.

Downloads the PDF and source tarball, extracts text from each (no stripping),
computes a word-level diff, and renders a two-column side-by-side diff.

Usage:
    uv run scripts/arxiv_diff.py <arxiv_id_or_url> [--width N]
"""

import argparse
import json
import os
import re
import sys
import tarfile
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

from tqdm import tqdm

ARXIV_CACHE_ROOT = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "pdfatlas" / "source-arxiv"

ARXIV_PDF_URL = "https://arxiv.org/pdf/{}.pdf"
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{}"

from pdf_viewer.core.arxiv_mapper import extract_arxiv_id_from_raw as extract_arxiv_id

BOLD = "\033[1m"
DIM = "\033[2m"
RED_BG = "\033[48;5;168m"
GREEN_BG = "\033[48;5;114m"
CYAN_BG = "\033[48;5;110m"
RESET = "\033[0m"


def download_arxiv(arxiv_id: str) -> tuple[Path, Path]:
    cache_dir = ARXIV_CACHE_ROOT / arxiv_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = cache_dir / "paper.pdf"
    if not pdf_path.exists():
        print("  Downloading PDF...", file=sys.stderr)
        urllib.request.urlretrieve(ARXIV_PDF_URL.format(arxiv_id), pdf_path)

    eprint_path = cache_dir / "source.tar.gz"
    if not eprint_path.exists():
        print("  Downloading source tarball...", file=sys.stderr)
        urllib.request.urlretrieve(ARXIV_EPRINT_URL.format(arxiv_id), eprint_path)

    if not any(cache_dir.glob("*.tex")):
        print("  Extracting source...", file=sys.stderr)
        with tarfile.open(eprint_path, "r:gz") as tar:
            tar.extractall(path=cache_dir)

    return pdf_path, cache_dir


def extract_pdf_text(pdf_path: Path) -> str:
    import fitz

    doc = fitz.open(pdf_path)
    parts = []
    for page in doc:
        parts.append(page.get_text())
    doc.close()
    return "".join(parts)


def _inline_imports(text: str, tex_dir: Path, seen: set[str] | None = None) -> str:
    if seen is None:
        seen = set()

    def _replace(m: re.Match) -> str:
        name = m.group(1).strip()
        if name in seen:
            return ""
        seen.add(name)

        if not name.endswith(".tex"):
            name += ".tex"
        sub = tex_dir / name
        if not sub.exists():
            return m.group(0)
        return _inline_imports(sub.read_text(encoding="utf-8", errors="replace"), tex_dir, seen)

    text = re.sub(r"\\input\{([^}]+)\}", _replace, text)
    text = re.sub(r"\\include\{([^}]+)\}", _replace, text)
    text = re.sub(r"\\subfile\{([^}]+)\}", _replace, text)
    return text


def extract_tex_body(tex_dir: Path) -> str:
    tex_files = sorted(tex_dir.glob("*.tex"))
    if not tex_files:
        print("  No .tex files found", file=sys.stderr)
        return ""

    for tex_file in tex_files:
        raw = tex_file.read_text(encoding="utf-8", errors="replace")
        begin = re.search(r"\\begin\{document\}", raw)
        end = re.search(r"\\end\{document\}", raw)
        if begin and end:
            body = raw[begin.end() : end.start()]
            body = _inline_imports(body, tex_dir)
            return re.sub(r"(?<!\\)%[^\n]*", "", body)

    return tex_files[0].read_text(encoding="utf-8", errors="replace")


def tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def word_diff(pdf_words: list[str], tex_words: list[str]) -> list[tuple]:
    matcher = SequenceMatcher(None, pdf_words, tex_words)
    result: list = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for w in pdf_words[i1:i2]:
                result.append(("eq", w))
        elif tag == "replace":
            pdf_chunk = pdf_words[i1:i2]
            tex_chunk = tex_words[j1:j2]
            max_len = max(len(pdf_chunk), len(tex_chunk))
            for k in range(max_len):
                pw = pdf_chunk[k] if k < len(pdf_chunk) else ""
                tw = tex_chunk[k] if k < len(tex_chunk) else ""
                result.append(("diff", pw, tw))
        elif tag == "delete":
            for w in pdf_words[i1:i2]:
                result.append(("pdf_only", w))
        elif tag == "insert":
            for w in tex_words[j1:j2]:
                result.append(("tex_only", w))
    return result


def build_sourcemap(pdf_words: list[str], tex_words: list[str], diff: list[tuple]) -> list[dict]:
    pdf_idx = 0
    tex_idx = 0
    mapping: list[dict] = []

    for item in diff:
        if item[0] == "eq":
            mapping.append({"pdf": pdf_idx, "tex": tex_idx, "text": item[1]})
            pdf_idx += 1
            tex_idx += 1
        elif item[0] == "diff":
            mapping.append({"pdf": pdf_idx, "tex": tex_idx, "text": item[1]})
            pdf_idx += 1
            tex_idx += 1
        elif item[0] == "pdf_only":
            mapping.append({"pdf": pdf_idx, "tex": None, "text": item[1]})
            pdf_idx += 1
        elif item[0] == "tex_only":
            mapping.append({"pdf": None, "tex": tex_idx, "text": item[1]})
            tex_idx += 1

    return mapping


def visible_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def render_two_col(diff: list[tuple], width: int = 120) -> str:
    half = (width - 3) // 2
    sep = f"{DIM}│{RESET}"

    pdf_line = ""
    tex_line = ""
    lines: list[str] = []

    def flush():
        nonlocal pdf_line, tex_line
        if pdf_line or tex_line:
            p_plain = re.sub(r"\033\[[0-9;]*m", "", pdf_line)
            t_plain = re.sub(r"\033\[[0-9;]*m", "", tex_line)
            lines.append(
                f"{pdf_line}{' ' * max(0, half - len(p_plain))}"
                f"{sep}"
                f"{tex_line}{' ' * max(0, half - len(t_plain))}"
            )
            pdf_line = ""
            tex_line = ""

    def add(side: str, word: str, color: str = ""):
        nonlocal pdf_line, tex_line
        display = f"{color}{word}{RESET}" if color else word

        if side == "both":
            if visible_len(pdf_line) + len(word) + 1 > half:
                flush()
            pdf_line = (pdf_line + " " + word) if pdf_line else word
            tex_line = (tex_line + " " + word) if tex_line else word
        elif side == "pdf":
            if visible_len(pdf_line) + len(word) + 1 > half:
                flush()
            pdf_line = (pdf_line + " " + display) if pdf_line else display
        elif side == "tex":
            if visible_len(tex_line) + len(word) + 1 > half:
                flush()
            tex_line = (tex_line + " " + display) if tex_line else display

    for item in tqdm(diff, desc="Rendering", unit="chunk"):
        if item[0] == "eq":
            add("both", item[1])
        elif item[0] == "diff":
            _, pw, tw = item
            if visible_len(pdf_line) + len(pw) + 1 > half or visible_len(tex_line) + len(tw) + 1 > half:
                flush()
            add("pdf", pw, RED_BG)
            add("tex", tw, CYAN_BG)
        elif item[0] == "pdf_only":
            add("pdf", item[1], GREEN_BG)
        elif item[0] == "tex_only":
            add("tex", item[1], GREEN_BG)

    flush()

    header = f"{BOLD}{'─' * half}┬{'─' * half}{RESET}"
    titles = f"{BOLD}{'PDF Text':^{half}}{sep}{'TeX Text':^{half}}{RESET}"
    divider = f"{BOLD}{'─' * half}┼{'─' * half}{RESET}"
    footer = f"{BOLD}{'─' * half}┴{'─' * half}{RESET}"

    return "\n".join([header, titles, divider, *lines, footer])


def main():
    parser = argparse.ArgumentParser(description="Word-level diff between arXiv PDF and TeX source")
    parser.add_argument("arxiv_id", help="arXiv ID or URL")
    parser.add_argument("--width", type=int, default=120, help="Terminal width (default: 120)")
    parser.add_argument("--sourcemap", action="store_true", help="Output JSON source map (pdf_idx -> tex_idx)")
    args = parser.parse_args()

    arxiv_id = extract_arxiv_id(args.arxiv_id)
    if not arxiv_id:
        print(f"Invalid arXiv ID or URL: {args.arxiv_id}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing arXiv:{arxiv_id}...", file=sys.stderr)
    pdf_path, tex_dir = download_arxiv(arxiv_id)

    print("Extracting PDF text...", file=sys.stderr)
    pdf_text = extract_pdf_text(pdf_path)

    print("Extracting TeX body...", file=sys.stderr)
    tex_text = extract_tex_body(tex_dir)

    pdf_words = tokenize(pdf_text)
    tex_words = tokenize(tex_text)

    print(f"PDF: {len(pdf_words)} words | TeX: {len(tex_words)} words", file=sys.stderr)
    print("Computing diff...", file=sys.stderr)

    diff = word_diff(pdf_words, tex_words)

    if args.sourcemap:
        mapping = build_sourcemap(pdf_words, tex_words, diff)
        print(json.dumps(mapping, indent=2))
    else:
        print(render_two_col(diff, width=args.width))


if __name__ == "__main__":
    main()
