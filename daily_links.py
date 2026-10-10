"""⑨ 이번 주 검색 기준 변화·정찰 추적과 오늘 논문의 연결 — 결정적 집계, LLM 미사용(2026-10-10).

외부 검토(사용자 전달, 2026-10-10): 기능은 이어져 있는데 메일에서 의미가 끊긴다 — 월요일에 정찰이 연구축을 찾고 주간 관리가 키워드를 더해도,
화요일 메일의 독자는 "오늘 이 논문이 그 변화와 관계가 있는가"를 스스로 이어 붙여야 했다.

여기서 **실제로 겹친 것만** 센다: 최근 7일 안에 더해진 검색어가 오늘 핵심 논문의 적중 키워드에 있는가, 정찰이 추적 중인 연구축 용어가 오늘 우리 검색
후보의 제목·초록에 있는가. 겹침이 없으면 아무것도 싣지 않는다(억지 연결 금지).

동향 서술 모델에는 넣지 않는다 — 넣으면 월요일의 변화가 매일 서술에 다시 들어가 근거 없이 "흐름이 커진다"로 굳는다(같은 검토의 자기 강화 지적).
그래서 문장도 Python 이 정해진 틀로 쓰고, 인과(키워드 덕분에 찾았다)는 주장하지 않는다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ADDED_WINDOW_DAYS = 7
_ORIGIN = {"agent": "주간 관리", "feedback": "반응", "user": "사용자", "rollback": "되돌리기"}


def _ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def added_keywords(db: Path, profile_id: str, now: datetime) -> list[dict]:
    """최근 7일 안에 더해진 검색어(핵심어·검색 시드) — [{keyword, origin}]. 그 뒤 다시 지워졌으면 뺀다."""
    since = (now - timedelta(days=ADDED_WINDOW_DAYS)).isoformat()
    with _ro(db) as con:
        rows = con.execute("SELECT keyword, kind, change, actor_origin FROM profile_keyword_events WHERE profile_id=? "
                           "AND julianday(created_at)>julianday(?) AND julianday(created_at)<=julianday(?) "
                           "AND kind IN ('core','s2_seed') ORDER BY julianday(created_at), event_id",
                           (profile_id, since, now.isoformat())).fetchall()
    # 종류까지 키로 둔다 — 같은 문자열의 검색 시드 삭제가 핵심어 추가를 지우면 안 된다(Codex 독립 검토 P2).
    state: dict[tuple[str, str], dict] = {}
    for keyword, kind, change, origin in rows:
        key = (keyword.lower(), kind)
        if change == "added":
            state[key] = {"keyword": keyword, "kind": kind, "origin": _ORIGIN.get(origin, origin or "")}
        elif change == "removed":
            state.pop(key, None)
    return list(state.values())


def collect(db: Path, profile_id: str, scan_id: str | None, papers: list[dict], now: datetime,
            watched: list[str] | None = None) -> dict | None:
    """{"added": [{keyword, origin, cards}], "watched": [{term, candidates, cards}]} — 겹침이 하나라도 있을 때만, 아니면 None.
    cards = 오늘 핵심 논문 중 그 키워드에 적중한(added) / 그 용어가 제목·초록에 있는(watched) 편수. candidates = 이번 스캔 관측 후보 중 용어가 있는 편수.
    어떤 실패도 None — 메일은 이 절 없이 나간다(규칙 6)."""
    import agent_maintenance as am
    try:
        hits = [{k.lower() for k in ((p.get("_score") or {}).get("core_hits") or [])} for p in papers]
        card_texts = [f"{p.get('title') or ''}. {p.get('abstract') or ''}" for p in papers]
        added = []
        for a in added_keywords(db, profile_id, now):
            # 핵심어는 채점기가 적어 둔 적중으로, 검색 시드는 카드 제목·초록에 그 말이 있는지로 센다(시드는 채점 적중에 안 남는다).
            if a["kind"] == "core":
                n = sum(a["keyword"].lower() in h for h in hits)
            else:
                n = sum(am._in_text(a["keyword"], t) for t in card_texts)
            if n:
                added.append({"keyword": a["keyword"], "origin": a["origin"], "cards": n})
        out_watch = []
        if watched and scan_id:
            with _ro(db) as con:
                rows = con.execute("SELECT o.title, COALESCE(o.abstract, c.abstract, '') FROM candidate_observations o "
                                   "LEFT JOIN search_candidates c ON c.profile_id=o.profile_id AND c.paper_key=o.paper_key "
                                   "WHERE o.scan_id=? AND o.profile_id=?", (scan_id, profile_id)).fetchall()
            texts = [f"{t or ''}. {a or ''}" for t, a in rows]
            for term in watched:
                n = sum(am._in_text(term, t) for t in texts)
                if n:
                    out_watch.append({"term": term, "candidates": n, "cards": sum(am._in_text(term, t) for t in card_texts)})
        return {"added": added, "watched": out_watch} if added or out_watch else None
    except Exception:  # noqa: BLE001 — 부가 절이다
        return None
