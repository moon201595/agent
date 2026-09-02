"""철회 미조회 큐를 실제로 비우는가 (2026-09-02).

실측 배경: `refresh_retraction_status` 는 요약 저장 시점에만 불린다. 설계
주석에는 "NULL 이 재시도 큐 역할을 한다"고 적혀 있었지만 논문이 다시
요약될 일이 없어서 **큐가 영원히 안 빠졌다.** 저장된 83편 중 69편(83%)이
NULL 이었고 원인이 둘이었다:

  54편  M5(2026-08-28) 도입 전에 저장 — 애초에 조회된 적 없음
  15편  신규 논문이라 OpenAlex 에 아직 없어 404

네트워크를 안 탄다 — refresh_retraction_status 를 스텁으로 바꿔 sweep 의
**선택·순서·상한** 로직만 검증한다.
"""

import asyncio
import sqlite3

import pytest

import server


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "DB_PATH", path)
    server._init_storage()
    return path


def _add(db, arxiv_id, fetched_at, is_retracted=None):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, fetched_at, is_retracted) "
                    "VALUES (?,?,?,?)", (arxiv_id, arxiv_id, fetched_at, is_retracted))


def _stub_lookup(monkeypatch, results):
    """results: arxiv_id → 0/1/2/None. 호출된 순서를 같이 돌려준다."""
    called = []

    async def fake(arxiv_id):
        called.append(arxiv_id)
        status = results.get(arxiv_id)
        if status is not None:
            with sqlite3.connect(server.DB_PATH) as con:
                con.execute("UPDATE papers SET is_retracted=? WHERE arxiv_id=?",
                            (status, arxiv_id))
        return status

    monkeypatch.setattr(server, "refresh_retraction_status", fake)
    return called


def test_only_unchecked_papers_are_swept(db, monkeypatch):
    """이미 확정된 논문을 다시 조회하면 크레딧만 쓴다 — 철회는 되돌아가지 않는다."""
    _add(db, "old", "2026-08-01", is_retracted=0)
    _add(db, "new", "2026-08-02", is_retracted=None)
    called = _stub_lookup(monkeypatch, {"new": 0})

    asyncio.run(server.sweep_retraction_status())
    assert called == ["new"]


def test_oldest_first(db, monkeypatch):
    """밀린 것부터 빠져야 한다. 갓 나온 논문은 아직 색인 전이라 어차피
    404 니 자연히 뒤로 밀린다 — 나이 문턱 상수를 따로 두지 않는 이유다
    (실측: 4~5일 된 논문도 조회에 성공했다)."""
    _add(db, "c", "2026-09-01")
    _add(db, "a", "2026-07-30")
    _add(db, "b", "2026-08-15")
    called = _stub_lookup(monkeypatch, {})

    asyncio.run(server.sweep_retraction_status())
    assert called == ["a", "b", "c"]


def test_limit_bounds_the_daily_cost(db, monkeypatch):
    for i in range(30):
        _add(db, f"p{i:02d}", f"2026-08-{i % 28 + 1:02d}")
    called = _stub_lookup(monkeypatch, {})

    asyncio.run(server.sweep_retraction_status(limit=5))
    assert len(called) == 5


def test_reports_progress_so_the_backlog_is_visible(db, monkeypatch):
    _add(db, "a", "2026-08-01")
    _add(db, "b", "2026-08-02")
    _add(db, "c", "2026-08-03")
    _stub_lookup(monkeypatch, {"a": 0, "b": 1})     # c 는 조회 실패(None)

    out = asyncio.run(server.sweep_retraction_status())
    assert out["checked"] == 3
    assert out["resolved"] == 2
    assert out["retracted"] == 1
    assert out["remaining"] == 1                    # c 는 NULL 로 남아 내일 다시


def test_failed_lookup_stays_queued(db, monkeypatch):
    """조회 실패를 '정상'으로 굳히면 안 된다 — NULL 로 남아야 다시 본다."""
    _add(db, "a", "2026-08-01")
    _stub_lookup(monkeypatch, {})

    asyncio.run(server.sweep_retraction_status())
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT is_retracted FROM papers WHERE arxiv_id='a'").fetchone()[0] is None


def test_empty_queue_is_not_an_error(db, monkeypatch):
    _add(db, "a", "2026-08-01", is_retracted=0)
    _stub_lookup(monkeypatch, {})
    out = asyncio.run(server.sweep_retraction_status())
    assert out == {"checked": 0, "resolved": 0, "retracted": 0, "remaining": 0}
