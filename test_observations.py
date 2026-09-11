"""B단계(2026-09-11) — 실행별 관측 보존. docs/ASTRA_PLAN_2026-09-10.md §6.1.

전부 운영 경로(scan_profile)를 부른다. 각 결함을 주입하면 실패하는지
돌연변이로 확인했다.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import http_client
import research_profile as rp
import run_profile_scan as rps
import s2_delta


def _profile(db):
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term"],
                      core_weights={"target term": 1.0, "trend term": 0.6},
                      exclude=["banned"], max_items=1, s2_seeds=["target term"])


def _run(db, monkeypatch, arxiv_pages, s2_papers=None):
    seen = []

    async def fake_get(client, params):
        seen.append(params["start"])
        class R: text = "<x/>"
        return R()

    async def fake_s2(client, keywords, since, until, limit=100):
        return {"papers": s2_papers or [], "status": "done", "query": "S2", "keywords_failed": 0}
    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_get)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _: arxiv_pages[seen[-1]])
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", fake_s2)
    return asyncio.run(rps.scan_profile(db, "p", None))


def _day(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _obs(db):
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM candidate_observations ORDER BY observed_at, paper_key")]


def test_관측은_실행에_묶이고_같은_논문은_실행마다_행이_생긴다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: run_id 를 안 잡고 빈 값을 넣는 것, 관측을 개체
    테이블처럼 덮어쓰는 것(같은 논문 두 실행 → 한 행)."""
    db = tmp_path / "t.db"; _profile(db)
    pages = {0: [{"arxiv_id": "a", "title": "target term", "abstract": "", "published": _day(1)}]}
    _run(db, monkeypatch, pages)
    _run(db, monkeypatch, pages)
    rows = _obs(db)
    assert len(rows) == 2, "같은 논문이 두 실행에서 보이면 관측 행도 둘"
    assert rows[0]["scan_id"] != rows[1]["scan_id"]
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        scans = {r["scan_id"]: dict(r) for r in con.execute("SELECT * FROM scan_runs")}
        arxiv_runs = {r[0] for r in con.execute("SELECT run_id FROM search_runs WHERE source='arxiv'")}
    assert {r["scan_id"] for r in rows} <= set(scans), "관측의 scan_id 는 실제 scan_runs 행을 가리켜야 한다"
    scan = scans[rows[0]["scan_id"]]
    assert scan["arxiv_run_id"] in arxiv_runs, "스캔 기록이 출처별 검색 행을 가리켜야 한다"
    assert scan["observations"] == 1 and scan["observation_error"] is None
    snap = json.loads(scan["profile_snapshot"])
    assert snap["core_weights"] == {"target term": 1.0, "trend term": 0.6}, "지문으로 복원 못 하는 가중치가 스냅샷에 있어야 한다"
    assert snap["max_items"] == 1 and snap["exclude"] == ["banned"]
    assert rows[0]["policy_version"] == rp.RANK_POLICY_VERSION == scan["policy_version"]
    assert rows[0]["core_signature"] and rows[0]["seed_signature"]


