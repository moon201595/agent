"""⑧ 축적 — "지난 한 주 내 검색 기준이 실제로 어떻게 바뀌었나". LLM 을 쓰지 않는다.

2026-09-19 사용자 결정. 그전까지 월요일 메일에는 `주간 동향 리뷰`(운영 진단 5,471자)가 붙었는데,
그 안의 기간 비교는 `trend_report.window_movement` 가 매일 하는 계산과 겹쳤고 나머지는 운영 지표였다.
질문 자체를 갈랐다 —

    동향   : "이번 주 연구 분야에서 무슨 일이 있었나"   → trend_report / 일일 서술
    여기   : "그래서 내 에이전트가 무엇을 배워 검색 기준을 바꿨나"
    진단   : "검색·시드·프로필 상태가 정상인가"          → trend_report.build (화면·DB 전용)

**순변화는 이벤트를 되짚지 않고 스냅숏 두 개를 견준다.** 한 주 안에 user·feedback·agent revision 이
섞여 돌기 때문에(실측 2026-09-12~19: user 19 · feedback 3 · agent 1) 이벤트를 더하면 중간 경로가
결과처럼 보인다. `profile_revisions.snapshot` 은 그 시점 키워드 전체라 시작·끝만 보면 순변화가 정확하다.
이벤트(`profile_keyword_events`·`feedback_weight_runs`·`agent_runs`)는 **누가·왜** 를 설명하는 데만 쓴다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 자동으로 일어난 변경은 상세히, 사람이 직접 한 변경은 건수만 — 이 절의 주인공은 "시스템이 무엇을 배웠나"다.
AUTO_ORIGINS = ("feedback", "agent")


def _rows(db: Path, sql: str, args: tuple) -> list[sqlite3.Row]:
    try:
        with sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            return list(con.execute(sql, args))
    except sqlite3.OperationalError:
        return []          # 표가 아직 없는 설치 — 미측정이지 "변경 없음"이 아니다


def _snapshot_at(db: Path, profile_id: str, moment: str) -> dict[tuple[str, str], float] | None:
    """그 시각 **이전** 마지막 revision 의 키워드 표. {(키워드, 종류): 가중치}.

    키를 (키워드, 종류)로 잡는다 — 같은 말이 core 이면서 s2_seed 인 경우가 실제로 있다
    (2026-09-19 실측: `defect detection` 이 둘 다였다). 키워드만으로 잡으면 한쪽이 덮여
    "core 1.6" 이 "s2_seed 0.6→1.0" 으로 뒤바뀌어 보인다.""" 
    rows = _rows(db, "SELECT snapshot FROM profile_revisions WHERE profile_id=? AND created_at<? "
                     "ORDER BY revision DESC LIMIT 1", (profile_id, moment))
    if not rows:
        return None
    try:
        kws = (json.loads(rows[0]["snapshot"]) or {}).get("keywords") or []
    except (json.JSONDecodeError, TypeError):
        return None
    return {(k, kind): float(w) for k, kind, w in kws}


def _latest_snapshot(db: Path, profile_id: str) -> dict[tuple[str, str], float] | None:
    rows = _rows(db, "SELECT snapshot FROM profile_revisions WHERE profile_id=? "
                     "ORDER BY revision DESC LIMIT 1", (profile_id,))
    if not rows:
        return None
    try:
        kws = (json.loads(rows[0]["snapshot"]) or {}).get("keywords") or []
    except (json.JSONDecodeError, TypeError):
        return None
    return {(k, kind): float(w) for k, kind, w in kws}


def _attribution(db: Path, profile_id: str, start_iso: str, end_iso: str) -> dict[str, tuple[str, ...]]:
    """{키워드: 그 주에 건드린 주체들}. 한 키워드를 여러 주체가 움직인 주가 실제로 있다
    (2026-09-19 실측: `defect detection` 을 사용자·반응·에이전트가 모두 건드려 0.6→1.7). 마지막 하나만
    적으면 나머지 기여가 사라져 오해를 준다 — 전부 남기고 렌더러가 함께 보여 준다. 가중치 변경에는 `profile_keyword_events` 가 남지 않아(추가·삭제만 남는다)
    반응·에이전트 쪽 기록에서 따로 찾아야 한다. 어느 쪽에도 없으면 넣지 않는다 — 모르는 것은 모른다고 둔다."""
    who: dict[str, set[str]] = {}
    for row in _rows(db, "SELECT changes_json FROM feedback_weight_runs WHERE profile_id=? "
                         "AND created_at>=? AND created_at<?", (profile_id, start_iso, end_iso)):
        try:
            for change in json.loads(row["changes_json"] or "[]"):
                if change.get("keyword"):
                    who.setdefault(change["keyword"], set()).add("feedback")
        except json.JSONDecodeError:
            continue
    for row in _rows(db, "SELECT applied_json FROM agent_runs WHERE profile_id=? "
                         "AND started_at>=? AND started_at<?", (profile_id, start_iso, end_iso)):
        try:
            for action in json.loads(row["applied_json"] or "[]"):
                if action.get("term"):
                    who.setdefault(action["term"], set()).add("agent")
        except json.JSONDecodeError:
            continue
    for row in _rows(db, "SELECT keyword, actor_origin FROM profile_keyword_events WHERE profile_id=? "
                         "AND created_at>=? AND created_at<? ORDER BY created_at",
                     (profile_id, start_iso, end_iso)):
        if row["actor_origin"]:
            who.setdefault(row["keyword"], set()).add(row["actor_origin"])
    return {k: tuple(sorted(v)) for k, v in who.items()}


def collect(db: Path, profile_id: str, days: int = 7,
            now: datetime | None = None) -> dict | None:
    """지난 `days` 일 순변화. 바뀐 게 없으면 None — 호출부는 그때 절 자체를 넣지 않는다.

    "이번 주 변경 없음"을 매주 출력하는 것은 소음이다(`agent_maintenance.pending_report` 와 같은 철학).
    """
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_iso, end_iso = start.isoformat(), end.isoformat()

    before = _snapshot_at(db, profile_id, start_iso)
    after = _latest_snapshot(db, profile_id)
    if before is None or after is None:
        return None            # 비교할 스냅숏이 없다 — 0 으로 채우지 않는다

    who = _attribution(db, profile_id, start_iso, end_iso)

    weights, added, removed = [], [], []
    for (kw, kind), w_after in sorted(after.items()):
        if (kw, kind) in before:
            w_before = before[(kw, kind)]
            if abs(w_after - w_before) >= 0.005:      # 부동소수 흔들림은 변화가 아니다
                weights.append({"keyword": kw, "kind": kind, "before": round(w_before, 2),
                                "after": round(w_after, 2), "delta": round(w_after - w_before, 2),
                                "origins": who.get(kw, ())})
        else:
            added.append({"keyword": kw, "kind": kind, "weight": round(w_after, 2),
                          "origins": who.get(kw, ())})
    for (kw, kind), w_before in sorted(before.items()):
        if (kw, kind) not in after:
            removed.append({"keyword": kw, "kind": kind, "weight": round(w_before, 2),
                            "origins": who.get(kw, ())})

    # 누가 얼마나 바꿨나 — revision 단위. 사용자 변경은 건수만 쓰므로 여기서 센다.
    by_actor: dict[str, int] = {}
    for row in _rows(db, "SELECT origin, COUNT(*) n FROM profile_revisions WHERE profile_id=? "
                         "AND created_at>=? AND created_at<? GROUP BY origin",
                     (profile_id, start_iso, end_iso)):
        by_actor[row["origin"] or "unknown"] = row["n"]

    # 반응이 실제로 몇 건 쓰였나 — 가중치가 왜 움직였는지의 근거
    reactions = sum(int(r["reactions_used"] or 0) for r in
                    _rows(db, "SELECT reactions_used FROM feedback_weight_runs WHERE profile_id=? "
                              "AND created_at>=? AND created_at<?", (profile_id, start_iso, end_iso)))

    agent = _agent_summary(db, profile_id, start_iso, end_iso)

    if not (weights or added or removed or agent.get("applied") or agent.get("failed")):
        return None

    return {"window": (start_iso, end_iso), "days": days,
            "weights": weights, "added": added, "removed": removed,
            "by_actor": by_actor, "reactions_used": reactions, "agent": agent}


def _agent_summary(db: Path, profile_id: str, start_iso: str, end_iso: str) -> dict:
    """주간 관리 에이전트가 이번 창에서 한 일. 사유는 모델이 쓴 문장 그대로 옮기고 요약하지 않는다."""
    out: dict = {"applied": [], "failed": None, "impact": None}
    rows = _rows(db, "SELECT status, error, applied_json, impact_json, base_revision, new_revision "
                     "FROM agent_runs WHERE profile_id=? AND started_at>=? AND started_at<? "
                     "ORDER BY started_at", (profile_id, start_iso, end_iso))
    for row in rows:
        if row["status"] == "applied":
            try:
                for action in json.loads(row["applied_json"] or "[]"):
                    out["applied"].append({"op": action.get("op"), "term": action.get("term"),
                                           "weight": action.get("weight"),
                                           "reason": (action.get("reason") or "").strip()})
            except json.JSONDecodeError:
                pass
            out["revisions"] = (row["base_revision"], row["new_revision"])
            try:
                impact = json.loads(row["impact_json"] or "null")
            except json.JSONDecodeError:
                impact = None
            if isinstance(impact, dict) and impact.get("status") == "ok":
                out["impact"] = {"gained": impact.get("gained"), "lost": impact.get("lost"),
                                 "topk_changed": impact.get("topk_changed")}
        elif row["status"] and row["status"] not in ("applied", "skipped_no_signal"):
            # 건너뛴 주(반응·동향 근거 없음)는 실패가 아니다 — 싣지 않는다.
            out["failed"] = row["error"] or row["status"]
    return out
