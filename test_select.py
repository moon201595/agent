"""② 중복 제거·선별 단위 테스트 — 네트워크 없이 돈다."""

from selection import dedupe, dedupe_and_rank, norm_title, rank


def test_norm_title_ignores_case_and_punctuation():
    assert norm_title("Attention Is All You Need!") == norm_title("attention is all-you need")


def test_dedupe_merges_by_arxiv_id_filling_empty_fields():
    thin = {"arxiv_id": "1706.03762", "title": "Attention", "abstract": ""}
    rich = {"arxiv_id": "1706.03762", "title": "Attention", "abstract": "긴 초록",
            "citation_count": 100000}

    out = dedupe([thin, rich])

    assert len(out) == 1
    assert out[0]["abstract"] == "긴 초록"
    assert out[0]["citation_count"] == 100000


def test_dedupe_merges_across_sources_when_only_title_matches():
    """S2 결과에 arxiv_id 가 없는 경우 — 제목으로 붙어야 인용수가 arXiv 레코드에 실린다."""
    arxiv = {"arxiv_id": "1706.03762", "title": "Attention Is All You Need"}
    s2 = {"arxiv_id": None, "title": "attention is all you need", "citation_count": 12345,
          "year": 2017}

    out = dedupe([arxiv, s2])

    assert len(out) == 1
    assert out[0]["arxiv_id"] == "1706.03762"
    assert out[0]["citation_count"] == 12345
    assert out[0]["year"] == 2017


def test_dedupe_keeps_zero_citation_count():
    """0 은 '모름'이 아니라 유효한 인용수다. None 으로 덮어써서는 안 된다."""
    out = dedupe([
        {"title": "A", "citation_count": 0},
        {"title": "A", "citation_count": None},
    ])
    assert out[0]["citation_count"] == 0


def test_dedupe_keeps_different_papers_and_preserves_order():
    out = dedupe([{"title": "First"}, {"title": "Second"}, {"title": "Third"}])
    assert [p["title"] for p in out] == ["First", "Second", "Third"]


def test_rank_orders_by_citations_then_year():
    papers = [
        {"title": "A", "citation_count": 10, "year": 2020},
        {"title": "B", "citation_count": 99, "year": 2019},
        {"title": "C", "citation_count": 10, "year": 2024},
    ]
    assert [p["title"] for p in rank(papers, 3)] == ["B", "C", "A"]


def test_rank_treats_unknown_citations_as_zero():
    papers = [{"title": "A"}, {"title": "B", "citation_count": 3}]
    assert [p["title"] for p in rank(papers, 2)] == ["B", "A"]


def test_dedupe_and_rank_reports_each_stage_count():
    papers = [
        {"arxiv_id": "1", "title": "A", "citation_count": 5},
        {"arxiv_id": "1", "title": "A"},
        {"arxiv_id": "2", "title": "B", "citation_count": 9},
        {"arxiv_id": "3", "title": "C", "citation_count": 1},
    ]
    out = dedupe_and_rank(papers, top_k=2)

    assert out["input_count"] == 4
    assert out["deduped_count"] == 3
    assert out["selected_count"] == 2
    assert [p["title"] for p in out["papers"]] == ["B", "A"]
