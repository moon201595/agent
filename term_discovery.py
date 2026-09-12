"""⑧ 축적 — 탐색 차선: 키워드에 안 걸린 논문에서 후보 용어를 **로컬에서** 찾는다.

②단계(2026-09-12, PROGRESS §8-97). 용어 발견(trend_report.emerging_terms)과 제안기 입력
(profile_advisor.select_papers)이 **이미 키워드에 걸린 논문만** 봤다 — 9/11 관측 947행
중 no_core_hit 429편은 한 번도 안 읽혔다. 닫힌 고리다: 아는 말 근처에서만 제안이 나온다.

고치는 방향(외부 검토 2026-09-12): "논문 3편을 뽑아 그 안에서 신조어를 찾는다"가 아니라
**"탈락 후보 전체를 n-gram 으로 훑어 후보 용어를 만들고, 용어마다 증거 논문 2~3편만
제안기에 보낸다."** LLM 은 발견자가 아니라 검토자다. target_domain 적중은 진입 조건이
아니라 **가점** — 조건으로 두면 core 폐쇄 고리가 domain 폐쇄 고리로 바뀐다.

여기서 나가는 것은 용어 문자열과 논문 키·제목·초록뿐이다. 편수·비율은 메일(코드 생성)에만
쓰고 프롬프트에는 안 넣는다(규칙 4·R5).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import profile_impact
import profile_scoring
import trend_report

DISCOVERY_VERSION = "discovery-v1"
DEFAULT_RULES: dict = {
    "min_papers": 3,          # 이 편수 미만은 우연이다(emerging_terms 와 같은 기준)
    "top_terms": 3,           # 제안기에 보낼 용어 수
    "evidence_per_term": 2,   # 용어당 증거 논문 수
}


def exploration_pool(db: Path, profile_id: str, start: datetime, end: datetime) -> list[dict]:
    """기간 안 관측 중 **키워드에 안 걸려 탈락한** 논문(no_core_hit). 같은 논문은 최신 관측
    하나. 제외어에 걸린 것은 다른 사유(exclude_hit)라 여기 없다.

    도메인 적중은 관측 시점 저장값(domain_hits)을 쓰고, 없는 옛 관측만 당시 스냅샷으로
    다시 센다. 초록은 abstract_ref 를 따라 복원한다(profile_impact 와 같은 규칙)."""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT o.*, s.profile_snapshot FROM candidate_observations o "
            "JOIN scan_runs s ON s.scan_id = o.scan_id "
            "WHERE o.profile_id=? AND o.filter_reason='no_core_hit' "
            "AND o.observed_at >= ? AND o.observed_at < ? ORDER BY o.observed_at, o.scan_id, o.paper_key",
            (profile_id, start.isoformat(), end.isoformat()))]
        by: dict[str, dict] = {}
        for r in rows:                       # 뒤 행이 앞 행을 덮는다 = 최신 관측
            cur = by.setdefault(r["paper_key"], {"seeds": set(), "sources": set()})
            cur["row"] = r
            cur["seeds"].update(json.loads(r["s2_seeds"]) if r["s2_seeds"] else [])
            srcs = json.loads(r["retrieval_sources"]) if r["retrieval_sources"] else [r["source"]]
            cur["sources"].update(s for s in srcs if s)
        pool = []
        for key, cur in by.items():
            r = cur["row"]
            abstract, _ = profile_impact._restore_abstract(con, r)
            paper = {"_paper_key": key, "title": r["title"] or "", "abstract": abstract or "",
                     "published": r["published"]}
            if r.get("domain_hits") is not None:
                dom = json.loads(r["domain_hits"])
            else:
                dom = profile_scoring.raw_hits(paper, json.loads(r["profile_snapshot"]))["domain_hits"]
            day, _prec = profile_scoring.publication_day(r["published"])
            pool.append({**paper, "domain_hits": dom, "s2_seeds": sorted(cur["seeds"]),
                         "retrieval_sources": sorted(cur["sources"]), "day": day})
    pool.sort(key=lambda p: p["_paper_key"])
    return pool


# 우산 용어의 낱말들. 조합의 **모든** 낱말이 여기 있으면 주제어가 아니라 분야 이름이다
# ("large language models", "deep learning", "multimodal large language"). 낱말 하나라도
# 밖에 있으면 살린다 — "vision language models"·"reinforcement learning" 은 후보다.
# 실측(2026-09-12, 9/11 탈락 429편): 상위 12개 중 8개가 이런 우산 용어였다.
_GENERIC_WORDS = frozenset("""
large language model models llm llms machine learning deep artificial intelligence
neural network networks multimodal generative pretrained pre trained transformer transformers mllm mllms
""".split())


def _generic(term: str) -> bool:
    return all(w in _GENERIC_WORDS for w in term.split())


def _known_terms(profile: dict) -> set[str]:
    return {t.lower() for k in ("core_topics", "target_domain", "exclude") for t in (profile.get(k) or [])}


def discover(pool: list[dict], profile: dict, rules: dict | None = None) -> list[dict]:
    """후보 용어와 증거 논문. 전부 결정적 — 편수 → 도메인 적중 편수 → 씨앗 편수 → 최신 →
    용어 순. 현재 키워드·도메인·제외어와 포함 관계인 조합은 뺀다(그건 이미 아는 말이다).
    더 긴 조합에 그대로 들어 있는 짧은 조합은 뺀다(trend_report._subsumed)."""
    r = {**DEFAULT_RULES, **(rules or {})}
    known = _known_terms(profile)
    papers_of: dict[str, list[dict]] = defaultdict(list)
    for p in pool:
        text = f"{p['title']}. {p['abstract']}"
        grams: set[str] = set()
        for n in (2, 3):
            grams.update(trend_report._ngrams(text, n))
        for g in grams:
            if _generic(g) or any(k in g or g in k for k in known):
                continue
            papers_of[g].append(p)
    cand = [g for g, ps in papers_of.items() if len(ps) >= r["min_papers"]]
    cand = [g for g in cand if not trend_report._subsumed(g, set(cand))]

    def stats(g: str) -> tuple:
        ps = papers_of[g]
        dom = sum(1 for p in ps if p["domain_hits"])
        seed = sum(1 for p in ps if p["s2_seeds"])
        newest = max((p["day"] for p in ps if p["day"] is not None), default=0)
        return (-len(ps), -dom, -seed, -newest, g)

    cand.sort(key=stats)
    out = []
    for g in cand[:r["top_terms"]]:
        ps = papers_of[g]
        # 증거: 초록 있는 것 → 도메인 적중 → 최신 → 키. 모델이 볼 텍스트가 있어야 근거다.
        ev = sorted(ps, key=lambda p: (not p["abstract"], -len(p["domain_hits"]),
                                       -(p["day"] or 0), p["_paper_key"]))[:r["evidence_per_term"]]
        out.append({"term": g, "support": len(ps),
                    "domain_papers": sum(1 for p in ps if p["domain_hits"]),
                    "seed_papers": sum(1 for p in ps if p["s2_seeds"]),
                    "evidence": [{"key": p["_paper_key"], "title": p["title"], "abstract": p["abstract"],
                                  "published": p["published"]} for p in ev]})
    return out


def format_discovery(terms: list[dict], pool_size: int | None) -> list[str]:
    """주간 리뷰용 — 코드가 만든 절, 편수를 쓴다(메일에는 써도 된다, 프롬프트에는 안 간다)."""
    if pool_size is None:
        return ["▶ 키워드에 안 걸린 논문의 반복어: 관측 이력 없음 — 미측정"]
    lines = [f"▶ 키워드에 안 걸린 논문에서 반복된 말 (탐색 풀 {pool_size}편 · 제안기가 검토한다)"]
    if not terms:
        lines.append("   없음 — 3편 이상 반복된 미등록 조합이 없다")
    for t in terms:
        lines.append(f"   {t['term']}: {t['support']}편 · 도메인 적중 {t['domain_papers']} · 씨앗 유입 {t['seed_papers']}"
                     f" · 예: {t['evidence'][0]['title'][:60] if t['evidence'] else '-'}")
    return lines
