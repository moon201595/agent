"""⑧ 축적 — 프로필 건강 지표: "적용 후 악화"를 라벨 없이 정의한다.

⑪단계(2026-09-12, PROGRESS §8-94). 자동 적용(auto_apply)을 열려면 되돌릴 근거가
있어야 한다 — 독립 정답 집합이 없으므로 **스캔별 관측(candidate_observations)에서
결정적으로 셀 수 있는 대리 지표**로 정의한다. LLM 을 쓰지 않는다(규칙 7).

지표는 스캔 하나마다 계산하고, 창(window)으로 모아 **적용 전 기준선**과 **적용 후
최근**을 비교한다. 악화 규칙(DEFAULT_HEALTH_RULES)은 profile_impact.DEFAULT_APPLY_RULES
와 같은 이유로 전부 None 으로 시작한다 — 기준선 없이 정한 숫자는 근거가 없다.
None 이면 판정은 "unconfigured" 다. 관측이 없는 기간은 0 이 아니라 미측정(None)이다.

anchor(사람이 넣은 키워드)와 auto(제안기가 넣은 키워드)의 구분이 지표의 축이다.
profile_keywords 에 provenance 컬럼이 아직 없으므로(⑨ 때 붙인다) **가장 이른 스캔의
프로필 스냅샷 + origin='user' revision 의 core** 를 anchor 로 본다. 지금은 auto 가
비어 있어 auto 계열 지표는 None 이다 — 그것이 정확한 현재 상태다.

외부 검토(2026-09-11)가 지적한 것: "악화의 정의가 없으면 auto-apply 를 열면 안 된다."
이 모듈이 그 정의다. astra 판정은 못 받았다(Codex 한도, 9/15 이후 재요청).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import profile_impact
import profile_scoring
import research_profile

HEALTH_VERSION = "health-v1"

# 전부 None = 미설정. 기준선(최소 MIN_BASELINE_SCANS 스캔)을 관측한 뒤 숫자를 넣는다.
# 값은 **비율의 절대 하한/상한**이다 — "중앙값 − δ" 는 기준선이 이미 나쁠 때
# 나쁜 상태를 정상으로 굳히므로 절대값을 기본으로 두고, δ 는 보조로만 쓴다.
DEFAULT_HEALTH_RULES: dict = {
    "min_anchor_share_topk": None,       # H1 상위 K 중 anchor 적중 비율 하한
    "max_exclude_collision_auto": None,  # H4 auto 적중 후보의 제외어 충돌 비율 상한
    "min_top_tier_share_topk": None,     # H5 상위 K 중 최상위 계층 비율 하한
    "min_topk_retention": None,          # H7 직전 프로필 대비 상위 K 유지 비율 하한
    "min_freshness_topk": None,          # H9 상위 K 중 최신일 논문 비율 하한
    "max_drop_from_baseline": None,      # 보조: 기준선 중앙값 대비 허용 하락 폭(비율)
}
MIN_BASELINE_SCANS = 14   # E2 의 "기준선 2주"와 같은 수

UNCONFIGURED = "unconfigured"
INSUFFICIENT_BASELINE = "insufficient_baseline"
INSUFFICIENT_RECENT = "insufficient_recent"
OK = "ok"
DETERIORATED = "deteriorated"


# ── anchor / auto ─────────────────────────────────────────────────────────
def anchor_keywords(db: Path, profile_id: str) -> tuple[set[str], str]:
    """returns (anchor core 키워드 집합, 근거).

    근거 우선순위: origin='user' revision 스냅샷의 core ∪ 가장 이른 scan_runs 스냅샷의
    core_topics. 둘 다 없으면 현재 프로필 전부(basis='current_all') — 자동 키워드가
    한 번도 안 들어간 프로필이므로 맞다. provenance 컬럼이 생기면 그쪽으로 옮긴다.
    """
    anchors: set[str] = set()
    basis: list[str] = []
    with sqlite3.connect(db) as con:
        for (snap,) in con.execute(
                "SELECT snapshot FROM profile_revisions WHERE profile_id=? AND origin='user'",
                (profile_id,)):
            kws = json.loads(snap).get("keywords") or []
            anchors.update(k for k, kind, _w in kws if kind == "core")
            basis.append("user_revision")
        first = con.execute(
            "SELECT profile_snapshot FROM scan_runs WHERE profile_id=? ORDER BY started_at LIMIT 1",
            (profile_id,)).fetchone()
        if first:
            anchors.update(json.loads(first[0]).get("core_topics") or [])
            basis.append("first_scan_snapshot")
    if not anchors:
        profile = research_profile.get_profile(db, profile_id)
        return set(profile.get("core_topics") or []), "current_all"
    return {a.lower() for a in anchors}, "+".join(sorted(set(basis)))


# ── 스캔 하나 ─────────────────────────────────────────────────────────────
def _scan_rows(con: sqlite3.Connection, scan_id: str) -> tuple[dict | None, list[dict]]:
    con.row_factory = sqlite3.Row
    run = con.execute("SELECT * FROM scan_runs WHERE scan_id=?", (scan_id,)).fetchone()
    if not run:
        return None, []
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM candidate_observations WHERE scan_id=? ORDER BY paper_key", (scan_id,))]
    return dict(run), rows


def _hits(paper: dict, profile: dict) -> tuple[set[str], bool]:
    """(core 적중 집합, 제외어 적중 여부). score_paper 는 제외되면 core_hits 를 비우므로
    제외어 없는 프로필로 한 번 더 채점해 적중을 따로 얻는다 — 가드(다의어)는 그대로 탄다."""
    full = profile_scoring.score_paper(paper, profile)
    hits = {h.lower() for h in profile_scoring.score_paper(paper, {**profile, "exclude": []})["core_hits"]}
    return hits, bool(full.get("excluded"))


def _day(published: str | None) -> int | None:
    ordinal, precision = profile_scoring.publication_day(published)
    return ordinal if precision == "day" else None


def scan_metrics(db: Path, scan_id: str, prev_profile: dict | None = None) -> dict | None:
    """스캔 하나의 지표. scan_runs 의 **당시 프로필 스냅샷**으로 다시 채점한다 — 현재
    프로필로 채점하면 "당시 무엇이 위였나"가 아니라 "지금 기준으로는"이 된다.
    관측이 없으면 None. `prev_profile` 을 주면 H7(직전 프로필 대비 상위 K 유지)을 센다.
    """
    with sqlite3.connect(db) as con:
        run, rows = _scan_rows(con, scan_id)
        if not run or not rows:
            return None
        profile = json.loads(run["profile_snapshot"])
        profile["profile_id"] = run["profile_id"]
        k = int(profile.get("max_items") or 6)
        anchors, basis = anchor_keywords(db, run["profile_id"])
        core = {c.lower() for c in profile.get("core_topics") or []}
        auto = core - anchors

        papers: list[dict] = []
        for r in rows:
            abstract, _status = profile_impact._restore_abstract(con, r)
            paper = {"_paper_key": r["paper_key"], "title": r["title"] or "",
                     "abstract": abstract or "", "published": r["published"]}
            hits, excluded = _hits(paper, profile)
            papers.append({**paper, "hits": hits, "excluded": excluded,
                           "eligible": r["filter_reason"] is None,
                           "rank_pos": r["rank_pos"], "tier_rank": r["tier_rank"],
                           "day": _day(r["published"])})

    eligible = [p for p in papers if p["eligible"]]
    topk = [p for p in papers if p["rank_pos"] is not None and p["rank_pos"] <= k]
    auto_hit = [p for p in papers if p["hits"] & auto]
    newest = max((p["day"] for p in eligible if p["day"] is not None), default=None)
    scan_day = _day(run["started_at"][:10])

    def share(items, pred) -> float | None:
        return (sum(1 for p in items if pred(p)) / len(items)) if items else None

    delays = [scan_day - p["day"] for p in topk if p["day"] is not None and scan_day is not None]
    keyword_hits = {kw: sum(1 for p in papers if kw in p["hits"]) for kw in sorted(core)}

    out = {
        "scan_id": scan_id, "profile_id": run["profile_id"], "started_at": run["started_at"],
        "k": k, "observations": len(papers), "eligible": len(eligible), "topk": len(topk),
        "anchor_basis": basis, "anchors": len(anchors), "auto": sorted(auto),
        # H1·H2 드리프트
        "anchor_share_topk": share(topk, lambda p: bool(p["hits"] & anchors)),
        "anchor_share_eligible": share(eligible, lambda p: bool(p["hits"] & anchors)),
        # H4 노이즈 — auto 가 없으면 None(미측정), 있는데 적중 0이면 None(분모 없음)
        "exclude_collision_auto": share(auto_hit, lambda p: p["excluded"]) if auto else None,
        # H5 잠식
        "top_tier_share_topk": share(topk, lambda p: p["tier_rank"] == 0),
        # H8 이득 — anchor 로는 no_core_hit 였을 적격 편수
        "new_eligible_from_auto": (sum(1 for p in eligible if p["hits"] and not (p["hits"] & anchors))
                                   if auto else None),
        # H9 최신성 계약
        "freshness_topk": share(topk, lambda p: p["day"] is not None and p["day"] == newest)
                          if newest is not None else None,
        "mean_delay_days_topk": (sum(delays) / len(delays)) if delays else None,
        # H11 키워드별 생존 — 0 이 이어지는 auto 키워드는 감쇠 후보
        "keyword_hits": keyword_hits,
        # H7 직전 프로필 대비 유지
        "topk_retention_vs_prev": None,
    }
    if prev_profile is not None and topk:
        prev = {**prev_profile, "profile_id": run["profile_id"]}
        ranked = profile_scoring.score_and_rank(
            [{k_: p[k_] for k_ in ("_paper_key", "title", "abstract", "published")} for p in papers], prev)["papers"]
        prev_top = {p["_paper_key"] for p in ranked[:k]}
        out["topk_retention_vs_prev"] = len(prev_top & {p["_paper_key"] for p in topk}) / len(topk)
    return out


# ── 창 ────────────────────────────────────────────────────────────────────
def series(db: Path, profile_id: str, start: datetime, end: datetime) -> list[dict]:
    """기간 안 스캔들의 지표(시간순). 관측 없는 스캔은 건너뛴다(0 으로 안 채운다).
    H7 은 직전 revision 이 있을 때만 — 없으면 None 그대로(§8-94 3번)."""
    with sqlite3.connect(db) as con:
        ids = [r[0] for r in con.execute(
            "SELECT scan_id FROM scan_runs WHERE profile_id=? AND started_at >= ? AND started_at < ? "
            "ORDER BY started_at", (profile_id, start.isoformat(), end.isoformat()))]
    return [m for m in (scan_metrics(db, sid) for sid in ids) if m]


_COMPARED = ("anchor_share_topk", "exclude_collision_auto", "top_tier_share_topk",
             "topk_retention_vs_prev", "freshness_topk")


def _median(values: list) -> float | None:
    xs = [v for v in values if v is not None]
    return median(xs) if xs else None


def assess(baseline: list[dict], recent: list[dict], rules: dict | None = None) -> dict:
    """악화 판정. 규칙이 하나도 안 정해졌으면 unconfigured — 판정하지 않는다.
    기준선·최근 창이 짧으면 판정하지 않는다. 걸린 사유는 전부 남긴다."""
    rules = {**DEFAULT_HEALTH_RULES, **(rules or {})}
    base_med = {m: _median([s.get(m) for s in baseline]) for m in _COMPARED}
    rec_med = {m: _median([s.get(m) for s in recent]) for m in _COMPARED}
    out = {"version": HEALTH_VERSION, "baseline_scans": len(baseline), "recent_scans": len(recent),
           "baseline": base_med, "recent": rec_med, "rules": rules, "reasons": []}
    if all(v is None for v in rules.values()):
        return {**out, "status": UNCONFIGURED}
    if len(baseline) < MIN_BASELINE_SCANS:
        return {**out, "status": INSUFFICIENT_BASELINE}
    if not recent:
        return {**out, "status": INSUFFICIENT_RECENT}

    checks = [("min_anchor_share_topk", "anchor_share_topk", "min"),
              ("max_exclude_collision_auto", "exclude_collision_auto", "max"),
              ("min_top_tier_share_topk", "top_tier_share_topk", "min"),
              ("min_topk_retention", "topk_retention_vs_prev", "min"),
              ("min_freshness_topk", "freshness_topk", "min")]
    reasons = []
    for rule, metric, kind in checks:
        limit = rules.get(rule)
        val = rec_med[metric]
        if limit is None or val is None:
            continue
        if (kind == "min" and val < limit) or (kind == "max" and val > limit):
            reasons.append(f"{metric}={val:.3f} {'<' if kind == 'min' else '>'} {rule}={limit}")
    drop = rules.get("max_drop_from_baseline")
    if drop is not None:
        for metric in ("anchor_share_topk", "top_tier_share_topk", "freshness_topk"):
            b, r = base_med[metric], rec_med[metric]
            if b is not None and r is not None and b - r > drop:
                reasons.append(f"{metric} fell {b:.3f}→{r:.3f} (> max_drop_from_baseline={drop})")
    return {**out, "status": DETERIORATED if reasons else OK, "reasons": reasons}


# ── 주간 리뷰용 ───────────────────────────────────────────────────────────
def _fmt(v: float | None) -> str:
    return "미측정" if v is None else f"{v:.2f}"


def format_health(rows: list[dict]) -> list[str]:
    """코드가 만든 절 — LLM 프롬프트에는 들어가지 않는다(규칙 4: 내부 집계)."""
    lines = ["■ 프로필 건강 지표 (스캔별 · 당시 프로필로 재채점 · 라벨 없는 대리 지표)"]
    if not rows:
        lines.append("  관측 이력 없음 — 미측정")
        return lines
    n = len(rows)
    med = {m: _median([r.get(m) for r in rows]) for m in
           ("anchor_share_topk", "anchor_share_eligible", "top_tier_share_topk",
            "freshness_topk", "mean_delay_days_topk", "exclude_collision_auto", "topk_retention_vs_prev")}
    lines.append(f"  스캔 {n}회 · anchor {rows[-1]['anchors']}개({rows[-1]['anchor_basis']})"
                 + (f" · auto {len(rows[-1]['auto'])}개" if rows[-1]["auto"] else " · auto 없음"))
    lines.append(f"  상위 K: anchor 적중 {_fmt(med['anchor_share_topk'])} · 최상위 계층 "
                 f"{_fmt(med['top_tier_share_topk'])} · 최신일 {_fmt(med['freshness_topk'])} · "
                 f"평균 지연 {_fmt(med['mean_delay_days_topk'])}일 (중앙값)")
    lines.append(f"  적격 풀: anchor 적중 {_fmt(med['anchor_share_eligible'])}")
    lines.append(f"  auto 계열: 제외어 충돌 {_fmt(med['exclude_collision_auto'])} · "
                 f"직전 프로필 대비 상위 K 유지 {_fmt(med['topk_retention_vs_prev'])}")
    dead = [kw for kw, c in rows[-1]["keyword_hits"].items() if c == 0]
    if dead:
        lines.append(f"  마지막 스캔에서 적중 0인 키워드 {len(dead)}개: {', '.join(dead[:8])}"
                     + (" …" if len(dead) > 8 else ""))
    lines.append(f"  악화 규칙: {'미설정 — 기준선 ' + str(MIN_BASELINE_SCANS) + '스캔 뒤 정한다' if all(v is None for v in DEFAULT_HEALTH_RULES.values()) else '설정됨'}")
    return lines
