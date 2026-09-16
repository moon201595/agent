"""term_hygiene — 용어 후보의 어휘 위생. 각 테스트 끝에 "무엇을 망가뜨리면 실패하는가"를 적었다(AGENTS.md)."""
from collections import Counter

import term_hygiene as H
import term_discovery as td
import trend_report
import profile_advisor
import rule_advisor


def _r(term: str):
    return H.reject_reason(term.lower().split())


def test_가드표_제거와_유지():
    """2026-09-13 외부 검토의 가드표 그대로. 550편 재생에서 실제로 상위에 올라온 것들이다."""
    dropped = {"state-of-the-art accuracy": "boilerplate_phrase", "state-of-the-art performance": "edge_stop",
               "previous studies": "edge_stop",
               "previous study": "edge_stop", "previous work": "edge_stop", "case studies": "boilerplate_word",
               "github com": "hard_noise", "low- and middle-income": "hard_noise", "falls short": "edge_stop",
               "jointly optimizes": "edge_stop", "two distinct": "edge_stop", "orders magnitude": "boilerplate_phrase"}
    kept = ["world model", "state estimation", "state space", "reinforcement learning", "vision-language models",
            "computational overhead", "two-stage detector", "inference latency", "multi-agent system"]
    for t, why in dropped.items():
        assert _r(t) == why, (t, _r(t))
    for t in kept:
        assert _r(t) is None, (t, _r(t))
    # 망가뜨리면: `state` 를 BOILERPLATE_WORDS 에 다시 넣으면 state estimation 이 죽어 실패.
    # 가장자리 검사를 정규화 토큰으로 바꾸면 two-stage detector 가 `two` 로 죽어 실패.
    # singular() 를 빼면 case studies 가 통과해 실패. 구절 목록에서 state-of-the-art 를 빼면 실패.


def test_띄어쓴_sota_와_하이픈_sota_가_같은_구절로_잡힌다():
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: 공용 생성기가 띄어쓰기·하이픈 표기를
    다른 담화 구절로 보고 state of the art 를 누락하는 것."""
    assert list(trend_report._ngrams("achieves state of the art performance on benchmarks", 3)) == []
    assert list(trend_report._ngrams("achieves state-of-the-art performance on benchmarks", 2)) == []
    # 망가뜨리면: _phrase_key 에서 짧은 낱말 제거를 빼면 띄어쓴 쪽이 [state, the, art] 로 남아 통과 → 실패.


def test_표시_문자열은_원문_표기를_유지한다():
    """정규화는 판정에만 쓴다 — 하이픈·복수형이 출력에 남아야 매처 변형 진단과 이어진다."""
    out = list(trend_report._ngrams("we study vision-language models for state estimation", 2))
    assert "vision-language models" in out and "state estimation" in out
    assert "vision language model" not in out
    # 망가뜨리면: _ngrams 가 norm_tokens 결과를 yield 하면 실패.


def test_우산어는_all_token_이고_하이픈형도_낱말로_푼다():
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: 우산어를 any-token으로 판정해
    world model 같은 실제 용어까지 제거하는 것."""
    assert H.is_umbrella("pre-trained transformer")            # pre + trained + transformer 전부 범용어
    assert H.is_umbrella("large language models")
    assert not H.is_umbrella("world model")                    # world 가 범용어가 아니다
    assert not H.is_umbrella("large language model agents")
    assert td._generic("pre-trained transformer") and not td._generic("world model")
    # 망가뜨리면: all() 을 any() 로 바꾸면 world model 이 우산어가 되어 실패.


def test_세_소비자가_같은_판정을_쓴다():
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: emerging_terms·discover·propose가
    문장 경계·담화 구절·우산어를 서로 다르게 통과시키는 것. 세 운영 함수를 직접 부른다."""
    text = "Previous studies report large language models. We use world models for state estimation and certain models."
    profile = {"core_topics": ["defect detection", "AI"],
               "core_weights": {"defect detection": 1.0, "AI": 0.5},
               "target_domain": [], "domain_hints": [], "exclude": []}
    assert "report large" not in H.candidate_ngrams("report. large language models")
    assert "report large" not in td._grams("report. large language models")
    rows = [{"title": "Defect detection", "abstract": text} for _ in range(3)]
    via_trend = {term for term, _support, _previous in
                 trend_report.emerging_terms(rows, profile, min_papers=2)}
    pool = [{"_paper_key": f"p{i}", "title": "Defect detection", "abstract": text,
             "published": "2026-09-10", "day": 20260910, "domain_hits": [],
             "s2_seeds": {"seed-a", "seed-b"}} for i in range(3)]
    via_discovery = {term["term"] for term in td.discover(pool, profile, {"min_papers": 2,
                                                                            "top_terms": 50})}
    sent = {"allowed_tiers": [1.0], "papers": [{"key": f"p{i}", "title": "Defect detection",
             "abstract": text} for i in range(3)]}
    via_rule = {proposal["term"] for proposal in rule_advisor.propose(sent, profile,
                                                                        {"min_support": 2,
                                                                         "max_proposals": 50})["proposals"]}
    for names in (via_trend, via_discovery, via_rule):
        assert "world models" in names and "large language models" not in names
        assert "certain models" in names
        assert "previous studies" not in names
    # 망가뜨리면: 어느 운영 함수가 자체 n-gram·우산어·부분 문자열 정책을 쓰면 집합 검사가 실패.


def test_토큰_경계와_단수화_예외를_보존한다():
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: vision model을 supervision model의
    부분 문자열로 보거나 AI를 training 안에서 찾아 제외하는 것, 과학 용어를 잘못 단수화하는 것."""
    assert not trend_report._subsumed("vision model", {"supervision model"})
    assert not H.overlaps_known("training", {"ai"})
    assert trend_report._subsumed("language models", {"large language models"})
    assert {w: H.singular(w) for w in ("analysis", "physics", "status", "lens", "series", "species", "news")} == {
        "analysis": "analysis", "physics": "physics", "status": "status", "lens": "lens",
        "series": "series", "species": "species", "news": "news"}