def test_탈락_사유_셋을_가르고_적격에는_순위를_남긴다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 사유를 하나로 뭉개는 것, 적격 논문의 rank_pos 누락,
    순위가 튜플 순서가 아닌 것."""
    db = tmp_path / "t.db"; _profile(db)
    rp.mark_shown(db, "p", [{"arxiv_id": "old", "title": "target term seen before"}])
    pages = {0: [
        {"arxiv_id": "old", "title": "target term seen before", "abstract": "", "published": _day(0)},
        {"arxiv_id": "ex", "title": "target term but banned", "abstract": "", "published": _day(0)},
        {"arxiv_id": "none", "title": "nothing here", "abstract": "", "published": _day(0)},
        {"arxiv_id": "t2", "title": "trend term", "abstract": "", "published": _day(0)},
        {"arxiv_id": "t1", "title": "target term", "abstract": "", "published": _day(2)},
    ]}
    _run(db, monkeypatch, pages)
    by = {r["paper_key"]: r for r in _obs(db)}
    assert by["old"]["filter_reason"] == rp.FILTER_ALREADY_SHOWN
    assert by["ex"]["filter_reason"] == rp.FILTER_EXCLUDE_HIT
    assert by["none"]["filter_reason"] == rp.FILTER_NO_CORE_HIT
    assert by["t1"]["filter_reason"] is None and by["t2"]["filter_reason"] is None
    # 계층이 먼저다 — 이틀 전 1.0 이 오늘 0.6 보다 앞
    assert (by["t1"]["rank_pos"], by["t2"]["rank_pos"]) == (1, 2)
    assert by["t1"]["tier_rank"] == 0 and by["t2"]["tier_rank"] == 1
    assert by["old"]["rank_pos"] is None


def test_씨앗_귀속은_중복_발견에서_합쳐지고_관측에_남는다(monkeypatch, tmp_path):
    """이 테스트가 잡는 것: S2 가 첫 씨앗만 남기고 뒤 씨앗의 발견을 지우는 것,
    관측이 s2_seeds 를 안 싣는 것."""
    calls = []

    async def fake_search(client, keyword, since, until, limit, max_wait=None):
        calls.append(keyword)
        paper = {"arxiv_id": None, "doi": "10.1/shared", "title": "target term paper",
                 "abstract": "", "source": "s2", "published": "2026-09-09"}
        return s2_delta.KeywordSearch([dict(paper)], 1, False)
    monkeypatch.setattr(s2_delta, "search_keyword_since", fake_search)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, ["seed one", "seed two"], datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 10, tzinfo=timezone.utc)))
    assert calls == ["seed one", "seed two"]
    assert len(out["papers"]) == 1
    assert out["papers"][0]["s2_seeds"] == ["seed one", "seed two"], "두 씨앗이 다 데려왔다는 기록이 남아야 한다"

    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, {0: []}, s2_papers=[out["papers"][0]])
    rows = _obs(db)
    assert json.loads(rows[0]["s2_seeds"]) == ["seed one", "seed two"]


def test_초록은_바뀐_경우에만_저장하고_같으면_실제_보유_행을_참조한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 행마다 초록을 복사하는 것, 해시만 남기고 초록을
    search_candidates 에 기대는 것(그 열은 재수집 때 덮어써진다), 참조 사슬
    (직전 행을 가리켜 실제 보유 행에 닿지 않는 것)."""
    db = tmp_path / "t.db"; _profile(db)
    a1 = {"arxiv_id": "a", "title": "target term", "abstract": "first abstract", "published": _day(0)}
    _run(db, monkeypatch, {0: [a1]})
    _run(db, monkeypatch, {0: [a1]})
    _run(db, monkeypatch, {0: [a1]})
    a2 = dict(a1, abstract="revised abstract")
    _run(db, monkeypatch, {0: [a2]})
    rows = _obs(db)
    assert [r["abstract"] for r in rows] == ["first abstract", None, None, "revised abstract"]
    holder = rows[0]["scan_id"]
    assert rows[1]["abstract_ref"] == holder and rows[2]["abstract_ref"] == holder, "항상 실제 보유 행을 가리킨다"
    assert rows[3]["abstract_ref"] is None and rows[3]["abstract_sha"] != rows[0]["abstract_sha"]
    # 개체 테이블은 최신 초록으로 덮여 있어도 과거 초록은 관측에서 복구된다
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT abstract FROM search_candidates").fetchone()[0] == "revised abstract"


# ── 신호 (§6.2). 운영 저장 함수로 관측을 만들고 신호 함수를 부른다.

import observation_signals as sig


def _seed_db(tmp_path):
    db = tmp_path / "s.db"; _profile(db)
    return db


