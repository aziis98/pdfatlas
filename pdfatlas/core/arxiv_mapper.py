import os
import re
import sys
import tarfile
import time
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, cast

import fitz

ARXIV_CACHE_ROOT = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "pdfatlas" / "source-arxiv"

ARXIV_PDF_URL = "https://arxiv.org/pdf/{}.pdf"
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{}"

ARXIV_ID_PATTERN = r"(?:[a-zA-Z\-]+(?:\.[a-zA-Z\-]+)?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?"

ARXIV_ID_RE = re.compile(
    r"(?:https?://arxiv\.org/(?:abs|pdf)/(" + ARXIV_ID_PATTERN + r")(?:\.pdf)?|(?:arxiv:)?(" + ARXIV_ID_PATTERN + r"))",
    re.IGNORECASE,
)


def extract_arxiv_id_from_raw(raw: str) -> Optional[str]:
    cleaned = raw.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[6:].strip()
    m = ARXIV_ID_RE.fullmatch(cleaned)
    if m:
        return m.group(1) or m.group(2)
    return None


def arxiv_id_from_path(path_str: str) -> Optional[str]:
    aid = extract_arxiv_id_from_raw(path_str)
    if aid:
        return aid

    p = Path(path_str)
    parts = p.parts

    if "source-arxiv" in parts:
        idx = parts.index("source-arxiv")
        sub_parts = list(parts[idx + 1:])
        if sub_parts and sub_parts[-1].endswith(".pdf"):
            sub_parts.pop()
        if sub_parts:
            candidate = "/".join(sub_parts)
            extracted = extract_arxiv_id_from_raw(candidate)
            if extracted:
                return extracted

    for part in parts:
        m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", part)
        if m:
            return m.group(1)

    for i in range(len(parts) - 1):
        candidate = f"{parts[i]}/{parts[i+1]}"
        if candidate.endswith(".pdf"):
            candidate = candidate[:-4]
        extracted = extract_arxiv_id_from_raw(candidate)
        if extracted:
            return extracted

    full_stem = p.stem
    extracted = extract_arxiv_id_from_raw(full_stem)
    if extracted:
        return extracted

    return None


def download_arxiv_source(arxiv_id: str) -> tuple[Path, Path]:
    cache_dir = ARXIV_CACHE_ROOT / arxiv_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = cache_dir / "paper.pdf"
    if not pdf_path.exists():
        print(f"[ArxivMapper] Downloading PDF for {arxiv_id}...", file=sys.stderr, flush=True)
        urllib.request.urlretrieve(ARXIV_PDF_URL.format(arxiv_id), pdf_path)

    eprint_path = cache_dir / "source.tar.gz"
    if not any(cache_dir.glob("*.tex")):
        if not eprint_path.exists():
            print(f"[ArxivMapper] Downloading source tarball for {arxiv_id}...", file=sys.stderr, flush=True)
            urllib.request.urlretrieve(ARXIV_EPRINT_URL.format(arxiv_id), eprint_path)
        print(f"[ArxivMapper] Extracting source tarball for {arxiv_id}...", file=sys.stderr, flush=True)
        with tarfile.open(eprint_path, "r:gz") as tar:
            tar.extractall(path=cache_dir)
        if eprint_path.exists():
            eprint_path.unlink()

    return pdf_path, cache_dir


def extract_pdf_text_with_metadata(pdf_path: str | Path) -> tuple[str, list[tuple[int, int, int]]]:
    """
    Extract PDF text words and build metadata linking each word index to (page_idx, char_start_on_page, char_end_on_page).
    Uses PyMuPDF's rawdict and words layout engines.
    """
    doc = fitz.open(str(pdf_path))
    word_metadata: list[tuple[int, int, int]] = []
    pdf_words: list[str] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            rawdict = cast(dict[str, Any], page.get_text("rawdict"))
        except (RuntimeError, ValueError):
            rawdict = {}

        page_chars: list[str] = []
        for b in rawdict.get("blocks", []):
            if b.get("type") == 0:
                for line in b.get("lines", []):
                    for s in line.get("spans", []):
                        for ch in s.get("chars", []):
                            page_chars.append(str(ch.get("c", "")))

        try:
            raw_words = page.get_text("words")
        except (RuntimeError, ValueError):
            raw_words = []

        char_pos = 0
        for w in raw_words:
            w_str = str(w[4]).strip()
            if not w_str:
                continue

            while char_pos < len(page_chars) and page_chars[char_pos] == " ":
                char_pos += 1

            w_start = char_pos
            w_end = min(len(page_chars) - 1, w_start + len(w_str) - 1) if page_chars else char_pos
            char_pos = max(char_pos + 1, w_end + 1)

            pdf_words.append(w_str)
            word_metadata.append((page_idx, w_start, w_end))

    doc.close()
    full_text = " ".join(pdf_words)
    return full_text, word_metadata





