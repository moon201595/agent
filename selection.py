"""② 중복 제거·선별 — 결정적 규칙, 네트워크·LLM 미사용.

검색 도구(arxiv_search_papers, s2_search_papers)가 돌려준 목록을 합쳐서
같은 논문을 하나로 만들고 고정된 규칙으로 정렬해 상위 k개만 남긴다.

이 단계를 LLM 판단에 맡기지 않는 이유: 선별 기준이 매 호출마다 달라지면
왜 이 논문이 뽑혔는지 사후에 설명할 수 없고, ⑧ 축적 단계에서 기준선이 안 생긴다.
인용수·연도는 기계가 비교할 수 있는 값이므로 코드가 정한다.

인용수는 Semantic Scholar 만 준다(arXiv API 는 주지 않음). 따라서 두 검색 결과를
함께 넣어야 랭킹이 의미 있어진다. Crossref 는 S2 가 이미 DOI·인용수를 주므로 쓰지 않는다.

파일명 주의: select.py 로 두면 표준 라이브러리 select 를 가려서
asyncio(selectors → select)가 깨진다. 그래서 selection.py 다.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# 빈 값으로 취급할 것들 — 병합 시 이런 값은 덮어쓴다
_EMPTY = (None, "", [], {})


def norm_title(title: str | None) -> str:
    """대소문자·구두점·공백 차이를 없앤 제목 키."""
    return _NON_ALNUM.sub(" ", (title or "").lower()).strip()


def _merge(base: dict, other: dict) -> dict:
    """같은 논문의 두 레코드를 합친다. 채워진 값이 빈 값을 이긴다."""
    out = dict(base)
    for key, value in other.items():
        if value in _EMPTY:
            continue
        if out.get(key) in _EMPTY:
            out[key] = value
    # 인용수·연도는 None 이 유효한 빈 값이므로 따로 챈다 (0 은 유효한 인용수다)
    for key in ("citation_count", "year"):
        if out.get(key) is None and other.get(key) is not None:
            out[key] = other[key]
    return out


def dedupe(papers: list[dict]) -> list[dict]:
    """arXiv ID 또는 정규화 제목이 겹치면 하나로 합친다.

    ID 로만 합치면 다른 소스에서 온 같은 논문을 놓치고(S2 는 arxiv_id 가 없을 수 있다),
    제목으로만 합치면 같은 논문의 개정판 제목이 달라졌을 때 놓친다. 그래서 둘 다 본다.
    입력 순서는 유지한다 — 정렬은 rank() 의 일이다.
    """
    merged: list[dict] = []
    by_id: dict[str, int] = {}
    by_title: dict[str, int] = {}

    for paper in papers:
        arxiv_id = str(paper.get("arxiv_id") or "").strip()
        title_key = norm_title(paper.get("title"))

        index = None
        if arxiv_id and arxiv_id in by_id:
            index = by_id[arxiv_id]
        elif title_key and title_key in by_title:
            index = by_title[title_key]

        if index is None:
            merged.append(dict(paper))
            index = len(merged) - 1
        else:
            merged[index] = _merge(merged[index], paper)

        if arxiv_id:
            by_id[arxiv_id] = index
        if title_key:
            by_title[title_key] = index

    return merged


def rank(papers: list[dict], top_k: int) -> list[dict]:
    """인용수 → 연도 내림차순. 인용수를 모르는 논문은 0으로 보고 뒤로 민다.

    '모름'을 0으로 취급하면 arXiv 단독 결과가 항상 밀린다. 이는 의도된 것이다 —
    인용수를 확인한 논문을 먼저 보여주는 편이 안전하고, S2 검색을 함께 넣으면 해소된다.
    """
    ordered = sorted(
        papers,
        key=lambda p: (p.get("citation_count") or 0, p.get("year") or 0),
        reverse=True,
    )
    return ordered[:top_k]


def dedupe_and_rank(papers: list[dict], top_k: int) -> dict:
    """②의 전체 동작. 각 단계 건수를 함께 돌려줘서 무엇이 걸러졌는지 보이게 한다."""
    merged = dedupe(papers)
    selected = rank(merged, top_k)
    return {
        "input_count": len(papers),
        "deduped_count": len(merged),
        "selected_count": len(selected),
        "papers": selected,
    }
