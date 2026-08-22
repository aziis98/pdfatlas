import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class PdfSource:
    source_type: str
    uri: str
    display_name: str

    @property
    def is_arxiv(self) -> bool:
        if self.source_type == "arxiv":
            return True
        from .arxiv_mapper import arxiv_id_from_path
        return arxiv_id_from_path(self.uri) is not None


def get_recent_file_path() -> Path:
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return xdg_data / "pdfatlas" / "recent.json"


def is_test_path(uri: str) -> bool:
    """Check if a URI belongs to a test run or test artifact."""
    if "pytest-" in uri or "/tmp/pytest" in uri or "/pytest_cache" in uri:
        return True
    return False


class RecentFilesManager:
    def __init__(self, path: Path | None = None, max_entries: int | None = None):
        self._path = path if path is not None else get_recent_file_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._entries: list[PdfSource] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    src = PdfSource(**item)
                    if not is_test_path(src.uri):
                        self._entries.append(src)
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                self._entries = []

    def _save(self):
        data = [asdict(e) for e in self._entries if not is_test_path(e.uri)]
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, source: PdfSource):
        if is_test_path(source.uri):
            return
        self._entries = [e for e in self._entries if e.uri != source.uri and not is_test_path(e.uri)]
        self._entries.insert(0, source)
        if self.max_entries is not None and len(self._entries) > self.max_entries:
            self._entries.pop()
        self._save()

    def get_recent(self, n: int | None = None) -> list[PdfSource]:
        if n is None:
            return list(self._entries)
        return self._entries[:n]

    def get_recent_by_type(self, source_type: str, n: int = 10) -> list[PdfSource]:
        return [e for e in self._entries if e.source_type == source_type][:n]

    def get_by_uri(self, uri: str) -> PdfSource | None:
        try:
            norm_uri = os.path.abspath(uri)
        except OSError:
            norm_uri = uri
        for entry in self._entries:
            try:
                if os.path.abspath(entry.uri) == norm_uri or entry.uri == uri:
                    return entry
            except OSError:
                if entry.uri == uri:
                    return entry
        return None

    def get_by_arxiv_id(self, aid: str) -> PdfSource | None:
        from .arxiv_mapper import arxiv_id_from_path
        for entry in self._entries:
            entry_aid = arxiv_id_from_path(entry.uri)
            if entry_aid == aid:
                return entry
        return None

    def clear(self):
        self._entries.clear()
        self._save()