def _inline_imports(text: str, tex_dir: Path, seen: Optional[set[str]] = None) -> str:
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
        return ""

    for tex_file in tex_files:
        raw = tex_file.read_text(encoding="utf-8", errors="replace")
        begin = re.search(r"\\begin\{document\}", raw)
        end = re.search(r"\\end\{document\}", raw)
        if begin and end:
            body = raw[begin.end() : end.start()]
            body = _inline_imports(body, tex_dir)
            return re.sub(r"(?<!\\)%[^\n]*", "", body)

    first_raw = tex_files[0].read_text(encoding="utf-8", errors="replace")
    return _inline_imports(first_raw, tex_dir)


def tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text)


class WordMapping(NamedTuple):
    pdf_word_idx: Optional[int]
    tex_word_idx: Optional[int]


class ArxivDiffMapper:
    """
    Computes and stores the word-level diff and sourcemap bijection between
    a PDF document and its arXiv LaTeX source.
    """

    def __init__(self):
        self.pdf_text: str = ""
        self.tex_text: str = ""
        self.pdf_words: list[str] = []
        self.tex_words: list[str] = []
        self.word_metadata: list[tuple[int, int, int]] = []
        self.diff_opcodes: list[tuple] = []
        self.pdf_to_tex_map: dict[int, int] = {}
        self.tex_to_pdf_map: dict[int, int] = {}
        self.mapped_pdf_indices: set[int] = set()
        self.is_ready: bool = False

    def process(
        self,
        arxiv_id: str,
        pdf_path: Path,
        progress_callback: Optional[Callable[[float], None]] = None,
    ):
        t_start = time.perf_counter()

        if progress_callback:
            progress_callback(0.05)

        # 1. Download/extract source tarball if needed
        t0 = time.perf_counter()
        _, tex_dir = download_arxiv_source(arxiv_id)
        t_download = time.perf_counter() - t0

        if progress_callback:
            progress_callback(0.20)

        # 2. Extract PDF text & metadata
        t0 = time.perf_counter()
        self.pdf_text, self.word_metadata = extract_pdf_text_with_metadata(pdf_path)
        t_pdf_extract = time.perf_counter() - t0

        if progress_callback:
            progress_callback(0.35)

        # 3. Extract TeX body
        t0 = time.perf_counter()
        self.tex_text = extract_tex_body(tex_dir)
        t_tex_extract = time.perf_counter() - t0

        if progress_callback:
            progress_callback(0.45)

        # 4. Tokenize
        t0 = time.perf_counter()
        self.pdf_words = tokenize(self.pdf_text)
        self.tex_words = tokenize(self.tex_text)

        # 5. Compute word diff with SequenceMatcher
        matcher = SequenceMatcher(None, self.pdf_words, self.tex_words)
        opcodes = matcher.get_opcodes()
        self.diff_opcodes = opcodes
        t_diff = time.perf_counter() - t0

        if progress_callback:
            progress_callback(0.85)

        # 6. Build index mappings
        t0 = time.perf_counter()
        pdf_idx = 0
        tex_idx = 0
        for tag, i1, i2, j1, j2 in opcodes:
            if tag in ("equal", "replace"):
                p_len = i2 - i1
                t_len = j2 - j1
                common_len = max(p_len, t_len)
                for k in range(common_len):
                    curr_p = i1 + k if k < p_len else i2 - 1
                    curr_t = j1 + k if k < t_len else j2 - 1
                    if curr_p < len(self.pdf_words) and curr_t < len(self.tex_words):
                        self.pdf_to_tex_map[curr_p] = curr_t
                        self.tex_to_pdf_map[curr_t] = curr_p
            elif tag == "delete":
                # PDF words deleted in TeX
                last_t = tex_idx - 1 if tex_idx > 0 else 0
                for p in range(i1, i2):
                    self.pdf_to_tex_map[p] = last_t
            elif tag == "insert":
                # TeX words inserted
                last_p = pdf_idx - 1 if pdf_idx > 0 else 0
                for t in range(j1, j2):
                    self.tex_to_pdf_map[t] = last_p

            pdf_idx = i2
            tex_idx = j2

        t_map = time.perf_counter() - t0
        t_total = time.perf_counter() - t_start
        self.mapped_pdf_indices = set(self.tex_to_pdf_map.values())
        self.is_ready = True

        if progress_callback:
            progress_callback(1.0)

        print(
            f"[ArxivDiff] Timings for arXiv:{arxiv_id} | "
            f"Download/extract: {t_download:.3f}s, "
            f"PDF text: {t_pdf_extract:.3f}s ({len(self.pdf_words)} words), "
            f"TeX body: {t_tex_extract:.3f}s ({len(self.tex_words)} words), "
            f"Word diff: {t_diff:.3f}s, "
            f"Sourcemap build: {t_map:.3f}s | "
            f"Total: {t_total:.3f}s",
            file=sys.stderr,
            flush=True,
        )

    def find_pdf_word_range(self, page_index: int, start_char: int, end_char: int) -> tuple[int, int]:
        """Find the start and end PDF word indices for a given page character range."""
        if not self.word_metadata:
            return (0, 0)

        min_w = None
        max_w = None

        for idx, (p_idx, c_start, c_end) in enumerate(self.word_metadata):
            if p_idx == page_index:
                if c_end >= start_char and c_start <= end_char:
                    if min_w is None:
                        min_w = idx
                    max_w = idx

        if min_w is None or max_w is None:
            # Fallback: locate nearest word on page
            for idx, (p_idx, c_start, _) in enumerate(self.word_metadata):
                if p_idx == page_index:
                    if min_w is None:
                        min_w = idx
                    max_w = idx

        if min_w is None or max_w is None:
            return (0, 0)

        return (min_w, max_w)

    def get_latex_for_pdf_range(self, page_index: int, start_char: int, end_char: int) -> str:
        """Map selected PDF character range to raw LaTeX source snippet."""
        if not self.is_ready or not self.pdf_words or not self.tex_words:
            return ""

        w_start, w_end = self.find_pdf_word_range(page_index, start_char, end_char)
        return self.get_latex_for_pdf_words(w_start, w_end)

    def get_latex_for_pdf_words(self, pdf_start_word: int, pdf_end_word: int) -> str:
        if not self.is_ready or not self.tex_words:
            return ""

        pdf_start_word = max(0, min(pdf_start_word, len(self.pdf_words) - 1))
        pdf_end_word = max(0, min(pdf_end_word, len(self.pdf_words) - 1))

        if pdf_start_word > pdf_end_word:
            pdf_start_word, pdf_end_word = pdf_end_word, pdf_start_word

        tex_start = self.pdf_to_tex_map.get(pdf_start_word, 0)
        tex_end = self.pdf_to_tex_map.get(pdf_end_word, len(self.tex_words) - 1)

        if tex_start > tex_end:
            tex_start, tex_end = tex_end, tex_start

        tex_end = min(tex_end, len(self.tex_words) - 1)
        selected_tex_words = self.tex_words[tex_start : tex_end + 1]
        return " ".join(selected_tex_words)

    def get_cursor_fragment(
        self, page_index: int, char_index: int, window_words: int = 50
    ) -> tuple[str, str]:
        """
        Return (~50 words PDF text, ~50 words TeX source) around the given page character index.
        """
        if not self.is_ready or not self.pdf_words or not self.tex_words:
            return ("", "")

        center_word = 0
        found = False
        last_page_word = 0
        for idx, (p_idx, c_start, c_end) in enumerate(self.word_metadata):
            if p_idx == page_index:
                last_page_word = idx
                if c_start <= char_index <= c_end:
                    center_word = idx
                    found = True
                    break

                elif char_index < c_start:
                    center_word = idx
                    found = True
                    break

        if not found:
            center_word = last_page_word

        p_start = center_word
        p_end = min(len(self.pdf_words) - 1, center_word + window_words)


        pdf_fragment = " ".join(self.pdf_words[p_start : p_end + 1])
        tex_fragment = self.get_latex_for_pdf_words(p_start, p_end)


        return (pdf_fragment, tex_fragment)
