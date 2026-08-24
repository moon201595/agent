"""profile_scoring.py 단위 테스트 — 네트워크 없이 돈다."""

from datetime import datetime, timedelta, timezone

from profile_scoring import Weights, recency_score, score_and_rank, score_paper, venue_hit

PROFILE = {
    "core_topics": ["agent", "digital twin"],
    "target_domain": ["robot hand", "edge sensing"],
    "exclude": ["medical"],
    "venues": ["IEEE TII"],
}


def _paper(title="", abstract="", published=None, venue=None):
    p = {"title": title, "abstract": abstract}
    if published is not None:
        p["published"] = published
    if venue is not None:
        p["venue"] = venue
    return p


def test_exclude_wins_over_core_topic_match():
    paper = _paper(title="An agent for medical diagnosis")
    result = score_paper(paper, PROFILE)
    assert result["excluded"] is True
    assert result["exclude_hits"] == ["medical"]
    assert result["priority"] == 0.0


def test_no_core_topic_hit_scores_zero_but_not_excluded():
    paper = _paper(title="A survey of database indexing")
    result = score_paper(paper, PROFILE)
    assert result["excluded"] is False
    assert result["core_hits"] == []
    assert result["priority"] == 0.0


def test_word_boundary_avoids_false_positive_substring_match():
    """"AI"가 "domain" 안에 우연히 들어있는 경우를 매칭으로 치면 안 된다."""
    profile = {"core_topics": ["AI"], "target_domain": [], "exclude": []}
    paper = _paper(title="A study of the problem domain")
    result = score_paper(paper, profile)
    assert result["core_hits"] == []
    assert result["priority"] == 0.0


def test_multi_word_keyword_matches_as_phrase():
    paper = _paper(abstract="We build a digital twin of the manufacturing line.")
    result = score_paper(paper, PROFILE)
    assert "digital twin" in result["core_hits"]


def test_domain_hits_add_bonus_on_top_of_core_relevance():
    base = _paper(title="An agent framework")
    with_domain = _paper(title="An agent framework", abstract="for robot hand control")

    base_score = score_paper(base, PROFILE)["priority"]
    domain_score = score_paper(with_domain, PROFILE)["priority"]

    assert domain_score > base_score


def test_venue_hit_is_none_when_paper_has_no_venue_field():
    """s2_search_papers가 지금 venue를 안 주므로(server.py 확인) 실전 기본값."""
    paper = _paper(title="An agent framework")
    result = score_paper(paper, PROFILE)
    assert result["venue_hit"] is None


def test_venue_hit_true_when_profile_venue_is_substring_of_paper_venue():
    paper = _paper(title="An agent framework",
                    venue="IEEE Transactions on Industrial Informatics (IEEE TII)")
    result = score_paper(paper, PROFILE)
    assert result["venue_hit"] is True


def test_recency_score_prefers_newer_paper():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert recency_score(recent, half_life_days=30) > recency_score(old, half_life_days=30)


def test_recency_score_none_for_missing_or_unparseable_date():
    assert recency_score(None, half_life_days=30) is None
    assert recency_score("2026", half_life_days=30) is None  # S2 스타일 연도만 있는 경우


def test_missing_recency_does_not_penalize_relative_to_zero_recency():
    """recency를 모르면 0점(최악)이 아니라 그 항목이 통째로 안 더해져야 한다 —
    아주 오래된 논문(recency≈0)보다는 점수가 같거나 높아야 한다."""
    no_date = _paper(title="An agent framework")
    ancient = _paper(title="An agent framework",
                      published="2000-01-01T00:00:00Z")
    assert score_paper(no_date, PROFILE)["priority"] >= score_paper(ancient, PROFILE)["priority"]


def test_score_and_rank_sorts_by_priority_and_counts_buckets():
    papers = [
        _paper(title="unrelated database paper"),                       # unmatched
        _paper(title="agent for medical use"),                          # excluded
        _paper(title="agent framework", abstract="robot hand control",  # 높은 점수
               published=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        _paper(title="a plain agent paper"),                            # core만 걸림, 낮은 점수
    ]

    result = score_and_rank(papers, PROFILE)

    assert result["input_count"] == 4
    assert result["excluded_count"] == 1
    assert result["unmatched_count"] == 1
    assert result["scored_count"] == 2
    assert result["papers"][0]["title"] == "agent framework"  # domain+recency로 1등


def test_score_and_rank_respects_top_k():
    papers = [_paper(title=f"agent paper {i}") for i in range(5)]
    result = score_and_rank(papers, PROFILE, top_k=2)
    assert result["scored_count"] == 2
    assert len(result["papers"]) == 2


def test_custom_weights_are_applied():
    paper = _paper(title="agent framework", abstract="robot hand control")
    low = score_paper(paper, PROFILE, Weights(domain_hit=0.1))["priority"]
    high = score_paper(paper, PROFILE, Weights(domain_hit=1.0))["priority"]
    assert high > low
