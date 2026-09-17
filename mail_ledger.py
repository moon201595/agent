"""⑨ 배달 — 보낸 메일 회차와 그 안의 논문을 남긴다(운영 화면용). 네트워크·LLM 없음.

왜 따로 두는가(2026-09-16, 사용자 요청 "프로필별로 총 몇 편의 메일이 나갔고 어떤 논문이 나갔는지 보고 싶다"):
`profile_shown` 은 논문마다 **처음** 나간 시각 하나만 남기고(중복 발송 방지용), `feedback_tokens` 는 버튼 설정이 있을 때만
회차(issue_id)를 남긴다. 둘 다 "몇 번째 메일에 무엇이 실렸나"를 답하지 못한다. 여기서는 발송 루프가 회차 하나마다 한 행,
논문마다 한 행을 남긴다. 버튼 설정이 없어도, 발송이 실패해도(status 로) 남는다.

이 표가 생기기 전(2026-09-16 이전) 회차는 `profile_shown` 을 KST 날짜로 묶어 **복원**해 보여 준다(`source="legacy"`). 복원본은
그날 처음 나간 논문의 집합이지 그날 메일의 정확한 목록이 아니다 — 화면에 그렇게 표시한다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from time_policy import KST, kst_day   # 2026-09-17: 시각 정책 통합
_ARXIV_KEY_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def _ddl(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS mail_issues ("
        " issue_id         TEXT PRIMARY KEY,"
        " profile_id       TEXT NOT NULL,"
        " sent_at          TEXT NOT NULL,"       # UTC ISO — 발송 루프가 끝난 시각
        " subject          TEXT,"
        " recipients_total INTEGER NOT NULL,"
        " recipients_sent  INTEGER NOT NULL,"
        " paper_count      INTEGER NOT NULL,"
        " status           TEXT NOT NULL)"       # sent | partial | failed
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_mail_issues_profile ON mail_issues(profile_id, sent_at)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS mail_issue_items ("
        " issue_id   TEXT NOT NULL,"
        " position   INTEGER NOT NULL,"
        " paper_key  TEXT NOT NULL,"
        " title      TEXT,"
        " link       TEXT,"
        " core_hits  TEXT,"                      # JSON 목록 — 그 논문이 걸린 핵심 키워드
        " PRIMARY KEY (issue_id, position))"
    )


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "mail_ledger")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_issue(db: Path, issue_id: str, profile_id: str, subject: str, papers: list[dict],
                 recipients_total: int, recipients_sent: int, when: datetime | None = None) -> str:
    """회차 하나를 남긴다. returns status. 수신자 0명(안 보냄)은 회차가 아니다 — 호출부가 부르지 않는다."""
    import digest
    import research_profile
    init_db(db)
    if recipients_sent <= 0:
        status = "failed"
    elif recipients_sent < recipients_total:
        status = "partial"
    else:
        status = "sent"
    sent_at = (when or _now()).isoformat()
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO mail_issues (issue_id, profile_id, sent_at, subject, recipients_total, "
                    "recipients_sent, paper_count, status) VALUES (?,?,?,?,?,?,?,?)",
                    (issue_id, profile_id, sent_at, subject, recipients_total, recipients_sent, len(papers), status))
        con.execute("DELETE FROM mail_issue_items WHERE issue_id=?", (issue_id,))
        for position, paper in enumerate(papers, start=1):
            score = paper.get("_score") or {}
            con.execute("INSERT INTO mail_issue_items (issue_id, position, paper_key, title, link, core_hits) "
                        "VALUES (?,?,?,?,?,?)",
                        (issue_id, position, research_profile.paper_key(paper), paper.get("title") or "",
                         digest.paper_link(paper), json.dumps(list(score.get("core_hits") or []), ensure_ascii=False)))
    return status


def _legacy_link(paper_key: str) -> str:
    if _ARXIV_KEY_RE.match(paper_key):
        return f"https://arxiv.org/abs/{paper_key}"
    if paper_key.startswith("doi:"):
        return f"https://doi.org/{paper_key[4:]}"
    return ""


def _legacy_issues(con: sqlite3.Connection, profile_id: str, before: str | None) -> list[dict]:
    """`profile_shown` 을 KST 날짜로 묶은 복원 회차 — **표의 첫 회차(`before`) 이전에 나간 것만**. 날짜 단위로 가리면 표가 생긴 날
    그 전에 나간 논문이 사라진다(외부 검토 2026-09-16). 발송 루프는 회차 기록 뒤에 `mark_shown` 을 하므로 첫 회차 이후 행은 전부 표에 있다."""
    by_day: dict[str, list[dict]] = {}
    has_obs = con.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_observations'").fetchone() is not None

    def _hits(key: str) -> list[str]:
        """복원 회차의 적중 키워드 — 그 논문의 마지막 관측에서. 없으면 빈 목록(모른다고 표시)."""
        if not has_obs:
            return []
        row = con.execute("SELECT core_hits FROM candidate_observations WHERE profile_id=? AND paper_key=? "
                          "ORDER BY observed_at DESC LIMIT 1", (profile_id, key)).fetchone()
        try:
            return list(json.loads((row[0] if row else None) or "[]"))
        except (TypeError, ValueError):
            return []

    for key, title, shown_at in con.execute(
            "SELECT paper_key, title, shown_at FROM profile_shown WHERE profile_id=? ORDER BY shown_at", (profile_id,)):
        try:
            day = kst_day(str(shown_at), missing="")
            if len(day) != 10 or day[4] != "-":
                raise ValueError(day)
        except ValueError:
            continue
        if before is not None and str(shown_at) >= before:
            continue
        by_day.setdefault(day, []).append({"paper_key": key, "title": title or "", "link": _legacy_link(key),
                                           "core_hits": _hits(key), "shown_at": shown_at})
    out = []
    for day, items in sorted(by_day.items(), reverse=True):
        out.append({"issue_id": f"legacy:{profile_id}:{day}", "profile_id": profile_id, "sent_at": items[0]["shown_at"],
                    "day": day, "subject": None, "recipients_total": None, "recipients_sent": None,
                    "paper_count": len(items), "status": "sent", "source": "legacy",
                    "items": [{"position": i, **it} for i, it in enumerate(items, start=1)]})
    return out


def list_issues(db: Path, profile_id: str, limit: int | None = None) -> list[dict]:
    """최신 회차부터. 표에 있는 회차 + 표 이전 날짜의 복원 회차. 각 회차에 items(위치·논문·링크·적중 키워드)를 붙인다."""
    init_db(db)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM mail_issues WHERE profile_id=? ORDER BY sent_at DESC", (profile_id,))]
        for r in rows:
            r["day"] = kst_day(r["sent_at"])
            r["source"] = "ledger"
            r["items"] = [{"position": it["position"], "paper_key": it["paper_key"], "title": it["title"] or "",
                           "link": it["link"] or "", "core_hits": json.loads(it["core_hits"] or "[]")}
                          for it in con.execute("SELECT * FROM mail_issue_items WHERE issue_id=? ORDER BY position",
                                                (r["issue_id"],))]
        # 표의 첫 회차 이전만 복원한다 — 그 뒤는 표가 진실이다(실패 회차만 있는 날도).
        first = min((r["sent_at"] for r in rows), default=None)
        rows += _legacy_issues(con, profile_id, first)
    rows.sort(key=lambda r: r["sent_at"], reverse=True)
    return rows[:limit] if limit else rows


def counts(db: Path, profile_id: str) -> dict:
    """{issues, papers, last_sent_at, last_status} — 복원 회차 포함."""
    issues = list_issues(db, profile_id)
    delivered = [i for i in issues if i["status"] != "failed"]
    return {"issues": len(delivered), "papers": sum(i["paper_count"] for i in delivered),
            "failed_issues": len(issues) - len(delivered),
            "last_sent_at": issues[0]["sent_at"] if issues else None,
            "last_status": issues[0]["status"] if issues else None}
