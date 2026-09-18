"""⑧ 축적 — 동향 서술과 주간 리뷰를 프로필·날짜별로 남긴다. 판정하지 않고 글과 출처만 보관한다.

왜 필요한가(2026-09-18 사용자 지적): 여태 서술은 **아무 데도 안 쌓였다**. 다이제스트 전문이
`profiles.last_digest` 에 들어가는데 그건 "가장 최근 하나"라 다음 날 덮어썼고(`research_profile.save_digest`
주석이 "이력 전체 아님"이라고 명시한다), 주간 리뷰는 저장 자체가 없어 그날 메일에만 남고 사라졌다.
쌓인 서술은 그 자체가 동향 자료다 — "지난 5일 동안 무엇을 동향이라고 불렀나"를 다시 읽을 수 있어야
오늘의 서술이 어제와 이어진다.

여기 있는 것은 **글과 그 글이 어디서 왔는지**뿐이다. 편수·증감 같은 수치는 여전히 Python 이 그때그때
DB 에서 센다(규칙 2) — 서술 안의 숫자를 나중에 사실로 다시 읽으면 안 되기 때문이다.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import schema_guard
from time_policy import KST

DAILY = "daily"
WEEKLY = "weekly"
KINDS = (DAILY, WEEKLY)


def _ddl(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS profile_narratives (
            narrative_id TEXT PRIMARY KEY,
            profile_id   TEXT NOT NULL,
            kind         TEXT NOT NULL,          -- daily | weekly
            reader_date  TEXT NOT NULL,          -- 읽는 사람(KST) 날짜 YYYY-MM-DD
            created_at   TEXT NOT NULL,          -- UTC ISO
            scan_id      TEXT,
            engine       TEXT,                   -- gemini | groq | codex — 누가 썼는지
            body         TEXT NOT NULL,
            audit_json   TEXT,                   -- citation_audit 결과 그대로
            papers_seen  INTEGER,                -- 이 글이 실제로 본 논문 수
            window_days  INTEGER                 -- 본 창의 길이(일). daily=1, weekly=7
        )""")
    # 같은 프로필·같은 날·같은 종류는 하나다. 하루에 두 번 돌면 뒤엣것이 이긴다(덮어쓰기).
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_narratives_unique "
                "ON profile_narratives(profile_id, kind, reader_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_narratives_recent "
                "ON profile_narratives(profile_id, kind, reader_date DESC)")


def init_db(db_path: Path) -> None:
    schema_guard.ensure(db_path, _ddl, "narrative_store")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reader_date(moment: datetime | None = None) -> str:
    """읽는 사람 날짜(KST). 05:00 KST 실행은 UTC 로 전날 20:00 이라 UTC 날짜로 묶으면 하루가 밀린다."""
    return (moment or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y-%m-%d")


def save(db_path: Path, profile_id: str, kind: str, body: str, *,
         scan_id: str | None = None, engine: str | None = None,
         audit: dict | None = None, papers_seen: int | None = None,
         window_days: int | None = None, moment: datetime | None = None) -> str | None:
    """서술 한 편을 남긴다. 빈 글은 저장하지 않는다(없는 것을 만들지 않는다 — 규칙 7).

    같은 프로필·날짜·종류가 이미 있으면 **덮어쓴다**. 하루 두 번 돌린 날(오늘 9/18 이 그랬다)
    마지막에 실제로 나간 글이 남아야 메일과 기록이 어긋나지 않는다.
    """
    if kind not in KINDS:
        raise ValueError(f"kind 는 {KINDS} 중 하나여야 한다: {kind!r}")
    body = (body or "").strip()
    if not body:
        return None
    init_db(db_path)
    nid = uuid.uuid4().hex
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO profile_narratives (narrative_id, profile_id, kind, reader_date, created_at,"
            " scan_id, engine, body, audit_json, papers_seen, window_days)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(profile_id, kind, reader_date) DO UPDATE SET"
            " created_at=excluded.created_at, scan_id=excluded.scan_id, engine=excluded.engine,"
            " body=excluded.body, audit_json=excluded.audit_json,"
            " papers_seen=excluded.papers_seen, window_days=excluded.window_days",
            (nid, profile_id, kind, reader_date(moment), _now(), scan_id, engine, body,
             json.dumps(audit, ensure_ascii=False) if audit else None,
             papers_seen, window_days))
    return nid


def recent(db_path: Path, profile_id: str, kind: str = DAILY, days: int = 5,
           before: str | None = None, limit: int | None = None) -> list[dict]:
    """최근 `days` 일치 서술을 **최신순**으로. `before`(YYYY-MM-DD)를 주면 그 날짜는 빼고 그 이전만.

    오늘 글을 쓰면서 지난 글을 읽는 자리라 **오늘 것은 빼는 게 기본 용법**이다 — `before=오늘`.
    날짜로 자르므로 스캔이 걸러 하루가 비면 그날은 그냥 없다(0 으로 채우지 않는다).
    """
    if kind not in KINDS:
        raise ValueError(f"kind 는 {KINDS} 중 하나여야 한다: {kind!r}")
    end = before or reader_date()
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    sql = ("SELECT * FROM profile_narratives WHERE profile_id=? AND kind=?"
           " AND reader_date>=? AND reader_date<? ORDER BY reader_date DESC")
    params: list = [profile_id, kind, start, end]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    try:
        with sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, params)]
    except sqlite3.OperationalError:
        return []          # 표가 아직 없는 설치 — 미측정이지 0 이 아니다. 호출부는 빈 목록을 그렇게 읽는다.
