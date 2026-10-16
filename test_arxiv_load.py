"""① 검색 — arXiv 요청 수를 줄이고 429 에 오래 버티지 않는다(2026-10-16).

그날 team_ai_advance 는 arXiv 질의 3개가 각각 30+60+120+240=450초를 기다리고도 전부 429 로 실패해 22분을 잃었다. arXiv 한도는 키 제도가
없고 IP 단위 요청 수로 걸린다. 10/15 하루 arXiv 결과 1,945편을 50편씩 받아 쪽 요청이 약 40개였다.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import delta_search
import find_new_papers
import http_client
import research_profile as rp
import scan_search

NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _paper(i, days_ago=0):
    return {"arxiv_id": f"2610.{i:05d}", "title": f"p{i}", "abstract": "",
            "published": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_scan_asks_arxiv_for_200_per_page_by_default(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: arXiv 쪽 크기를 다시 50 으로 돌려 같은 결과에 요청을 네 배 쓰거나, 호출부가 쪽 크기를 직접 주면
    그 값을 무시하면 실패한다."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "team_x", "x", core_topics=["robot policy"], max_items=5)
    seen = []

    async def fake_search(client, query, since, page_size=50, max_pages=10, **kw):
        seen.append(page_size)
        return {"papers": [], "status": "done", "until": NOW.isoformat(), "since": since.isoformat(), "query": query}

    monkeypatch.setattr(scan_search.find_new_papers, "find_new_papers_since", fake_search)
    monkeypatch.setattr(scan_search.s2_delta, "keywords_for_s2", lambda profile: [])
    asyncio.run(scan_search.scan_profile(db, "team_x", None, max_pages=2))
    assert seen == [scan_search.ARXIV_PAGE_SIZE] and scan_search.ARXIV_PAGE_SIZE == 200
    seen.clear()
    asyncio.run(scan_search.scan_profile(db, "team_x", None, page_size=50, max_pages=2))
    assert seen == [50]


def test_search_pages_give_up_on_arxiv_429_after_90_seconds(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 검색 단계가 arXiv 429 에 기존 상한(30+60+120+240초)까지 버티거나, 검색이 대기 상한을 넘기지 않거나,
    상한을 다른 arXiv 호출(본문 받기)에도 씌우면 실패한다."""
    kwargs = []

    async def fake_throttled(client, params, **kw):
        kwargs.append(kw)
        return httpx.Response(200, text="<feed></feed>", request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    asyncio.run(find_new_papers.find_new_papers_since(None, "all:robot", NOW - timedelta(days=1), page_size=200, max_pages=1))
    assert kwargs and kwargs[0].get("max_wait") == find_new_papers.SEARCH_RATE_LIMIT_MAX_WAIT == 90.0

    waits = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)
    request = httpx.Request("GET", "http://export.arxiv.org/api/query")

    async def always_429():
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("429", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http_client.with_retry(always_429, "arXiv API", max_wait=90.0))
    assert waits == [30.0, 60.0]                                    # 세 번째(120초)는 상한을 넘어 기다리지 않는다
    waits.clear()
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http_client.with_retry(always_429, "arXiv API"))
    assert waits == [30.0, 60.0, 120.0, 240.0]                      # 상한 없는 호출(본문 받기 등)은 그대로


def test_a_later_page_failure_keeps_earlier_pages_as_partial():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 뒤쪽 한 쪽의 실패로 앞쪽에서 받은 논문까지 버리거나(그전 동작), 그 경우를 done 으로 돌려 검색 창을
    전진시키거나(그 사이 논문을 영영 못 본다), 첫 쪽 실패를 빈 성공으로 숨기면 실패한다."""
    since = NOW - timedelta(days=3)

    async def pages(start, size):
        if start == 0:
            return [_paper(i) for i in range(size)]
        raise RuntimeError("429")

    got = asyncio.run(delta_search.collect_since(pages, since, page_size=2, max_pages=5))
    assert got["status"] == "partial" and [p["arxiv_id"] for p in got["papers"]] == ["2610.00000", "2610.00001"]

    async def first_fails(start, size):
        raise RuntimeError("429")

    with pytest.raises(RuntimeError):
        asyncio.run(delta_search.collect_since(first_fails, since, page_size=2, max_pages=5))


def test_arxiv_getter_forwards_the_wait_cap(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: throttled_arxiv_get 이 받은 max_wait 를 재시도 함수에 넘기지 않아 검색이 다시 450초씩 버티면 실패한다."""
    waits = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)          # 같은 asyncio 모듈 — 페이서의 3초 간격도 여기로 온다
    request = httpx.Request("GET", http_client.ARXIV_API)

    class Client:
        async def get(self, url, params=None, timeout=None):
            return httpx.Response(429, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http_client.throttled_arxiv_get(Client(), {"search_query": "all:x"}, max_wait=90.0))
    assert [w for w in waits if w >= 30] == [30.0, 60.0]                   # 429 대기만 — 페이서 간격(3초 이하)은 뺀다


def test_later_page_failure_is_recorded_and_the_window_is_searched_again(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P3): 뒤쪽 쪽 실패의 원인을 검색 기록에서 잃어 쪽 상한 partial 과 구분할 수 없게 하거나,
    그 실행을 done 으로 남겨 다음 실행의 검색 창이 그 구간을 건너뛰면 실패한다 — 두 번째 쪽 실패 → DB partial → 다음 since 유지를 한 흐름으로 본다."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "team_x", "x", core_topics=["robot policy"], max_items=5)
    pages = []

    async def fake_throttled(client, params, **kw):
        pages.append(params["start"])
        if params["start"] > 0:
            request = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
        entries = "".join(
            f"<entry><id>http://arxiv.org/abs/2610.{i:05d}v1</id><title>robot policy {i}</title><summary>s</summary>"
            f"<published>{(datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')}</published></entry>" for i in range(2))
        return httpx.Response(200, text=f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>', request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(scan_search.s2_delta, "keywords_for_s2", lambda profile: [])
    asyncio.run(scan_search.scan_profile(db, "team_x", None, page_size=2, max_pages=5))
    import sqlite3
    with sqlite3.connect(db) as con:
        status, window_from, error = con.execute(
            "SELECT status, window_from, error_detail FROM search_runs WHERE source='arxiv' ORDER BY started_at DESC").fetchone()
    assert pages == [0, 2] and status == "partial" and "2쪽 HTTPStatusError" in (error or "")
    assert rp.next_since(db, "team_x", "arxiv").isoformat()[:19] == datetime.fromisoformat(window_from).isoformat()[:19]
