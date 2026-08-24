"""find_new_papers.py 단위 테스트 — server._throttled_arxiv_get만 모킹, 네트워크 없음.

test_summarize_chunking.py와 같은 패턴(monkeypatch로 HTTP 경계만 끊고 나머지
실제 로직은 그대로 돌린다) — asyncio.run()으로 감싸는 것도 같은 이유
(pytest-asyncio 미설치, 기존 관례)."""

import asyncio
from datetime import datetime, timezone

import find_new_papers
import server


def test_build_query_with_range_disabled_has_no_range_clause():
    """명시적으로 끄면(use_server_side_range=False) 절이 안 붙어야 한다 —
    라이브 검증 전 방어적으로 꺼둘 수 있는 경로는 남겨둔다."""
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    q = find_new_papers._build_query("agent", None, since, until, use_server_side_range=False)
    assert q == "all:agent"
    assert "submittedDate" not in q


def test_find_new_papers_since_defaults_to_server_side_range(monkeypatch):
    """2026-08-24 라이브 확인 완료(모듈 docstring 참고: 같은 조건에서 범위
    절 없이는 6페이지에도 status=partial, 켜면 3페이지에 status=done) —
    기본값이 True로 바뀐 걸 회귀 테스트로 고정해둔다."""
    async def fake_throttled(client, params):
        assert "submittedDate" in params["search_query"]  # 기본값으로 범위 절이 붙어야 함

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    def fake_parse(_xml_text):
        return []

    monkeypatch.setattr(server, "_throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(server, "_parse_arxiv_feed", fake_parse)

    async def main():
        since = datetime(2026, 8, 20, tzinfo=timezone.utc)
        return await find_new_papers.find_new_papers_since(None, "agent", since)  # 기본값 그대로 호출

    result = asyncio.run(main())
    assert "submittedDate" in result["query"]


def test_build_query_passes_through_already_field_qualified_query_unchanged():
    """run_profile_scan.py가 프로필 core_topics를 "all:agent OR all:\"digital
    twin\""처럼 이미 필드 한정자 붙여서 넘긴다 — 여기서 또 all:을 덧씌우면
    "all:all:agent"가 돼버리므로, 콜론이 있으면 그대로 통과시켜야 한다."""
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    already_qualified = 'all:agent OR all:"digital twin"'
    q = find_new_papers._build_query(already_qualified, None, since, until,
                                      use_server_side_range=False)
    assert q == already_qualified


def test_build_query_with_category_and_server_side_range():
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    q = find_new_papers._build_query("agent", "cs.AI", since, until, use_server_side_range=True)
    assert q == "(cat:cs.AI AND (all:agent)) AND submittedDate:[202608010000 TO 202608200000]"


def test_build_query_wraps_or_expression_before_anding_range_clause():
    """2026-08-24 실측으로 발견한 버그의 회귀 테스트: q에 OR이 있으면 괄호로
    감싸지 않고 그냥 "AND submittedDate:[...]"를 이어붙이면 arXiv 파서가
    OR의 앞쪽 항엔 날짜 제약을 안 건다(실측: 2026년 1월로 좁혀 요청했는데
    8월 논문이 나옴). 괄호로 감싸면 전체에 걸린다는 것까지 같은 방식으로
    재확인함."""
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 8, 20, tzinfo=timezone.utc)
    or_query = 'all:agent OR all:"digital twin"'
    q = find_new_papers._build_query(or_query, None, since, until, use_server_side_range=True)
    assert q == '(all:agent OR all:"digital twin") AND submittedDate:[202608010000 TO 202608200000]'


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
