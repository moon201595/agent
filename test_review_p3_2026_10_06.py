"""2026-10-06 독립 검토 P3 4건의 회귀 — 저장 메일 꼬리 문구(평문·줄 끝), 상세 화면 하위 제목, 조회 helper 읽기 전용."""
from datetime import datetime, timedelta, timezone
import sqlite3
import sys

import pytest

import mail_ledger
import ops_dashboard as od
import research_profile as rp
import saved_digest
import storage
import trend_report

PHRASE = "그 밖에 작게 움직인 가중치 1건"


@pytest.mark.parametrize("line,expected", [
    (f"{PHRASE} · 반영된 사용자 반응 15건", "반영된 사용자 반응 15건"),      # 운영 DB 에 실제로 있는 순서(team_ai_advance 10/6)
    (f"반영된 사용자 반응 15건 · {PHRASE}", "반영된 사용자 반응 15건"),      # P3-2: 줄 끝 — 앞 구분자가 남으면 안 된다
    (f"A 1건 · {PHRASE} · 반영된 사용자 반응 3건", "A 1건 · 반영된 사용자 반응 3건"),
    (PHRASE, ""),
    ("원문 분석 · 초록 기반", "원문 분석 · 초록 기반"),                      # 문구 없는 줄의 구분자는 그대로
])
def test_hidden_moves_phrase_removed_in_any_position(line, expected):
    """뒤 구분자만 먹는 옛 정규식으로 되돌리면 줄 끝 경우가 `… 15건 ·` 로 남아 실패한다. 반응 건수를 지우거나 다른 줄의
    구분자를 건드려도 실패한다."""
    assert saved_digest._without_hidden_moves(line) == expected
    assert saved_digest._line(line).count("·") == expected.count("·")


def test_plain_text_drops_phrase_but_keeps_reactions_and_layout():
    """P3-1: 평문 쪽에서 문구를 안 빼거나, 문구뿐이던 줄을 빈 줄로 남기거나, 들여쓰기·다른 줄을 바꾸면 실패한다."""
    body = ("■ 지난 8일 검색 기준 변화\n"
            f"   {PHRASE} · 반영된 사용자 반응 15건\n"
            f"   {PHRASE}\n"
            "   원문 분석 · 초록 기반\n")
    got = saved_digest.plain_text(body)
    assert "작게 움직인" not in got
    assert got == ("■ 지난 8일 검색 기준 변화\n"
                   "   반영된 사용자 반응 15건\n"
                   "   원문 분석 · 초록 기반\n")


def test_reformat_sends_cleaned_plain_text_and_keeps_record(tmp_path, monkeypatch):
    """P3-1: 다시 보내는 평문에 문구가 남거나, 원 기록(운영 DB 저장 평문)을 고치거나, 원본 해시를 고친 평문으로 재면 실패한다."""
    import feedback_links as feedback
    import mail_reformat
    from test_mail_layout import SAVED
    saved = SAVED.rstrip("\n") + f"\n   {PHRASE} · 반영된 사용자 반응 15건\n"
    db = tmp_path / "original.db"
    cfg = ("https://script.google.com/macros/s/" + "a" * 24 + "/exec", "test-secret-" * 5)
    monkeypatch.setattr(feedback, "config", lambda: cfg)
    monkeypatch.setattr(feedback, "page_url", lambda: "")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(mail_reformat, "datetime", Clock)
    rp.create_profile(db, "p", "팀", ["agent"])
    rp.save_digest(db, "p", saved)
    paper = {"arxiv_id": "2609.00001", "title": "One & Paper"}
    when = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
    mail_ledger.record_issue(db, "original-issue", "p", "브리핑 2026-10-02", [paper], 1, 1, when=when)
    feedback.issue_links(db, profile_id="p", issue_id="original-issue", recipient="user@example.com", papers=[paper], now=when)
    feedback.mark_delivered(db, "original-issue", "user@example.com", when=when)
    before = db.read_bytes()
    got = mail_reformat.load_snapshot(db, "p", "2026-10-02", "user@example.com")
    assert "작게 움직인" not in got["text"] and "반영된 사용자 반응 15건" in got["text"]
    assert "작게 움직인" not in got["html"] and "반영된 사용자 반응 15건" in got["html"]
    assert got["source_text"] == saved and db.read_bytes() == before
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT last_digest FROM profiles WHERE profile_id='p'").fetchone()[0] == saved


@pytest.mark.parametrize("line,label", [("#### 학습 과정", "학습 과정"), ("### **2. 세부 절**", "2. 세부 절"), ("  ##### 끝 ###", "끝")])
def test_detail_subheading_renders_without_hashes(line, label):
    """P3-3: 하위 제목 분기를 지우면 `####` 가 글자로 남아 실패한다. 제목 글을 잃거나 굵은 표시 기호가 남아도 실패한다."""
    import review_app
    html = review_app._summary_line_html(line)
    assert "#" not in html and "**" not in html and f"<b>{label}</b>" in html


def test_detail_regular_line_unchanged():
    """하위 제목 판정이 넓어져 일반 요약 줄까지 소제목으로 그리면 실패한다."""
    import review_app
    html = review_app._summary_line_html("- 항목: 값 #3 [S0006]")
    assert "rm-subhead" not in html and "<b>항목</b>" in html and "#3" in html


def _populated(tmp_path):
    db = tmp_path / "ro.db"
    rp.create_profile(db, "p", "P", ["robot"])
    storage.init_storage(db)
    paper = {"title": "A robot paper", "arxiv_id": "2610.00001",
             "_score": {"core_hits": ["robot"], "domain_hits": [], "venue_hit": None, "priority": 1.0}}
    mail_ledger.record_issue(db, "i1", "p", "S", [paper], 1, 1, when=datetime(2026, 10, 2, tzinfo=timezone.utc))
    return db


def test_dashboard_query_helpers_open_readonly(tmp_path, monkeypatch):
    """P3-4: 조회 helper 중 하나라도 `sqlite3.connect(db)`(쓰기 가능)로 되돌리면 그 자리에서 실패한다."""
    db = _populated(tmp_path)
    original = sqlite3.connect
    seen = []

    def connect(database, *args, **kwargs):
        # helper 자신의 연결만 본다 — `get_profile` 이 부르는 `schema_guard.ensure` 는 이 검토의 범위 밖이다(운영 DB 에는 DDL 을 안 돈다).
        caller = sys._getframe(1).f_globals.get("__name__")
        if caller in ("ops_dashboard", "trend_report"):
            seen.append(str(database))
            assert kwargs.get("uri") and str(database).endswith("?mode=ro"), database
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    od.profile_overview(db, "p")
    od.weight_history(db, "p")
    od.issues_with_reactions(db, "p")
    od.revision_history(db, "p")
    od.agent_history(db, "p")
    trend_report.collection_rows(db, "p", now - timedelta(days=7), now)
    for fn in (od.keyword_table, od.reaction_log):     # 표가 없으면 그 표 탓으로 실패할 수 있다 — 연결 방식만 본다
        try:
            fn(db, "p")
        except sqlite3.OperationalError as e:
            assert "no such table" in str(e)
    assert len(seen) >= 8


def test_readonly_helper_refuses_writes(tmp_path):
    """`_ro` 가 `mode=ro` 를 잃으면 쓰기가 성공해 실패한다."""
    db = _populated(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with od._ro(db) as con:
            con.execute("DELETE FROM profiles")
