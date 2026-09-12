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
import re
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
    "min_seed_breadth": 2,    # 씨앗 축 후보의 최소 독립 씨앗 수 — 단일 씨앗 반복은 검색 잡음 진단으로만
}


def exploration_pool(db: Path, profile_id: str, start: datetime, end: datetime) -> list[dict]:
    """기간 안 관측에서 논문별 **최신 관측**을 먼저 고르고, 그 최신 상태가 no_core_hit
    인 논문만. 제외어에 걸린 것은 다른 사유(exclude_hit)라 여기 없다.

    순서가 중요하다 — no_core_hit 로 먼저 자르고 최신을 고르면 "월요일 탈락 → 수요일
    키워드 추가 → 금요일 적중"인 논문이 월요일 행으로 살아남는다(외부 검토 2026-09-12).
    이제는 잡히는 논문은 탐색 대상이 아니다. 반대(적중 → 탈락)는 들어온다.

    도메인 적중은 관측 시점 저장값(domain_hits)을 쓰고, 없는 옛 관측만 당시 스냅샷으로
    다시 센다. 초록은 abstract_ref 를 따라 복원한다(profile_impact 와 같은 규칙)."""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT o.*, s.profile_snapshot FROM candidate_observations o "
            "JOIN scan_runs s ON s.scan_id = o.scan_id "
            "WHERE o.profile_id=? AND o.observed_at >= ? AND o.observed_at < ? "
            "ORDER BY o.observed_at, o.scan_id, o.paper_key",
            (profile_id, start.isoformat(), end.isoformat()))]
        by: dict[str, dict] = {}
        for r in rows:                       # 뒤 행이 앞 행을 덮는다 = 최신 관측(사유 무관)
            cur = by.setdefault(r["paper_key"], {"seeds": set(), "sources": set()})
            cur["row"] = r
            cur["seeds"].update(json.loads(r["s2_seeds"]) if r["s2_seeds"] else [])
            srcs = json.loads(r["retrieval_sources"]) if r["retrieval_sources"] else [r["source"]]
            cur["sources"].update(s for s in srcs if s)
        pool = []
        for key, cur in by.items():
            r = cur["row"]
            if r["filter_reason"] != "no_core_hit":
                continue
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


_SEGMENT_RE = re.compile(r"[.;:!?\n]+")


def _grams(text: str) -> set[str]:
    """문장 경계를 넘는 조합을 만들지 않는다 — 제목과 초록을 이어 붙이면 'factory item.
    spiking sensor' 에서 'item spiking sensor' 가 생겨 진짜 용어를 흡수한다(실측 2026-09-12)."""
    grams: set[str] = set()
    for seg in _SEGMENT_RE.split(text):
        for n in (2, 3):
            grams.update(trend_report._ngrams(seg, n))
    return grams


def _generic(term: str) -> bool:
    return all(w in _GENERIC_WORDS for w in term.split())


def canonical(term: str) -> str:
    """표기 변형을 한 형태로 — 하이픈↔공백, 연속 공백, 마지막 낱말의 단순 복수형.
    'vision-language model' 과 'vision language models' 가 같아진다. 검색 매처를 바꾸는 게
    아니라 **제안 전 동치 판정**에만 쓴다 — recall 은 그대로고 오탐도 안 는다(2026-09-12,
    외부 검토: 매칭 결함이 만든 표기 변형에 shadow 검색 비용을 쓰면 안 된다)."""
    words = " ".join(term.lower().replace("-", " ").split()).split()
    if words:
        last = words[-1]
        if last.endswith("ies") and len(last) > 4:
            words[-1] = last[:-3] + "y"
        elif last.endswith("es") and len(last) > 4 and last[-3] in "sxz":
            words[-1] = last[:-2]
        elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
            words[-1] = last[:-1]
    return " ".join(words)


def _known_terms(profile: dict) -> set[str]:
    return {t.lower() for k in ("core_topics", "target_domain", "exclude") for t in (profile.get(k) or [])}


def is_variant_of_known(term: str, profile: dict) -> str | None:
    """기존 키워드·도메인·제외어의 표기 변형이면 그 원형을 돌려준다."""
    c = canonical(term)
    for k in ("core_topics", "target_domain", "exclude"):
        for t in profile.get(k) or []:
            if canonical(t) == c:
                return t
    return None


