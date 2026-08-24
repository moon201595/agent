"""profile_scoring.py — Fast Layer 2차 스코어링 (신설, selection.py 확장 아님).

2026-08-24 설계 리뷰에서 확인한 것들을 전제로 짰다:

1. selection.py의 rank()는 citation_count·year만 본다 — 이 모듈이 제안하는
   relevance/domain_hit/venue/recency 가중합은 코드에 전혀 없던 것이라
   "확장"이 아니라 옆에 새로 얹는 레이어다. selection.py는 그대로 두고,
   이 모듈은 selection.dedupe()가 이미 병합해 준 논문 목록을 입력으로 받는다
   (같은 결로: 이 모듈도 dedupe는 하지 않는다).

2. relevance 판단에 임베딩(Gemini API 호출)을 쓰지 않는다. Fast Layer는
   매일 신규 후보 수십~수백 편을 스코어링해야 하는데, 임베딩도 API
   호출이라 ④ 요약과 같은 무료 티어 한도를 나눠 쓰게 된다("임베딩이라
   LLM 호출이 아니다"는 리뷰에서 스스로 정정한 오류). 대신 프로필의
   core_topics/target_domain/exclude 키워드를 제목+초록에 대해 단어
   경계 정규식으로 매칭한다 — selection.py와 같은 이유(판단이 매번
   달라지면 사후 설명이 안 된다)로 결정론적 쪽을 택했다. 나중에 GPU
   임베딩이 자리 잡으면(§9 Compute Router 확인 후) relevance 항목만
   교체하면 되게 인터페이스를 분리해뒀다(score_paper의 반환값에 매칭된
   키워드 목록을 그대로 남겨 "왜 이 점수인지"가 항상 설명 가능하게 함).

3. venue_score는 기본 0/None이다 — s2_search_papers가 지금 요청하는
   fields에 venue가 빠져 있어(server.py 확인) 이 데이터 자체가 없다.
   paper dict에 "venue" 키가 있으면만 매칭을 시도한다 — 문자열 완전일치가
   아니라 프로필의 venue 이름이 S2가 주는 venue 문자열에 부분 포함되는지로
   본다(예: 프로필 "IEEE TII" vs S2 "IEEE Transactions on Industrial
   Informatics"는 지금 방식으론 안 걸린다 — venue 매칭 자체가 아직 미해결
   문제라는 걸 반환값의 venue_hit=None으로 구분해 남긴다).

exclude 매칭은 다른 항목보다 먼저 본다 — 하나라도 걸리면 나머지 계산 없이
바로 제외한다(우선순위 설계, selection.py의 "판단 기준이 고정돼야 사후
설명이 된다" 원칙과 같은 이유로 exclude가 core_topics를 항상 이긴다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _keyword_pattern(keyword: str) -> re.Pattern:
    """다단어 키워드("digital twin")도 통째로 단어 경계 매칭한다. 순수
    substring 매칭을 안 쓰는 이유: "AI"가 "domain" 안에 우연히 들어있는
    것 같은 거짓 양성을 막기 위해서다."""
    pat = _WORD_RE_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
        _WORD_RE_CACHE[keyword] = pat
    return pat


def _find_hits(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if _keyword_pattern(kw).search(text)]


def _paper_text(paper: dict) -> str:
    return f"{paper.get('title') or ''} {paper.get('abstract') or ''}"


def recency_score(published: str | None, half_life_days: float) -> float | None:
    """published(ISO 8601, 'Z' 종료)를 오늘 기준 지수 감쇠 점수(0~1)로.
    파싱 실패(형식이 다르거나 S2처럼 연도만 있는 경우)는 None을 돌려준다 —
    호출부가 "모른다"를 "오래됐다(0점)"로 오인해 불이익을 주지 않도록,
    None은 가중합에서 그냥 빠진다(0을 더하는 게 아니라 항 자체가 없어짐).
    """
    if not published:
        return None
    try:
        dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    if age_days < 0:
        age_days = 0
    return 0.5 ** (age_days / half_life_days)


def venue_hit(paper: dict, profile_venues: list[str]) -> bool | None:
    """venue 매칭 — 데이터가 아예 없으면(None) "안 맞음(False)"과 구분한다.
    지금 s2_search_papers는 venue 필드를 안 주므로(server.py 확인,
    2026-08-24) 실전에서는 대부분 None이 나온다 — fields 확장이 먼저
    필요하다는 걸 이 반환값이 드러낸다."""
    venue = paper.get("venue")
    if not venue or not profile_venues:
        return None
    venue_lower = venue.lower()
    return any(v.lower() in venue_lower for v in profile_venues)


@dataclass
class Weights:
    core_topic: float = 1.0     # core_hits 비율에 곱함
    domain_hit: float = 0.3     # target_domain 매칭 1건당
    venue_hit: float = 0.3      # venue 매칭 시 고정 가점
    recency: float = 0.4        # recency_score(0~1)에 곱함
    recency_half_life_days: float = 30.0


def score_paper(paper: dict, profile: dict, weights: Weights = Weights()) -> dict:
    """returns 점수 breakdown — priority 숫자 하나만이 아니라 core_hits 등
    매칭된 키워드 목록을 항상 같이 돌려준다. "왜 이 논문이 위에 있는지"를
    사후에 설명할 수 있어야 한다는 이 프로젝트의 반복된 설계 원칙(②
    selection.py, ⑤ 검증기 grounding과 같은 결)을 여기서도 지킨다.
    """
    text = _paper_text(paper)

    exclude_hits = _find_hits(text, profile.get("exclude", []))
    if exclude_hits:
        return {"priority": 0.0, "excluded": True, "exclude_hits": exclude_hits,
                "core_hits": [], "domain_hits": [], "venue_hit": None, "recency": None}

    core_topics = profile.get("core_topics", [])
    core_hits = _find_hits(text, core_topics)
    if core_topics and not core_hits:
        # core_topics는 OR 조건 — 프로필 설명(설계 문서 §1)과 같다. 하나도
        # 안 걸리면 이 프로필과 무관한 논문으로 보고 0점 처리(제외는 아님 —
        # exclude와 구분해서, 호출부가 "그냥 순위가 낮다"와 "명시적으로
        # 걸러졌다"를 구분할 수 있게 excluded=False로 둔다).
        return {"priority": 0.0, "excluded": False, "exclude_hits": [],
                "core_hits": [], "domain_hits": [], "venue_hit": None, "recency": None}

    domain_hits = _find_hits(text, profile.get("target_domain", []))
    v_hit = venue_hit(paper, profile.get("venues", []))
    recency = recency_score(paper.get("published"), weights.recency_half_life_days)

    relevance = (len(core_hits) / len(core_topics)) if core_topics else 0.0
    priority = relevance * weights.core_topic
    priority += len(domain_hits) * weights.domain_hit
    if v_hit:
        priority += weights.venue_hit
    if recency is not None:
        priority += recency * weights.recency

    return {
        "priority": round(priority, 4), "excluded": False,
        "exclude_hits": [], "core_hits": core_hits, "domain_hits": domain_hits,
        "venue_hit": v_hit, "recency": recency,
    }


def score_and_rank(
    papers: list[dict], profile: dict, weights: Weights = Weights(), top_k: int | None = None,
) -> dict:
    """selection.dedupe_and_rank()와 같은 모양의 출력 — 각 단계 건수를 같이
    돌려줘서 무엇이 걸러졌는지 보이게 한다(같은 설계 원칙)."""
    scored = []
    excluded_count = 0
    unmatched_count = 0
    for p in papers:
        result = score_paper(p, profile, weights)
        if result["excluded"]:
            excluded_count += 1
            continue
        if result["priority"] == 0.0 and not result["core_hits"]:
            unmatched_count += 1
            continue
        scored.append({**p, "_score": result})

    scored.sort(key=lambda p: p["_score"]["priority"], reverse=True)
    if top_k is not None:
        scored = scored[:top_k]

    return {
        "input_count": len(papers),
        "excluded_count": excluded_count,
        "unmatched_count": unmatched_count,
        "scored_count": len(scored),
        "papers": scored,
    }