def test_운영에_올라온_담화와_단위_표현만_추가한다():
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: 실제 출력 근거 없이 금지어를 넓히거나
    systematic review·percentage points·yet를 다시 후보에 올리는 것."""
    assert H.reject_reason(["systematic", "review"]) == "boilerplate_phrase"
    assert H.reject_reason(["percentage", "points"]) == "boilerplate_phrase"
    assert H.reject_reason(["yet", "existing"]) == "edge_stop"
    assert H.reject_reason(["state", "estimation"]) is None


def _paper(key, title, abstract, seeds=(), domain=()):
    return {"_paper_key": key, "title": title, "abstract": abstract, "day": 20260910, "published": "2026-09-10",
            "s2_seeds": set(seeds), "domain_hits": list(domain)}


def test_discover_는_사유별_진단을_세고_제안기_입력에는_안_싣는다():
    prof = {"core_topics": ["LLM agent"], "core_weights": {"LLM agent": 1.0}, "target_domain": [], "exclude": []}
    pool = [_paper(f"p{i}", "State estimation with world models",
                   "Previous studies show state-of-the-art performance. Our state estimation uses world models. "
                   "See https://github.com/x. Pre-trained transformer baselines are compared.", seeds=("a", "b")) for i in range(3)]
    diag = H.new_diag()
    terms = td.discover(pool, prof, {"min_papers": 3, "top_terms": 50}, diag)
    names = {t["term"] for t in terms}
    assert "state estimation" in names and "world models" in names
    assert "previous studies" not in names and "state-of-the-art performance" not in names
    for k in ("edge_stop", "boilerplate_phrase", "hard_noise", "umbrella_generic"):
        assert diag[k] > 0, (k, dict(diag))
    sent = profile_advisor.build_input({"papers": []}, prof, [], terms) if hasattr(profile_advisor, "build_input") else {}
    flat = str(sent)
    for k in diag:
        assert k not in flat                                      # 진단 키가 밖으로 안 나간다
    # 망가뜨리면: discover 가 diag 에 안 세면 첫 assert 가 실패. build_input 이 diag 를 실으면 마지막이 실패.


def test_재생_픽스처_before_after_가_기록과_같다():
    """2026-09-13 고정 픽스처(550편)가 있으면 그 재생 결과를 기록(§8-109)과 대조한다. 없으면 건너뛴다."""
    import json, pathlib, pytest
    fx = pathlib.Path("data/fixtures/exploration_pool_2026-09-13_7d.json")
    if not fx.exists():
        pytest.skip("고정 픽스처 없음(로컬 전용)")
    data = json.loads(fx.read_text())
    pool = [{**p, "s2_seeds": set(p.get("s2_seeds") or []), "domain_hits": p.get("domain_hits") or []} for p in data["papers"]]
    top3 = [(t["term"], t["lane"]) for t in td.discover(pool, data["profile"])]
    assert ("state-of-the-art performance", "support") not in top3 and ("previous studies", "seed") not in top3
    assert top3[0] == ("computer vision", "support")
    allc = {t["term"] for t in td.discover(pool, data["profile"], {"top_terms": 100000})}
    assert "state estimation" in allc                               # 고치기 전에는 `state` 때문에 죽어 있었다
    assert "computational overhead" in allc                         # 검토 의견대로 일단 유지
    assert "data" not in H.EDGE_STOP and "time" not in H.EDGE_STOP and "times" not in H.EDGE_STOP
    assert "analysis" in H.EDGE_STOP                                  # 제거 시 `analysis reveals`가 상위에 올라온다
