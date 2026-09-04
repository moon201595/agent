"""find_new_papers.py — 오케스트레이터 프로토타입: ① 위에 delta 검색을 얹는다.

server.py 핵심은 건드리지 않는다 — arxiv_search_papers가 이미 하는 HTTP
호출·재시도·페이싱(http_client.throttled_arxiv_get)을 그대로 재사용하고, "언제
페이지네이션을 멈출지"만 delta_search.collect_since()가 결정한다. batch_
summarize.py가 server.py를 라이브러리로 직접 import해서 쓰는 것과 같은
패턴이다 — 이 파일도 아직 MCP 도구로 등록하지 않았다(오케스트레이터 계층
자체가 아직 없어서, 등록해도 부를 곳이 없다).

2026-08-24 설계 리뷰에서 나온 것: Semantic Scholar는 year_from(연 단위)만
지원해 day-level delta에 못 쓴다 — arXiv가 유일한 delta 발견 소스이고,
S2는 이미 발견된 후보의 인용수 보강에만 쓴다.

같은 리뷰에서 나온 가설(arXiv 쿼리의 submittedDate 범위 절로 서버 사이드
날짜 필터가 가능할 수 있다)은 arXiv API가 장시간 429를 반환해(이 세션에서
누적된 다른 검색 트래픽 때문으로 추정) 한동안 라이브 확인을 못 하고 있다가,
2026-08-24 같은 날 나중에 한도가 풀려 실측으로 확인했다 — 실제로 서버가
날짜로 걸러준다(쿼리 "all:agent AND submittedDate:[... TO ...]"로 5건을
받았고 전부 요청 범위 안이었다). find_new_papers_since()로도 재확인:
같은 조건에서 범위 절 없이는 페이지 6개(120건)를 받아도 경계를 못 만나
status="partial"이었던 게, 범위 절을 켜니 페이지 3개(50건)만에
status="done"으로 끝났다 — "agent"처럼 흔한 단어일수록 이 차이가 크다.
그래서 use_server_side_range 기본값을 True로 바꿨다. collect_since()의
클라이언트 사이드 컷은 그대로 남겨둔다 — "서버 응답을 무조건 믿지
않는다"는 전제는 성능 최적화가 생겼다고 없앨 이유가 아니다(delta_search.py
참고, 방어 비용은 이제 대부분 즉시 통과하므로 사실상 공짜).
"""

from __future__ import annotations

from datetime import datetime, timezone

import http_client
import httpx

import delta_search


def _build_query(
    query: str, category: str | None, since: datetime, until: datetime,
    use_server_side_range: bool,
) -> str:
    # query에 이미 필드 한정자(예: "all:agent OR all:\"digital twin\"")가 있으면
    # 그대로 쓰고, 없으면("agent" 같은 맨 단어) 기본 all: 을 붙인다 — 프로필의
    # core_topics를 OR로 조립한 완성된 쿼리를 그대로 넘길 수 있게 하기 위해서다
    # (run_profile_scan.py 참고). 콜론 유무로만 판단하는 건 단순하지만, arXiv
    # 필드 한정자가 전부 "이름:" 형태라 실용적으로 충분하다.
    q = query if ":" in query else f"all:{query}"
    if category:
        q = f"cat:{category} AND ({q})"
    if use_server_side_range:
        # 2026-08-24 실측으로 발견한 버그: q에 OR이 들어있으면(예: 프로필
        # core_topics를 OR로 조립한 쿼리) 괄호 없이 "AND submittedDate:[...]"를
        # 그냥 이어붙이면 arXiv 파서가 "all:agent OR (all:\"digital twin\" AND
        # submittedDate:[...])"로 해석한다 — OR의 앞쪽 항(agent)에는 날짜
        # 제약이 전혀 안 걸린다. 실제로 2026년 1월로 범위를 좁혀 요청했는데
        # 8월 논문이 나오는 것으로 재현·확인했다. q를 괄호로 감싸면
        # "(all:agent OR ...) AND submittedDate:[...]"가 돼 전체에 걸린다 —
        # 다시 같은 실측으로 고쳐진 것도 확인함(전부 요청 범위 안으로 나옴).
        q = f"({q}) AND {delta_search.to_arxiv_range_param(since, until)}"
    return q


async def find_new_papers_since(
    client: httpx.AsyncClient, query: str, since: datetime, category: str | None = None,
    page_size: int = 50, max_pages: int = 10, use_server_side_range: bool = True,
) -> dict:
    """since 이후 새로 나온 논문을 arXiv에서 찾는다.

    returns delta_search.collect_since()의 결과에 since/until/query를 더해
    돌려준다 — 호출부(미래의 Scheduler)가 search_runs 테이블에 그대로
    기록할 수 있는 모양(status/papers/pages_used + 검색 조건)이다.
    """
    until = datetime.now(timezone.utc)
    q = _build_query(query, category, since, until, use_server_side_range)

    async def fetch_page(start: int, size: int) -> list[dict]:
        resp = await http_client.throttled_arxiv_get(client, {
            "search_query": q, "start": start, "max_results": size,
            "sortBy": "submittedDate", "sortOrder": "descending",
        })
        return http_client.parse_arxiv_feed(resp.text)

    result = await delta_search.collect_since(fetch_page, since, page_size, max_pages)
    result["since"] = since.isoformat()
    result["until"] = until.isoformat()
    result["query"] = q
    return result
