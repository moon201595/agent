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
import scan_search          # 2026-09-17 분할: 검색 소스 모듈은 여기서 patch 한다
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
    monkeypatch.setattr(scan_search.s2_delta, "find_new_papers_since", fake_s2)
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


def test_시드_귀속은_중복_발견에서_합쳐지고_관측에_남는다(monkeypatch, tmp_path):
    """이 테스트가 잡는 것: S2 가 첫 시드만 남기고 뒤 시드의 발견을 지우는 것,
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
    assert out["papers"][0]["s2_seeds"] == ["seed one", "seed two"], "두 시드가 다 데려왔다는 기록이 남아야 한다"

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


def test_시드_수율은_논문_단위이고_고유_기여는_arXiv와_다른_시드를_뺀다(tmp_path):
    """이 테스트가 잡는 것: 관측 행 수로 편수를 세는 것(재발견을 두 번 셈),
    arXiv 에도 있는 논문을 시드 고유로 치는 것, 두 시드가 함께 데려온 것을 고유로 치는 것."""
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
    예산 미시도를 한 숫자로 평탄화하는 것, seed_attempts가 전부 NULL인 실행을 빈 dict로
    보고하는 것."""
    db = _seed_db(tmp_path)
    prof = rp.get_profile(db, "p")
    start, end = datetime(2026, 1, 1), datetime(2027, 1, 1)
    assert sig.scan_health(db, "p", start, end) is None
    assert sig.seed_attempts(db, "p", start, end) is None
    assert sig.seed_yield(db, "p", start, end) is None
    lines = sig.format_signals(None, None, None, None, None)
    assert any("관측 이력 없음" in ln for ln in lines)
    assert not any(("0편" in ln or "0회" in ln) for ln in lines), "미측정을 0 으로 채우면 안 된다"

    incomplete_db = tmp_path / "incomplete.db"
    _profile(incomplete_db)
    incomplete_profile = rp.get_profile(incomplete_db, "p")
    rp.begin_scan(incomplete_db, "p", incomplete_profile)
    assert sig.seed_attempts(incomplete_db, "p", start, end) is None

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
    # 표 행: 시드 | 시도 | 완료 | 부분 | 실패 | 예산에 밀려 미시도 | 반환 합계 — 셋이 각자 다른 칸에 1
    assert "| zero | 1 | 1 | 0 | 0 | 0 | 0 |" in text
    assert "| broken | 1 | 0 | 0 | 1 | 0 | 0 |" in text
    assert "| starved | 1 | 0 | 0 | 0 | 1 | 0 |" in text


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


def test_병합은_시드와_검색_출처를_합집합으로_남긴다():
    """이 테스트가 잡는 것: `_merge` 가 첫 값을 지켜 둘째 행의 시드·출처를 지우는 것."""
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


def _reserve_scan(db, texts, *, profile_id="p", started_at="2026-09-18T00:00:00+00:00",
                  outcome="reserve"):
    profile = rp.get_profile(db, profile_id)
    scan = rp.begin_scan(db, profile_id, profile, started_at=started_at)
    rows = []
    for i, (title, abstract) in enumerate(texts):
        row = _row(f"paper-{i}", "arxiv", title=title, outcome=outcome)
        row["abstract"] = abstract
        rows.append(row)
    rp.record_observations(db, scan, profile_id, rows, core_signature="c", seed_signature="s")
    return scan


def test_reserve_terms_uses_shared_ngrams(tmp_path, monkeypatch):
    """공용 ngrams 우회, 문장 경계 연결, 담화 구절 유입을 잡는다."""
    import term_hygiene
    db = _seed_db(tmp_path)
    texts = [("Quantum", "Sensors. State estimation. In this paper.")] * 3
    scan = _reserve_scan(db, texts)
    original = term_hygiene.ngrams
    calls = []

    def tracked(text):
        calls.append(text)
        yield from original(text)

    monkeypatch.setattr(term_hygiene, "ngrams", tracked)
    assert sig.reserve_terms(db, "p", scan) == {
        "count": 3, "terms": [("state estimation", 3)]}
    assert calls == ["Quantum. Sensors. State estimation. In this paper."] * 3


def test_reserve_terms_counts_each_paper_once(tmp_path):
    """초록 안 반복을 편수로 중복 계산하거나 최소 편수 문턱을 무시하면 실패한다."""
    db = _seed_db(tmp_path)
    scan = _reserve_scan(db, [("", "Quantum sensing. Quantum sensing. Quantum sensing.")])
    assert sig.reserve_terms(db, "p", scan, min_papers=1) == {
        "count": 1, "terms": [("quantum sensing", 1)]}
    assert sig.reserve_terms(db, "p", scan) == {"count": 1, "terms": []}


