"""①⑨ 아침 순차 실행의 고정 순서와 arXiv 실패 프로필 재시도(2026-11-06).

그날 첫 arXiv 요청이 429 를 받아 90초 안에 안 풀리면 맨 앞 프로필(그때 가나다순 맨 앞이던 team_agent)만 arXiv 를 잃었다(10/22·11/04·11/06).
뒤 프로필은 몇 분 뒤라 통과했다. 순서를 고정하고, 첫 바퀴에서 arXiv 가 통째로 실패한 프로필은 S2·요약 전에 멈춰 맨 뒤에서 한 번 더 돈다.
"""
import asyncio
from datetime import datetime, timezone

import pytest

import research_profile as rp
import run_profile_scan as rps
import scan_search


def test_daily_order_is_fixed_and_new_profiles_follow():
    """고정 순서(우리팀 → 에이전트 → 로봇 → 비전)를 바꾸거나, 목록에 없는 새 프로필을 빼먹거나 앞에 두면 실패한다."""
    ids = ["team_vision", "team_agent", "z_new", "team_ai_advance", "a_new", "team_robot"]
    assert rps.daily_order(ids) == ["team_ai_advance", "team_agent", "team_robot", "team_vision", "a_new", "z_new"]


def _run(tmp_path, monkeypatch, fail_on_retry=False):
    db = tmp_path / "t.db"
    for pid in ("team_vision", "team_agent", "team_robot", "team_ai_advance"):
        rp.create_profile(db, pid, pid, ["alpha"])
    events: list[str] = []

    async def scan(db_path, profile_id, client, max_pages=10):
        events.append(f"scan:{profile_id}")
        if profile_id == "team_agent":
            if scan_search.ARXIV_DEFER.get():
                raise scan_search.ArxivDeferred("HTTPStatusError: 429")
            if fail_on_retry:
                raise RuntimeError("검색 소스가 전부 실패했다")
        return ({"run_status": "done", "candidates_found": 0, "scored_count": 0}, "본문")

    async def diag(db_path, profile_id, result, now=None):
        events.append(f"diag:{profile_id}")

    monkeypatch.setattr(rps, "scan_and_digest", scan)
    monkeypatch.setattr(rps, "record_weekly_diagnostics", diag)
    monkeypatch.setattr(rps, "_deliver", lambda d, pid, r, t: events.append(f"mail:{pid}") or "발송 완료 → 1명")
    monkeypatch.setattr(rps, "RETRY_GAP_S", 0)
    summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
    return events, summary


def test_arxiv_failed_profile_is_retried_after_the_others_without_deferral(tmp_path, monkeypatch):
    """첫 바퀴에서 미룸을 켜지 않거나(실패한 채 S2 만으로 보냄), 미룬 프로필을 다시 돌지 않거나, 재시도에서도 미룸을 켜 두어 메일이 끝내 안 나가면 실패한다.
    미룬 프로필 메일은 다른 프로필 메일 뒤에 한 번만 나간다."""
    events, summary = _run(tmp_path, monkeypatch)
    scans = [e for e in events if e.startswith("scan:")]
    mails = [e for e in events if e.startswith("mail:")]
    assert scans == ["scan:team_ai_advance", "scan:team_agent", "scan:team_robot", "scan:team_vision", "scan:team_agent"]
    assert mails == ["mail:team_ai_advance", "mail:team_robot", "mail:team_vision", "mail:team_agent"]
    assert summary["team_agent"]["status"] == "ok"


def test_retry_failure_is_reported_as_error(tmp_path, monkeypatch):
    """재시도마저 실패했는데 '미룸' 상태로 남겨 종료코드·점검이 실패를 못 보면 실패한다."""
    _, summary = _run(tmp_path, monkeypatch, fail_on_retry=True)
    assert summary["team_agent"]["status"] == "error"
    assert rps._exit_code(summary) != 0