def _row(key, source, seeds=None, reason=None, rank=None, outcome="reserve", title=None, published="2026-09-09"):
    p = {"arxiv_id": key if source == "arxiv" else None, "doi": None if source == "arxiv" else key,
         "title": title or key, "abstract": "", "source": source, "published": published,
         "outcome": outcome, "rank_pos": rank, "filter_reason": reason,
         "_score": {"core_hits": ["target term"] if reason is None else [], "domain_hits": [],
                    "excluded": reason == rp.FILTER_EXCLUDE_HIT, "tier_rank": 0 if reason is None else None,
                    "date_precision": "day"}}
    if seeds: p["s2_seeds"] = seeds
    return p


def test_씨앗_수율은_논문_단위이고_고유_기여는_arXiv와_다른_씨앗을_뺀다(tmp_path):
    """이 테스트가 잡는 것: 관측 행 수로 편수를 세는 것(재발견을 두 번 셈),
    arXiv 에도 있는 논문을 씨앗 고유로 치는 것, 두 씨앗이 함께 데려온 것을 고유로 치는 것."""
    db = _seed_db(tmp_path)
    prof = rp.get_profile(db, "p")
    s1 = rp.begin_scan(db, "p", prof); s2 = rp.begin_scan(db, "p", prof)
    rows = [
        _row("10.1/only-a", "s2", ["A"], rank=1, outcome="content"),
        _row("10.1/a-and-b", "s2", ["A", "B"], rank=2),
        _row("10.1/a-and-arxiv", "s2", ["A"], rank=3),
    ]
    rp.record_observations(db, s1, "p", rows, core_signature="c", seed_signature="s")
    # 둘째 스캔: 같은 논문 재발견 + arXiv 가 세 번째 논문을 따로 봤다
    # 둘째 스캔에서 같은 논문이 arXiv 로도 왔다 — 병합되면 retrieval_sources 가 합집합이다
    both = _row("10.1/a-and-arxiv", "s2", ["A"], rank=3); both["retrieval_sources"] = ["arxiv", "s2"]
    rp.record_observations(db, s2, "p", rows[:2] + [both], core_signature="c", seed_signature="s")
    start, end = datetime(2026, 1, 1), datetime(2027, 1, 1)
    y = sig.seed_yield(db, "p", start, end)
    assert y["A"]["returned"] == 3, "재발견은 한 편"
    assert y["A"]["unique"] == 1, "B 와 같이 온 것, arXiv 도 본 것은 고유가 아니다"
    assert y["A"]["content"] == 1 and y["B"]["returned"] == 1 and y["B"]["unique"] == 0
    src = sig.source_contribution(db, "p", start, end)
    assert src["s2"]["papers"] == 3 and src["s2"]["unique"] == 2 and src["arxiv"]["unique"] == 0


def test_스캔_기록이_없으면_미측정이고_있으면_0편과_미시도를_가른다(tmp_path):
    """이 테스트가 잡는 것: 관측 행 없음을 전부 0 으로 채우는 것, 정상 0편·실패·
    예산 미시도를 한 숫자로 평탄화하는 것."""
    db = _seed_db(tmp_path)
    prof = rp.get_profile(db, "p")
    start, end = datetime(2026, 1, 1), datetime(2027, 1, 1)
    assert sig.scan_health(db, "p", start, end) is None
    assert sig.seed_attempts(db, "p", start, end) is None
    assert sig.seed_yield(db, "p", start, end) is None
    lines = sig.format_signals(None, None, None, None, None)
    assert any("관측 이력 없음" in ln for ln in lines)
    assert not any(("0편" in ln or "0회" in ln) for ln in lines), "미측정을 0 으로 채우면 안 된다"

    s = rp.begin_scan(db, "p", prof)
    rp.finish_scan(db, s, seed_attempts=[
        {"keyword": "zero", "status": "done", "returned": 0, "reason": None},
        {"keyword": "broken", "status": "failed", "returned": 0, "reason": None},
        {"keyword": "starved", "status": "not_attempted", "returned": 0, "reason": "budget"},
    ], observations=0)
    a = sig.seed_attempts(db, "p", start, end)
    assert a["zero"]["done"] == 1 and a["broken"]["failed"] == 1 and a["starved"]["not_attempted"] == 1
    h = sig.scan_health(db, "p", start, end)
    assert h == {"scans": 1, "observed": 1, "failed": 0, "incomplete": 0}
    text = "\n".join(sig.format_signals(sig.seed_yield(db, "p", start, end), None, None, a, h))
    assert "예산에 밀려 미시도 1" in text and "실패 1" in text and "완료 1" in text


