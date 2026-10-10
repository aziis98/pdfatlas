from pdfatlas.core.search_cluster import cluster_search_results, merge_search_results


def test_empty_results():
    assert cluster_search_results([]) == []


def test_single_result():
    results = [{"id": 1, "page": 1, "y0": 100.0, "y1": 150.0}]
    clusters = cluster_search_results(results, default_page_height=1000.0)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.page == 1
    assert c.region == "Top"
    assert c.title == "Page 1"
    assert "Top" in c.subtitle
    assert "1 match" in c.subtitle
    assert len(c.results) == 1


def test_linear_grouping_small_gap_and_span():
    # Gap is 50 pt (5% of 1000), total span is 150 pt (15% of 1000)
    results = [
        {"id": 1, "page": 1, "y0": 100.0, "y1": 150.0},
        {"id": 2, "page": 1, "y0": 200.0, "y1": 250.0},
    ]
    clusters = cluster_search_results(results, default_page_height=1000.0)
    assert len(clusters) == 1
    assert len(clusters[0].results) == 2
    assert clusters[0].title == "Page 1"
    assert "2 matches" in clusters[0].subtitle


def test_gap_threshold_splits_clusters():
    # Gap between y1=150 and y0=500 is 350 pt (35% of 1000 >= 30% threshold)
    results = [
        {"id": 1, "page": 1, "y0": 100.0, "y1": 150.0},
        {"id": 2, "page": 1, "y0": 500.0, "y1": 550.0},
    ]
    clusters = cluster_search_results(results, min_gap_ratio=0.30, default_page_height=1000.0)
    assert len(clusters) == 2
    assert clusters[0].title == "Page 1 • Top"
    assert clusters[1].title == "Page 1 • Middle"
    assert len(clusters[0].results) == 1
    assert len(clusters[1].results) == 1


def test_max_span_splits_clusters():
    # Gaps are small (50 pt), but accumulated span exceeds 50% (500 pt)
    results = [
        {"id": 1, "page": 1, "y0": 100.0, "y1": 150.0},
        {"id": 2, "page": 1, "y0": 200.0, "y1": 250.0},
        {"id": 3, "page": 1, "y0": 300.0, "y1": 350.0},
        {"id": 4, "page": 1, "y0": 400.0, "y1": 450.0},
        {"id": 5, "page": 1, "y0": 500.0, "y1": 550.0},
        {"id": 6, "page": 1, "y0": 620.0, "y1": 670.0},  # Potential span 100..670 = 570 > 500
    ]
    clusters = cluster_search_results(results, max_span_ratio=0.50, default_page_height=1000.0)
    assert len(clusters) == 2
    assert len(clusters[0].results) == 5
    assert len(clusters[1].results) == 1
    assert clusters[1].results[0]["id"] == 6


def test_multi_page_separation():
    results = [
        {"id": 1, "page": 1, "y0": 100.0, "y1": 150.0},
        {"id": 2, "page": 2, "y0": 100.0, "y1": 150.0},
    ]
    clusters = cluster_search_results(results, default_page_height=1000.0)
    assert len(clusters) == 2
    assert clusters[0].page == 1
    assert clusters[1].page == 2


def test_page_height_getter_callback():
    results = [
        {"id": 1, "page": 1, "y0": 100.0, "y1": 150.0},
        {"id": 2, "page": 1, "y0": 350.0, "y1": 400.0},
    ]
    # On a 500pt page, gap = 350 - 150 = 200 (40% >= 30%) -> 2 clusters
    clusters_short = cluster_search_results(
        results,
        page_height_getter=lambda p: 500.0,
    )
    assert len(clusters_short) == 2

    # On a 2000pt page, gap = 200 (10% < 30%) and span = 300 (15% < 50%) -> 1 cluster
    clusters_tall = cluster_search_results(
        results,
        page_height_getter=lambda p: 2000.0,
    )
    assert len(clusters_tall) == 1


def test_merge_empty_results():
    assert merge_search_results([]) == []


def test_merge_single_result():
    results = [{"id": 42, "page": 3, "x0": 50.0, "y0": 120.0, "x1": 400.0, "y1": 160.0}]
    merged = merge_search_results(results, default_page_height=1000.0)
    assert len(merged) == 1
    m = merged[0]
    assert m["id"] == 42
    assert m["page"] == 3
    assert m["match_count"] == 1
    assert m["merged_ids"] == [42]
    assert m["y0"] == 120.0
    assert m["y1"] == 160.0


def test_merge_nearby_results_on_same_page():
    # 4 matches on Page 3 like in user scenario (e.g. searching "stack")
    results = [
        {"id": 1, "page": 3, "x0": 50.0, "y0": 100.0, "x1": 300.0, "y1": 120.0},
        {"id": 2, "page": 3, "x0": 60.0, "y0": 125.0, "x1": 310.0, "y1": 145.0},
        {"id": 3, "page": 3, "x0": 55.0, "y0": 150.0, "x1": 320.0, "y1": 170.0},
        {"id": 4, "page": 3, "x0": 50.0, "y0": 180.0, "x1": 330.0, "y1": 200.0},
    ]
    merged = merge_search_results(results, default_page_height=1000.0)
    assert len(merged) == 1
    m = merged[0]
    assert m["id"] == 1
    assert m["page"] == 3
    assert m["match_count"] == 4
    assert m["merged_ids"] == [1, 2, 3, 4]
    assert m["x0"] == 50.0
    assert m["x1"] == 330.0
    assert m["y0"] == 100.0
    assert m["y1"] == 200.0


def test_merge_separates_distant_matches_and_different_pages():
    results = [
        {"id": 1, "page": 1, "x0": 50.0, "y0": 100.0, "x1": 300.0, "y1": 120.0},
        {"id": 2, "page": 1, "x0": 50.0, "y0": 600.0, "x1": 300.0, "y1": 620.0},  # distant gap on p1
        {"id": 3, "page": 2, "x0": 50.0, "y0": 100.0, "x1": 300.0, "y1": 120.0},  # p2
    ]
    merged = merge_search_results(results, min_gap_ratio=0.30, default_page_height=1000.0)
    assert len(merged) == 3
    assert merged[0]["id"] == 1 and merged[0]["page"] == 1 and merged[0]["match_count"] == 1
    assert merged[1]["id"] == 2 and merged[1]["page"] == 1 and merged[1]["match_count"] == 1
    assert merged[2]["id"] == 3 and merged[2]["page"] == 2 and merged[2]["match_count"] == 1


def test_merge_query_terms_match_counting():
    results = [
        {"id": 1, "page": 1, "text": "The apple and the orange and the pear", "x0": 10, "y0": 100, "x1": 200, "y1": 150},
        {"id": 2, "page": 1, "text": "The banana and the kiwi", "x0": 10, "y0": 160, "x1": 200, "y1": 200},
    ]
    merged = merge_search_results(results, query_terms={"the"}, default_page_height=1000.0)
    assert len(merged) == 1
    # "the" appears 3 times in r1, 2 times in r2 -> total 5 matches
    assert merged[0]["match_count"] == 5


