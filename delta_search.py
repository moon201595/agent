"""delta_search.py — 오케스트레이터 프로토타입: "지난 실행 이후 새로 나온 논문만".

설계 문서 검토(2026-08-24)에서 나온 두 가지를 실측으로 확인하기 전에 먼저
바로잡는다:

1. Semantic Scholar 는 `year_from`(연 단위)만 지원해 "어제 이후" 급 delta에
   못 쓴다(server.py의 S2SearchInput 참고). 그래서 delta 발견은 arXiv 단독
   소스로만 한다 — S2는 발견된 후보의 인용수 보강에만 쓴다.
2. arXiv 검색은 `sortBy=submittedDate`로 **정렬**만 될 뿐 날짜로 **거르지는**
   않는다(server.py의 arxiv_search_papers 참고) — 그래서 "최신순으로 페이지를
   넘기다 경계에서 끊는" 클라이언트 사이드 로직이 필요하다고 가정했었다.
   그런데 arXiv API 자체가 검색 쿼리 안에 `submittedDate:[YYYYMMDDHHMM TO
   YYYYMMDDHHMM]` 범위 절을 지원한다(arXiv API User Manual) — 서버가 이미
   날짜로 걸러서 준다면 클라이언트 사이드 컷 로직은 "혹시 서버가 경계를
   정확히 안 지켰을 때의 방어" 정도로만 남고, 메인 로직은 훨씬 단순해진다.
   이 모듈은 그 가설이 맞다는 전제로 짜되, 실측 전까지는 방어적 컷
   함수(cut_before)를 반드시 같이 써서 서버 응답을 신뢰하지 않는다("서버는
   판단하지 않는다"와 같은 결 — 여기서는 "외부 API 응답을 무조건 믿지
   않는다"는 의미로).

이 파일은 순수 계산만 한다(네트워크 없음) — selection.py/hybrid_search.py와
같은 경계 원칙. 실제 HTTP 페이지네이션 루프는 server.py 쪽에 별도로 둔다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable


def parse_arxiv_date(raw: str) -> datetime:
    """arXiv Atom 피드의 published 필드("2026-08-19T13:22:10Z")를 datetime으로.

    형식이 항상 이 꼴이라는 보장은 문서상으로만 있고 실측 확인 전이라,
    실패하면 ValueError를 그대로 올린다 — 조용히 None을 돌려주면 호출부가
    "이 논문은 날짜를 모른다"를 "경계 밖이다"로 오해해 delta에서 통째로
    빠뜨릴 위험이 있다.
    """
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def to_arxiv_range_param(since: datetime, until: datetime) -> str:
    """datetime 두 개를 arXiv 쿼리용 submittedDate 범위 절 문자열로.

    arXiv API 는 YYYYMMDDHHMM(분 단위, 초 없음) 형식을 쓴다. since는 그대로
    내림, until은 분 단위로 올림하지 않는다 — 범위를 넓히는 쪽(올림)이 아니라
    좁히는 쪽(내림)으로 반올림해야 "다음 실행이 이 시각 이후를 다시 본다"는
    delta 경계 불변식이 안 깨진다(같은 초 안의 논문을 두 번 세는 것보다,
    아주 드물게 한 번 늦게 잡는 게 낫다 — 다음 실행이 어차피 그 구간을 다시
    본다).
    """
    fmt = "%Y%m%d%H%M"
    return f"submittedDate:[{since.strftime(fmt)} TO {until.strftime(fmt)}]"


def cut_before(papers: list[dict], since: datetime) -> tuple[list[dict], bool]:
    """papers(최신순으로 정렬돼 있다고 가정)에서 published >= since 인 것만 남긴다.

    returns (kept, boundary_hit) — boundary_hit=True면 이 페이지 안에서
    since보다 오래된 논문을 실제로 만났다는 뜻(더 오래된 페이지를 안 불러도
    됨). False면 이 페이지 전체가 since 이후였다는 뜻 — 다음 페이지도 더
    있는지 계속 봐야 한다(서버가 날짜로 걸러줬어도 상한 max_results 때문에
    이 페이지 전체가 여전히 경계 안일 수 있다).

    published 파싱에 실패한 항목은 버리지 않고 남긴다 — "날짜를 모른다"를
    "오래된 논문이라 제외"로 조용히 처리하면 실제로 새 논문인데 빠뜨릴
    위험이 더 크다(보수적으로 남기고, 다음 단계인 papers.db 중복 제거에서
    자연히 걸러지게 둔다).
    """
    kept: list[dict] = []
    boundary_hit = False
    for p in papers:
        raw = p.get("published", "")
        try:
            dt = parse_arxiv_date(raw)
        except ValueError:
            kept.append(p)
            continue
        if dt >= since:
            kept.append(p)
        else:
            boundary_hit = True
    return kept, boundary_hit


FetchPage = Callable[[int, int], Awaitable[list[dict]]]


async def collect_since(
    fetch_page: FetchPage, since: datetime, page_size: int = 50, max_pages: int = 10,
) -> dict:
    """fetch_page(start, page_size)로 최신순 페이지를 계속 받으며 since 이후
    논문만 모은다. 네트워크를 모른다 — server.py가 실제 arXiv 호출을 감싼
    콜백을 넘긴다(테스트에선 가짜 콜백으로 네트워크 없이 이 함수의 정지
    조건만 검증할 수 있다).

    server.py 쪽 검색 쿼리가 submittedDate 범위 절로 이미 서버 사이드
    필터링을 하는지 여부와 무관하게 항상 정답을 낸다:
    - 서버가 안 걸러줬다면 → 어느 페이지 안에서 since 이전 논문을 만나
      boundary_hit=True로 멈춘다.
    - 서버가 걸러줬다면 → 모든 페이지가 끝까지 kept이고, 마지막 페이지가
      page_size보다 짧게(또는 빈 페이지로) 와서 자연히 멈춘다.
    즉 서버 필터는 성능 최적화일 뿐 정확성은 이 함수가 클라이언트 사이드로
    보장한다 — "외부 API 응답을 무조건 믿지 않는다"는 이 모듈의 설계 전제.

    returns {"status": "done"|"partial", "papers": [...], "pages_used": int}
    "partial"은 max_pages를 다 썼는데도 경계를 못 만났다는 뜻 — 호출부가
    search_runs에 이 상태를 남겨 다음 실행에서 이어받아야 한다(설계 문서
    §3, 2026-08-19 리뷰에서 나온 요구사항).
    """
    collected: list[dict] = []
    for page_num in range(max_pages):
        start = page_num * page_size
        papers = await fetch_page(start, page_size)
        if not papers:
            return {"status": "done", "papers": collected, "pages_used": page_num}
        kept, boundary_hit = cut_before(papers, since)
        collected.extend(kept)
        if boundary_hit or len(papers) < page_size:
            return {"status": "done", "papers": collected, "pages_used": page_num + 1}
    return {"status": "partial", "papers": collected, "pages_used": max_pages}
