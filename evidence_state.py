"""⑧⑨ 배달 뒤 근거 상태의 변화 — 재요약 없이 관측하고 같은 메일로 알린다.

M9(2026-09-10): 표시 문구가 아니라 저장된 상태를 비교한다. 스냅샷은
수신자별 SMTP 전송 성공 뒤에만 남긴다. 별도 지휘자·Docker 실행 경로는 없다.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import retraction
import research_profile as rp
import storage

# 무료 한도가 아니라 초기 운영 정책이다. 효과·최적 주기는 미실측이다.
LOOKBACK_DAYS = 30
RECHECK_DAYS = 7
RECHECK_LIMIT = 3
RECHECK_SECONDS = 60
NOTICE_LIMIT = 10
REPRO_REVIEW_DAYS = 14


def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다(§8-98)."""
    con.execute("CREATE TABLE IF NOT EXISTS evidence_notifications ("
                "id INTEGER PRIMARY KEY, delivery_id TEXT NOT NULL, profile_id TEXT NOT NULL, "
                "recipient TEXT NOT NULL, paper_key TEXT NOT NULL, arxiv_id TEXT, title TEXT, "
                "state_json TEXT NOT NULL, sent_at TEXT NOT NULL, "
                "UNIQUE(delivery_id,recipient,paper_key))")
    con.execute("CREATE INDEX IF NOT EXISTS idx_evidence_delivered ON "
                "evidence_notifications(profile_id,recipient,paper_key,id)")
    con.execute("CREATE TABLE IF NOT EXISTS evidence_rechecks ("
                "arxiv_id TEXT PRIMARY KEY,last_attempt TEXT NOT NULL,last_result INTEGER)")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "evidence_state")
def _time(value: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value or "")
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _doi(paper: dict) -> str | None:
    if paper.get("doi"):
        return paper["doi"]
    match = re.search(r"10\.\d{4,9}/[^\s]+", unquote(paper.get("source") or ""))
    return match.group(0) if match else retraction.arxiv_doi(paper.get("arxiv_id") or "")


def tracked_papers(db: Path, profile_id: str, now: datetime) -> list[dict]:
    """합성 ID는 DOI와 다르므로 출처의 DOI나 저장된 도착 ID로만 연결한다."""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        papers = [dict(r) for r in con.execute("SELECT * FROM papers")]
        shown = con.execute(
            "SELECT h.*, c.arxiv_id AS arrival_id, c.doi FROM profile_shown h "
            "LEFT JOIN search_candidates c ON c.profile_id=h.profile_id AND c.paper_key=h.paper_key "
            "WHERE h.profile_id=? AND julianday(h.shown_at)>=julianday(?) ORDER BY h.shown_at,h.paper_key",
            (profile_id, (now - timedelta(days=LOOKBACK_DAYS)).isoformat())).fetchall()
    by_id = {p["arxiv_id"]: p for p in papers}
    by_key: dict[str, list[dict]] = {}
    for p in papers:
        key = rp.paper_key({**p, "doi": _doi(p)})
        by_key.setdefault(key, []).append(p)
    out = []
    for row in shown:
        found = by_id.get(row["paper_key"]) or by_id.get(row["arrival_id"])
        if found is None:
            matches = by_key.get(row["paper_key"], [])
            found = matches[0] if len(matches) == 1 else None
        if found is not None:
            out.append({**found, "paper_key": row["paper_key"], "shown_at": row["shown_at"],
                        "doi": row["doi"] or _doi(found)})
    return out


