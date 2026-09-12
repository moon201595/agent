"""⑦단계 — shadow 검색: 검색 집합을 바꾸는 프로필 변경안을 **격리해서** 실측한다.

2026-09-12(PROGRESS §8-100). 가중치·적격 조건 변경은 저장된 관측을 다시 채점하면 평가할
수 있다(profile_impact). 검색어·씨앗 변경은 **후보 집합 자체가 바뀌므로** 저장된 데이터로는
원리적으로 평가할 수 없다 — 그래서 게이트가 `needs_shadow_search` 로 보류한다(§8-88).
여기가 그 보류를 푸는 실행부다.

격리의 뜻: 운영 커서(search_runs)·후보(search_candidates)·배달 기록(profile_shown)·
프로필 revision 을 **읽지도 쓰지도 않는다.** 검색 함수(find_new_papers·s2_delta)는 원래
DB 를 안 건드리는 순수 함수라 그대로 부르고, 결과는 `shadow_runs` 한 표에만 남긴다.
API 호출은 `purpose="shadow"` 로 계측해 일일 예산과 갈라 센다.

두 팔(arm): baseline = 변경 전 프로필, candidate = 변경 후. 같은 창(window)을 같은 시각에
검색한다. 두 팔이 공유하는 씨앗·질의는 **한 번만** 부른다 — 차이 나는 부분만 비용이다.
재는 것: 팔별 고유 후보(다른 팔에는 없는 논문)·핵심 적격 수·상위 K 겹침·씨앗별 수율과
잡음(제외어 적중)·API 호출·소요. 판정은 안 한다 — 숫자를 남기고 게이트 규칙(미설정으로
시작)이 뒤에 읽는다.

첫 실험(설계 검토 2026-09-12): S2 씨앗에서 `world model` 제거. 관측된 잡음(random forest ·
retrospective cohort · world bank)이 사라지고 고유 관련 논문이 거의 안 줄면 제거 확정.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import api_usage
import find_new_papers
import profile_impact
import profile_scoring
import research_profile
import s2_delta
import selection
from run_profile_scan import _arxiv_query_from_core_topics

SHADOW_VERSION = "shadow-v1"
DEFAULT_WINDOW_DAYS = 7
SHADOW_BUDGET_S = 300.0          # 팔 하나가 아니라 실험 전체
ARXIV_MAX_PAGES = 6              # 일일 스캔(30)보다 작게 — 실험이지 수집이 아니다


def _ddl(con: sqlite3.Connection) -> None:
    """shadow_runs — 실험 하나에 한 행. 추가만 하고 고치지 않는다."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS shadow_runs ("
        " shadow_id    TEXT PRIMARY KEY,"
        " profile_id   TEXT NOT NULL,"
        " analysis_id  TEXT,"            # impact_analyses 와 연결. 없으면 수동 실험
        " created_at   TEXT NOT NULL,"
        " window_start TEXT NOT NULL,"
        " window_end   TEXT NOT NULL,"
        " before_hash  TEXT NOT NULL,"
        " after_hash   TEXT NOT NULL,"
        " arms_json    TEXT NOT NULL,"   # 팔별 씨앗·질의
        " metrics_json TEXT NOT NULL,"
        " api_calls    INTEGER NOT NULL,"
        " seconds      REAL NOT NULL,"
        " status       TEXT NOT NULL,"   # done | partial | failed
        " version      TEXT NOT NULL)"
    )


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "shadow_search")


def arms_for(before: dict, after: dict) -> dict:
    """두 팔의 검색 사양. 무엇이 다른지도 같이 — 같은 것은 한 번만 검색한다."""
    b_seeds, a_seeds = s2_delta.keywords_for_s2(before), s2_delta.keywords_for_s2(after)
    b_q, a_q = _arxiv_query_from_core_topics(before.get("core_topics") or []), \
        _arxiv_query_from_core_topics(after.get("core_topics") or [])
    return {
        "baseline": {"seeds": sorted(b_seeds), "arxiv_query": b_q},
        "candidate": {"seeds": sorted(a_seeds), "arxiv_query": a_q},
        "seed_changed": sorted(b_seeds) != sorted(a_seeds),
        "arxiv_query_changed": b_q != a_q,
        "seeds_removed": sorted(set(b_seeds) - set(a_seeds)),
        "seeds_added": sorted(set(a_seeds) - set(b_seeds)),
    }