def discover(pool: list[dict], profile: dict, rules: dict | None = None) -> list[dict]:
    """후보 용어와 증거 논문. 전부 결정적 — 편수 → 도메인 적중 편수 → 씨앗 편수 → 최신 →
    용어 순. 현재 키워드·도메인·제외어와 포함 관계인 조합은 뺀다(그건 이미 아는 말이다).
    더 긴 조합에 그대로 들어 있는 짧은 조합은 뺀다(trend_report._subsumed)."""
    r = {**DEFAULT_RULES, **(rules or {})}
    known = _known_terms(profile)
    papers_of: dict[str, list[dict]] = defaultdict(list)
    for p in pool:
        for g in _grams(f"{p['title']}. {p['abstract']}"):
            if _generic(g) or any(k in g or g in k for k in known):
                continue
            papers_of[g].append(p)
    cand = [g for g, ps in papers_of.items() if len(ps) >= r["min_papers"]]
    cand = [g for g in cand if not trend_report._subsumed(g, set(cand))]
    # 기존 키워드의 표기 변형은 후보가 아니다 — 매칭 구멍은 여기서 제안으로 새지 않고
    # 주간 리뷰의 "표기 변형" 줄로만 보고한다(호출부가 variants 를 따로 받는다).
    variants = {g: is_variant_of_known(g, profile) for g in cand}
    cand = [g for g in cand if variants[g] is None]

    def dom_ratio(g: str) -> float:
        ps = papers_of[g]
        return sum(1 for p in ps if p["domain_hits"]) / len(ps)

    def seed_breadth(g: str) -> int:
        """서로 다른 씨앗 경로 수. "씨앗으로 들어왔는가"(비율)는 관련도가 아니라 S2 관련도 잡음의
        표시였다 — 실측 'foreign language' 4편 전부 한 씨앗(외부 검토 2026-09-12). 독립된 씨앗
        둘 이상에서 반복돼야 씨앗 축의 후보다."""
        return len({sd for p in papers_of[g] for sd in p["s2_seeds"]})

    def newest(g: str) -> int:
        return max((p["day"] for p in papers_of[g] if p["day"] is not None), default=0)

    # **차선(lane) 선발** — support 를 절대 1순위로 두면 잡음이 커질수록 범용 용어가
    # 위를 독식한다(실측 9/12: reinforcement learning · random forest). 가중합도 안 만든다.
    # 자리를 셋으로 나눠 각 자리를 다른 축의 1등에게 준다: ① support ② 도메인 연관 비율
    # ③ 씨앗 연관 비율·최신. 자리가 남으면(후보 부족·중복) support 순으로 채운다.
    lanes = [
        sorted(cand, key=lambda g: (-len(papers_of[g]), -dom_ratio(g), g)),
        sorted([g for g in cand if dom_ratio(g) > 0], key=lambda g: (-dom_ratio(g), -len(papers_of[g]), g)),
        sorted([g for g in cand if seed_breadth(g) >= r["min_seed_breadth"]],
               key=lambda g: (-seed_breadth(g), -newest(g), -len(papers_of[g]), g)),
    ]
    names = ("support", "domain", "seed")
    chosen: list[str] = []
    lane_of: dict[str, str] = {}
    for i in range(r["top_terms"]):
        li = i % len(lanes)
        pick = next((g for g in lanes[li] if g not in chosen), None)
        if pick is None and li != 0:          # 그 축에 후보가 없으면 자리를 버리지 않고 support 로
            li = 0
            pick = next((g for g in lanes[0] if g not in chosen), None)
        if pick is not None:
            chosen.append(pick)
            lane_of[pick] = names[li]         # 실제로 뽑힌 축 — 자리 번호가 아니다
    out = []
    for g in chosen:
        ps = papers_of[g]
        # 증거: 초록 있는 것 → 도메인 적중 → 최신 → 키. 모델이 볼 텍스트가 있어야 근거다.
        ev = sorted(ps, key=lambda p: (not p["abstract"], -len(p["domain_hits"]),
                                       -(p["day"] or 0), p["_paper_key"]))[:r["evidence_per_term"]]
        out.append({"term": g, "support": len(ps),
                    "domain_papers": sum(1 for p in ps if p["domain_hits"]),
                    "seed_papers": sum(1 for p in ps if p["s2_seeds"]),
                    "seed_breadth": seed_breadth(g),
                    "lane": lane_of[g],
                    "evidence": [{"key": p["_paper_key"], "title": p["title"],
                                  "abstract": snippet(p["abstract"], g, EVIDENCE_CHARS),
                                  "published": p["published"]} for p in ev]})
    return out


