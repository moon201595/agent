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
    # 픽스처에서 "quantization"을 뺐다(2026-08-31) — 이 낱말에 다의어 가드가
    # 붙어서 ML 문맥이 없으면 적중으로 안 세게 됐기 때문이다. 이 테스트가 보려는
    # 것은 가드가 아니라 "핵심 적중이 많은 쪽이 도메인 가점 하나를 이긴다"라서,
    # 가드 없는 낱말로 바꿔 주장은 그대로 두었다. 가드 자체는 아래 별도 테스트가 본다.
    profile = {"core_topics": ["agent", "vision", "pruning"] + [f"kw{i}" for i in range(18)],
               "target_domain": ["digital twin"], "exclude": []}
    one_core_one_domain = _paper_with("an agent for digital twin systems")
    two_core = _paper_with("vision and pruning study")

    assert score_paper(two_core, profile)["priority"] > \
           score_paper(one_core_one_domain, profile)["priority"]


def test_core_hits_saturate_so_one_paper_cannot_dominate():
    """상한이 없으면 키워드를 쓸어담은 논문 하나가 다른 신호를 다 눌러버린다."""
    profile = {"core_topics": [f"kw{i}" for i in range(10)], "target_domain": [], "exclude": []}
    three = _paper_with("kw0 kw1 kw2 study")
    many = _paper_with("kw0 kw1 kw2 kw3 kw4 kw5 study")

    assert score_paper(three, profile)["priority"] == score_paper(many, profile)["priority"]


# ---------------------------------------------------------------- 2026-08-31 랭킹 개편

def test_heavier_core_keyword_outranks_lighter_one():
    """키워드를 좁히면 대부분의 논문이 핵심 1개만 맞힌다(실측 120/120). 그
    세계에서 유일하게 남은 정보는 "어떤 1개인지"이므로 가중치가 순위를 갈라야 한다."""
    profile = {"core_topics": ["defect detection", "sim-to-real"],
               "target_domain": [], "exclude": [],
               "core_weights": {"defect detection": 1.0, "sim-to-real": 0.6}}
    bullseye = _paper_with("a defect detection method")
    generic = _paper_with("a sim-to-real transfer method")
    assert score_paper(bullseye, profile)["priority"] > score_paper(generic, profile)["priority"]


def test_recency_no_longer_flips_topical_fit():
    """실측 회귀(2026-08-31): 무선통신 논문(sim-to-real + digital twin, 3일 최신)이
    PCB 핀 검사 논문(defect detection + PCB)을 1.0076 대 0.9757 로 눌렀다.
    최신성이 주제 적합도를 뒤집으면 안 된다."""
    profile = {"core_topics": ["defect detection", "sim-to-real"],
               "target_domain": ["PCB", "digital twin"], "exclude": [],
               "core_weights": {"defect detection": 1.0, "sim-to-real": 0.6}}
    off_topic_newer = _paper_with("sim-to-real for a digital twin of a radio network",
                                  published_days_ago=3)
    on_topic_older = _paper_with("defect detection on a PCB assembly line",
                                 published_days_ago=7)
    assert score_paper(on_topic_older, profile)["priority"] > \
           score_paper(off_topic_newer, profile)["priority"]


def test_polysemy_guard_rejects_quantization_without_ml_context():
    """실측 사례(CSymPlan, arXiv 2608.22983): 제어 상태공간 이산화를 뜻하는
    "quantization"이 모델 경량화 키워드로 걸렸다."""
    profile = {"core_topics": ["quantization"], "target_domain": [], "exclude": []}
    control = _paper_with("certified symbolic planning with state space quantization")
    assert score_paper(control, profile)["core_hits"] == []
    assert score_paper(control, profile)["priority"] == 0.0


def test_polysemy_guard_accepts_quantization_with_ml_context():
    """가드가 진짜 경량화 논문까지 막으면 그건 조용한 누락이다."""
    profile = {"core_topics": ["quantization"], "target_domain": [], "exclude": []}
    ml = _paper_with("post-training quantization of transformer weights to int8")
    assert score_paper(ml, profile)["core_hits"] == ["quantization"]


def test_plural_form_of_keyword_matches():
    """초록은 대부분 복수형으로 쓴다 — "event cameras"를 놓치면 통째로 못 본다."""
    profile = {"core_topics": ["event camera", "wearable biosensor"],
               "target_domain": [], "exclude": []}
    paper = _paper(abstract="We evaluate event cameras and wearable biosensors.")
    assert set(score_paper(paper, profile)["core_hits"]) == {"event camera", "wearable biosensor"}


def test_domain_bonus_is_capped():
    """도메인 낱말을 여럿 스치는 논문이 핵심 적합도를 압도하면 안 된다."""
    profile = {"core_topics": ["agent"],
               "target_domain": ["robot hand", "manipulator", "PCB", "wafer"], "exclude": []}
    two = _paper_with("agent for robot hand and manipulator")
    four = _paper_with("agent for robot hand and manipulator on PCB and wafer")
    assert score_paper(two, profile)["priority"] == score_paper(four, profile)["priority"]


def test_score_and_rank_reports_core_hit_counts_over_all_candidates():
    """동향 집계는 top_k 로 자르기 **전** 후보 전체를 세야 한다."""
    profile = {"core_topics": ["agent", "autofocus"], "target_domain": [], "exclude": []}
    papers = [_paper_with("agent one"), _paper_with("agent two"),
              _paper_with("autofocus study"), _paper_with("unrelated database paper")]
    result = score_and_rank(papers, profile, top_k=1)
    assert result["scored_count"] == 1              # 잘린 결과
    assert result["core_hit_counts"] == {"agent": 2, "autofocus": 1}   # 자르기 전 집계


def test_polysemy_guard_is_not_satisfied_by_generic_words():
    """실측 회귀(2026-08-31): 가드어를 부분문자열로 헐겁게 잡았더니 CSymPlan 이
    "modeling inaccuracies" 의 "model" 하나로 통과했다. 제어·로보틱스 논문에
    흔한 범용어가 가드를 열어주면 가드가 없는 것과 같다."""
    profile = {"core_topics": ["quantization"], "target_domain": [], "exclude": []}
    control = _paper_with(
        "refines the symbolic policy through a quantization--lookup--torque pipeline, "
        "treating modeling inaccuracies and measurement uncertainty as bounded disturbances")
    assert score_paper(control, profile)["core_hits"] == []


def test_guard_substring_does_not_leak_through_unrelated_word():
    """가드어를 단어 경계로 보지 않으면 "arbitrary" 안의 "bit" 같은 게 통과한다."""
    profile = {"core_topics": ["quantization"], "target_domain": [], "exclude": []}
    paper = _paper_with("arbitrary quantization of the abstract state space")
    assert score_paper(paper, profile)["core_hits"] == []