async def recheck_retractions(db: Path, papers: list[dict], client,
                              api_key: str | None, mailto: str | None = None,
                              now: datetime | None = None) -> dict:
    """0·미확정도 재조회한다. 조회 실패는 이전 상태를 지우지 않는다."""
    now = now or datetime.now(timezone.utc)
    if client is None or not api_key:
        return {"checked": 0, "resolved": 0, "status": "조회 설정 없음"}
    init_db(db)
    with sqlite3.connect(db) as con:
        attempts = {r[0]: (r[1], r[2]) for r in con.execute("SELECT * FROM evidence_rechecks")}
    due = []
    for paper in papers:
        if paper.get("is_retracted") == 1 or not _doi(paper):
            continue
        previous = attempts.get(paper["arxiv_id"])
        if previous:
            last = _time(previous[0])
            cooldown = 1 if previous[1] is None else RECHECK_DAYS
            if last and now - last < timedelta(days=cooldown):
                continue
        due.append(paper)
    due.sort(key=lambda p: (attempts.get(p["arxiv_id"], ("", None))[0], p["arxiv_id"]))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + RECHECK_SECONDS
    checked = resolved = 0
    for paper in due[:RECHECK_LIMIT]:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        aid = paper["arxiv_id"]
        async def query() -> int | None:
            flag = await retraction.openalex_is_retracted(client, _doi(paper), api_key)
            updates = None
            if flag:
                try:
                    updates = await retraction.crossref_update_types(client, _doi(paper), mailto)
                except Exception:
                    # 교차확인이 실패해도 OpenAlex의 신호를 정상으로 낮추지 않는다.
                    pass
            return retraction.classify(flag, updates)
        try:
            status = await asyncio.wait_for(query(), timeout=remaining)
        except Exception:
            status = None
        checked += 1
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO evidence_rechecks VALUES (?,?,?) ON CONFLICT(arxiv_id) "
                        "DO UPDATE SET last_attempt=excluded.last_attempt,last_result=excluded.last_result",
                        (aid, now.isoformat(), status))
            if status is not None:
                con.execute("UPDATE papers SET is_retracted=? WHERE arxiv_id=?", (status, aid))
                resolved += 1
    return {"checked": checked, "resolved": resolved, "status": "관측 완료"}


def capture(db: Path, paper: dict, now: datetime) -> dict:
    """다섯 축의 원시 값을 남기되 v1의 변화 판정은 철회·재현만 쓴다."""
    aid = paper.get("arxiv_id") or ""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        p = con.execute("SELECT is_retracted,injection_suspect FROM papers WHERE arxiv_id=?", (aid,)).fetchone()
        summary = con.execute("SELECT numbers_total,numbers_matched,coverage_ratio,coverage_kind "
                              "FROM summaries WHERE arxiv_id=?", (aid,)).fetchone()
        repros = con.execute("SELECT success,stage,fail_detail,exit_code FROM repro_results "
                            "WHERE arxiv_id=? ORDER BY attempt", (aid,)).fetchall()
    rank = {"clone": 0, "no_target": 1, "build": 2, "install_only": 3, "run": 4}
    successes = [r for r in repros if r["success"]]
    best = (successes[0] if successes else max(repros, key=lambda r: rank.get(r["stage"], -1))) if repros else None
    repro = dict(best) if best else {"success": None, "stage": None, "fail_detail": None, "exit_code": None}
    marker = storage.REPRO_DIR / f"{aid.replace('/', '_')}.running"
    repro["marker_present"] = bool(aid and marker.exists())
    shown_at = _time(paper.get("shown_at"))
    repro["review_due"] = bool(shown_at and now - shown_at >= timedelta(days=REPRO_REVIEW_DAYS)
                               and not repro["success"] and not repro["marker_present"]
                               and repro["stage"] in ("clone", "no_target"))
    state = {"version": 1, "retraction": p["is_retracted"] if p else None,
             "repro": repro, "injection": p["injection_suspect"] if p else None,
             "verification": {"total": summary["numbers_total"], "matched": summary["numbers_matched"]} if summary else None,
             "coverage": {"ratio": summary["coverage_ratio"], "kind": summary["coverage_kind"]} if summary else None}
    return {"paper_key": paper.get("paper_key") or rp.paper_key(paper), "arxiv_id": aid,
            "title": paper.get("title") or "(제목 없음)", "doi": _doi(paper),
            "state": state, "observed_at": now.isoformat()}


def changed_axes(before: dict, after: dict) -> list[str]:
    return [axis for axis in ("retraction", "repro") if before.get(axis) != after.get(axis)]