async def _search(client: httpx.AsyncClient, arms: dict, since: datetime, until: datetime,
                  budget_s: float) -> tuple[dict, dict, str]:
    """씨앗별·질의별 결과를 한 번씩만 모은다. returns (seed→papers, query→papers, status)."""
    deadline = time.monotonic() + budget_s
    seed_results: dict[str, list[dict]] = {}
    status = "done"
    all_seeds = sorted(set(arms["baseline"]["seeds"]) | set(arms["candidate"]["seeds"]))
    for seed in all_seeds:
        left = deadline - time.monotonic()
        if left <= 0:
            status = "partial"
            break
        res = await s2_delta.find_new_papers_since(client, [seed], since, until, budget_s=left)
        seed_results[seed] = res.get("papers") or []
        if res.get("status") != "done":
            status = "partial"
    query_results: dict[str, list[dict]] = {}
    # arXiv 질의가 안 바뀌어도 **한 번** 검색해 두 팔에 공유한다 — 손실은 합집합 기준이어야
    # 뜻이 있다. 첫 dry-run(2026-09-12)에서 S2 팔만 비교하니 "잃은 적격 28편"이 나왔는데
    # 24편은 일일 arXiv 검색이 어차피 잡는 논문이었다. 바뀐 질의는 각각 검색한다.
    queries = {arms["baseline"]["arxiv_query"], arms["candidate"]["arxiv_query"]}
    for q in sorted(queries):
        if time.monotonic() >= deadline:
            status = "partial"
            break
        res = await find_new_papers.find_new_papers_since(client, q, since, max_pages=ARXIV_MAX_PAGES)
        query_results[q] = res.get("papers") or []
        if res.get("status") != "done":
            status = "partial"
    return seed_results, query_results, status


def _arm_papers(arm: dict, seed_results: dict, query_results: dict) -> list[dict]:
    papers: list[dict] = []
    for seed in arm["seeds"]:
        for p in seed_results.get(seed, []):
            papers.append({**p, "s2_seeds": sorted(set(p.get("s2_seeds") or []) | {seed})})
    papers += query_results.get(arm["arxiv_query"], [])
    return selection.dedupe(papers)


def _measure(papers: list[dict], profile: dict, k: int, seeds: list[str]) -> dict:
    """한 팔의 숫자. 판정 없음."""
    rows = []
    for p in papers:
        hits = profile_scoring.raw_hits(p, profile)
        sc = profile_scoring.score_paper(p, profile)
        rows.append({"key": research_profile.paper_key(p), "core": hits["core_hits"],
                     "excluded": bool(hits["exclude_hits"]), "eligible": bool(sc["core_hits"]) and not sc["excluded"],
                     "seeds": p.get("s2_seeds") or []})
    ranked = profile_scoring.score_and_rank(papers, profile)["papers"]
    topk = [research_profile.paper_key(p) for p in ranked[:k]]
    per_seed = {}
    for s in seeds:
        mine = [r for r in rows if s in r["seeds"]]
        per_seed[s] = {"returned": len(mine), "eligible": sum(1 for r in mine if r["eligible"]),
                       "noise": sum(1 for r in mine if r["excluded"] or not r["core"])}
    return {"returned": len(rows), "eligible": sum(1 for r in rows if r["eligible"]),
            "excluded": sum(1 for r in rows if r["excluded"]),
            "no_core": sum(1 for r in rows if not r["core"] and not r["excluded"]),
            "topk": topk, "per_seed": per_seed,
            "keys": sorted(r["key"] for r in rows),
            "eligible_keys": sorted(r["key"] for r in rows if r["eligible"])}


def compare(base: dict, cand: dict, k: int) -> dict:
    """두 팔의 차이. '후보 팔에서 사라진 적격 논문'이 핵심 손실 지표다."""
    b_keys, c_keys = set(base["keys"]), set(cand["keys"])
    b_el, c_el = set(base["eligible_keys"]), set(cand["eligible_keys"])
    topk_b, topk_c = set(base["topk"]), set(cand["topk"])
    return {
        "unique_to_baseline": len(b_keys - c_keys), "unique_to_candidate": len(c_keys - b_keys),
        "eligible_lost": sorted(b_el - c_el), "eligible_gained": sorted(c_el - b_el),
        "eligible_before": len(b_el), "eligible_after": len(c_el),
        "noise_before": base["excluded"] + base["no_core"], "noise_after": cand["excluded"] + cand["no_core"],
        "topk_overlap": (len(topk_b & topk_c) / k) if k else None,
        "topk_left": sorted(topk_b - topk_c), "topk_entered": sorted(topk_c - topk_b),
    }