def test_scan_profile_defers_before_s2_only_when_asked(tmp_path, monkeypatch):
    """미룸이 켜졌는데 arXiv 통째 실패 뒤 S2 검색·선별로 넘어가 시간·모델 호출을 쓰거나, 미룸이 꺼졌는데(재시도·수동 실행) 메일 경로를 끊으면 실패한다."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "P", ["alpha"], s2_seeds=["alpha"])
    s2_calls: list[str] = []

    async def arxiv_down(*a, **k):
        raise RuntimeError("arXiv 질의 1개 전부 실패 — HTTPStatusError: 429")

    async def s2(client, keywords, since, until):
        s2_calls.append("s2")
        return {"papers": [], "status": "done", "query": "S2"}

    monkeypatch.setattr(scan_search, "_search_arxiv_chunked", arxiv_down)
    monkeypatch.setattr(scan_search.s2_delta, "find_new_papers_since", s2)
    token = scan_search.ARXIV_DEFER.set(True)
    try:
        with pytest.raises(scan_search.ArxivDeferred):
            asyncio.run(scan_search.scan_profile(db, "p", None))
    finally:
        scan_search.ARXIV_DEFER.reset(token)
    assert s2_calls == []
    # 미룬 시도는 닫힌 기록으로 남는다 — arXiv 실행과 이어지고 '미완'이 아니다(Codex 검토: 닫지 않으면 재시도가 성공해도 미완으로 영구히 남았다).
    import observation_signals as sig
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(db)) as con:
        row = con.execute("SELECT arxiv_run_id, observations, observation_error FROM scan_runs WHERE profile_id='p'").fetchone()
    assert row[0] and row[1] is None and row[2].startswith(rp.DEFERRED_SCAN_PREFIX)
    asyncio.run(scan_search.scan_profile(db, "p", None))          # 미룸 꺼짐 — S2 만으로 이어간다
    assert s2_calls == ["s2"]
    health = sig.scan_health(db, "p", datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc))
    assert health["deferred"] == 1 and health["failed"] == 0 and health["incomplete"] == 0
    assert "미뤘다 다시 돈 시도 1회" in "\n".join(sig.format_signals(None, None, None, None, health))


def test_empty_message_error_does_not_abort_the_remaining_queries(monkeypatch):
    """메시지가 빈 예외(타임아웃 등)를 `str(e).splitlines()[0]` 로 읽어 IndexError 가 나 남은 질의를 건너뛰고 원인을 가리면 실패한다."""
    calls: list[str] = []

    async def fake(client, q, since, page_size, max_pages):
        calls.append(q)
        if q == "q1":
            raise TimeoutError()
        return {"papers": [{"arxiv_id": "2611.00001", "title": "T"}], "status": "done", "until": "2026-11-06T00:00:00+00:00"}

    monkeypatch.setattr(scan_search.find_new_papers, "find_new_papers_since", fake)
    out = asyncio.run(scan_search._search_arxiv_chunked(None, ["q1", "q2"], datetime(2026, 11, 5, tzinfo=timezone.utc), 200, 3))
    assert calls == ["q1", "q2"] and out["status"] == "partial" and len(out["papers"]) == 1
    assert out["chunk_errors"] == ["TimeoutError: "]


@pytest.mark.parametrize("step, waits", [(30, [60.0]), (100, [])])
def test_retry_waits_until_the_gap_has_passed_since_the_failure(tmp_path, monkeypatch, step, waits):
    """실패 직후 바로 다시 불러 같은 429 를 맞거나(대기 삭제), 이미 간격이 지났는데도 또 기다리면 실패한다.
    프로필 하나가 step 초 걸린다고 두면: 에이전트 실패 시각 = 2·step, 재시도 직전 = 4·step → 대기 = 120 − 2·step(음수면 0)."""
    import types
    db = tmp_path / "t.db"
    for pid in ("team_vision", "team_agent", "team_robot", "team_ai_advance"):
        rp.create_profile(db, pid, pid, ["alpha"])
    clock = [0.0]
    slept: list[float] = []

    async def scan(db_path, profile_id, client, max_pages=10):
        clock[0] += step
        if profile_id == "team_agent" and scan_search.ARXIV_DEFER.get():
            raise scan_search.ArxivDeferred("HTTPStatusError: 429")
        return ({"run_status": "done", "candidates_found": 0, "scored_count": 0}, "본문")

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    async def diag(*a, **k):
        return None

    monkeypatch.setattr(rps, "scan_and_digest", scan)
    monkeypatch.setattr(rps, "record_weekly_diagnostics", diag)
    monkeypatch.setattr(rps, "_deliver", lambda d, pid, r, t: "발송 완료 → 1명")
    monkeypatch.setattr(rps, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(rps, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
    assert slept == waits and summary["team_agent"]["status"] == "ok"
