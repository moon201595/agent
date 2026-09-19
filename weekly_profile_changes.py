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

from time_policy import KST

# 자동으로 일어난 변경은 상세히, 사람이 직접 한 변경은 건수만 — 이 절의 주인공은 "시스템이 무엇을 배웠나"다.
AUTO_ORIGINS = ("feedback", "agent")

# 에이전트 행동이 **어느 종류**를 건드리는지. `agent_maintenance.OPS` 와 짝이고, 그 목록이 늘면 여기도 늘려야
# 한다 — `test_every_agent_op_has_a_kind` 가 짝이 어긋나면 깨진다. 판정 근거는 `agent_maintenance.apply_actions`.
OP_KIND = {"add_keyword": "core", "set_weight": "core", "remove_keyword": "core",
           "add_seed": "s2_seed", "remove_seed": "s2_seed", "add_exclude": "exclude"}


def _key(keyword: str) -> str:
    """귀속을 맞출 때 쓰는 키. **이벤트 표는 소문자 정규화, 스냅숏은 표기 그대로**라서(2026-09-19 실측:
    이벤트 `llm agent` · 스냅숏 `LLM agent`) 그냥 맞추면 대문자가 섞인 키워드는 이벤트 경로에서 통째로
    빠진다 — `LLM agent`·`MVTec AD` 가 그랬다. 양쪽을 같은 모양으로 눌러서 맞춘다."""
    return " ".join(str(keyword).split()).lower()


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


def _attribution(db: Path, profile_id: str, start_iso: str, end_iso: str) -> dict[tuple[str, str], tuple[str, ...]]:
    """{(키워드, 종류): 그 기간에 건드린 주체들}. 한 키워드를 여러 주체가 움직인 주가 실제로 있다
    (2026-09-19 실측: `defect detection` 을 사용자·반응·에이전트가 모두 건드려 0.6→1.7). 마지막 하나만
    적으면 나머지 기여가 사라져 오해를 준다 — 전부 남기고 렌더러가 함께 보여 준다.

    **종류까지 키로 잡는다**(2026-09-19 지적). 같은 말이 core 이면서 s2_seed 인 경우가 있어
    (`defect detection`), 키워드만으로 잡으면 `core 는 반응이, s2_seed 는 에이전트가` 건드린 주를
    둘 다 "에이전트·반응"으로 적는다. 순변화 쪽에서 (키워드, 종류)로 가른 것이 여기서 도로 합쳐진다.

    종류는 자료원마다 다르게 안다 — 반응은 core 가중치만 만진다(`feedback_weights` 가 `kind == "core"`
    만 읽는다), 에이전트는 op 이 말해 준다(`OP_KIND`), 이벤트 표는 `kind` 컬럼을 갖고 있다.
    가중치 변경에는 이벤트가 남지 않아(추가·삭제만 남는다) 반응·에이전트 쪽 기록에서 따로 찾아야 한다.
    어느 쪽에도 없으면 넣지 않는다 — 모르는 것은 모른다고 둔다.

    `origins` 는 **그 기간에 이 항목을 건드린 주체들**이지 기여 분해가 아니다. 스냅숏 두 개는
    순이동만 안다 — "에이전트가 +0.9, 반응이 +0.2" 는 재지 않았고, 재지 않은 값은 쓰지 않는다(규칙 7)."""
    who: dict[tuple[str, str], set[str]] = {}
    for row in _rows(db, "SELECT changes_json FROM feedback_weight_runs WHERE profile_id=? "
                         "AND created_at>=? AND created_at<?", (profile_id, start_iso, end_iso)):
        try:
            for change in json.loads(row["changes_json"] or "[]"):
                if change.get("keyword"):
                    who.setdefault((_key(change["keyword"]), "core"), set()).add("feedback")
        except json.JSONDecodeError:
            continue
    for row in _rows(db, "SELECT applied_json FROM agent_runs WHERE profile_id=? "
                         "AND started_at>=? AND started_at<?", (profile_id, start_iso, end_iso)):
        try:
            for action in json.loads(row["applied_json"] or "[]"):
                kind = OP_KIND.get(action.get("op"))
                if action.get("term") and kind:
                    who.setdefault((_key(action["term"]), kind), set()).add("agent")
        except json.JSONDecodeError:
            continue
    for row in _rows(db, "SELECT keyword, kind, actor_origin FROM profile_keyword_events WHERE profile_id=? "
                         "AND created_at>=? AND created_at<? ORDER BY created_at",
                     (profile_id, start_iso, end_iso)):
        if row["actor_origin"]:
            who.setdefault((_key(row["keyword"]), row["kind"]), set()).add(row["actor_origin"])
    return {k: tuple(sorted(v)) for k, v in who.items()}