def single_seed_noise(pool: list[dict], profile: dict, rules: dict | None = None, top: int = 5) -> list[dict]:
    """한 씨앗에서만 반복된 미등록 용어 — 후보가 아니라 **검색 잡음 진단**이다. 그 씨앗이
    관련도 순위로 무엇을 끌어오는지 보여 주며, 씨앗 교체 판단의 재료가 된다(주간 리뷰 전용)."""
    r = {**DEFAULT_RULES, **(rules or {})}
    known = _known_terms(profile)
    seeds_of: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for p in pool:
        if not p["s2_seeds"]:
            continue
        for g in _grams(f"{p['title']}. {p['abstract']}"):
            if _generic(g) or any(k in g or g in k for k in known) or is_variant_of_known(g, profile):
                continue
            counts[g] += 1
            seeds_of[g].update(p["s2_seeds"])
    out = [{"term": g, "support": n, "seed": next(iter(seeds_of[g]))}
           for g, n in counts.items() if n >= r["min_papers"] and len(seeds_of[g]) == 1]
    out.sort(key=lambda x: (-x["support"], x["term"]))
    return out[:top]


def known_variants(pool: list[dict], profile: dict, rules: dict | None = None) -> list[dict]:
    """탐색 풀에서 발견됐지만 기존 키워드의 표기 변형이라 후보에서 뺀 것 — 주간 리뷰 보고용.
    (용어, 원형, 편수). 이건 키워드 매칭 구멍의 실측이다(§8-97 vision language models)."""
    r = {**DEFAULT_RULES, **(rules or {})}
    counts: dict[str, int] = defaultdict(int)
    for p in pool:
        for g in _grams(f"{p['title']}. {p['abstract']}"):
            counts[g] += 1
    out = []
    for g, n in counts.items():
        if n < r["min_papers"]:
            continue
        orig = is_variant_of_known(g, profile)
        if orig and g != orig.lower():
            out.append({"term": g, "known": orig, "support": n})
    out.sort(key=lambda x: (-x["support"], x["term"]))
    return out


EVIDENCE_CHARS = 500


def snippet(text: str, term: str, chars: int) -> str:
    """용어를 **가운데** 둔 조각. 앞 N 자를 자르면 용어가 초록 뒤쪽에만 있을 때 전송 텍스트에
    용어가 없어 R7(문자열 실재) 검증에서 죽는다(외부 검토 2026-09-12). 용어가 없으면 앞부터."""
    if not text or len(text) <= chars:
        return text or ""
    pat = profile_scoring._keyword_pattern(term)
    m = pat.search(text)
    if not m:
        return text[:chars]
    mid = (m.start() + m.end()) // 2
    start = max(0, min(mid - chars // 2, len(text) - chars))
    piece = text[start:start + chars]
    return ("…" if start > 0 else "") + piece + ("…" if start + chars < len(text) else "")


def format_discovery(terms: list[dict], pool_size: int | None,
                     variants: list[dict] | None = None, noise: list[dict] | None = None) -> list[str]:
    """주간 리뷰용 — 코드가 만든 절, 편수를 쓴다(메일에는 써도 된다, 프롬프트에는 안 간다)."""
    if pool_size is None:
        return ["▶ 키워드에 안 걸린 논문의 반복어: 관측 이력 없음 — 미측정"]
    lines = [f"▶ 키워드에 안 걸린 논문에서 반복된 말 (탐색 풀 {pool_size}편 · 제안기가 검토한다)"]
    if not terms:
        lines.append("   없음 — 3편 이상 반복된 미등록 조합이 없다")
    for t in terms:
        lines.append(f"   [{t.get('lane', '-')}] {t['term']}: {t['support']}편 · 도메인 적중 {t['domain_papers']}"
                     f" · 씨앗 {t.get('seed_breadth', 0)}종/{t['seed_papers']}편 · 예: {t['evidence'][0]['title'][:60] if t['evidence'] else '-'}")
    if variants:
        lines.append("   기존 키워드의 표기 변형이라 제안하지 않은 것 (키워드 매칭이 놓치는 표기):")
        for v in variants[:5]:
            lines.append(f"     {v['term']} ≈ {v['known']} · {v['support']}편")
    if noise:
        lines.append("   한 씨앗에서만 반복된 말 — 후보가 아니라 그 씨앗의 검색 잡음 진단:")
        for x in noise:
            lines.append(f"     {x['term']} · {x['support']}편 · 씨앗 '{x['seed']}'")
    return lines
