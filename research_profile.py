"""research_profile.py — Research Profile 저장소 + search_runs 이력.

설계 문서(2026-08-19) §1·§3을 코드로 옮긴 것. server.py의 papers.db를
그대로 재사용한다(같은 SQLite 파일, 새 테이블만 추가) — 하네스 핵심
스키마(papers/summaries/repro_results/paper_embeddings)는 전혀 안 건드리고,
_init_storage()와 같은 자리에 새 테이블을 얹는 방식도 그대로 따른다(멱등한
CREATE TABLE IF NOT EXISTS).

키워드를 profile_keywords로 별도 테이블에 둔 이유: 설계 문서 Phase 4(키워드
자동 확장)에서 "새 키워드를 행으로 추가 + 승인 이력"이 필요해지는데, 지금
행 단위로 둬야 그때 added_by/approved_at 같은 컬럼을 얹기 쉽다.

search_runs로 "마지막에 어디까지 봤는지"를 기록한다(§3, "빠진 논문" 문제).
다음 실행이 봐야 할 since 시각은 이 이력에서 결정된다 — 마지막 실행이
done이면 그 실행의 window_to부터, partial/failed면 그 실행의 window_from을
그대로 다시 본다(그 구간을 다 못 봤을 수 있으니 앞당기지 않는다).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT,
    max_items INTEGER DEFAULT 8,
    schedule_frequency TEXT DEFAULT 'daily',
    schedule_time TEXT DEFAULT '05:00',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS profile_keywords (
    profile_id TEXT,
    keyword TEXT,
    kind TEXT,              -- 'core' | 'target' | 'exclude'
    weight REAL DEFAULT 1.0,
    added_at TEXT,
    PRIMARY KEY (profile_id, keyword, kind)
);
CREATE TABLE IF NOT EXISTS profile_venues (
    profile_id TEXT,
    venue TEXT,
    PRIMARY KEY (profile_id, venue)
);
CREATE TABLE IF NOT EXISTS profile_recipients (
    profile_id TEXT,
    email TEXT,
    active INTEGER DEFAULT 1,
    PRIMARY KEY (profile_id, email)
);
CREATE TABLE IF NOT EXISTS search_runs (
    run_id TEXT PRIMARY KEY,
    profile_id TEXT,
    source TEXT,             -- 지금은 'arxiv'뿐 (S2는 day-level delta 불가, 2026-08-24 리뷰)
    query TEXT,
    window_from TEXT,
    window_to TEXT,
    status TEXT,              -- 'done' | 'partial' | 'failed'
    retrieved_count INTEGER,
    error_detail TEXT,
    started_at TEXT,
    finished_at TEXT
);
"""


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as con:
        con.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_profile(
    db_path: Path, profile_id: str, name: str,
    core_topics: list[str], target_domain: list[str] | None = None,
    exclude: list[str] | None = None, venues: list[str] | None = None,
    max_items: int = 8, schedule_frequency: str = "daily", schedule_time: str = "05:00",
) -> None:
    """기존 프로필이면 통째로 덮어쓴다(키워드도 전부 지우고 다시 씀) —
    "일부만 바뀐 것"과 "이전 키워드가 실수로 안 지워진 것"을 구분 못 하게
    두느니, 매번 전체 상태를 새로 쓰는 쪽을 택했다(batch_summarize.py의
    _write_progress와 같은 이유)."""
    init_db(db_path)
    now = _now()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO profiles (profile_id, name, max_items, schedule_frequency, "
            "schedule_time, created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(profile_id) DO UPDATE SET name=excluded.name, "
            "max_items=excluded.max_items, schedule_frequency=excluded.schedule_frequency, "
            "schedule_time=excluded.schedule_time, updated_at=excluded.updated_at",
            (profile_id, name, max_items, schedule_frequency, schedule_time, now, now),
        )
        con.execute("DELETE FROM profile_keywords WHERE profile_id=?", (profile_id,))
        con.execute("DELETE FROM profile_venues WHERE profile_id=?", (profile_id,))
        for kind, kws in (("core", core_topics), ("target", target_domain or []),
                          ("exclude", exclude or [])):
            for kw in kws:
                con.execute(
                    "INSERT INTO profile_keywords (profile_id, keyword, kind, weight, added_at) "
                    "VALUES (?,?,?,?,?)",
                    (profile_id, kw, kind, 1.0, now),
                )
        for v in (venues or []):
            con.execute(
                "INSERT INTO profile_venues (profile_id, venue) VALUES (?,?)",
                (profile_id, v),
            )


