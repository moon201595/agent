"""⑧ 축적 — 실행별 관측(candidate_observations)에서 결정적 신호를 센다.

B단계(2026-09-11, docs/ASTRA_PLAN_2026-09-10.md §6.2). LLM 을 쓰지 않는다 —
전부 편수 셈이고, 관측 이력이 없는 기간은 0 이 아니라 **미측정**으로 답한다
(§7.5 "데이터가 없어 지표를 계산할 수 없으면 0으로 채우지 않는다").

trend_report 의 emerging_terms·keyword_counts·source_mix 는 논문 **개체**
(search_candidates)를 센다. 여기는 **관측**을 센다 — 같은 논문이 여러 실행에서
보이면 실행마다 한 번이다. 둘은 분모가 다르므로 한 표에 섞지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import research_profile


def _rows(db: Path, profile_id: str, start: datetime, end: datetime) -> list[dict]:
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM candidate_observations WHERE profile_id=? "
            "AND observed_at >= ? AND observed_at < ? ORDER BY observed_at, scan_id, paper_key",
            (profile_id, start.isoformat(), end.isoformat()))]


def seed_yield(db: Path, profile_id: str, start: datetime, end: datetime) -> dict | None:
    """씨앗별 {returned, unique, eligible, content}. 관측이 없으면 None(미측정).

    unique 는 **그 씨앗만** 데려온 논문(다른 씨앗도, arXiv 도 못 봤다). 같은
    기간 안에서 논문 단위로 센다 — 실행이 반복돼도 한 편은 한 번.
    """
    rows = _rows(db, profile_id, start, end)
    if not rows:
        return None
    by_paper: dict[str, dict] = {}
    for r in rows:
        k = r["paper_key"]
        seeds = json.loads(r["s2_seeds"]) if r["s2_seeds"] else []
        cur = by_paper.setdefault(k, {"seeds": set(), "arxiv": False,
                                      "eligible": False, "content": False})
        cur["seeds"].update(seeds)
        # 실제 발견 출처의 합집합을 본다 — 병합된 논문은 `source` 하나로는
        # "arXiv 도 봤다"를 알 수 없다(selection._merge 가 retrieval_sources 를 합친다).
        srcs = json.loads(r["retrieval_sources"]) if r["retrieval_sources"] else [r["source"]]
        if "arxiv" in srcs:
            cur["arxiv"] = True
        if r["filter_reason"] is None and r["rank_pos"] is not None:
            cur["eligible"] = True
        if r["outcome"] == research_profile.OUTCOME_CONTENT:
            cur["content"] = True
    out: dict[str, dict] = defaultdict(lambda: {"returned": 0, "unique": 0, "eligible": 0, "content": 0})
    for info in by_paper.values():
        for s in info["seeds"]:
            out[s]["returned"] += 1
            if len(info["seeds"]) == 1 and not info["arxiv"]:
                out[s]["unique"] += 1
            if info["eligible"]:
                out[s]["eligible"] += 1
            if info["content"]:
                out[s]["content"] += 1
    return dict(out)


def source_contribution(db: Path, profile_id: str, start: datetime, end: datetime) -> dict | None:
    """출처별 {papers, unique, eligible}. unique 는 다른 출처에 없는 논문. 관측 없으면 None."""
    rows = _rows(db, profile_id, start, end)
    if not rows:
        return None
    by_paper: dict[str, dict] = {}
    for r in rows:
        cur = by_paper.setdefault(r["paper_key"], {"sources": set(), "eligible": False})
        srcs = json.loads(r["retrieval_sources"]) if r["retrieval_sources"] else [r["source"]]
        cur["sources"].update(s for s in srcs if s)
        if r["filter_reason"] is None and r["rank_pos"] is not None:
            cur["eligible"] = True
    out: dict[str, dict] = defaultdict(lambda: {"papers": 0, "unique": 0, "eligible": 0})
    for info in by_paper.values():
        for s in info["sources"]:
            out[s]["papers"] += 1
            if len(info["sources"]) == 1:
                out[s]["unique"] += 1
            if info["eligible"]:
                out[s]["eligible"] += 1
    return dict(out)


def filter_distribution(db: Path, profile_id: str, start: datetime, end: datetime,
                        examples: int = 2) -> dict | None:
    """탈락 사유별 {count, examples}. 논문 단위(같은 논문의 반복 관측은 한 번).
    관측 없으면 None."""
    rows = _rows(db, profile_id, start, end)
    if not rows:
        return None
    seen: set[str] = set()
    counts: Counter = Counter()
    sample: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r["paper_key"] in seen or not r["filter_reason"]:
            continue
        seen.add(r["paper_key"])
        counts[r["filter_reason"]] += 1
        if len(sample[r["filter_reason"]]) < examples:
            sample[r["filter_reason"]].append(r["title"] or r["paper_key"])
    return {reason: {"count": n, "examples": sample[reason]} for reason, n in counts.most_common()}


def seed_attempts(db: Path, profile_id: str, start: datetime, end: datetime) -> dict | None:
    """씨앗별 시도 집계 {keyword: {scans, done, partial, failed, not_attempted, returned}}.
    scan_runs 가 없으면 None(미측정) — 관측 행이 없는 것과 스캔 기록이 없는 것은 다르다.
    이게 있어야 "0편"이 정상 0인지, 실패인지, 예산에 밀려 시도조차 못 한 것인지 갈린다."""
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT seed_attempts FROM scan_runs WHERE profile_id=? AND started_at >= ? AND started_at < ?",
            (profile_id, start.isoformat(), end.isoformat())).fetchall()
    if not rows:
        return None
    out: dict[str, dict] = defaultdict(lambda: {"scans": 0, "done": 0, "partial": 0, "failed": 0,
                                               "not_attempted": 0, "returned": 0})
    for (raw,) in rows:
        for a in (json.loads(raw) if raw else []):
            k = a["keyword"]
            out[k]["scans"] += 1
            out[k][a["status"]] = out[k].get(a["status"], 0) + 1
            out[k]["returned"] += int(a.get("returned") or 0)
    return dict(out)


def scan_health(db: Path, profile_id: str, start: datetime, end: datetime) -> dict | None:
    """기간 내 스캔 수와 관측 저장 상태. 없으면 None."""
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT observations, observation_error FROM scan_runs "
            "WHERE profile_id=? AND started_at >= ? AND started_at < ?",
            (profile_id, start.isoformat(), end.isoformat())).fetchall()
    if not rows:
        return None
    return {"scans": len(rows),
            "observed": sum(1 for o, e in rows if o is not None),
            "failed": sum(1 for o, e in rows if e),
            "incomplete": sum(1 for o, e in rows if o is None and not e)}


def format_signals(seed: dict | None, source: dict | None, filters: dict | None,
                   attempts: dict | None = None, health: dict | None = None) -> list[str]:
    """주간 리뷰에 붙일 줄들. 미측정은 미측정이라고 쓴다."""
    lines = ["■ 관측 신호 (스캔별 관측 기준 — 위 편수 표와 분모가 다르다)"]
    if health is None:
        lines.append("  관측 이력 없음 — 이 기간에는 스캔 기록을 남기지 않았다(2026-09-11 이전)")
        return lines
    lines.append(f"  스캔 {health['scans']}회 · 관측 저장 {health['observed']}회"
                 + (f" · 저장 실패 {health['failed']}회" if health['failed'] else "")
                 + (f" · 미완 {health['incomplete']}회" if health['incomplete'] else ""))
    if attempts:
        lines.append("  S2 씨앗별 시도:")
        for k, v in sorted(attempts.items(), key=lambda kv: -kv[1]["returned"]):
            parts = [f"{v['scans']}회 시도"]
            if v["done"]: parts.append(f"완료 {v['done']}")
            if v["partial"]: parts.append(f"부분 {v['partial']}")
            if v["failed"]: parts.append(f"실패 {v['failed']}")
            if v["not_attempted"]: parts.append(f"예산에 밀려 미시도 {v['not_attempted']}")
            lines.append(f"    {k}: " + " · ".join(parts) + f" · 반환 합계 {v['returned']}편")
    if seed:
        lines.append("  S2 씨앗별 수율 (논문 단위 — 같은 논문은 한 번):")
        for s, v in sorted(seed.items(), key=lambda kv: -kv[1]["returned"]):
            lines.append(f"    {s}: 반환 {v['returned']} · 그 씨앗만 {v['unique']} · "
                         f"적격 {v['eligible']} · 내용 자리 {v['content']}")
    elif seed is not None and attempts:
        lines.append("  S2 씨앗별 수율: 시도는 있었으나 관측된 후보 없음")
    if source:
        lines.append("  출처별 기여:")
        for s, v in sorted(source.items(), key=lambda kv: -kv[1]["papers"]):
            lines.append(f"    {s}: {v['papers']}편 · 이 출처만 {v['unique']} · 적격 {v['eligible']}")
    if filters:
        lines.append("  탈락 사유:")
        label = {research_profile.FILTER_EXCLUDE_HIT: "제외어 적중",
                 research_profile.FILTER_NO_CORE_HIT: "핵심 무적중",
                 research_profile.FILTER_ALREADY_SHOWN: "이미 보낸 논문"}
        for reason, v in filters.items():
            ex = " · ".join(t[:40] for t in v["examples"])
            lines.append(f"    {label.get(reason, reason)}: {v['count']}편{'  예: ' + ex if ex else ''}")
    return lines