async def run_shadow(db: Path, profile_id: str, before: dict, after: dict,
                     client: httpx.AsyncClient, *, analysis_id: str | None = None,
                     window_days: int = DEFAULT_WINDOW_DAYS, budget_s: float = SHADOW_BUDGET_S,
                     now: datetime | None = None, store: bool = True) -> dict:
    """격리 실험 하나. store=False 면 DB 에 아무것도 쓰지 않는다(dry-run)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    arms = arms_for(before, after)
    if not (arms["seed_changed"] or arms["arxiv_query_changed"]):
        return {"status": "skipped", "reason": "no_search_change", "arms": arms}
    k = int(after.get("max_items") or before.get("max_items") or 6)
    scope = api_usage.Scope()
    started = time.monotonic()
    with scope:
        try:
            seed_results, query_results, status = await _search(client, arms, since, now, budget_s)
        except Exception as e:  # noqa: BLE001 — 실험 실패는 실패로 남긴다
            seed_results, query_results, status = {}, {}, f"failed: {type(e).__name__}: {str(e)[:120]}"
    seconds = time.monotonic() - started
    calls = scope.total()
    base = _measure(_arm_papers(arms["baseline"], seed_results, query_results), before, k, arms["baseline"]["seeds"])
    cand = _measure(_arm_papers(arms["candidate"], seed_results, query_results), after, k, arms["candidate"]["seeds"])
    metrics = {"baseline": {kk: v for kk, v in base.items() if kk not in ("keys", "eligible_keys")},
               "candidate": {kk: v for kk, v in cand.items() if kk not in ("keys", "eligible_keys")},
               "diff": compare(base, cand, k), "k": k, "window_days": window_days,
               "api": scope.snapshot()}
    out = {"shadow_id": uuid.uuid4().hex[:12], "profile_id": profile_id, "analysis_id": analysis_id,
           "created_at": now.isoformat(), "window_start": since.isoformat(), "window_end": now.isoformat(),
           "before_hash": profile_impact.profile_hash(before), "after_hash": profile_impact.profile_hash(after),
           "arms": arms, "metrics": metrics, "api_calls": calls, "seconds": round(seconds, 1),
           "status": status if status in ("done", "partial") else "failed", "error": None if status in ("done", "partial") else status,
           "version": SHADOW_VERSION}
    if store:
        init_db(db)
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO shadow_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (out["shadow_id"], profile_id, analysis_id, out["created_at"], out["window_start"],
                         out["window_end"], out["before_hash"], out["after_hash"],
                         json.dumps(arms, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False),
                         calls, out["seconds"], out["status"], SHADOW_VERSION))
    return out


def format_shadow(out: dict) -> list[str]:
    """사람이 읽을 요약. 판정 문구 없음 — 숫자만."""
    if out.get("status") == "skipped":
        return [f"shadow 건너뜀: {out['reason']}"]
    a, m, d = out["arms"], out["metrics"], out["metrics"]["diff"]
    lines = [f"shadow {out['shadow_id']} · {out['status']} · 창 {m['window_days']}일 · API {out['api_calls']}회 · {out['seconds']}초",
             f"  baseline 씨앗 {a['baseline']['seeds']} → candidate 씨앗 {a['candidate']['seeds']}"
             + (f" (제거 {a['seeds_removed']})" if a['seeds_removed'] else "") + (f" (추가 {a['seeds_added']})" if a['seeds_added'] else ""),
             f"  반환 {m['baseline']['returned']} → {m['candidate']['returned']} · 적격 {d['eligible_before']} → {d['eligible_after']}"
             f" (잃음 {len(d['eligible_lost'])} · 얻음 {len(d['eligible_gained'])}) · 잡음 {d['noise_before']} → {d['noise_after']}",
             f"  상위 {m['k']} 겹침 {d['topk_overlap']:.2f} · 이탈 {len(d['topk_left'])} · 진입 {len(d['topk_entered'])}"
             if d['topk_overlap'] is not None else "  상위 K 미측정"]
    for arm in ("baseline", "candidate"):
        for s, v in m[arm]["per_seed"].items():
            if arm == "candidate" and s in m["baseline"]["per_seed"]:
                continue   # 공유 씨앗은 baseline 줄로 충분
            lines.append(f"  [{arm}] {s}: 반환 {v['returned']} · 적격 {v['eligible']} · 잡음 {v['noise']}")
    if d["eligible_lost"]:
        lines.append(f"  잃은 적격 논문: {', '.join(d['eligible_lost'][:8])}" + (" …" if len(d["eligible_lost"]) > 8 else ""))
    return lines
