import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
RECENT_FILE = XDG_DATA_HOME / "pdfatlas" / "recent.json"
RECENT_MAX = 10


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



class RecentFilesManager:
    def __init__(self, path: Path = RECENT_FILE):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[PdfSource] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    self._entries.append(PdfSource(**item))
            except Exception:
                self._entries = []

    def _save(self):
        data = [asdict(e) for e in self._entries]
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, source: PdfSource):
        self._entries = [e for e in self._entries if e.uri != source.uri]
        self._entries.insert(0, source)
        if len(self._entries) > RECENT_MAX:
            self._entries.pop()
        self._save()

    def get_recent(self, n: int = 5) -> list[PdfSource]:
        return self._entries[:n]

    def get_recent_by_type(self, source_type: str, n: int = 10) -> list[PdfSource]:
        return [e for e in self._entries if e.source_type == source_type][:n]

    def get_by_uri(self, uri: str) -> PdfSource | None:
        try:
            norm_uri = os.path.abspath(uri)
        except Exception:
            norm_uri = uri
        for entry in self._entries:
            try:
                if os.path.abspath(entry.uri) == norm_uri or entry.uri == uri:
                    return entry
            except Exception:
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