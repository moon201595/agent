"""find_new_papers.py 단위 테스트 — server._throttled_arxiv_get만 모킹, 네트워크 없음.

test_summarize_chunking.py와 같은 패턴(monkeypatch로 HTTP 경계만 끊고 나머지
실제 로직은 그대로 돌린다) — asyncio.run()으로 감싸는 것도 같은 이유
(pytest-asyncio 미설치, 기존 관례)."""

import asyncio
from datetime import datetime, timezone

import find_new_papers
import server


def test_build_query_default_has_no_range_clause():
    """use_server_side_range 기본 False — 아직 라이브로 검증 못 한 가설이라
    검증 전엔 안 켠다(모듈 docstring 참고)."""
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    q = find_new_papers._build_query("agent", None, since, until, use_server_side_range=False)
    assert q == "all:agent"
    assert "submittedDate" not in q


def test_build_query_with_category_and_server_side_range():
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    q = find_new_papers._build_query("agent", "cs.AI", since, until, use_server_side_range=True)
    assert q == "cat:cs.AI AND (all:agent) AND submittedDate:[202608010000 TO 202608200000]"


def test_find_new_papers_since_wires_collect_since_to_real_arxiv_helpers(monkeypatch):
    """server._throttled_arxiv_get/_parse_arxiv_feed만 가짜로 바꾸고, 나머지
    (쿼리 조립 → collect_since 페이지네이션·컷)는 실제 코드 경로 그대로 돈다."""
    since = datetime(2026, 8, 18, tzinfo=timezone.utc)
    pages = {
        0: [
            {"arxiv_id": "p1", "title": "new", "published": "2026-08-20T00:00:00Z"},
            {"arxiv_id": "p2", "title": "old", "published": "2026-08-15T00:00:00Z"},  # 경계 밖
        ],
    }
    starts_seen = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    def fake_parse(_xml_text):
        return pages[starts_seen[-1]]

    monkeypatch.setattr(server, "_throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(server, "_parse_arxiv_feed", fake_parse)

    async def main():
        return await find_new_papers.find_new_papers_since(
            None, "agent", since, page_size=10, max_pages=3,
        )

    result = asyncio.run(main())

    assert [p["arxiv_id"] for p in result["papers"]] == ["p1"]  # p2는 경계 밖이라 제외
    assert result["status"] == "done"
    assert result["pages_used"] == 1
    assert starts_seen == [0]  # 경계를 첫 페이지에서 만나 더 안 불렀다
    assert "query" in result and "since" in result and "until" in result