def state_label(state: dict, axis: str) -> str:
    if axis == "retraction":
        return {None: "철회 여부 미확인", 0: "조회 시 철회 신호 없음", 1: "철회 교차확인", 2: "정정·우려 등 신호 / 철회 미확정"}.get(state.get(axis), "철회 상태 미기록")
    repro = state.get("repro") or {}
    if repro.get("marker_present"):
        return "재현 실행 마커 있음(프로세스 생존 미확인)"
    if repro.get("success"):
        return f"재현 성공 기록 · 종료코드 {repro.get('exit_code')} (논문 결과 재현 미확인)"
    if repro.get("stage"):
        return f"재현 {repro['stage']} 단계 · {repro.get('fail_detail') or '상세 미기록'}"
    return "재현 기록 없음"


def pending_updates(db: Path, profile_id: str, recipient: str, states: list[dict],
                    content_keys: set[str]) -> list[dict]:
    """마지막으로 실제 전송한 상태와 비교한다. 생성·미리보기는 기준을 전진시키지 않는다."""
    init_db(db)
    out = []
    with sqlite3.connect(db) as con:
        for item in states:
            key = item["paper_key"]
            if key in content_keys:
                continue  # 상세 목록에서 이미 보여주는 새 논문을 중복 안내하지 않는다.
            row = con.execute("SELECT state_json FROM evidence_notifications WHERE profile_id=? "
                              "AND recipient=? AND paper_key=? ORDER BY id DESC LIMIT 1",
                              (profile_id, recipient, key)).fetchone()
            before = json.loads(row[0]) if row else None
            axes = changed_axes(before, item["state"]) if before else ["retraction", "repro"]
            if axes:
                out.append({**item, "before": before, "axes": axes})
    # 확정 철회와 변경 통지를 초기 기준 등록보다 먼저 보낸다.
    out.sort(key=lambda item: (item["state"]["retraction"] != 1,
                               item["before"] is None, item["paper_key"]))
    return out[:NOTICE_LIMIT]


def acknowledge(db: Path, profile_id: str, recipient: str, states: list[dict],
                delivery_id: str | None = None) -> None:
    """SMTP 전송 성공 뒤 호출한다. 사용자 수신함 도착이나 exactly-once를 뜻하지 않는다."""
    init_db(db)
    delivery_id = delivery_id or str(uuid.uuid4())
    with sqlite3.connect(db) as con:
        con.executemany("INSERT OR IGNORE INTO evidence_notifications "
                        "(delivery_id,profile_id,recipient,paper_key,arxiv_id,title,state_json,sent_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        [(delivery_id, profile_id, recipient, i["paper_key"], i["arxiv_id"], i["title"],
                          json.dumps(i["state"], ensure_ascii=False, sort_keys=True), rp._now()) for i in states])


async def prepare(db: Path, profile_id: str, result: dict, client,
                  api_key: str | None, mailto: str | None = None) -> None:
    """기존 scan_and_digest가 호출하는 부가 관측이다. 재요약·재현을 실행하지 않는다."""
    init_db(db)
    now = datetime.now(timezone.utc)
    # 사용자에게 필요한 것은 오늘 읽는 논문의 상태다. 과거 배달 논문을
    # 이 목록에 섞으면 첫 기준 등록까지 변경 소식으로 메일 앞을 차지한다.
    items = [{**p, "paper_key": rp.paper_key(p)} for p in result.get("papers") or []]
    # 초록만 있는 항목은 저장 논문 ID가 없을 수 있다. 저장된 논문의 실제
    # 철회 판정을 가져와 이미 확정한 철회를 다시 정상으로 낮추지 않는다.
    recheck_items = []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        for item in items:
            stored = con.execute("SELECT source,is_retracted FROM papers WHERE arxiv_id=?",
                                 (item.get("arxiv_id"),)).fetchone()
            if stored:
                recheck_items.append({**item, **dict(stored)})
    result["state_recheck"] = await recheck_retractions(db, recheck_items, client, api_key, mailto, now)
    states = [capture(db, p, now) for p in items]
    result["_evidence_states"] = states
    by_key = {i["paper_key"]: i["state"] for i in states}
    for p in result.get("papers") or []:
        p["_delivered_state"] = by_key[rp.paper_key(p)]
    result.pop("state_updates", None)
