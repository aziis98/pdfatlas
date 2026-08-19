from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from .index import DatabaseService


@dataclass(slots=True)
class SearchResult:
    """Represents a single match from a search query across a document."""

    id: int
    page: int
    snippet: str
    x0: float
    y0: float
    x1: float
    y1: float
    filepath: str
    doc_title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page": self.page,
            "snippet": self.snippet,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "filepath": self.filepath,
            "doc_title": self.doc_title,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], filepath: str = "", doc_title: str = "") -> SearchResult:
        return cls(
            id=data.get("id", 0),
            page=data.get("page", 1),
            snippet=data.get("snippet", ""),
            x0=float(data.get("x0", 0.0)),
            y0=float(data.get("y0", 0.0)),
            x1=float(data.get("x1", 0.0)),
            y1=float(data.get("y1", 0.0)),
            filepath=data.get("filepath", filepath),
            doc_title=data.get("doc_title", doc_title),
            extra={
                k: v
                for k, v in data.items()
                if k not in ("id", "page", "snippet", "x0", "y0", "x1", "y1", "filepath", "doc_title")
            },
        )


class SearchProvider(Protocol):
    """Protocol for search result providers (single document, multi-document, etc.)."""

    def search(
        self,
        query: str,
        limit: int,
        search_id: int,
        on_results: Callable[[list[SearchResult], int], None],
    ) -> None:
        """Executes search query asynchronously and invokes on_results(results, search_id)."""
        ...


class SingleDocumentSearchProvider:
    """SearchProvider implementation querying a single active PDF's FTS5 index via DatabaseService."""

    def __init__(self, db_service: DatabaseService, filepath: str = "", doc_title: str = ""):
        self.db_service = db_service
        self.filepath = filepath
        self.doc_title = doc_title

    def search(
        self,
        query: str,
        limit: int,
        search_id: int,
        on_results: Callable[[list[SearchResult], int], None],
    ) -> None:
        def _bridge_callback(raw_results: list[dict[str, Any]], sid: int):
            structured = [
                SearchResult.from_dict(r, filepath=self.filepath, doc_title=self.doc_title)
                for r in raw_results
            ]
            on_results(structured, sid)

        self.db_service.search(query, limit=limit, search_id=search_id, on_results=_bridge_callback)

