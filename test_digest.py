"""digest.py 단위 테스트 — 네트워크 없이 돈다."""

from digest import generate_digest


def _scored_paper(arxiv_id, title, priority, core_hits=None, domain_hits=None,
                   venue_hit=None, abstract=""):
    return {
        "arxiv_id": arxiv_id, "title": title, "abstract": abstract,
        "_score": {
            "priority": priority, "core_hits": core_hits or [],
            "domain_hits": domain_hits or [], "venue_hit": venue_hit,
        },
    }


def test_generate_digest_reports_no_papers_case():
    result = {"papers": [], "candidates_found": 12, "excluded_count": 3, "unmatched_count": 9}
    text = generate_digest(result, "우리팀")
    assert "우리팀" in text
    assert "새로 걸린 논문이 없습니다" in text
    assert "12" in text


def test_generate_digest_includes_title_arxiv_link_and_match_reason():
    paper = _scored_paper("1706.03762", "Attention Is All You Need", 1.5,
                           core_hits=["agent"], domain_hits=["robot hand"])
    result = {"papers": [paper], "candidates_found": 1, "excluded_count": 0, "unmatched_count": 0}

    text = generate_digest(result, "우리팀")

    assert "Attention Is All You Need" in text
    assert "https://arxiv.org/abs/1706.03762" in text
    assert "agent" in text
    assert "robot hand" in text
    assert "[미검증 · 초록 기반]" in text  # ⑥ 승인을 대신하지 않는다는 표시가 항상 있어야 함


def test_generate_digest_stars_scale_with_priority():
    high = _scored_paper("a", "높은 점수", 1.5)
    mid = _scored_paper("b", "중간 점수", 0.8)
    low = _scored_paper("c", "낮은 점수", 0.1)
    result = {"papers": [high, mid, low], "candidates_found": 3, "excluded_count": 0, "unmatched_count": 0}

    text = generate_digest(result, "p")

    assert "[★★★] 높은 점수" in text
    assert "[★★] 중간 점수" in text
    assert "[★] 낮은 점수" in text


def test_generate_digest_truncates_long_abstract_and_labels_it_excerpt_not_summary():
    long_abstract = "x" * 500
    paper = _scored_paper("a", "제목", 1.0, abstract=long_abstract)
    result = {"papers": [paper], "candidates_found": 1, "excluded_count": 0, "unmatched_count": 0}

    text = generate_digest(result, "p")

    assert "초록 발췌" in text  # "요약"이라고 쓰면 안 됨 — LLM 요약 아님
    assert "…" in text
    assert long_abstract not in text  # 잘렸는지 확인


def test_generate_digest_reports_filtered_counts_when_present():
    paper = _scored_paper("a", "제목", 1.0)
    result = {"papers": [paper], "candidates_found": 5, "excluded_count": 2, "unmatched_count": 2}

    text = generate_digest(result, "p")

    assert "제외 규칙 2건" in text
    assert "조건 불일치 2건" in text
