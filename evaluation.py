"""⑧ 축적 — 효과 평가 지표 (E1, 2026-09-11). docs/ASTRA_PLAN_2026-09-10.md §11.

운영 경로 밖의 읽기 전용 계산이다. LLM·네트워크 없음. **없는 것을 0 으로 채우지
않는다** — 독립 라벨이 없으면 의미상 지표는 None 이고 `labels_unavailable` 을 붙인다.
미포착 논문은 지연 계산에서 지우지 않고 따로 센다(§11.2).

수치는 전부 **고유 논문 단위**다. 같은 논문이 여러 날 등장한 것을 독립 표본
여러 개로 세지 않는다(§11.7).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

import profile_scoring
import research_profile

LABELS_UNAVAILABLE = "labels_unavailable"
NOT_REPLAYABLE = "not_replayable"


def _dt(x: str | None) -> datetime | None:
    if not x:
        return None
    try:
        d = datetime.fromisoformat(x.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _quantiles(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "median": statistics.median(s),
            "p90": s[min(len(s) - 1, int(len(s) * 0.9))], "max": s[-1], "min": s[0]}


# ── 지연 (§11.2) ────────────────────────────────────────────────────────
def latency(db: Path, profile_id: str, start: datetime, end: datetime,
            observation_start: datetime | None = None) -> dict:
    """공개 → 최초 관측 → 전달의 시점 차이. 고유 논문 단위.

    `observation_start` 는 후보 기록이 시작된 시각이다(search_candidates 는
    2026-09-07 에 생겼다). 그 전에 공개된 논문은 first_seen 이 테이블 생성일에
    묶여 지연이 부풀려지므로 **분모에서 뺀다** — 지운 게 아니라 `censored_before_
    observation` 으로 따로 센다. 미포착(전달 안 됨)도 삭제하지 않고 센다.
    """
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT c.paper_key, c.published, c.first_seen, c.source, s.shown_at "
            "FROM search_candidates c LEFT JOIN profile_shown s "
            "ON s.profile_id=c.profile_id AND s.paper_key=c.paper_key "
            "WHERE c.profile_id=? AND c.first_seen >= ? AND c.first_seen < ?",
            (profile_id, start.isoformat(), end.isoformat())).fetchall()
    coll: dict[str, list[float]] = {}
    deliv: list[float] = []
    censored = undated = not_delivered = 0
    for key, pub, fs, src, shown in rows:
        day, prec = profile_scoring.publication_day(pub)
        first = _dt(fs)
        if day is None or first is None:
            undated += 1
            continue
        if observation_start and datetime.fromordinal(day).replace(tzinfo=timezone.utc) < observation_start:
            censored += 1
            continue
        coll.setdefault(src or "?", []).append(first.date().toordinal() - day)
        coll.setdefault("all", []).append(first.date().toordinal() - day)
        sh = _dt(shown)
        if sh:
            deliv.append((sh - first).total_seconds() / 86400)
        else:
            not_delivered += 1
    return {
        "unit": "days", "papers": len(rows),
        "collection": {k: _quantiles(v) for k, v in coll.items()},
        "delivery": _quantiles(deliv),
        "not_delivered": not_delivered,               # 미포착 — 지연 분모에서 뺐지만 지우지 않았다
        "censored_before_observation": censored,      # 관측 시작 전 공개 — 지연이 부풀려져 뺐다
        "undated": undated,
        "note": "collection = first_seen − published (day); delivery = shown_at − first_seen. "
                "창 7일 재검색 구조라 collection 상한이 7일 근처에 몰린다.",
    }


def term_adoption_latency(db: Path, profile_id: str) -> dict:
    """용어 반영 지연 — 제안이 만들어진 시각 → 적용 revision 시각. advisor_events 로 센다.
    적용된 게 없으면 n=0 이지 0일이 아니다."""
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute(
                "SELECT e.at, r.created_at FROM advisor_events e "
                "JOIN advisor_proposals p ON p.bundle_analysis_id = e.ref_id "
                "JOIN advisor_runs r ON r.run_id = p.run_id "
                "WHERE e.profile_id=? AND e.kind='applied'", (profile_id,)).fetchall()
    except sqlite3.OperationalError:
        return {"n": 0, "note": "advisor tables absent"}
    xs = [(_dt(a) - _dt(c)).total_seconds() / 86400 for a, c in rows if _dt(a) and _dt(c)]
    return {"unit": "days", **_quantiles(xs)}


# ── 제안 효율·비용 (§11.2) ───────────────────────────────────────────────
def proposal_efficiency(db: Path, profile_id: str, start: datetime, end: datetime) -> dict:
    try:
        with sqlite3.connect(db) as con:
            runs = con.execute(
                "SELECT status, COUNT(*) FROM advisor_runs WHERE profile_id=? AND created_at>=? AND created_at<? "
                "GROUP BY status", (profile_id, start.isoformat(), end.isoformat())).fetchall()
            gates = con.execute(
                "SELECT a.gate_status, COUNT(*) FROM advisor_proposals p JOIN advisor_runs r ON r.run_id=p.run_id "
                "JOIN impact_analyses a ON a.analysis_id=p.bundle_analysis_id "
                "WHERE r.profile_id=? AND r.created_at>=? AND r.created_at<? GROUP BY a.gate_status",
                (profile_id, start.isoformat(), end.isoformat())).fetchall()
            events = con.execute(
                "SELECT kind, COUNT(*) FROM advisor_events WHERE profile_id=? AND at>=? AND at<? GROUP BY kind",
                (profile_id, start.isoformat(), end.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return {"note": "advisor tables absent"}
    return {"runs": dict(runs), "gate": dict(gates), "events": dict(events)}


def operating_cost(db: Path, profile_id: str, start: datetime, end: datetime) -> dict:
    """제안기의 요청·사용량. 토큰은 provider 보고값이 있을 때만 합산하고 종류를 표시한다.
    일일 요약·서술의 비용은 api_usage(프로세스 내 계측)라 여기 없다 — 로그를 봐야 한다."""
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute(
                "SELECT a.outcome, a.usage_json, a.usage_kind FROM advisor_attempts a JOIN advisor_runs r "
                "ON r.run_id=a.run_id WHERE r.profile_id=? AND a.started_at>=? AND a.started_at<?",
                (profile_id, start.isoformat(), end.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return {"note": "advisor tables absent"}
    out = {"requests": len(rows), "by_outcome": {}, "tokens": {"prompt": 0, "output": 0},
           "usage_kind": {}}
    for outcome, usage, kind in rows:
        out["by_outcome"][outcome] = out["by_outcome"].get(outcome, 0) + 1
        out["usage_kind"][kind or "unknown"] = out["usage_kind"].get(kind or "unknown", 0) + 1
        if usage and kind == "provider_reported":
            u = json.loads(usage)
            out["tokens"]["prompt"] += int(u.get("promptTokenCount") or 0)
            out["tokens"]["output"] += int(u.get("candidatesTokenCount") or 0)
    if out["usage_kind"].get("provider_reported", 0) < len(rows):
        out["tokens"]["note"] = "일부 시도는 provider 보고값이 없어 합계에 빠졌다"
    return out


# ── 키워드 적중 비율 (precision 이 아니다) ───────────────────────────────
def core_hit_ratio(db: Path, profile_id: str, start: datetime, end: datetime, profile: dict | None = None) -> dict:
    """guard 후 core 적격 고유 후보 / 평가 대상 고유 후보. **운영 진단용**이고
    precision 이 아니다(§11.2) — 의미상 관련성은 독립 라벨이 있어야 한다."""
    profile = profile or research_profile.get_profile(db, profile_id)
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT paper_key, title, abstract, published, source FROM search_candidates "
            "WHERE profile_id=? AND first_seen>=? AND first_seen<?",
            (profile_id, start.isoformat(), end.isoformat())).fetchall()
    # **guard 후 core 적중, 제외어 필터 전**(C 의 정의와 같다). 최종 적격은 따로 낸다 —
    # 제외어가 먼저 걸리면 core 적중이 0 으로 보여 "안 걸렸다"와 "걸렸는데 제외됐다"가 섞인다.
    no_ex = dict(profile, exclude=[])
    by_src: dict[str, dict] = {}
    for key, t, a, p, src in rows:
        paper = {"title": t or "", "abstract": a or "", "published": p}
        hit = bool(profile_scoring.score_paper(paper, no_ex)["core_hits"])
        final = profile_scoring.score_paper(paper, profile)
        cell = by_src.setdefault(src or "?", {"papers": 0, "core_hit": 0, "excluded": 0, "eligible": 0})
        cell["papers"] += 1
        cell["core_hit"] += hit
        cell["excluded"] += bool(final["excluded"])
        cell["eligible"] += bool(final["core_hits"])
    for cell in by_src.values():
        cell["ratio"] = (cell["core_hit"] / cell["papers"]) if cell["papers"] else None
        cell["eligible_ratio"] = (cell["eligible"] / cell["papers"]) if cell["papers"] else None
    return {"by_source": by_src,
            "note": "core_hit = guard 후·제외어 전. eligible = 제외어까지 통과. 둘 다 precision 이 아니다 — "
                    "독립 라벨 없이는 의미상 관련성을 말하지 않는다"}


# ── 의미상 지표 — 독립 라벨이 있을 때만 ───────────────────────────────────
def semantic_metrics(labels: dict[str, str] | None, ranked_keys: list[str], k: int) -> dict:
    """labels: {paper_key: 'direct'|'indirect'|'irrelevant'|'unknown'} — **독립** 기준 집합.
    없으면 None + labels_unavailable. 미판정·불확실은 분모에서 분리하고 판정률을 보고한다."""
    if not labels:
        return {"precision_at_k": None, "candidate_recall": None, "noise_rate": None,
                "status": LABELS_UNAVAILABLE}
    topk = ranked_keys[:k]
    judged = {kk: v for kk, v in labels.items() if v in ("direct", "indirect", "irrelevant")}
    rel = {kk for kk, v in judged.items() if v in ("direct", "indirect")}
    top_j = [kk for kk in topk if kk in judged]
    return {
        "k": k, "returned": len(topk), "filled": len(topk) >= k,
        "precision_at_k": (sum(1 for kk in top_j if kk in rel) / len(top_j)) if top_j else None,
        "candidate_recall": (sum(1 for kk in rel if kk in set(ranked_keys)) / len(rel)) if rel else None,
        "noise_rate": (sum(1 for kk in judged if judged[kk] == "irrelevant") / len(judged)) if judged else None,
        "judged": len(judged), "unjudged": len(labels) - len(judged),
        "judgement_rate": (len(judged) / len(labels)) if labels else None,
    }


# ── 재생 (§11.6) — 시점 누수 방지 ─────────────────────────────────────────
def replay(db: Path, profile_id: str, cutoff: datetime, k: int) -> dict:
    """cutoff 까지의 관측만으로 스냅샷을 만들고, **그 시점 scan_runs 의 프로필 스냅샷**
    으로 재채점한다("당시 입력·정책으로 재생"). cutoff 이후 관측은 안 쓴다.
    관측 도입 전이면 not_replayable — 개체 테이블로 부분 재생을 시도하지 않는다
    (그 테이블은 최신 상태라 당시 입력이 아니다)."""
    import profile_impact
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT profile_snapshot, policy_version FROM scan_runs WHERE profile_id=? AND started_at<? "
            "ORDER BY started_at DESC LIMIT 1", (profile_id, cutoff.isoformat())).fetchone()
    if not row:
        return {"status": NOT_REPLAYABLE, "reason": "no_scan_before_cutoff"}
    profile = json.loads(row[0])
    snap = profile_impact.snapshot(db, profile_id, datetime(1970, 1, 1, tzinfo=timezone.utc), cutoff)
    if snap["paper_count"] == 0:
        return {"status": NOT_REPLAYABLE, "reason": "no_observations_before_cutoff"}
    ranked = profile_scoring.score_and_rank(snap["papers"], profile)["papers"]
    return {"status": "replayed", "cutoff": cutoff.isoformat(), "policy_version": row[1],
            "snapshot_id": snap["snapshot_id"], "papers": snap["paper_count"],
            "topk": [p["_paper_key"] for p in ranked[:k]], "eligible": len(ranked)}


def rescore_current(db: Path, profile_id: str, cutoff: datetime, k: int) -> dict:
    """같은 관측을 **현재** 프로필·정책으로 재채점 — replay 와 이름부터 다르다."""
    import profile_impact
    profile = research_profile.get_profile(db, profile_id)
    snap = profile_impact.snapshot(db, profile_id, datetime(1970, 1, 1, tzinfo=timezone.utc), cutoff)
    if snap["paper_count"] == 0:
        return {"status": NOT_REPLAYABLE, "reason": "no_observations_before_cutoff"}
    ranked = profile_scoring.score_and_rank(snap["papers"], profile)["papers"]
    return {"status": "rescored", "cutoff": cutoff.isoformat(), "policy_version": research_profile.RANK_POLICY_VERSION,
            "topk": [p["_paper_key"] for p in ranked[:k]], "eligible": len(ranked)}


# ── 보고 ───────────────────────────────────────────────────────────────
def report(db: Path, profile_id: str, start: datetime, end: datetime,
           observation_start: datetime | None = None, labels: dict[str, str] | None = None) -> dict:
    """주간 평가 보고 — 지표 + 원시 분모 + 미측정 목록. 성과 칸을 가상 값으로 채우지 않는다."""
    lat = latency(db, profile_id, start, end, observation_start)
    unmeasured = []
    sem = semantic_metrics(labels, [], 0)
    if sem.get("status") == LABELS_UNAVAILABLE:
        unmeasured += ["precision_at_k", "candidate_recall", "semantic_noise_rate"]
    unmeasured += ["independent_set_capture_rate", "summary_semantic_accuracy", "new_seed_search_yield"]
    return {
        "profile_id": profile_id, "window": [start.isoformat(), end.isoformat()],
        "latency": lat,
        "term_adoption_latency": term_adoption_latency(db, profile_id),
        "core_hit_ratio": core_hit_ratio(db, profile_id, start, end),
        "proposal_efficiency": proposal_efficiency(db, profile_id, start, end),
        "operating_cost": operating_cost(db, profile_id, start, end),
        "semantic": sem,
        "unmeasured": unmeasured,
    }


# ── 실험 manifest (§11.7·§15) — 별도 평가 DB, freeze 뒤 불변 ───────────────
FROZEN = "frozen"
DRAFT = "draft"


def _ddl(con: sqlite3.Connection) -> None:
    """평가 저장소 스키마. schema_guard 를 통해서만 돈다(§8-98)."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS experiments ("
        " experiment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,"
        " manifest_json TEXT NOT NULL, manifest_sha256 TEXT, frozen_at TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS experiment_runs ("
        " run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, arm TEXT NOT NULL, at TEXT NOT NULL,"
        " manifest_sha256 TEXT NOT NULL, snapshot_sha256 TEXT, arm_revision INTEGER, result_json TEXT)")
    # frozen 행은 바꿀 수 없다 — 해시만 남기고 원문 변경을 허용하면 불변이 아니다.
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS experiments_frozen_immutable BEFORE UPDATE ON experiments "
        "WHEN OLD.status='frozen' BEGIN SELECT RAISE(ABORT, 'frozen experiment is immutable'); END")
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS experiments_frozen_nodelete BEFORE DELETE ON experiments "
        "WHEN OLD.status='frozen' BEGIN SELECT RAISE(ABORT, 'frozen experiment is immutable'); END")


