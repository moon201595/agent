"""profile_scoring.py 단위 테스트 — 네트워크 없이 돈다."""

from datetime import datetime, timedelta, timezone

import pytest

from profile_scoring import Weights, recency_score, score_and_rank, score_paper

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


# ---------------------------------------------------------------- 가중치 상쇄 (2026-09-02)


def test_domain_bonus_must_not_cancel_the_tier_gap():
    """실측 결함(2026-09-02): 도메인 가점이 0.2 였는데 계층 격차도
    (1.0-0.6)/2.0 = 0.20 이라 정확히 상쇄됐다. 표적어 논문과 동향어+도메인
    논문의 점수가 **완전히 같아져**, 계층을 둔 의미가 사라졌다.

    그날 상위 6편이 0.6191~0.6422(폭 0.023)로 뭉쳐 순위가 사실상 무작위였다.
    이 테스트가 그 파라미터 조합으로 되돌아가는 걸 막는다."""
    profile = {"core_topics": ["defect detection", "sim-to-real"],
               "target_domain": ["digital twin"], "exclude": [],
               "core_weights": {"defect detection": 1.0, "sim-to-real": 0.6}}
    target_only = _paper_with("a defect detection method")
    trend_with_domain = _paper_with("sim-to-real for a digital twin")

    assert score_paper(target_only, profile)["priority"] > \
           score_paper(trend_with_domain, profile)["priority"]


def test_one_target_hit_beats_two_trend_hits():
    """**실측 회귀**(2026-09-06). 별점은 계층(최댓값)을 보는데 순위는 합을
    봐서, 동향어 두 개(0.6+0.6=1.2)가 표적어 하나(1.0)를 이겼다. 그 결과
    상위 6칸이 전부 ★ 이 되고 팀 표적 분야가 메일에서 사라졌다.

    순위와 별점이 같은 것을 봐야 한다 — 안 그러면 "★★ 인데 8위"가 생긴다.
    """
    profile = {"core_topics": ["defect detection", "robot learning",
                               "vision-language-action"],
               "target_domain": [], "exclude": [],
               "core_weights": {"defect detection": 1.0, "robot learning": 0.6,
                                "vision-language-action": 0.6}}
    target = _paper_with("a defect detection method")
    two_trends = _paper_with("robot learning with a vision-language-action policy")

    assert score_paper(target, profile)["priority"] > \
           score_paper(two_trends, profile)["priority"]


def test_breadth_still_separates_within_a_tier():
    """계층이 이겨야 한다고 폭을 죽이면 안 된다 — 같은 계층 안에서는
    두 개념을 다룬 논문이 하나만 다룬 논문보다 위다."""
    profile = {"core_topics": ["robot learning", "vision-language-action"],
               "target_domain": [], "exclude": [],
               "core_weights": {"robot learning": 0.6, "vision-language-action": 0.6}}
    one = _paper_with("robot learning from demonstrations")
    two = _paper_with("robot learning with a vision-language-action policy")

    assert score_paper(two, profile)["priority"] > score_paper(one, profile)["priority"]


def test_full_text_route_is_reported_but_does_not_score():
    """**동점 가르개(+0.05)를 넣었다가 뺐다**(2026-09-06, 같은 날).

    처음엔 관련도 동점(실측: 상위 10편이 전부 0.933)을 가르려고 점수에 넣었다.
    그런데 자리 배분을 `run_profile_scan._full_text_slots` 가 문지기로 처리하게
    되면서, 내용 자리에 오는 논문은 **전부** 이 조건을 만족하게 됐다 — 가르개가
    가를 게 없어진 것이다. 아무것도 안 하는 항을 남기지 않는다.

    판정 자체는 남는다: 문지기가 이 함수를 쓰고, score_paper 출력의 `full_text`
    키로 사람이 볼 수 있다. 이 테스트가 "점수에 안 들어간다 + 판정은 보인다"를
    같이 못박는다.
    """
    profile = {"core_topics": ["defect detection"], "target_domain": [], "exclude": [],
               "core_weights": {"defect detection": 1.0}}
    same = "a defect detection method"
    with_text = score_paper({**_paper_with(same), "arxiv_id": "2609.1"}, profile)
    without = score_paper({**_paper_with(same), "arxiv_id": None}, profile)

    assert with_text["priority"] == without["priority"]   # 점수는 같다
    assert with_text["full_text"] is True                 # 판정은 보인다
    assert without["full_text"] is False


def test_full_text_route_accepts_arxiv_or_open_access_link():
    """저널이라고 무조건 아니라고 하지 않는다 — 소형 OA 저널은 실측 6/8 로
    실제 PDF 를 준다(2026-09-06, 표본 20편)."""
    assert profile_scoring.has_full_text_route({"arxiv_id": "2609.1"}) is True
    assert profile_scoring.has_full_text_route(
        {"arxiv_id": None, "open_access_pdf": "https://dergipark.org.tr/x.pdf"}) is True
    assert profile_scoring.has_full_text_route(
        {"arxiv_id": None, "doi": "10.1016/j.x"}) is False