def test_필터_분포는_논문_단위이고_사유별_예시를_준다(tmp_path):
    """이 테스트가 잡는 것: 같은 논문의 반복 관측을 사유별로 여러 번 세는 것."""
    db = _seed_db(tmp_path)
    prof = rp.get_profile(db, "p")
    for _ in range(3):
        s = rp.begin_scan(db, "p", prof)
        rp.record_observations(db, s, "p", [
            _row("x1", "arxiv", reason=rp.FILTER_EXCLUDE_HIT, outcome="dropped", title="Banned one"),
            _row("x2", "arxiv", reason=rp.FILTER_NO_CORE_HIT, outcome="dropped", title="Nothing"),
            _row("x3", "arxiv", reason=rp.FILTER_NO_CORE_HIT, outcome="dropped", title="Nothing two"),
        ], core_signature="c", seed_signature="s")
    f = sig.filter_distribution(db, "p", datetime(2026, 1, 1), datetime(2027, 1, 1))
    assert f[rp.FILTER_NO_CORE_HIT]["count"] == 2 and f[rp.FILTER_EXCLUDE_HIT]["count"] == 1
    assert f[rp.FILTER_NO_CORE_HIT]["examples"] == ["Nothing", "Nothing two"]


def test_병합은_씨앗과_검색_출처를_합집합으로_남긴다():
    """이 테스트가 잡는 것: `_merge` 가 첫 값을 지켜 둘째 행의 씨앗·출처를 지우는 것."""
    import selection
    a = {"title": "Same Paper", "arxiv_id": "2609.1", "source": "arxiv"}
    b = {"title": "same paper", "doi": "10.1/x", "source": "s2", "s2_seeds": ["B"]}
    c = {"title": "Same  Paper", "doi": "10.1/x", "source": "s2", "s2_seeds": ["A"]}
    m = selection._merge(selection._merge(a, b), c)
    assert m["s2_seeds"] == ["A", "B"]
    assert m["retrieval_sources"] == ["arxiv", "s2"]
    assert m["source"] == "arxiv", "기존 단일 source 는 그대로 둔다 — 하류가 읽는다"


def test_관측_저장_실패는_스캔에_남고_배달을_막지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 관측 실패를 후보 기록과 한 try 에 묶어 조용히 넘기는 것,
    실패를 scan_runs 에 안 남기는 것(집계가 "0편"과 "저장 실패"를 못 가른다)."""
    db = tmp_path / "t.db"; _profile(db)
    def boom(*a, **k): raise RuntimeError("disk full")
    monkeypatch.setattr(rp, "record_observations", boom)
    pages = {0: [{"arxiv_id": "a", "title": "target term", "abstract": "", "published": _day(0)}]}
    result = _run(db, monkeypatch, pages)
    assert result["papers"], "관측 실패가 선별·배달을 막으면 안 된다"
    with sqlite3.connect(db) as con:
        obs, err, cand = con.execute(
            "SELECT observations, observation_error, (SELECT count(*) FROM search_candidates) FROM scan_runs").fetchone()
    assert obs is None and "disk full" in err
    assert cand == 1, "후보(개체) 기록은 별도 try 라 살아남아야 한다"