REQUIRED_MANIFEST = ("primary_metric", "direction", "max_degradation", "k", "denominator",
                 "label_rule", "missing_policy", "follow_up_days", "budget", "r_rules", "gate_rules",
                 "initial_profile_hash", "policy_version", "prompt_version", "arms")


def experiments_init(eval_db: Path) -> None:
    """평가 저장소는 운영 DB 와 **다른 파일**이다. 운영 DB 는 평가 도구가 읽기만 한다."""
    import schema_guard
    schema_guard.ensure(eval_db, _ddl, "evaluation")
def experiment_draft(eval_db: Path, manifest: dict) -> str:
    experiments_init(eval_db)
    import uuid
    eid = uuid.uuid4().hex[:12]
    with sqlite3.connect(eval_db) as con:
        con.execute("INSERT INTO experiments (experiment_id, created_at, status, manifest_json) VALUES (?,?,?,?)",
                    (eid, datetime.now(timezone.utc).isoformat(timespec="seconds"), DRAFT,
                     json.dumps(manifest, ensure_ascii=False, sort_keys=True)))
    return eid


def experiment_freeze(eval_db: Path, experiment_id: str, baseline_end: datetime,
                      now: datetime | None = None) -> dict:
    """draft → frozen. 필수 항목이 다 있어야 하고, **기준선이 끝난 뒤**여야 한다.
    임계값(max_degradation·gate_rules)이 None 이면 얼리지 않는다 — 효과 판정을
    못 하는 실험을 유효한 비교로 인정하지 않는다."""
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(eval_db) as con:
        row = con.execute("SELECT status, manifest_json FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if not row:
            return {"frozen": False, "reason": "not_found"}
        if row[0] == FROZEN:
            return {"frozen": False, "reason": "already_frozen"}
        m = json.loads(row[1])
        missing = [k for k in REQUIRED_MANIFEST if k not in m or m[k] is None]
        if missing:
            return {"frozen": False, "reason": f"manifest_incomplete:{','.join(missing)}"}
        if now < baseline_end:
            return {"frozen": False, "reason": "baseline_not_finished"}
        if any(v is None for v in (m.get("gate_rules") or {}).values()):
            return {"frozen": False, "reason": "gate_rules_unconfigured"}
        canon = json.dumps(m, ensure_ascii=False, sort_keys=True)
        sha = __import__("hashlib").sha256(canon.encode("utf-8")).hexdigest()
        con.execute("UPDATE experiments SET status=?, manifest_json=?, manifest_sha256=?, frozen_at=? WHERE experiment_id=?",
                    (FROZEN, canon, sha, now.isoformat(timespec="seconds"), experiment_id))
    return {"frozen": True, "manifest_sha256": sha}


def experiment_record_run(eval_db: Path, experiment_id: str, arm: str, snapshot_sha256: str | None,
                          arm_revision: int | None, result: dict) -> str:
    """실행마다 manifest 해시를 같이 남긴다 — 얼린 뒤 설정이 바뀌면 실행이 어긋난다."""
    import uuid
    with sqlite3.connect(eval_db) as con:
        row = con.execute("SELECT status, manifest_sha256 FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if not row or row[0] != FROZEN:
            raise ValueError("experiment must be frozen before recording runs")
        rid = uuid.uuid4().hex[:12]
        con.execute("INSERT INTO experiment_runs (run_id, experiment_id, arm, at, manifest_sha256, snapshot_sha256,"
                    " arm_revision, result_json) VALUES (?,?,?,?,?,?,?,?)",
                    (rid, experiment_id, arm, datetime.now(timezone.utc).isoformat(timespec="seconds"), row[1],
                     snapshot_sha256, arm_revision, json.dumps(result, ensure_ascii=False, default=list)))
    return rid


def feedback_usefulness(db: Path, profile_id: str, start: datetime, end: datetime) -> dict:
    """`briefing_feedback` 는 **독립 관련성 라벨이 아니다** — 배달된 논문에만 붙고, 값이
    useful/known/out_of_scope 라 관련성 판정과 다르다(`known` 은 관련성 부정이 아니다).
    별도 지표로만 낸다. precision 이라 부르지 않는다."""
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute(
                "SELECT usefulness, COUNT(DISTINCT paper_key) FROM briefing_feedback "
                "WHERE profile_id=? AND created_at>=? AND created_at<? GROUP BY usefulness",
                (profile_id, start.isoformat(), end.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return {"papers_with_feedback": 0, "note": "feedback table absent"}
    return {"by_value": dict(rows), "papers_with_feedback": sum(n for _, n in rows),
            "note": "배달 논문에만 존재 · 관련성 라벨 아님 · arm/순위 비공개 여부 미기록"}
