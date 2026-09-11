"""A단계(2026-09-11) — 선별 순서 계약 회귀.

docs/ASTRA_PLAN_2026-09-10.md §5. 순위는 가중합이 아니라 튜플
(계층, 날짜 유무, 날짜, 적중 폭, 도메인, 키)로 정한다. 여기 테스트는 전부
**운영 함수를 부르고**, 각 결함을 주입하면 실패하는지 돌연변이로 확인했다.
"""
from datetime import datetime, timedelta, timezone

import profile_scoring as ps
import research_profile as rp
import run_profile_scan as rps

PROFILE = {
    "core_topics": ["target term", "trend term", "aux term", "tool term"],
    "core_weights": {"target term": 1.0, "trend term": 0.6, "aux term": 0.4, "tool term": 0.35},
    "target_domain": ["factory", "wafer"],
    "exclude": [],
}


def _day(n: int) -> str:
    return (datetime(2026, 9, 10, tzinfo=timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paper(key: str, title: str, published: str | None, abstract: str = "") -> dict:
    return {"arxiv_id": key, "title": title, "abstract": abstract, "published": published}


def _order(papers: list[dict]) -> list[str]:
    return [p["arxiv_id"] for p in ps.score_and_rank(papers, PROFILE)["papers"]]


def test_계층이_최신성보다_먼저다():
    """이 테스트가 잡는 것: 튜플 대신 가중합으로 되돌리는 것, 계층 부호 반전.

    옛 가중합에서는 '동향어 둘 + 도메인 둘'(0.60+0.20+최신성)이 '표적어 하나'
    (0.80)를 이길 수 있었다. 계약은 계층이 먼저다 — 오래된 표적어 논문이
    최신 동향어 결합 논문 위에 온다.
    """
    old_target = _paper("old-target", "A target term study", _day(6))
    new_combo = _paper("new-combo", "trend term with aux term in a factory wafer line", _day(0))
    assert _order([new_combo, old_target]) == ["old-target", "new-combo"]


def test_같은_계층이면_최신이_먼저다():
    """이 테스트가 잡는 것: 날짜 키 누락, 날짜 부호 반전, 도메인이 날짜를 이기는 것.

    실측(2026-09-11, 저장 후보): 9월 4일 논문 둘이 도메인 가점으로 9월 9일
    논문 여섯 위에 있었다. 같은 계층이면 도메인이 아니라 날짜가 먼저다.
    """
    old_with_domain = _paper("old", "target term for factory wafer", _day(5))
    new_plain = _paper("new", "a target term paper", _day(0))
    assert _order([old_with_domain, new_plain]) == ["new", "old"]


def test_같은_날이면_적중_폭_그다음_도메인():
    """이 테스트가 잡는 것: 적중 폭 키 누락(같은 날 core 둘이 core 하나+도메인에 밀림),
    도메인 키 누락.
    """
    two_core = _paper("two", "target term and trend term", _day(0))
    one_core_domain = _paper("one-d", "target term in a factory", _day(0))
    one_core = _paper("one", "target term only", _day(0))
    assert _order([one_core, one_core_domain, two_core]) == ["two", "one-d", "one"]


def test_날짜_결측은_같은_계층_뒤로_가지만_계층을_넘지_않는다():
    """이 테스트가 잡는 것: 결측을 0일로 취급해 맨 앞에 두는 것, 결측을 계층
    아래로 떨어뜨리는 것, 연도만 있는 값을 1월 1일로 지어내는 것."""
    dated = _paper("dated", "target term", _day(3))
    year_only = _paper("year", "target term", "2026")
    missing = _paper("missing", "target term", None)
    lower_tier_new = _paper("lower", "trend term", _day(0))
    got = _order([missing, lower_tier_new, year_only, dated])
    assert got[0] == "dated"
    assert set(got[1:3]) == {"year", "missing"}, "결측은 같은 계층 안에서 날짜 있는 것 뒤"
    assert got[3] == "lower", "결측이 계층을 넘어 아래 계층 뒤로 가면 안 된다"
    r = ps.score_paper(year_only, PROFILE)
    assert r["date_precision"] == "year" and r["pub_day"] is None


def test_동률은_입력_순서가_아니라_키로_정한다():
    """이 테스트가 잡는 것: 정렬 키 마지막 항목(paper_key) 누락 — 동률이 입력
    순서에 의존하면 같은 후보 집합이 실행마다 다른 순서로 나온다."""
    a = _paper("2609.00001", "target term", _day(0))
    b = _paper("2609.00002", "target term", _day(0))
    assert _order([b, a]) == _order([a, b]) == ["2609.00001", "2609.00002"]


def test_계층은_적중_키워드와_원_가중치로_구한다():
    """이 테스트가 잡는 것: 반올림된 top_core_weight 를 역조회하는 것, 가중치
    누락 키워드를 계층표에서 빼는 것."""
    prof = {"core_topics": ["a", "b", "c"], "core_weights": {"a": 0.123456, "b": 0.6},
            "target_domain": [], "exclude": []}
    assert ps.tier_table(prof) == [1.0, 0.6, 0.123456], "가중치 없는 c 는 1.0 으로 계층에 들어간다"
    assert ps.tier_rank(prof, ["a"]) == 2
    assert ps.tier_rank(prof, ["c"]) == 0
    r = ps.score_paper({"title": "a and c", "abstract": "", "published": None}, prof)
    assert r["tier_rank"] == 0


def test_점수는_설명일_뿐_자격을_정하지_않는다():
    """이 테스트가 잡는 것: 자격 판정을 `priority == 0` 에 다시 묶는 것.

    보통 프로필에서는 적중이 있으면 점수가 양수라 두 판정이 같은 답을 낸다 —
    그래서 가중치 0.0 키워드로 가른다. 적중은 있는데 점수는 0 인 논문이
    자격을 잃으면 자격이 점수에 묶여 있는 것이다.
    """
    prof = {"core_topics": ["zero term", "target term"],
            "core_weights": {"zero term": 0.0, "target term": 1.0},
            "target_domain": [], "exclude": []}
    zero = _paper("zero", "a zero term paper", None)
    miss = _paper("miss", "nothing relevant here", None)
    out = ps.score_and_rank([zero, miss], prof)
    assert [p["arxiv_id"] for p in out["papers"]] == ["zero"], "적중이 있으면 점수가 0 이어도 자격이 있다"
    assert out["unmatched_count"] == 1
    assert out["papers"][0]["_score"]["priority"] == 0.0
    assert "priority" in out["papers"][0]["_score"], "설명 필드는 남긴다"


def test_본문_링크_유무가_선정_순서를_바꾸지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: `_eligible_for_content` 문지기 재도입.

    상위 관련 논문이 초록만 있어도 자리를 받는다(§5.5·5.6). 원래 걱정(자리만
    먹고 처리 실패)은 초록 정리 경로(§8-41)가 해소했다. 운영 경로
    scan_profile 을 실제로 부른다.
    """
    import asyncio, http_client
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term"],
                      core_weights={"target term": 1.0, "trend term": 0.6}, max_items=1)
    now = datetime.now(timezone.utc)
    pages = {0: [
        {"arxiv_id": "with-text", "title": "trend term paper", "abstract": "",
         "published": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"arxiv_id": None, "doi": "10.1/no-link", "title": "target term paper", "abstract": "",
         "published": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
    ]}
    seen = []

    async def fake_get(client, params):
        seen.append(params["start"])
        class R: text = "<x/>"
        return R()
    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_get)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _: pages[seen[-1]])
    result = asyncio.run(rps.scan_profile(db, "p", None))
    assert result["papers"][0].get("doi") == "10.1/no-link", "링크 없는 상위 계층 논문이 자리를 받아야 한다"
    assert [p["arxiv_id"] for p in result["reserve"]] == ["with-text"]


def test_reserve_도_같은_계약_순서다():
    """이 테스트가 잡는 것: reserve 를 priority 로 다시 정렬하거나 문지기 탈락
    목록을 뒤에 붙이는 것. Deep Layer 는 papers+reserve 를 순서대로 돈다."""
    papers = [_paper(f"p{i}", "target term" if i % 2 else "trend term", _day(i)) for i in range(6)]
    out = ps.score_and_rank(papers, PROFILE)["papers"]
    keys = [p["arxiv_id"] for p in out]
    # 계층 1.0(홀수) 이 먼저, 그 안에서 최신순; 그 다음 0.6(짝수) 최신순
    assert keys == ["p1", "p3", "p5", "p0", "p2", "p4"]


def test_병합은_더_이른_공개일을_남긴다():
    """이 테스트가 잡는 것: `_merge` 가 먼저 온 출처의 날짜를 그대로 두는 것."""
    import selection
    a = {"title": "same", "published": "2026-09-08T00:00:00Z", "arxiv_id": "x"}
    b = {"title": "same", "published": "2026-09-02", "doi": "10.1/x"}
    assert selection._merge(a, b)["published"] == "2026-09-02"
    assert selection._merge(b, a)["published"] == "2026-09-02"
