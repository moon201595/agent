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

**재현 모드**(2026-09-12 두 번째 검토, §8-95). 적중은 관측 시점에 저장된 값
(candidate_observations.core_hits 등)을 쓴다 = `as_observed`. 그 컬럼이 없는 옛 관측
(9/11 스캔)만 현재 코드로 다시 세고 `recomputed` 로 표시한다 — 두 모드를 한 창에서
섞어 판정하지 않는다. 처음 구현은 순위는 당시 값, 적중은 현재 코드로 세어 어느 쪽도
아니었다.
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
    "min_new_eligible_from_auto": None,  # H8 auto 덕에 새로 적격이 된 편수 하한 — 0 이면 변경이 헛것
    "max_drop_from_baseline": None,      # 보조: 기준선 중앙값 대비 허용 하락 폭(비율)
}
MIN_BASELINE_SCANS = 14   # E2 의 "기준선 2주"와 같은 수 —
MIN_BASELINE_DAYS = 14    # 수동 스캔이 섞이면 14회가 14일보다 훨씬 빨리 찬다. 둘 다 본다.
MIN_RECENT_SCANS = 3      # 최근 창도 표본이 필요하다 — 1회면 중앙값이 그 값이라 한 번 튄 것에 반응한다.

UNCONFIGURED = "unconfigured"
INSUFFICIENT_BASELINE = "insufficient_baseline"
INSUFFICIENT_RECENT = "insufficient_recent"
UNMEASURED_REQUIRED = "unmeasured_required"   # 규칙이 요구하는 지표가 미측정 — fail-closed
MIXED_MODES = "mixed_modes"                   # as_observed 와 recomputed 가 한 창에 섞임
OK = "ok"
DETERIORATED = "deteriorated"


# ── anchor / auto ─────────────────────────────────────────────────────────
def anchor_keywords(db: Path, profile_id: str) -> tuple[set[str], str]:
    """returns (anchor core 키워드 집합, 근거). anchor = 처음 나타난 이력의 origin 이 'user'
    인 키워드(research_profile.keyword_provenance — 시점 고정). 이력이 전혀 없으면 현재
    프로필 전부(basis='current_all')."""
    prov = research_profile.keyword_provenance(db, profile_id)
    if not prov:
        profile = research_profile.get_profile(db, profile_id)
        return {k.lower() for k in profile.get("core_topics") or []}, "current_all"
    return {k for k, v in prov.items() if v["origin"] == "user"}, "provenance"


def _profile_sha(profile: dict) -> str:
    return profile_impact.profile_hash({k: profile.get(k) for k in
                                        ("core_topics", "core_weights", "target_domain", "exclude", "s2_seeds", "max_items")})


