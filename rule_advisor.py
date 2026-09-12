"""⑧ 축적 — 규칙 기반 프로필 제안기 R (E1, 2026-09-11). docs/ASTRA_PLAN §11.5.

F/R/A 비교의 R 팔이다. **A(LLM)와 같은 입력·같은 액션·같은 게이트**를 쓴다 —
그래야 공정 비교다(외부 점검 2026-09-11). 입력은 A 가 실제로 본
`sent_input_json`(대표 논문 제목·초록·키 + 현재 관심사)이지 전체 집계가
아니다. R 만 전체 후보의 빈도·수율을 받으면 "같은 신호" 조건이 깨진다.

규칙(알고리즘은 지금 고정, **수치 파라미터는 기준선 뒤 평가 시작 전에 고정**):
  1. 전송 논문에서 고유 논문 단위 2·3-gram 편수.
  2. 기존 core 와 같은 논문에서 동시 출현하고 그 core 가 guard 를 통과한 것만.
  3. 최소 지지 편수·동시 출현 비율을 넘는 용어를 편수 내림차순, 동률은 문자열순.
  4. 허용 계층 중 **가장 낮은** 계층에 `add_core_term`. 없으면 `no_change`.
  5. 출력은 D 의 `validate_proposals` 와 C 의 게이트를 그대로 지난다.

LLM 요청 0회 — 그 차이 자체가 비용 결과다. 운영 DB 에 적용하지 않는다.
"""
from __future__ import annotations

from collections import Counter

import profile_scoring
import trend_report

# 기준선 뒤 고정할 파라미터. 지금 값은 **초기값**이며 근거 실측 전이다.
DEFAULT_RULES = {"min_support": 2, "min_cooccurrence_ratio": 0.5, "max_proposals": 3,
    "min_exploration_evidence": 2,   # 탐색 용어의 최소 증거 편수 — min_support 와 별개
    "exploration_slots": 1,          # 탐색 후보에 예약하는 자리
}


def propose(sent: dict, profile: dict, rules: dict | None = None) -> dict:
    """A 와 같은 `sent`(profile_advisor.build_input 결과)에서 제안 JSON 을 만든다.
    반환 형식은 A 의 응답 계약과 같다 — 그래서 같은 검증기를 통과한다."""
    r = {**DEFAULT_RULES, **(rules or {})}
    core = list(profile.get("core_topics") or [])
    tiers = sorted({float(t) for t in sent.get("allowed_tiers") or []})
    if not tiers or not sent.get("papers"):
        return {"decision": "no_change", "proposals": [], "rule": "no_tiers_or_papers"}
    lowest = tiers[0]
    known = {k.lower() for k in core}
    support: Counter = Counter()
    cooc: Counter = Counter()
    evidence: dict[str, list[str]] = {}
    for p in sent["papers"]:
        text = f"{p['title']}. {p['abstract']}"
        hits = profile_scoring.score_paper({"title": p["title"], "abstract": p["abstract"]}, profile)["core_hits"]
        grams = set()
        for n in (2, 3):
            grams.update(trend_report._ngrams(text, n))
        for g in grams:
            if any(k in g or g in k for k in known):
                continue
            support[g] += 1
            if hits:                      # guard 를 통과한 core 와 같은 논문에 있다
                cooc[g] += 1
                evidence.setdefault(g, []).append(p["key"])
    cand = [g for g, n in support.items()
            if n >= r["min_support"] and cooc.get(g, 0) / n >= r["min_cooccurrence_ratio"]]
    cand = [g for g in cand if not trend_report._subsumed(g, set(cand))]
    cand.sort(key=lambda g: (-support[g], g))
    proposals = [{"action": "add_core_term", "term": g, "proposed_tier": lowest,
                  "evidence_paper_keys": evidence.get(g, [])[:5],
                  "reason": f"rule:support={support[g]},cooccurrence={cooc.get(g, 0)}",
                  "ambiguity_risks": []} for g in cand]
    # 탐색 차선(②, §8-97): A 와 같은 `sent["exploration"]` 을 R 도 본다 — 아니면 F/R/A 비교에서
    # A 만 탈락 후보를 보는 셈이다. 두 차선이 **같이 경쟁**한다 — 일반 차선이 먼저 3자리를
    # 채우면 탐색 후보는 경쟁도 못 한다(외부 검토 2026-09-12). 탐색 후보가 있으면 자리
    # 하나를 예약한다(exploration_slots). 문턱도 갈랐다 — 탐색 증거는 전체 풀의 편수가
    # 아니라 모델에 보낸 2편이라, min_support 를 올리면 탐색이 통째로 죽는다.
    import term_discovery
    explo = []
    for t in sent.get("exploration") or []:
        keys = [e["key"] for e in t.get("papers") or []]
        if t["term"].lower() in known or term_discovery.is_variant_of_known(t["term"], profile):
            continue                       # 기존 키워드의 표기 변형은 제안이 아니다
        if len(keys) < r["min_exploration_evidence"]:
            continue
        explo.append({"action": "add_core_term", "term": t["term"], "proposed_tier": lowest,
                      "evidence_paper_keys": keys[:5], "reason": f"rule:exploration,evidence={len(keys)}",
                      "ambiguity_risks": []})
    normal = [p for p in proposals if p["term"] not in {e["term"] for e in explo}]
    reserved = min(r["exploration_slots"], len(explo)) if explo else 0
    proposals = normal[:max(0, r["max_proposals"] - reserved)]
    proposals += explo[:r["max_proposals"] - len(proposals)]
    return {"decision": "propose" if proposals else "no_change", "proposals": proposals,
            "rule": "ngram-cooccurrence-v1", "params": r}
