"""⑧ 관측 성능 경계(Observed Frontier) — 논문 표에서 확인한 (벤치마크·지표·값)을 쌓고, 새 결과를 **다른 논문의** 관측값과 견준다.

왜(2026-09-30 사용자): 매일 외부를 처음부터 뒤지지 않고, 우리가 읽은 논문에서 이미 확인한 값과 먼저 견준다. 새 논문이 기존 관측 최고를 넘으면
그게 외부 조사의 강한 트리거다. **이름은 "관측 범위"다** — 우리가 읽은 논문 안에서의 최고이지 세계 SOTA 가 아니다. 메일도 그렇게 쓴다.

지키는 것:
- 표 구조(행×열)로 확인한 값만 넣는다(`performance_results.table_results`). 문장에서 읽은 숫자는 넣지 않는다.
- 같은 논문 표 안의 기준선 값은 "그 논문이 보고한 값"으로 따로 표시해 넣는다(`own=0`, `reported_in`). 비교할 때는 **자기 논문 표를 빼고** 본다 —
  그건 논문이 스스로 한 비교다.
- 방향(↑/↓)을 모르는 지표는 대소를 말하지 않는다. 키는 (벤치마크, 열 경로) 정규화 — "1-shot mIoU" 와 "5-shot mIoU" 는 다른 키다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다."""
    con.execute("CREATE TABLE IF NOT EXISTS observed_results ("
                " reported_in TEXT NOT NULL, locator TEXT NOT NULL,"
                " bench_key TEXT NOT NULL, metric_key TEXT NOT NULL, benchmark TEXT NOT NULL, metric TEXT NOT NULL,"
                " direction TEXT, value REAL NOT NULL, cell_text TEXT, model TEXT NOT NULL, own INTEGER NOT NULL,"
                " published TEXT, observed_at TEXT NOT NULL, PRIMARY KEY (reported_in, locator))")
    con.execute("CREATE INDEX IF NOT EXISTS idx_observed_results_key ON observed_results(bench_key, metric_key)")
    con.execute("CREATE TABLE IF NOT EXISTS frontier_external ("
                " paper_id TEXT NOT NULL, checked_on TEXT NOT NULL, bench_key TEXT NOT NULL, metric_key TEXT NOT NULL,"
                " status TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY (paper_id, checked_on))")
    con.execute("CREATE INDEX IF NOT EXISTS idx_frontier_external_key ON frontier_external(bench_key, metric_key, checked_on)")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "frontier_store")


def record(db: Path, paper_id: str, results: list[dict], published: str | None = None,
           now: datetime | None = None) -> int:
    """표 결과를 쌓는다. 같은 (논문, 셀)은 덮어쓴다. 돌려주는 값은 넣은 행 수."""
    if not results:
        return 0
    init_db(db)
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with sqlite3.connect(db) as con:
        # 다시 뽑으면 그 논문 행을 통째로 바꾼다 — 이전 추출에만 있던 셀이 남지 않게(Codex 검토 2026-09-30). 빈 결과(받기 실패)는 위에서 걸러
        # 기존 관측을 지우지 않는다.
        con.execute("DELETE FROM observed_results WHERE reported_in=?", (paper_id,))
        con.executemany(
            "INSERT OR REPLACE INTO observed_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(paper_id, r["locator"], r["bench_key"], r["metric_key"], r["benchmark"], r["metric"], r.get("direction"),
              float(r["value"]), r.get("text"), r.get("model") or "", 1 if r.get("own") else 0, published, ts) for r in results])
    return len(results)


def compare(db: Path, paper_id: str, result: dict) -> dict:
    """이 논문의 결과 하나를 **다른 논문들**의 관측값과 견준다.

    {status: 'no_direction' | 'first' | 'above_observed' | 'equal' | 'below_observed', best: {value, model, reported_in, own} | None, n: 관측 수}
    'above_observed' 는 "우리가 관측한 다른 논문 값보다 높다(낮은 게 좋은 지표면 낮다)" 이지 SOTA 가 아니다."""
    direction = result.get("direction")
    if direction not in ("higher", "lower"):
        return {"status": "no_direction", "best": None, "n": 0}
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute(
                "SELECT value, model, reported_in, own FROM observed_results "
                "WHERE bench_key=? AND metric_key=? AND reported_in<>? AND (direction=? OR direction IS NULL)",
                (result["bench_key"], result["metric_key"], paper_id, direction)).fetchall()
    except sqlite3.Error:
        rows = []
    if not rows:
        return {"status": "first", "best": None, "n": 0}
    import external_evidence
    scaled = [(external_evidence.comparable_values(result["value"], r[0], result.get("metric", "")), r) for r in rows]
    scaled = [(pair, r) for pair, r in scaled if pair is not None]     # 척도를 맞출 수 없는 관측은 견주지 않는다
    if not scaled:
        return {"status": "first", "best": None, "n": 0}
    pick = max if direction == "higher" else min
    (mine, theirs), (value, model, reported_in, own) = pick(scaled, key=lambda x: x[0][1])
    if mine == theirs:
        status = "equal"
    else:
        status = "above_observed" if (mine > theirs if direction == "higher" else mine < theirs) else "below_observed"
    return {"status": status,
            "best": {"value": value, "model": model, "reported_in": reported_in, "own": bool(own)}, "n": len(rows)}


def external_today(db: Path, day: str) -> list[dict]:
    """오늘 이미 한 외부 조사(프로필이 여럿이라 같은 날 여러 번 불린다 — 하루 상한은 여기서 센다)."""
    import json
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute("SELECT paper_id, status, result_json FROM frontier_external WHERE checked_on=?", (day,)).fetchall()
    except sqlite3.Error:
        return []
    return [{"paper_id": p, "status": s, **json.loads(j)} for p, s, j in rows]


def external_recent(db: Path, bench_key: str, metric_key: str, since_day: str) -> dict | None:
    """같은 (벤치마크, 지표)를 최근에 조사했으면 그 결과(경쟁 수치 재사용 — 사용자 결정: 같은 벤치마크는 캐시)."""
    import json
    try:
        with sqlite3.connect(db) as con:
            row = con.execute("SELECT paper_id, checked_on, status, result_json FROM frontier_external "
                              "WHERE bench_key=? AND metric_key=? AND checked_on>=? AND status='done' ORDER BY checked_on DESC LIMIT 1",
                              (bench_key, metric_key, since_day)).fetchone()
    except sqlite3.Error:
        return None
    return {"paper_id": row[0], "checked_on": row[1], "status": row[2], **json.loads(row[3])} if row else None


def save_external(db: Path, paper_id: str, day: str, bench_key: str, metric_key: str, status: str, result: dict) -> None:
    import json
    init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO frontier_external VALUES (?,?,?,?,?,?)",
                    (paper_id, day, bench_key, metric_key, status, json.dumps(result, ensure_ascii=False)))