def test_domain_weight_is_strictly_below_the_tier_gap():
    """산수로 못박는다 — 파라미터를 손대도 이 관계가 깨지면 안 된다."""
    w = Weights()
    # 격차 = "표적어 1개"(1.0×BASE) − "동향어 2개"(0.6×1.0). 산식이 바뀌었으므로
    # 상수가 아니라 **실제 점수 두 개의 차이**로 잰다 — 그래야 다음에 산식을
    # 손대도 이 관계가 자동으로 다시 검사된다.
    profile = {"core_topics": ["defect detection", "robot learning",
                               "vision-language-action"],
               "target_domain": [], "exclude": [],
               "core_weights": {"defect detection": 1.0, "robot learning": 0.6,
                                "vision-language-action": 0.6}}
    hi = score_paper(_paper_with("a defect detection method"), profile)
    lo = score_paper(_paper_with("robot learning with a vision-language-action policy"),
                     profile)
    tier_gap = hi["priority"] - lo["priority"]
    assert tier_gap == pytest.approx(0.2, abs=1e-6)   # 옛 공식의 격차와 같다
    assert w.domain_hit < tier_gap


def test_containing_keyword_absorbs_the_shorter_one():
    """**실측 회귀**(2026-09-06). core_topics 에 'defect detection' 과
    'micro defect detection' 이 둘 다 있으면, 논문이 "micro defect detection"
    한 번만 써도 적중이 2개가 되어 relevance 가 0.5 → 1.0 으로 뛴다.

    그 차이(+0.500)는 계층 차이(+0.200)나 도메인 가점(+0.200)보다 크고,
    7일 창에서 최신성이 낼 수 있는 최대 차이(+0.022)의 23배다. 실제로
    2026-09-06 메일의 1위(PhyHGNet, priority 1.133 ★★★)가 이것이었다.
    """
    profile = {"core_topics": ["defect detection", "micro defect detection"],
               "core_weights": {"defect detection": 1.0, "micro defect detection": 1.0}}
    paper = {"title": "Physics guided micro defect detection in PV imaging", "abstract": ""}
    hits, total, top = profile_scoring.core_hits_with_weight(paper, profile)
    assert hits == ["micro defect detection"]     # 긴 쪽만 남는다
    assert total == 1.0                            # 2.0 이 아니다

    # **공식이 아니라 관계를 못박는다** — 한 문구만 쓴 논문이 진짜로 두 개념을
    # 다룬 논문과 같은 점수를 받으면 안 된다. 산식이 바뀌어도 이건 유지돼야 한다.
    two = {"core_topics": ["defect detection", "surface inspection"],
           "core_weights": {"defect detection": 1.0, "surface inspection": 1.0}}
    genuine = {"title": "surface inspection with defect detection", "abstract": ""}
    assert (profile_scoring.score_paper(paper, profile)["priority"]
            < profile_scoring.score_paper(genuine, two)["priority"])


def test_genuinely_distinct_concepts_still_count_twice():
    """흡수는 **포함관계일 때만**이다. 서로 다른 개념 둘은 그대로 2적중이고,
    그건 부풀림이 아니라 진짜 신호다 — 이걸 같이 죽이면 고친 게 아니다."""
    profile = {"core_topics": ["in-sensor computing", "neuromorphic"],
               "core_weights": {"in-sensor computing": 1.0, "neuromorphic": 0.6}}
    paper = {"title": "Optoelectronic memories for in-sensor computing",
             "abstract": "a neuromorphic vision device"}
    hits, total, top = profile_scoring.core_hits_with_weight(paper, profile)
    assert set(hits) == {"in-sensor computing", "neuromorphic"}
    assert total == 1.6
    assert top == 1.0


def test_absorption_keeps_the_heavier_weight_when_tiers_differ():
    """긴 쪽이 가벼워도 흡수는 문자열 포함관계로 판정한다 — 그래야
    "같은 문구를 두 번 세지 않는다"는 규칙이 가중치 설정과 무관하게 성립한다.
    """
    profile = {"core_topics": ["defect detection", "few-shot defect detection"],
               "core_weights": {"defect detection": 1.0,
                                "few-shot defect detection": 0.6}}
    paper = {"title": "A few-shot defect detection benchmark", "abstract": ""}
    hits, total, _ = profile_scoring.core_hits_with_weight(paper, profile)
    assert hits == ["few-shot defect detection"]
    assert total == 0.6


def test_top_core_weight_distinguishes_target_from_multiple_trend_hits():
    """가중치 **합**으로는 표적 여부를 못 판단한다 — 동향어 두 개
    (0.6+0.6=1.2)가 표적어 하나(1.0)보다 크다. 별점이 이 값을 쓴다."""
    profile = {"core_topics": ["defect detection", "sim-to-real", "neuromorphic"],
               "target_domain": [], "exclude": [],
               "core_weights": {"defect detection": 1.0,
                                "sim-to-real": 0.6, "neuromorphic": 0.6}}
    target = score_paper(_paper_with("a defect detection method"), profile)
    two_trends = score_paper(_paper_with("sim-to-real neuromorphic study"), profile)

    assert two_trends["core_weight"] > target["core_weight"]     # 합은 뒤집힌다
    assert target["top_core_weight"] > two_trends["top_core_weight"]   # 최댓값은 안 뒤집힌다


import profile_scoring  # noqa: E402  (위 테스트가 상수를 참조한다)
