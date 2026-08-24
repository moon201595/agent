"""run_profile_scan.py 통합 테스트 — server._throttled_arxiv_get만 모킹,
research_profile은 임시 SQLite로 실제 로직 그대로 돈다. 네트워크 없음."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import research_profile as rp
import run_profile_scan as rps
import server


def _setup_profile(db_path):
    rp.create_profile(
        db_path, "team_ai", "우리팀",
        core_topics=["agent", "digital twin"],
        target_domain=["robot hand"],
        exclude=["medical"],
        max_items=5,
    )


def test_arxiv_query_from_core_topics_quotes_multi_word_terms():
    q = rps._arxiv_query_from_core_topics(["agent", "digital twin"])
    assert q == 'all:agent OR all:"digital twin"'


def test_scan_profile_raises_clear_error_when_profile_missing(tmp_path):
    db_path = tmp_path / "t.db"

    async def main():
        return await rps.scan_profile(db_path, "nope", client=None)

    with pytest.raises(ValueError, match="없음"):
        asyncio.run(main())


def test_scan_profile_end_to_end_with_mocked_arxiv(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)

    # 절대 날짜를 쓰면 default_lookback_days=7 경계와 실제 실행 시각의 관계에
    # 따라 테스트가 통과/실패가 갈리는 시간 의존 취약점이 생긴다(실측: 이
    # 샌드박스 시계가 실제로 2026-08-24라 "2026-08-17"을 썼더니 since(now-7일)
    # 경계에 바로 걸려버렸다) — 그래서 항상 지금 기준 상대 날짜로 만든다
    # (test_profile_scoring.py의 recency 테스트와 같은 이유).
    now = datetime.now(timezone.utc)

    def _days_ago(n: int) -> str:
        return (now - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pages = {
        0: [
            {"arxiv_id": "p1", "title": "An agent for robot hand control",
             "abstract": "", "published": _days_ago(1)},
            {"arxiv_id": "p2", "title": "An agent framework, unrelated to domain",
             "abstract": "", "published": _days_ago(2)},
            {"arxiv_id": "p3", "title": "Medical agent diagnosis tool",  # exclude
             "abstract": "", "published": _days_ago(3)},
            {"arxiv_id": "p4", "title": "A database indexing survey",  # core 불일치
             "abstract": "", "published": _days_ago(4)},
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
        return await rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=3)

    result = asyncio.run(main())

    # p3(exclude)·p4(core 불일치)는 빠지고, p1이 domain 보너스로 p2보다 위
    assert [p["arxiv_id"] for p in result["papers"]] == ["p1", "p2"]
    assert result["excluded_count"] == 1
    assert result["unmatched_count"] == 1
    assert result["candidates_found"] == 4
    assert result["run_status"] == "done"

    # search_runs에 이번 실행이 기록됐는지 — done이었으니 다음 next_since는
    # 이번 실행의 until로 갱신돼야 한다(이전 since로 되돌아가면 안 됨)
    assert rp.next_since(db_path, "team_ai") == datetime.fromisoformat(result["until"])


def _mock_empty_arxiv(monkeypatch):
    """빈 결과만 주는 가장 단순한 mock — 다이제스트 저장 배선만 확인할 때 씀."""
    async def fake_throttled(client, params):
        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    monkeypatch.setattr(server, "_throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(server, "_parse_arxiv_feed", lambda _xml: [])


def test_scan_and_digest_saves_digest_to_db_not_just_return_value(tmp_path, monkeypatch):
    """2026-08-24: session_state가 아니라 DB에 남아야 cron이 만든 결과도
    review_app.py가 보여줄 수 있다 — 이 배선이 핵심이라 별도로 검증한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_empty_arxiv(monkeypatch)

    assert rp.get_latest_digest(db_path, "team_ai") is None  # 저장 전엔 없음

    async def main():
        return await rps.scan_and_digest(db_path, "team_ai", None, max_pages=2)

    result, digest_text = asyncio.run(main())

    assert "우리팀" in digest_text
    saved_text, saved_at = rp.get_latest_digest(db_path, "team_ai")
    assert saved_text == digest_text
    assert saved_at


def test_scan_all_profiles_isolates_failure_of_one_profile(tmp_path, monkeypatch):
    """프로필 하나가 실패해도(여기선 core_topics 없음) 나머지는 계속
    처리돼야 한다 — cron이 한 프로필의 설정 실수 때문에 전체를 멈추면 안
    된다는 게 scan_all_profiles의 핵심 설계."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)  # "team_ai" — 정상
    rp.create_profile(db_path, "broken", "깨진 프로필", core_topics=[])  # core_topics 없음 → 실패
    _mock_empty_arxiv(monkeypatch)

    async def main():
        return await rps.scan_all_profiles(db_path, None, max_pages=2)

    summary = asyncio.run(main())

    assert summary["team_ai"]["status"] == "ok"
    assert summary["broken"]["status"] == "error"
    assert "core_topics" in summary["broken"]["detail"]
    # 성공한 쪽은 다이제스트도 실제로 저장됐어야 함
    assert rp.get_latest_digest(db_path, "team_ai") is not None
    assert rp.get_latest_digest(db_path, "broken") is None