def get_profile(db_path: Path, profile_id: str) -> dict | None:
    """profile_scoring.score_paper()가 바로 받는 모양으로 조립해서 돌려준다
    — 이 함수의 출력이 곧 그 함수의 입력이라는 계약을 여기 문서화해둔다."""
    init_db(db_path)  # DB 파일이 아직 없는 상태(테이블조차 없음)에서 조회해도
    # "없음(None)"으로 답해야지 OperationalError를 내면 안 된다.
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not row:
            return None
        kw_rows = con.execute(
            "SELECT keyword, kind FROM profile_keywords WHERE profile_id=?", (profile_id,)
        ).fetchall()
        venue_rows = con.execute(
            "SELECT venue FROM profile_venues WHERE profile_id=?", (profile_id,)
        ).fetchall()

    by_kind: dict[str, list[str]] = {"core": [], "target": [], "exclude": []}
    for r in kw_rows:
        by_kind.setdefault(r["kind"], []).append(r["keyword"])

    return {
        "profile_id": row["profile_id"], "name": row["name"],
        "max_items": row["max_items"],
        "core_topics": by_kind["core"], "target_domain": by_kind["target"],
        "exclude": by_kind["exclude"], "venues": [v["venue"] for v in venue_rows],
    }


def add_recipient(db_path: Path, profile_id: str, email: str, active: bool = True) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO profile_recipients (profile_id, email, active) VALUES (?,?,?) "
            "ON CONFLICT(profile_id, email) DO UPDATE SET active=excluded.active",
            (profile_id, email, 1 if active else 0),
        )


def get_recipients(db_path: Path, profile_id: str) -> list[str]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT email FROM profile_recipients WHERE profile_id=? AND active=1 ORDER BY email",
            (profile_id,),
        ).fetchall()
    return [r[0] for r in rows]


def list_profiles(db_path: Path) -> list[str]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        return [r[0] for r in con.execute("SELECT profile_id FROM profiles ORDER BY profile_id")]


def next_since(db_path: Path, profile_id: str, source: str = "arxiv",
               default_lookback_days: int = 7) -> datetime:
    """다음 검색이 봐야 할 시작 시각. 프로필의 첫 실행이면 과거 N일부터
    (설계 문서 §3 기준, 이전 실행 이력이 없어 delta의 출발점을 정할 방법이
    없다 — 무한정 과거로 갈 수 없으니 상한을 둔다, 이 프로젝트의 "상한 있는
    예외 처리" 원칙과 같은 결)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT status, window_from, window_to FROM search_runs "
            "WHERE profile_id=? AND source=? ORDER BY started_at DESC LIMIT 1",
            (profile_id, source),
        ).fetchone()
    if not row:
        return datetime.now(timezone.utc) - timedelta(days=default_lookback_days)
    field = "window_to" if row["status"] == "done" else "window_from"
    return datetime.fromisoformat(row[field])


def record_run(
    db_path: Path, profile_id: str, source: str, query: str,
    window_from: datetime, window_to: datetime, status: str,
    retrieved_count: int, error_detail: str | None = None,
    started_at: str | None = None,
) -> str:
    init_db(db_path)
    run_id = uuid.uuid4().hex[:12]
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO search_runs (run_id, profile_id, source, query, window_from, "
            "window_to, status, retrieved_count, error_detail, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, profile_id, source, query, window_from.isoformat(), window_to.isoformat(),
             status, retrieved_count, error_detail, started_at or _now(), _now()),
        )
    return run_id
