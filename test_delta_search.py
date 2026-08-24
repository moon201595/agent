"""delta_search.py 단위 테스트 — 네트워크 없이 돈다.

collect_since() 테스트는 asyncio.run()으로 감싼다 — pytest-asyncio가 설치돼
있지 않고(test_summarize_chunking.py와 같은 기존 관례), 이 프로젝트는
이 패턴을 이미 여러 곳에서 쓰고 있다.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from delta_search import collect_since, cut_before, parse_arxiv_date, to_arxiv_range_param


def test_parse_arxiv_date_roundtrip():
    dt = parse_arxiv_date("2026-08-19T13:22:10Z")
    assert dt == datetime(2026, 8, 19, 13, 22, 10, tzinfo=timezone.utc)


def test_parse_arxiv_date_rejects_unexpected_format():
    with pytest.raises(ValueError):
        parse_arxiv_date("2026-08-19")  # 시간 없음 — 실측 전 가정과 다른 형식


def test_to_arxiv_range_param_uses_minute_precision_no_seconds():
    since = datetime(2026, 8, 18, 5, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 8, 19, 5, 0, 30, tzinfo=timezone.utc)
    assert to_arxiv_range_param(since, until) == "submittedDate:[202608180500 TO 202608190500]"


def _paper(published: str, title: str = "x") -> dict:
    return {"arxiv_id": title, "title": title, "published": published}


def test_cut_before_keeps_all_when_every_paper_is_after_since():
    since = datetime(2026, 8, 18, tzinfo=timezone.utc)
    papers = [_paper("2026-08-20T00:00:00Z"), _paper("2026-08-19T00:00:00Z")]

    kept, boundary_hit = cut_before(papers, since)

    assert kept == papers
    assert boundary_hit is False  # 이 페이지 전체가 경계 안 — 다음 페이지도 봐야 함


def test_cut_before_stops_at_first_older_paper_and_reports_boundary():
    since = datetime(2026, 8, 18, tzinfo=timezone.utc)
    papers = [
        _paper("2026-08-20T00:00:00Z", "new1"),
        _paper("2026-08-19T00:00:00Z", "new2"),
        _paper("2026-08-15T00:00:00Z", "old1"),  # since 이전 — 여기서 경계
        _paper("2026-08-01T00:00:00Z", "old2"),
    ]

    kept, boundary_hit = cut_before(papers, since)

    assert [p["arxiv_id"] for p in kept] == ["new1", "new2"]
    assert boundary_hit is True


def test_cut_before_keeps_unparseable_dates_instead_of_dropping_silently():
    """날짜 형식이 실측과 다르면(예: arXiv가 포맷을 바꿈) 조용히 빠뜨리지 않고
    남겨서 다음 단계(papers.db 중복 제거)로 넘긴다 — false negative가
    "새 논문을 놓친다"는 더 나쁜 실패라서 보수적으로 남기는 쪽을 택했다."""
    since = datetime(2026, 8, 18, tzinfo=timezone.utc)
    papers = [_paper("이상한 형식", "weird")]

    kept, boundary_hit = cut_before(papers, since)

    assert kept == papers
    assert boundary_hit is False


def _dated(n: int, day: int) -> dict:
    return _paper(f"2026-08-{day:02d}T00:00:00Z", f"p{n}")


def test_collect_since_stops_when_boundary_found_mid_page():
    """서버가 날짜로 안 걸러준 경우 — 한 페이지 안에 새/오래된 논문이 섞여 온다."""
    since = datetime(2026, 8, 18, tzinfo=timezone.utc)
    page1 = [_dated(1, 20), _dated(2, 19), _dated(3, 15), _dated(4, 14)]  # 3부터 경계 밖
    calls = []

    async def fetch_page(start, size):
        calls.append(start)
        assert start == 0  # 경계가 첫 페이지에서 바로 잡히므로 더 안 불러야 함
        return page1

    async def main():
        return await collect_since(fetch_page, since, page_size=10, max_pages=5)

    result = asyncio.run(main())

    assert [p["arxiv_id"] for p in result["papers"]] == ["p1", "p2"]
    assert result["status"] == "done"
    assert result["pages_used"] == 1
    assert calls == [0]


def test_collect_since_stops_on_short_final_page_when_server_prefiltered():
    """서버가 이미 날짜로 걸러줬다고 가정 — 모든 페이지가 경계 안이고, 마지막
    페이지만 page_size보다 짧게 와서 자연히 멈춘다(경계를 직접 만나지 않음)."""
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    pages = {
        0: [_dated(i, 20) for i in range(3)],   # 3건, 꽉 참
        3: [_dated(i, 19) for i in range(3, 5)],  # 2건, page_size(3)보다 짧음 — 마지막
    }

    async def fetch_page(start, size):
        return pages.get(start, [])

    async def main():
        return await collect_since(fetch_page, since, page_size=3, max_pages=5)

    result = asyncio.run(main())

    assert len(result["papers"]) == 5
    assert result["status"] == "done"
    assert result["pages_used"] == 2


def test_collect_since_reports_partial_when_max_pages_exhausted():
    """경계도 못 만나고 짧은 페이지도 안 나온 채 max_pages를 다 씀 — 다음
    실행이 이어받아야 하는 상태(search_runs status='partial')."""
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)  # 아주 먼 과거라 절대 안 만남

    async def fetch_page(start, size):
        return [_dated(0, 20)] * size  # 항상 꽉 찬 페이지만 옴

    async def main():
        return await collect_since(fetch_page, since, page_size=5, max_pages=3)

    result = asyncio.run(main())

    assert result["status"] == "partial"
    assert result["pages_used"] == 3
    assert len(result["papers"]) == 15


def test_collect_since_handles_empty_first_page():
    async def fetch_page(start, size):
        return []

    async def main():
        return await collect_since(fetch_page, datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    page_size=10, max_pages=3)

    result = asyncio.run(main())

    assert result == {"status": "done", "papers": [], "pages_used": 0}