def test_reserve_terms_excludes_known_topics_and_hints(tmp_path, monkeypatch):
    """핵심어·domain_hints와의 양방향 정규화 겹침 제외를 없애면 실패한다."""
    db = _seed_db(tmp_path)
    rp.create_profile(db, "p", "이름", core_topics=["robot planning", "large language model"])
    scan = _reserve_scan(db, [("", "Robot planning. Language models. Quantum sensing. State estimation.")] * 3)
    original = rp.get_profile
    calls = []

    def with_hints(path, profile_id):
        calls.append((path, profile_id))
        return dict(original(path, profile_id), domain_hints=["quantum sensing"])

    monkeypatch.setattr(rp, "get_profile", with_hints)
    assert sig.reserve_terms(db, "p", scan) == {
        "count": 3, "terms": [("state estimation", 3)]}
    assert calls == [(db, "p")]


def test_reserve_terms_no_observations_is_none(tmp_path):
    """관측 없음을 0으로 채우거나 없는 DB 파일을 생성하면 실패한다."""
    db = _seed_db(tmp_path)
    assert sig.reserve_terms(db, "p") is None
    assert sig.reserve_terms(db, "p", "missing") is None
    empty_scan = _reserve_scan(db, [])
    assert sig.reserve_terms(db, "p", empty_scan) is None
    missing = tmp_path / "missing.db"
    assert sig.reserve_terms(missing, "p") is None
    assert not missing.exists()


def test_reserve_terms_latest_scan_only(tmp_path):
    """실행을 합치거나 삽입 순서로 최신을 고르거나 reserve 없는 최신 실행을 건너뛰면 실패한다."""
    db = _seed_db(tmp_path)
    latest = _reserve_scan(db, [("Quantum sensing", "")] * 3,
                           started_at="2026-09-18T00:00:00+00:00")
    old = _reserve_scan(db, [("State estimation", "")] * 4,
                        started_at="2026-09-17T00:00:00+00:00")
    assert sig.reserve_terms(db, "p") == sig.reserve_terms(db, "p", latest) == {
        "count": 3, "terms": [("quantum sensing", 3)]}
    assert sig.reserve_terms(db, "p", old) == {
        "count": 4, "terms": [("state estimation", 4)]}
    _reserve_scan(db, [("State estimation", "")] * 3,
                  started_at="2026-09-19T00:00:00+00:00", outcome="content")
    assert sig.reserve_terms(db, "p") is None


def test_reserve_terms_filters_outcomes_and_profiles(tmp_path):
    """content·title_only·dropped 또는 다른 프로필 관측이 분모·용어에 섞이면 실패한다."""
    db = _seed_db(tmp_path)
    scan = _reserve_scan(db, [("Quantum sensing", "")] * 3)
    rows = [_row(f"other-{outcome}-{i}", "arxiv", title="State estimation", outcome=outcome)
            for outcome in ("content", "title_only", "dropped") for i in range(3)]
    rp.record_observations(db, scan, "p", rows, core_signature="c", seed_signature="s")
    rp.create_profile(db, "other", "다른 프로필", core_topics=["target term"])
    other = _reserve_scan(db, [("State estimation", "")] * 4, profile_id="other",
                          started_at="2026-09-20T00:00:00+00:00")
    assert sig.reserve_terms(db, "p") == {
        "count": 3, "terms": [("quantum sensing", 3)]}
    assert sig.reserve_terms(db, "p", other) is None


def test_reserve_terms_restores_referenced_abstracts_readonly(tmp_path):
    """이전 관측의 초록 참조를 놓치거나 조회가 DB 내용을 쓰면 실패한다."""
    db = _seed_db(tmp_path)
    texts = [("", "Quantum sensing.")] * 3
    _reserve_scan(db, texts, started_at="2026-09-17T00:00:00+00:00", outcome="dropped")
    scan = _reserve_scan(db, texts)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_observations WHERE scan_id=? "
                           "AND abstract IS NULL AND abstract_ref IS NOT NULL", (scan,)).fetchone()[0] == 3
    before = db.read_bytes()
    assert sig.reserve_terms(db, "p", scan) == {
        "count": 3, "terms": [("quantum sensing", 3)]}
    assert db.read_bytes() == before


def test_reserve_terms_subsumption_sort_and_limit(tmp_path):
    """짧은 조합 중복 제거, 편수·사전순 정렬, top_n 제한 중 하나를 깨면 실패한다."""
    db = _seed_db(tmp_path)
    texts = [("", "Quantum sensing. State estimation. Neural radiance fields.")] * 3
    texts.append(("", "State estimation."))
    scan = _reserve_scan(db, texts)
    expected = [("state estimation", 4), ("neural radiance fields", 3), ("quantum sensing", 3)]
    assert sig.reserve_terms(db, "p", scan) == {"count": 4, "terms": expected}
    assert sig.reserve_terms(db, "p", scan, top_n=2) == {"count": 4, "terms": expected[:2]}


def test_reserve_terms_does_not_migrate_profile_schema(tmp_path):
    """get_profile의 초기화가 누락 테이블을 집계 중 생성하도록 두면 실패한다."""
    import pytest
    import schema_guard
    db = _seed_db(tmp_path)
    scan = _reserve_scan(db, [("Quantum sensing", "")] * 3)
    with sqlite3.connect(db) as con:
        con.execute("DROP TABLE profile_venues")
    before = db.read_bytes()
    with pytest.raises(schema_guard.SchemaOutOfDate):
        sig.reserve_terms(db, "p", scan)
    assert db.read_bytes() == before
