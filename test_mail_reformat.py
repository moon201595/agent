"""재발송은 기존 수신자·본문·토큰을 보존하고 운영 DB를 바꾸지 않는다."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import feedback_links as feedback
import mail_ledger
import mail_reformat
import research_profile as profile
from test_mail_layout import SAVED


@pytest.fixture
def original(tmp_path, monkeypatch):
    db = tmp_path / "original.db"
    cfg = ("https://script.google.com/macros/s/" + "a" * 24 + "/exec", "test-secret-" * 5)
    monkeypatch.setattr(feedback, "config", lambda: cfg)
    monkeypatch.setattr(feedback, "page_url", lambda: "")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(mail_reformat, "datetime", Clock)
    profile.create_profile(db, "p", "팀", ["agent"])
    profile.save_digest(db, "p", SAVED)
    paper = {"arxiv_id": "2609.00001", "title": "One & Paper"}
    when = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
    mail_ledger.record_issue(db, "original-issue", "p", "브리핑 2026-10-02", [paper], 1, 1, when=when)
    links = feedback.issue_links(db, profile_id="p", issue_id="original-issue", recipient="user@example.com", papers=[paper], now=when)
    feedback.mark_delivered(db, "original-issue", "user@example.com", when=when)
    return db, links, cfg


def test_snapshot_reuses_recipient_tokens_and_keeps_database_unchanged(original, monkeypatch):
    """새 회차·토큰·발송 시각을 쓰거나 다른 사용자 토큰을 붙이면 실패한다. 초기화 함수도 호출하지 않는다."""
    db, links, cfg = original
    before = db.read_bytes()
    opened = []
    real_connect = sqlite3.connect
    def connect(path, **kwargs):
        con = real_connect(path, **kwargs)
        opened.append((path, kwargs, con))
        return con
    monkeypatch.setattr(mail_reformat.sqlite3, "connect", connect)
    got = mail_reformat.load_snapshot(db, "p", "2026-10-02", "user@example.com", title_only_count=8)
    assert got["text"] == SAVED and got["issue_id"] == "original-issue"
    assert got["papers"] == 1 and got["subject"] == "브리핑 2026-10-02 [형식 수정본]"
    assert db.read_bytes() == before
    assert len(opened) == 1 and str(opened[0][0]).endswith("?mode=ro") and opened[0][1]["uri"] is True
    assert opened[0][2].execute("PRAGMA query_only").fetchone()[0] == 1
    for url in next(iter(links.values())).values():
        token = parse_qs(urlsplit(url).query)["t"][0]
        assert feedback.verify_token(token, cfg[1]) and token in got["html"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM mail_issues").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM feedback_tokens").fetchone()[0] == 1


@pytest.mark.parametrize("date,recipient", [("2026-10-01", "user@example.com"), ("2026-10-02", "other@example.com")])
def test_snapshot_rejects_wrong_date_or_recipient(original, date, recipient):
    """다른 날짜의 본문이나 원래 받지 않은 사람에게 재발송하면 실패해야 한다."""
    with pytest.raises(ValueError):
        mail_reformat.load_snapshot(original[0], "p", date, recipient)


def test_snapshot_rejects_expired_original_tokens(original):
    """만료 토큰을 새 토큰으로 조용히 바꾸면 사용자 반응이 다른 회차로 갈 수 있어 실패해야 한다."""
    db = original[0]
    with sqlite3.connect(db) as con:
        con.execute("UPDATE feedback_tokens SET expires_at=1")
    with pytest.raises(ValueError, match="만료"):
        mail_reformat.load_snapshot(db, "p", "2026-10-02", "user@example.com")


def test_preview_sends_nothing_and_send_backs_up_once_for_one_recipient(original, tmp_path, monkeypatch, capsys):
    """--send 없이 SMTP를 부르거나 백업 없이 보내거나 수신자를 확대하거나 성공 기록 뒤 재발송하면 실패한다."""
    import email_delivery
    sent = []
    output = tmp_path / "preview"
    def send(text, subject, recipients, html):
        assert len(list(output.glob("*.backup.db"))) == 1
        sent.append((text, recipients, html))
    monkeypatch.setattr(email_delivery, "send_digest_email", send)
    argv = ["mail_reformat.py", "--profile", "p", "--date", "2026-10-02", "--recipient", "user@example.com",
            "--db", str(original[0]), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    mail_reformat.main()
    assert not sent and not list(output.glob("*.backup.db"))
    monkeypatch.setattr(sys, "argv", argv + ["--send"])
    mail_reformat.main()
    assert len(sent) == 1 and sent[0][0] == SAVED and sent[0][1] == ["user@example.com"]
    backup = next(output.glob("*.backup.db"))
    with sqlite3.connect(backup) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM mail_issues").fetchone()[0] == 1
    outcome = json.loads(next(output.glob("*.sent.json")).read_text())
    assert outcome["sent"] is True
    with pytest.raises(ValueError, match="중복"):
        mail_reformat.main()
    assert len(sent) == 1 and "test-secret" not in capsys.readouterr().out