def _keywords_of(profile_like: dict) -> dict[tuple[str, str], float]:
    """`get_profile` 모양 → {(키워드, 종류): 가중치}. `scan_runs.profile_snapshot` 이 이 모양이다
    (`research_profile.profile_from_snapshot` 의 반대 방향). core 아닌 종류는 가중치를 안 갖고,
    revision 스냅숏이 그 자리에 1.0 을 적으므로 여기서도 1.0 으로 맞춘다 — 두 자료원이 같은 값을 내야
    한쪽을 baseline, 다른 쪽을 현재로 견줄 수 있다."""
    weights = profile_like.get("core_weights") or {}
    out = {(k, "core"): float(weights.get(k, 1.0)) for k in profile_like.get("core_topics") or []}
    for field, kind in (("target_domain", "target"), ("exclude", "exclude"), ("s2_seeds", "s2_seed")):
        for k in profile_like.get(field) or []:
            out[(k, kind)] = 1.0
    return out


def _previous_cycle(db: Path, profile_id: str, end: datetime,
                    days: int) -> tuple[str, dict[tuple[str, str], float] | None] | None:
    """지난번 이 보고가 나간 회차 — (스캔 시작 시각, 그 스캔이 실제 쓴 프로필). 기록이 없으면 None.

    기간의 뜻은 달력 `days` 일이 아니라 **"지난 보고 이후 ~ 지금"** 이다(2026-09-19 지적).
    주간 관리와 반응 반영은 스캔이 시작되기 **전**에 끝난다(`run_daily_scan.sh` 월요일 블록 →
    `scan_and_digest` 0번 절 → `scan_profile` 이 `scan_runs` 를 남긴다). 그래서 지난 회차가 경계다.

    고정 창이 왜 모자란가: PC 가 자면 그날 스캔이 늦게 뜬다(2026-09-18 실측 — 05:00 을 건너뛰고
    09:45 에 떴다). 지난주가 늦으면 `end - 7일` 창이 지난주 변경을 그대로 삼키고, 지난주가 이르면
    그 사이 변경이 **어느 주에도 안 실린 채** 사라진다. 그래서 `max()` 로 섞지 않고 지난 회차를 그대로 쓴다.

    baseline 을 그 회차의 `profile_snapshot` 에서 가져오는 이유: 그것이 **지난 월요일 메일이 실제로
    검색에 쓴 프로필**이다. revision 을 시각으로 되짚으면 `created_at` 과 `started_at` 의 동시각 경계를
    다시 따져야 하는데, 그 회차가 자기 스냅숏을 이미 들고 있으므로 그럴 이유가 없다. 같은 날 두 번 돌았으면
    마지막 회차가 실제로 나간 메일이다."""
    target = (end.astimezone(KST) - timedelta(days=days)).date()
    lo = datetime(target.year, target.month, target.day, tzinfo=KST)
    rows = _rows(db, "SELECT started_at, profile_snapshot FROM scan_runs WHERE profile_id=? "
                     "AND started_at>=? AND started_at<? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                 (profile_id, lo.astimezone(timezone.utc).isoformat(),
                  (lo + timedelta(days=1)).astimezone(timezone.utc).isoformat()))
    if not rows:
        return None
    try:
        snapshot = _keywords_of(json.loads(rows[0]["profile_snapshot"]) or {})
    except (json.JSONDecodeError, TypeError, AttributeError):
        snapshot = None            # 모양이 깨졌으면 revision 쪽으로 물러난다
    return rows[0]["started_at"], snapshot


def collect(db: Path, profile_id: str, days: int = 7,
            now: datetime | None = None) -> dict | None:
    """지난 보고 이후의 순변화. 바뀐 게 없으면 None — 호출부는 그때 절 자체를 넣지 않는다.

    기간은 **지난 회차 스캔부터 지금까지**이고(`_previous_cycle`), 그 기록이 없을 때만 `days` 일로
    물러난다. 이번 새벽 주간 관리가 방금 바꾼 것은 스캔보다 앞서 일어나므로 이번 보고에 들어오고,
    지난 보고에 실린 것은 다시 안 들어온다. 견주는 대상도 시각으로 되짚은 revision 이 아니라
    **지난 회차가 실제로 검색에 쓴 프로필**이다.

    "이번 주 변경 없음"을 매주 출력하는 것은 소음이다(`agent_maintenance.pending_report` 와 같은 철학).
    """
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:                 # 저장값은 오프셋이 붙어 있다 — 섞이면 문자열 비교가 깨진다
        end = end.replace(tzinfo=timezone.utc)
    end_iso = end.isoformat()
    previous = _previous_cycle(db, profile_id, end, days)
    # 지난 회차가 있으면 **그 회차가 경계**다. 달력 `days` 일은 지난 회차 기록이 없을 때의 폴백이다.
    start_iso, before = previous if previous else ((end - timedelta(days=days)).isoformat(), None)
    if before is None:
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
                                "origins": who.get((_key(kw), kind), ())})
        else:
            added.append({"keyword": kw, "kind": kind, "weight": round(w_after, 2),
                          "origins": who.get((_key(kw), kind), ())})
    for (kw, kind), w_before in sorted(before.items()):
        if (kw, kind) not in after:
            removed.append({"keyword": kw, "kind": kind, "weight": round(w_before, 2),
                            "origins": who.get((_key(kw), kind), ())})

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
