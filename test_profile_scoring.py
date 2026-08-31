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


# ---------------------------------------------------------------- 키워드 개수 희석 방지


def _paper_with(title, published_days_ago=1):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(days=published_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _paper(title=title, published=ts)


def test_relevance_does_not_shrink_when_more_keywords_registered():
    """실측으로 잡은 결함(2026-08-31): 예전엔 relevance = 적중/전체키워드라
    키워드를 자세히 적을수록 개당 기여가 쪼그라들었다. 키워드 21개를 넣으니
    1개 적중이 0.048점이 되어 최신성(최대 0.4)이 핵심 8개 적중과 맞먹었다.
    "더 자세히 적었더니 랭킹이 나빠진다"는 명백한 결함이라 개수 기준으로 바꿨다."""
    small = {"core_topics": ["agent", "vision"], "target_domain": [], "exclude": []}
    large = {"core_topics": ["agent", "vision"] + [f"kw{i}" for i in range(19)],
             "target_domain": [], "exclude": []}
    paper = _paper_with("an agent paper")

    s_small = score_paper(paper, small)["priority"]
    s_large = score_paper(paper, large)["priority"]

    assert s_small == s_large  # 키워드 목록 길이가 점수를 바꾸면 안 된다


def test_more_core_hits_beats_fewer_even_with_domain_bonus():
    """실측 사례 회귀: 핵심 1개 + 도메인 1개짜리 무관한 논문이 핵심 2개짜리를
    이겼다. 핵심 적중이 도메인 가점 하나에 밀리면 안 된다."""
    profile = {"core_topics": ["agent", "vision", "quantization"] + [f"kw{i}" for i in range(18)],
               "target_domain": ["digital twin"], "exclude": []}
    one_core_one_domain = _paper_with("an agent for digital twin systems")
    two_core = _paper_with("vision and quantization study")

    assert score_paper(two_core, profile)["priority"] > \
           score_paper(one_core_one_domain, profile)["priority"]


def test_core_hits_saturate_so_one_paper_cannot_dominate():
    """상한이 없으면 키워드를 쓸어담은 논문 하나가 다른 신호를 다 눌러버린다."""
    profile = {"core_topics": [f"kw{i}" for i in range(10)], "target_domain": [], "exclude": []}
    three = _paper_with("kw0 kw1 kw2 study")
    many = _paper_with("kw0 kw1 kw2 kw3 kw4 kw5 study")

    assert score_paper(three, profile)["priority"] == score_paper(many, profile)["priority"]
