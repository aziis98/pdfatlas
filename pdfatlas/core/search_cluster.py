from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class SearchCluster:
    """A cluster of search result cards sharing a page and vertical region."""

    page: int
    region: str
    y_min: float
    y_max: float
    y_min_ratio: float
    y_max_ratio: float
    results: list[dict[str, Any]]
    total_page_clusters: int = 1

    @property
    def title(self) -> str:
        if self.total_page_clusters > 1 and self.region:
            return f"Page {self.page} • {self.region}"
        return f"Page {self.page}"

    @property
    def subtitle(self) -> str:
        pct_start = int(round(self.y_min_ratio * 100))
        pct_end = int(round(self.y_max_ratio * 100))
        count = len(self.results)
        unit = "match" if count == 1 else "matches"
        if self.total_page_clusters == 1 and self.region:
            return f"{self.region} ({pct_start}%–{pct_end}%) • {count} {unit}"
        return f"{pct_start}%–{pct_end}% • {count} {unit}"


def cluster_search_results(
    results: list[dict[str, Any]],
    min_gap_ratio: float = 0.30,
    max_span_ratio: float = 0.50,
    default_page_height: float = 792.0,
    page_height_getter: Callable[[int], float] | None = None,
) -> list[SearchCluster]:
    """
    Cluster search results per-page along the vertical y-axis.

    Splits results into a new cluster when:
    - The gap between consecutive items is >= min_gap_ratio * page_height (default 30%).
    - Adding the item would cause the cluster's vertical span to exceed max_span_ratio * page_height (default 50%).
    """
    if not results:
        return []

    # Sort results by (page, y0, x0)
    sorted_results = sorted(
        results,
        key=lambda r: (
            int(r.get("page", 1)),
            float(r.get("y0", 0.0)),
            float(r.get("x0", 0.0)),
        ),
    )

    # Group by page
    pages_dict: dict[int, list[dict[str, Any]]] = {}
    for r in sorted_results:
        p = int(r.get("page", 1))
        pages_dict.setdefault(p, []).append(r)

    clusters: list[SearchCluster] = []

    for page, page_results in pages_dict.items():
        # Determine page height
        page_h = default_page_height
        if page_height_getter is not None:
            try:
                h = page_height_getter(page)
                if h > 0:
                    page_h = float(h)
            except Exception:
                page_h = default_page_height

        # Safety fallback against tiny/zero heights
        max_y1 = max((float(r.get("y1", 0.0)) for r in page_results), default=0.0)
        page_h = max(page_h, max_y1 * 1.05, 100.0)

        gap_threshold = min_gap_ratio * page_h
        max_span = max_span_ratio * page_h

        # Greedily build raw clusters for this page
        raw_clusters: list[list[dict[str, Any]]] = []
        curr: list[dict[str, Any]] = []
        curr_min_y = 0.0
        curr_max_y = 0.0

        for r in page_results:
            y0 = float(r.get("y0", 0.0))
            y1 = float(r.get("y1", y0))

            if not curr:
                curr = [r]
                curr_min_y = y0
                curr_max_y = max(y0, y1)
                continue

            prev = curr[-1]
            prev_y1 = float(prev.get("y1", prev.get("y0", 0.0)))
            gap = max(0.0, y0 - prev_y1)

            new_min_y = min(curr_min_y, y0)
            new_max_y = max(curr_max_y, y1)
            potential_span = new_max_y - new_min_y

            if gap >= gap_threshold or (potential_span > max_span and len(curr) >= 1):
                raw_clusters.append(curr)
                curr = [r]
                curr_min_y = y0
                curr_max_y = max(y0, y1)
            else:
                curr.append(r)
                curr_min_y = new_min_y
                curr_max_y = new_max_y

        if curr:
            raw_clusters.append(curr)

        total_clusters_on_page = len(raw_clusters)

        for raw in raw_clusters:
            c_y_min = min(float(r.get("y0", 0.0)) for r in raw)
            c_y_max = max(float(r.get("y1", r.get("y0", 0.0))) for r in raw)
            y_min_ratio = max(0.0, min(1.0, c_y_min / page_h))
            y_max_ratio = max(0.0, min(1.0, c_y_max / page_h))
            y_mid_ratio = (y_min_ratio + y_max_ratio) / 2.0

            if y_mid_ratio < 0.35:
                region = "Top"
            elif y_mid_ratio > 0.65:
                region = "Bottom"
            else:
                region = "Middle"

            clusters.append(
                SearchCluster(
                    page=page,
                    region=region,
                    y_min=c_y_min,
                    y_max=c_y_max,
                    y_min_ratio=y_min_ratio,
                    y_max_ratio=y_max_ratio,
                    results=raw,
                    total_page_clusters=total_clusters_on_page,
                )
            )

    return clusters


def count_query_matches_in_blocks(
    results: list[dict[str, Any]],
    query_terms: set[str] | list[str] | None = None,
) -> int:
    """
    Counts total occurrences of query terms across all text blocks in the cluster.
    Falls back to match_count or 1 if no query_terms or no text is provided.
    """
    if not query_terms:
        return sum(int(r.get("match_count", 1)) for r in results)

    clean_terms = [t.lower() for t in query_terms if t]
    if not clean_terms:
        return sum(int(r.get("match_count", 1)) for r in results)

    total = 0
    for r in results:
        text = str(r.get("text", "")).lower()
        if not text:
            total += int(r.get("match_count", 1))
            continue
        block_matches = 0
        for qt in clean_terms:
            block_matches += text.count(qt)
        total += max(1, block_matches)

    return total


def merge_search_results(
    results: list[dict[str, Any]],
    query_terms: set[str] | list[str] | None = None,
    min_gap_ratio: float = 0.30,
    max_span_ratio: float = 0.50,
    default_page_height: float = 792.0,
    page_height_getter: Callable[[int], float] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge nearby search results on the same page into unified result entries.

    Consecutive results with gap < min_gap_ratio * page_height and total
    span <= max_span_ratio * page_height are combined into a single entry
    covering their bounding envelope [min_x0, min_y0, max_x1, max_y1].
    """
    clusters = cluster_search_results(
        results,
        min_gap_ratio=min_gap_ratio,
        max_span_ratio=max_span_ratio,
        default_page_height=default_page_height,
        page_height_getter=page_height_getter,
    )

    merged_results: list[dict[str, Any]] = []
    for c in clusters:
        if not c.results:
            continue
        first = c.results[0]
        min_x0 = min(float(r.get("x0", 0.0)) for r in c.results)
        max_x1 = max(float(r.get("x1", 0.0)) for r in c.results)
        min_y0 = min(float(r.get("y0", 0.0)) for r in c.results)
        max_y1 = max(float(r.get("y1", 0.0)) for r in c.results)

        merged_ids: list[Any] = []
        for r in c.results:
            if "merged_ids" in r and isinstance(r["merged_ids"], list):
                merged_ids.extend(r["merged_ids"])
            elif "id" in r:
                merged_ids.append(r["id"])

        total_matches = count_query_matches_in_blocks(c.results, query_terms)

        entry = dict(first)
        entry["x0"] = min_x0
        entry["y0"] = min_y0
        entry["x1"] = max_x1
        entry["y1"] = max_y1
        entry["match_count"] = total_matches
        entry["merged_ids"] = merged_ids
        entry["cluster_region"] = c.region
        merged_results.append(entry)

    return merged_results

