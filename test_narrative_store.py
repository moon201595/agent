"""⑧ 동향 서술 보관 — 프로필·날짜별로 쌓인다(2026-09-18 사용자 지적).

그전까지 서술은 `profiles.last_digest` 안에서 매일 덮어써졌고 주간 리뷰는 저장 자체가 없었다.
여기서 지키는 계약: 같은 날 두 번 돌면 마지막 것이 남고(메일과 기록이 어긋나면 안 된다),
빈 글은 저장하지 않으며, 읽는 날짜는 KST 로 묶는다(05:00 KST 실행은 UTC 로 전날이다)."""
from datetime import datetime, timedelta, timezone

import pytest

import narrative_store as ns


def _all(db, pid, kind=ns.DAILY):
    """이 저장소의 모든 기록 — `recent` 는 **`before` 에서 거꾸로 센 N일 창**이라 둘을 같이 늘려야 한다.
    (36500일=100년을 2999 에서 빼면 2899 라 2026 년 기록이 창 밖으로 나간다. 실제로 그렇게 헤맸다.)"""
    return ns.recent(db, pid, kind, days=3650, before="2036-01-01")


def test_same_day_rerun_replaces_instead_of_duplicating(tmp_path):
    """이 테스트가 잡는 것: 하루 두 번 돌린 날(2026-09-18 이 그랬다) 같은 날짜 서술이 둘 쌓여
    나중에 "그날 뭐라고 썼나"를 물었을 때 어느 것이 실제로 나간 글인지 못 가리게 되는 것."""
    db = tmp_path / "p.db"
    first = ns.save(db, "p1", ns.DAILY, "아침 서술", engine="gemini", papers_seen=13)
    second = ns.save(db, "p1", ns.DAILY, "다시 돌린 서술", engine="codex", papers_seen=13)
    assert first and second
    rows = _all(db, "p1")
    assert len(rows) == 1
    assert rows[0]["body"] == "다시 돌린 서술"
    assert rows[0]["engine"] == "codex"


def test_empty_body_is_not_stored(tmp_path):
    """이 테스트가 잡는 것: 서술이 실패해 빈 문자열이 왔는데 빈 기록을 남겨,
    나중에 '그날은 동향이 있었다'고 잘못 읽게 되는 것(규칙 7)."""
    db = tmp_path / "p.db"
    assert ns.save(db, "p1", ns.DAILY, "") is None
    assert ns.save(db, "p1", ns.DAILY, "   \n ") is None
    assert _all(db, "p1") == []


def test_recent_excludes_today_and_respects_window(tmp_path):
    """이 테스트가 잡는 것: 오늘 글을 쓰면서 오늘 글을 자기 입력으로 다시 읽는 것(자기 참조) ·
    창 밖의 오래된 서술까지 끌고 오는 것."""
    db = tmp_path / "p.db"
    base = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)      # KST 로 9/18 13:00
    for back in range(0, 8):
        ns.save(db, "p1", ns.DAILY, f"{back}일 전 서술",
                moment=base - timedelta(days=back))
    rows = ns.recent(db, "p1", days=5, before=ns.reader_date(base))
    dates = [r["reader_date"] for r in rows]
    assert ns.reader_date(base) not in dates          # 오늘은 빠진다
    assert len(rows) == 5                              # 창 5일
    assert dates == sorted(dates, reverse=True)        # 최신순


def test_weekly_and_daily_do_not_collide(tmp_path):
    """이 테스트가 잡는 것: 같은 날 주간 리뷰가 일일 서술을 덮어쓰는 것(유일 색인이 kind 를 빼먹는 것)."""
    db = tmp_path / "p.db"
    ns.save(db, "p1", ns.DAILY, "일일")
    ns.save(db, "p1", ns.WEEKLY, "주간")
    assert _all(db, "p1", ns.DAILY)[0]["body"] == "일일"
    assert _all(db, "p1", ns.WEEKLY)[0]["body"] == "주간"


def test_unknown_kind_is_refused(tmp_path):
    """이 테스트가 잡는 것: 오타난 kind 가 조용히 저장돼 나중에 아무 조회에도 안 걸리는 것."""
    db = tmp_path / "p.db"
    with pytest.raises(ValueError):
        ns.save(db, "p1", "montly", "글")
    with pytest.raises(ValueError):
        ns.recent(db, "p1", "montly")


def test_missing_table_reads_as_empty_not_crash(tmp_path):
    """이 테스트가 잡는 것: 표가 아직 없는 설치(마이그레이션 전)에서 조회가 터져 메일을 막는 것."""
    db = tmp_path / "빈.db"
    db.write_bytes(b"")
    assert ns.recent(db, "p1") == []


def test_history_context_carries_past_text_but_not_its_evidence_ids():
    """이 테스트가 잡는 것: 지난 서술을 통째로 넣어 오늘 글보다 길어지는 것(모델이 어제 얘기를 되풀이한다) ·
    지난 글의 근거 ID 를 오늘 자료의 것인 양 쓰게 두는 것 · 빈 목록에 머리말만 남기는 것."""
    import trend_report as tr
    ctx = tr._history_context([{"reader_date": "2026-09-17", "body": "어제 흐름 [P3:A]" + "가" * 3000},
                               {"reader_date": "2026-09-16", "body": "그제 흐름"}], max_chars=200)
    assert "2026-09-17" in ctx and "2026-09-16" in ctx
    assert "잘랐다" in ctx                      # 자른 사실을 밝힌다
    assert len(ctx) < 1200                       # 통째로 넣지 않는다
    assert "근거 ID" in ctx and "쓰지 않는다" in ctx
    assert tr._history_context([]) == "" and tr._history_context(None) == ""
    assert tr._history_context([{"reader_date": "2026-09-17", "body": "  "}]) == ""
