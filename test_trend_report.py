"""주간 동향 리뷰 — 네트워크 없이 돈다 (2026-09-02).

"논문들을 쭉 넣은 다음 마지막에 동향 보고가 필요하다"는 요구의 답이다.
**LLM 을 안 쓴다** — 전부 셈과 문자열 대조라 위조가 불가능하다(CLAUDE.md 7).
서술형 리뷰는 검증할 수 없는 산출물이라 넣지 않았다.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import trend_report

PROFILE = {
    "core_topics": ["defect detection", "robot manipulation", "quantization"],
    "core_weights": {"defect detection": 1.0, "robot manipulation": 0.6,
                     "quantization": 0.35},
    "target_domain": [], "exclude": [],
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, title TEXT, "
                    "published TEXT, source TEXT)")
        con.execute("CREATE TABLE summaries (arxiv_id TEXT PRIMARY KEY, created_at TEXT, "
                    "engine TEXT, coverage_ratio REAL)")
    return path


def _add(db, aid, title, days_ago, source=None, engine="gemini", coverage=1.0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers VALUES (?,?,?,?)", (aid, title, ts, source))
        con.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?)",
                    (aid, ts, engine, coverage))


def _build(db):
    return asyncio.run(trend_report.build(db, PROFILE, client=None))


def test_counts_this_week_and_compares_with_last_week(db):
    _add(db, "a", "A defect detection method", 1)
    _add(db, "b", "Another defect detection study", 2)
    _add(db, "c", "An old defect detection paper", 9)      # 지난주
    text = _build(db)
    assert "처리한 논문 2편 (지난주 1편)" in text
    assert "defect detection 2  (1→2, +1)" in text


def test_keyword_that_vanished_is_reported(db):
    """줄어든 것도 동향이다 — 늘어난 것만 보여주면 절반만 보는 것이다."""
    _add(db, "old", "quantization aware training int8", 9)
    _add(db, "new", "A defect detection method", 1)
    text = _build(db)
    assert "지난주엔 있었으나 이번주 없음" in text
    assert "quantization" in text


def test_source_mix_separates_journal_from_arxiv(db):
    """S2 를 붙인 뒤 이 비율이 바뀌는지가 관심사다(§ arXiv 커버리지 3%)."""
    _add(db, "a", "defect detection", 1, source=None)
    _add(db, "pdf-x", "surface inspection", 1, source="open-access: 10.1/x")
    text = _build(db)
    assert "arXiv 1편" in text
    assert "저널(오픈액세스) 1편" in text


def test_partial_coverage_is_surfaced(db):
    """Groq 폴백 날 요약이 원문 절반만 본 걸 주간 단위로도 드러낸다(§8-25)."""
    _add(db, "a", "defect detection", 1, engine="groq", coverage=0.40)
    text = _build(db)
    assert "원문을 다 못 본 요약 1편" in text
    assert "40%" in text


def test_title_only_matching_avoids_body_noise(db):
    """제목만 본다 — 주간 추이는 '무엇에 대한 논문이 나왔나'이지
    '어떤 낱말이 본문 어딘가에 있었나'가 아니다."""
    _add(db, "a", "A study of robot manipulation", 1)
    counts = trend_report.keyword_counts(trend_report._rows_between(
        db, datetime.now(timezone.utc) - timedelta(days=7),
        datetime.now(timezone.utc)), PROFILE)
    assert counts["robot manipulation"] == 1
    assert "defect detection" not in counts


def test_empty_week_does_not_crash(db):
    text = _build(db)
    assert "처리한 논문 0편" in text


# ---------------------------------------------------------------- 공통 인용


class _Rows(list):
    pass


def _row(aid):
    return {"arxiv_id": aid, "title": "T", "published": None,
            "source": None, "engine": "gemini", "coverage_ratio": 1.0}


def test_shared_references_counts_papers_not_occurrences(monkeypatch):
    """한 논문이 같은 참고문헌을 두 번 인용해도 1로 센다 — '몇 편이 함께
    인용했나'가 신호이지 '총 몇 번 등장했나'가 아니다."""
    async def fake(aid, limit, edge):
        import json
        return json.dumps({"papers": [{"title": "Base Paper"}, {"title": "Base Paper"},
                                      {"title": "Other"}]})

    monkeypatch.setattr(trend_report.server, "_s2_citation_graph", fake)
    shared, examined, targets = asyncio.run(
        trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert ("Base Paper", 2) in shared
    assert examined == 2 and targets == 2


def test_shared_requires_more_than_one_paper(monkeypatch):
    """1편만 인용한 건 '공통'이 아니라 그냥 참고문헌 목록이다."""
    async def fake(aid, limit, edge):
        import json
        return json.dumps({"papers": [{"title": f"Ref for {aid}"}]})

    monkeypatch.setattr(trend_report.server, "_s2_citation_graph", fake)
    shared, _e, _t = asyncio.run(trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert shared == []


def test_synthetic_pdf_ids_are_skipped(monkeypatch):
    """S2 인용망은 arXiv ID 로 찾는다 — pdf-<해시> 는 조회 대상이 아니다."""
    calls = []

    async def fake(aid, limit, edge):
        calls.append(aid)
        import json
        return json.dumps({"papers": []})

    monkeypatch.setattr(trend_report.server, "_s2_citation_graph", fake)
    _s, examined, targets = asyncio.run(
        trend_report.shared_references(None, [_row("2608.1"), _row("pdf-abc")]))
    assert calls == ["2608.1"]
    assert targets == 1


def test_budget_stops_early_and_reports_the_sample_size(monkeypatch):
    """실측(2026-09-02): 8편으로 돌렸더니 7분을 넘겼다. 부분 결과를 내되
    표본 크기를 숨기지 않는다 — 조용히 전체인 척하면 안 된다."""
    clock = {"t": 0.0}

    async def fake(aid, limit, edge):
        clock["t"] += 100.0
        import json
        return json.dumps({"papers": [{"title": "Base"}]})

    monkeypatch.setattr(trend_report.server, "_s2_citation_graph", fake)
    monkeypatch.setattr(trend_report.time, "monotonic", lambda: clock["t"])
    _s, examined, targets = asyncio.run(trend_report.shared_references(
        None, [_row(str(i)) for i in range(10)], budget_s=250.0))
    assert examined < targets
    assert targets == 10


def test_lookup_failure_does_not_break_the_report(monkeypatch):
    """동향 보고가 못 나온다고 다이제스트를 막으면 안 된다."""
    async def fake(aid, limit, edge):
        raise RuntimeError("S2 죽음")

    monkeypatch.setattr(trend_report.server, "_s2_citation_graph", fake)
    shared, examined, targets = asyncio.run(
        trend_report.shared_references(None, [_row("1")]))
    assert shared == [] and examined == 0 and targets == 1


def test_report_states_the_sample_when_partial():
    rows = [_row(str(i)) for i in range(10)]
    text = trend_report.format_report(rows, [], PROFILE,
                                      shared=[("Base", 3)], examined=4, targets=10)
    assert "4/10편 조회" in text
    assert "시간 예산으로 일부만 봤다" in text