def freeze_scan(db: Path, scan_id: str, profile_id: str, obs: list[dict], profile: dict) -> dict:
    """스캔이 끝난 자리에서 부른다(run_profile_scan). anchor/auto 와 H7 반사실을 계산해
    scan_health 에 얼린다. obs 는 record_observations 에 넘긴 그 행들(rank_pos·_hits 포함)."""
    k = int(profile.get("max_items") or 6)
    prov = research_profile.keyword_provenance(db, profile_id)
    core = {c.lower() for c in profile.get("core_topics") or []}
    anchors = {kw for kw in core if prov.get(kw, {"origin": "user"})["origin"] == "user"}
    topk = [research_profile.paper_key(p) for p in obs
            if p.get("rank_pos") is not None and p["rank_pos"] <= k]
    prev = previous_profile(db, profile_id, scan_id)
    prev_sha, prev_topk, retention = None, None, None
    if prev is not None and topk:
        prev_sha = _profile_sha(prev)
        ranked = profile_scoring.score_and_rank(
            [{"_paper_key": research_profile.paper_key(p), "title": p.get("title") or "",
              "abstract": p.get("abstract") or "", "published": p.get("published")} for p in obs],
            {**prev, "profile_id": profile_id})["papers"]
        prev_topk = [p["_paper_key"] for p in ranked[:k]]
        retention = len(set(prev_topk) & set(topk)) / len(topk)
    research_profile.record_scan_health(
        db, scan_id, profile_id, anchor_terms=sorted(anchors), auto_terms=sorted(core - anchors),
        provenance={kw: prov[kw] for kw in sorted(core) if kw in prov}, topk=topk,
        prev_profile_sha=prev_sha, prev_topk=prev_topk, retention=retention)
    return {"anchors": sorted(anchors), "auto": sorted(core - anchors), "retention": retention}


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
        core = {c.lower() for c in profile.get("core_topics") or []}
        frozen = con.execute("SELECT * FROM scan_health WHERE scan_id=?", (scan_id,)).fetchone()
        if frozen:                       # 스캔 시점에 얼린 값 — 이력·코드가 바뀌어도 그대로
            anchors = set(json.loads(frozen["anchor_terms"])); basis = "frozen"
        else:                            # 얼리기 전 스캔(2026-09-12 이전) — 그 시점 이력으로 도출
            anchors, basis = anchor_keywords(db, run["profile_id"])
        auto = core - anchors

        papers: list[dict] = []
        modes: set[str] = set()
        for r in rows:
            abstract, _status = profile_impact._restore_abstract(con, r)
            paper = {"_paper_key": r["paper_key"], "title": r["title"] or "",
                     "abstract": abstract or "", "published": r["published"]}
            if r.get("core_hits") is not None:
                hits = {h.lower() for h in json.loads(r["core_hits"])}
                excluded = bool(json.loads(r["exclude_hits"] or "[]"))
                modes.add("as_observed")
            else:
                hits, excluded = _hits(paper, profile)
                modes.add("recomputed")
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

    # 재현 모드: 적중(관측 저장)과 anchor/H7(스캔 시점 얼림) 둘 다 있어야 as_observed.
    # 둘 다 없으면 recomputed, 하나만 있으면 partial — assess 는 한 창에 한 모드만 받는다.
    hits_mode = "mixed" if len(modes) > 1 else next(iter(modes))
    mode = ("as_observed" if hits_mode == "as_observed" and frozen
            else "recomputed" if hits_mode == "recomputed" and not frozen else "partial")
    out = {
        "scan_id": scan_id, "profile_id": run["profile_id"], "started_at": run["started_at"],
        "mode": mode, "hits_mode": hits_mode, "frozen": bool(frozen),
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
    if frozen:                           # H7 도 얼린 값 — 나중 채점 코드로 다시 세지 않는다
        out["topk_retention_vs_prev"] = frozen["retention"]
    elif prev_profile is not None and topk:
        prev = {**prev_profile, "profile_id": run["profile_id"]}
        ranked = profile_scoring.score_and_rank(
            [{k_: p[k_] for k_ in ("_paper_key", "title", "abstract", "published")} for p in papers], prev)["papers"]
        prev_top = {p["_paper_key"] for p in ranked[:k]}
        out["topk_retention_vs_prev"] = len(prev_top & {p["_paper_key"] for p in topk}) / len(topk)
    return out


# ── 창 ────────────────────────────────────────────────────────────────────
def previous_profile(db: Path, profile_id: str, scan_id: str) -> dict | None:
    """H7 의 기준 — 이 스캔보다 앞선 스캔 중 **프로필 스냅샷이 다른** 가장 최근 것.
    revision 표가 아니라 스캔 스냅샷을 쓴다: 그때 실제로 쓰인 프로필이 그것이고,
    revision 을 기록하기 전(2026-09-11 이전) 프로필도 다룰 수 있다. 앞선 스캔이 전부
    같은 프로필이면 None — 유지율은 프로필이 바뀐 뒤에만 뜻이 있다."""
    with sqlite3.connect(db) as con:
        cur = con.execute("SELECT started_at, profile_snapshot FROM scan_runs WHERE scan_id=?",
                          (scan_id,)).fetchone()
        if not cur:
            return None
        # started_at 은 마이크로초 해상도(begin_scan) — 순서를 rowid 에 맡기지 않는다.
        for (snap,) in con.execute(
                "SELECT profile_snapshot FROM scan_runs WHERE profile_id=? AND started_at < ? "
                "ORDER BY started_at DESC", (profile_id, cur[0])):
            if snap != cur[1]:
                return json.loads(snap)
    return None


def series(db: Path, profile_id: str, start: datetime, end: datetime) -> list[dict]:
    """기간 안 스캔들의 지표(시간순). 관측 없는 스캔은 건너뛴다(0 으로 안 채운다).
    H7 은 `previous_profile` 이 있을 때 센다 — 처음 구현은 여기서 prev_profile 을 안 넘겨
    운영 경로에서 늘 미측정이었다(외부 검토 2026-09-12)."""
    with sqlite3.connect(db) as con:
        ids = [r[0] for r in con.execute(
            "SELECT scan_id FROM scan_runs WHERE profile_id=? AND started_at >= ? AND started_at < ? "
            "ORDER BY started_at", (profile_id, start.isoformat(), end.isoformat()))]
    out = []
    for sid in ids:
        m = scan_metrics(db, sid, prev_profile=previous_profile(db, profile_id, sid))
        if m:
            out.append(m)
    return out


_COMPARED = ("anchor_share_topk", "exclude_collision_auto", "top_tier_share_topk",
             "topk_retention_vs_prev", "freshness_topk", "new_eligible_from_auto")


def _span_days(rows: list[dict]) -> float | None:
    ts = []
    for r in rows:
        v = r.get("started_at")
        if not v:
            return None
        ts.append(datetime.fromisoformat(v.replace("Z", "+00:00")))
    return (max(ts) - min(ts)).total_seconds() / 86400 if ts else None


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
    span = _span_days(baseline)
    out["baseline_days"] = span
    if len(baseline) < MIN_BASELINE_SCANS or span is None or span < MIN_BASELINE_DAYS:
        return {**out, "status": INSUFFICIENT_BASELINE}
    if len(recent) < MIN_RECENT_SCANS:
        return {**out, "status": INSUFFICIENT_RECENT}
    modes = {r.get("mode") for r in baseline + recent} - {None}
    if len(modes) > 1 or "partial" in modes:
        return {**out, "status": MIXED_MODES, "reasons": [f"modes={sorted(modes)}"]}

    checks = [("min_anchor_share_topk", "anchor_share_topk", "min"),
              ("max_exclude_collision_auto", "exclude_collision_auto", "max"),
              ("min_top_tier_share_topk", "top_tier_share_topk", "min"),
              ("min_topk_retention", "topk_retention_vs_prev", "min"),
              ("min_freshness_topk", "freshness_topk", "min"),
              ("min_new_eligible_from_auto", "new_eligible_from_auto", "min")]
    reasons, unmeasured = [], []
    for rule, metric, kind in checks:
        limit = rules.get(rule)
        if limit is None:
            continue
        val = rec_med[metric]
        if val is None:                 # 규칙이 요구하는데 못 쟀다 — 통과가 아니다(fail-closed)
            unmeasured.append(f"{metric} unmeasured (required by {rule})")
            continue
        if (kind == "min" and val < limit) or (kind == "max" and val > limit):
            reasons.append(f"{metric}={val:.3f} {'<' if kind == 'min' else '>'} {rule}={limit}")
    drop = rules.get("max_drop_from_baseline")
    if drop is not None:
        for metric in ("anchor_share_topk", "top_tier_share_topk", "freshness_topk"):
            b, r = base_med[metric], rec_med[metric]
            if b is None or r is None:  # 보조 규칙도 같은 원칙 — 못 쟀으면 통과가 아니다
                unmeasured.append(f"{metric} unmeasured (required by max_drop_from_baseline)")
            elif b - r > drop:
                reasons.append(f"{metric} fell {b:.3f}→{r:.3f} (> max_drop_from_baseline={drop})")
    if unmeasured:
        return {**out, "status": UNMEASURED_REQUIRED, "reasons": unmeasured + reasons}
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
            "freshness_topk", "mean_delay_days_topk", "exclude_collision_auto",
            "topk_retention_vs_prev", "new_eligible_from_auto")}
    modes = sorted({r.get("mode") for r in rows} - {None})
    lines.append(f"  스캔 {n}회 · anchor {rows[-1]['anchors']}개({rows[-1]['anchor_basis']})"
                 + (f" · auto {len(rows[-1]['auto'])}개" if rows[-1]["auto"] else " · auto 없음")
                 + f" · 적중 근거 {'/'.join(modes)}"
                 + (" (recomputed·partial 은 관측 시점에 얼리지 못한 옛 스캔 — 현재 코드·이력으로 다시 셈)"
                    if modes != ["as_observed"] else ""))
    lines.append(f"  상위 K: anchor 적중 {_fmt(med['anchor_share_topk'])} · 최상위 계층 "
                 f"{_fmt(med['top_tier_share_topk'])} · 최신일 {_fmt(med['freshness_topk'])} · "
                 f"평균 지연 {_fmt(med['mean_delay_days_topk'])}일 (중앙값)")
    lines.append(f"  적격 풀: anchor 적중 {_fmt(med['anchor_share_eligible'])}")
    lines.append(f"  auto 계열: 제외어 충돌 {_fmt(med['exclude_collision_auto'])} · "
                 f"auto 덕에 새로 적격 {_fmt(med['new_eligible_from_auto'])}편 · "
                 f"직전 프로필 대비 상위 K 유지 {_fmt(med['topk_retention_vs_prev'])}")
    dead = [kw for kw, c in rows[-1]["keyword_hits"].items() if c == 0]
    if dead:
        lines.append(f"  마지막 스캔에서 적중 0인 키워드 {len(dead)}개: {', '.join(dead[:8])}"
                     + (" …" if len(dead) > 8 else ""))
    lines.append(f"  악화 규칙: {'미설정 — 기준선 ' + str(MIN_BASELINE_SCANS) + '스캔 뒤 정한다' if all(v is None for v in DEFAULT_HEALTH_RULES.values()) else '설정됨'}")
    return lines
