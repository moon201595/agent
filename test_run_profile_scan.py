"""run_profile_scan.py 통합 테스트 — server._throttled_arxiv_get만 모킹,
research_profile은 임시 SQLite로 실제 로직 그대로 돈다. 네트워크 없음."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import research_profile as rp
import run_profile_scan as rps
import server


@pytest.fixture(autouse=True)
def no_retraction_sweep(monkeypatch):
    """scan_and_digest 가 부르는 철회 따라잡기를 테스트에서 막는다.

    2026-09-02 실측 사고: 이 스텁 없이 돌렸더니 테스트 전체가 10초에서
    **232초**로 늘었고, 운영 DB(data/papers.db)의 is_retracted 49건이
    실제로 갱신됐다. sweep 이 **자기 httpx 클라이언트를 직접 만들기**
    때문에 테스트가 주입한 mock 을 우회한 것이다.

    교훈: 함수가 클라이언트를 스스로 만들면 테스트 주입 지점을 빠져나간다.
    sweep 자체는 test_retraction_sweep.py 가 임시 DB로 따로 검증한다.
    """
    async def _noop(limit: int = 20):
        return {"checked": 0, "resolved": 0, "retracted": 0, "remaining": 0}

    monkeypatch.setattr(server, "sweep_retraction_status", _noop)


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

    # search_runs에 이번 실행이 기록됐는지 — done이었으니 커서가 전진해야 하고
    # 이전 since로 되돌아가면 안 된다.
    #
    # 2026-09-01: 기대값을 "정확히 until"에서 "until 과 (지금-색인여유) 중 이른
    # 쪽"으로 옮겼다. 규칙이 바뀌었기 때문이다 — arXiv 색인이 며칠 뒤처져서,
    # until 까지 다 봤다고 기록해도 그 구간은 조회 시점에 아직 색인 전일 수
    # 있다. 그대로 전진하면 나중에 색인된 논문을 영영 못 본다(§8-26, 실측:
    # 2026-09-01 정기 실행이 사흘치를 지나쳤다). 주장 자체("되돌아가지 않는다")는
    # 그대로 두고 새 눈금으로 옮긴 것이다.
    now = datetime.now(timezone.utc)
    expected = min(datetime.fromisoformat(result["until"]),
                   now - timedelta(days=rp.REINDEX_SAFETY_DAYS))
    actual = rp.next_since(db_path, "team_ai")
    assert abs((actual - expected).total_seconds()) < 5
    assert actual > datetime.fromisoformat(result["since"])


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


# ---------------------------------------------------------------- M1: Deep Layer 연결


def _mock_arxiv_three_agent_papers(monkeypatch):
    """스코어링을 통과하는 논문 3편(전부 "agent" 포함, 최신순) — Deep Layer
    직렬 처리 검증용. recency를 하루씩 다르게 줘서 우선순위 정렬이
    p1→p2→p3 순으로 확정되게 한다(동점이면 순서가 불안정할 수 있어서)."""
    now = datetime.now(timezone.utc)

    def _days_ago(n):
        return (now - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")

    papers = [
        {"arxiv_id": "p1", "title": "agent paper one", "abstract": "", "published": _days_ago(1)},
        {"arxiv_id": "p2", "title": "agent paper two", "abstract": "", "published": _days_ago(2)},
        {"arxiv_id": "p3", "title": "agent paper three", "abstract": "", "published": _days_ago(3)},
    ]

    async def fake_throttled(client, params):
        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    monkeypatch.setattr(server, "_throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(server, "_parse_arxiv_feed", lambda _xml: papers)


def _run_scan_and_digest(db_path):
    async def main():
        return await rps.scan_and_digest(db_path, "team_ai", None, max_pages=2)

    return asyncio.run(main())


def test_deep_layer_processes_each_scored_paper_serially(tmp_path, monkeypatch):
    """(a) _process_paper 호출 횟수 == 스코어링 결과 논문 수(≤ max_items)."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda _aid: False)

    calls = []

    async def fake_process(client, arxiv_id, on_progress=None):
        calls.append(arxiv_id)
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    assert calls == ["p1", "p2", "p3"]  # 직렬 + 우선순위 순서 그대로
    assert result["scored_count"] == 3
    assert all(p["deep_status"] == "ok" for p in result["papers"])


def test_deep_layer_isolates_failure_of_one_paper(tmp_path, monkeypatch):
    """(b) 두 번째 논문이 예외를 던져도 세 번째가 처리되고, 실패 논문의
    deep_status에 실패 사유가 기록된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda _aid: False)

    calls = []

    async def fake_process(client, arxiv_id, on_progress=None):
        calls.append(arxiv_id)
        if arxiv_id == "p2":
            raise RuntimeError("Gemini·Groq 둘 다 실패: 테스트 예외")
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    assert calls == ["p1", "p2", "p3"]  # p2 실패에도 p3가 처리됨
    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    assert statuses["p1"] == "ok"
    assert statuses["p2"].startswith("failed:")
    assert "테스트 예외" in statuses["p2"]
    assert statuses["p3"] == "ok"


def test_deep_layer_never_calls_launch_background_directly(tmp_path, monkeypatch):
    """(c) ⑦ 트리거는 _process_paper 내부가 소유한다(CLAUDE.md 5) — 스캔
    경로가 launch_background를 직접 부르지 않음을 감시한다(_process_paper를
    mock한 상태이므로 호출이 있다면 스캔 경로 자신의 위반이다)."""
    import docker_runner

    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda _aid: False)

    lb_calls = []
    monkeypatch.setattr(docker_runner, "launch_background",
                         lambda aid: lb_calls.append(aid) or "mocked")

    async def fake_process(client, arxiv_id, on_progress=None):
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    _run_scan_and_digest(db_path)

    assert lb_calls == []


def test_deep_layer_skips_already_summarized_paper(tmp_path, monkeypatch):
    """(d) 이미 요약 저장된 논문은 _process_paper를 아예 안 부른다 —
    재호출하면 요약 단계가 무조건 재실행이라(실측 확인) 무료 API 한도를
    그대로 태우는 낭비다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda aid: aid == "p2")

    calls = []

    async def fake_process(client, arxiv_id, on_progress=None):
        calls.append(arxiv_id)
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    assert calls == ["p1", "p3"]  # p2는 스킵
    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    assert statuses["p2"].startswith("skipped:")


def test_deep_layer_records_fetch_failed_dict_as_failure(tmp_path, monkeypatch):
    """fetch 실패는 예외가 아니라 status="fetch_failed" dict로 온다(실측
    확인) — 이 경로도 failed로 기록돼야 한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda _aid: False)

    async def fake_process(client, arxiv_id, on_progress=None):
        if arxiv_id == "p1":
            return {"arxiv_id": arxiv_id, "status": "fetch_failed",
                    "detail": {"error": "HTML도 PDF도 없음"}}
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    assert statuses["p1"].startswith("failed:")
    assert statuses["p2"] == "ok"


# ---------------------------------------------------------------- M8: --all 경로 메일 발송


def test_scan_all_does_not_send_by_default(tmp_path, monkeypatch):
    """send=False(기본)면 메일 관련 코드를 아예 안 탄다 — 실수로 메일이
    나가는 사고를 막는다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")
    _mock_empty_arxiv(monkeypatch)

    calls = []
    monkeypatch.setattr(rps, "_deliver", lambda *a: calls.append(a) or "sent")

    async def main():
        return await rps.scan_all_profiles(db_path, None, max_pages=2)

    summary = asyncio.run(main())
    assert calls == []
    assert "delivery" not in summary["team_ai"]


def test_scan_all_sends_when_requested(tmp_path, monkeypatch):
    """cron이 쓰는 경로 — send=True면 프로필마다 발송하고 결과를 summary에
    남긴다(cron 로그만 보고 "메일이 나갔나"를 알 수 있어야 한다)."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_empty_arxiv(monkeypatch)
    monkeypatch.setattr(rps, "_deliver", lambda *a: "발송 완료 → 1명")

    async def main():
        return await rps.scan_all_profiles(db_path, None, max_pages=2, send=True)

    summary = asyncio.run(main())
    assert summary["team_ai"]["delivery"] == "발송 완료 → 1명"


def test_delivery_failure_does_not_stop_other_profiles(tmp_path, monkeypatch):
    """한 프로필의 SMTP 실패가 나머지 프로필의 스캔·발송을 막으면 안 된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.create_profile(db_path, "second", "두 번째", core_topics=["agent"])
    _mock_empty_arxiv(monkeypatch)

    def flaky(db, pid, result, text):
        if pid == "second":
            return "발송 완료 → 1명"
        raise RuntimeError("SMTP 죽음")

    monkeypatch.setattr(rps, "_deliver", flaky)

    async def main():
        return await rps.scan_all_profiles(db_path, None, max_pages=2, send=True)

    summary = asyncio.run(main())
    # 첫 프로필은 error로 기록되지만 두 번째는 정상 처리돼야 한다
    assert summary["second"]["delivery"] == "발송 완료 → 1명"


def test_deliver_skips_when_no_recipients(tmp_path, monkeypatch):
    """수신자가 없으면 조용히 넘어가는 게 아니라 그 사실을 문자열로 남긴다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    msg = rps._deliver(db_path, "team_ai", {"papers": []}, "본문")
    assert "수신자 없음" in msg


def test_deliver_reports_smtp_failure_without_raising(tmp_path, monkeypatch):
    """발송 실패를 예외로 올리면 나머지 프로필 처리가 멈춘다 — 문자열로 보고."""
    import email_delivery
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")

    def boom(*a, **kw):
        raise RuntimeError("SMTP 인증 실패")

    monkeypatch.setattr(email_delivery, "send_digest_email", boom)
    msg = rps._deliver(db_path, "team_ai", {"papers": []}, "본문")
    assert msg.startswith("발송 실패")
    assert "SMTP 인증 실패" in msg


def test_deliver_sends_even_with_zero_papers(tmp_path, monkeypatch):
    """논문 0편이어도 보낸다 — 매일 오는 메일 자체가 파이프라인이 살아 있다는
    증거이고, dead-man's switch를 안 붙인 지금 그 역할을 대신한다."""
    import email_delivery
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")

    sent = {}
    monkeypatch.setattr(email_delivery, "send_digest_email",
                         lambda t, s, r, h=None: sent.update(text=t, to=r))
    msg = rps._deliver(db_path, "team_ai", {"papers": [], "candidates_found": 0}, "빈 다이제스트")

    assert msg == "발송 완료 → 1명"
    assert sent["to"] == ["a@example.com"]


# ------------------------------------------- 이미 보낸 논문 제외 (§8-26)


def _seed_summary(monkeypatch, tmp_path, arxiv_ids):
    """server.DB_PATH 쪽 summaries 테이블에 '이미 요약됨'을 심는다.
    scan_profile 은 프로필 DB 가 아니라 server.DB_PATH 를 본다(_summary_exists
    와 같은 이유 — 운영에선 같은 파일이지만 테스트에선 다르다)."""
    import sqlite3
    sdb = tmp_path / "server.db"
    with sqlite3.connect(sdb) as con:
        con.execute("CREATE TABLE IF NOT EXISTS summaries ("
                    "arxiv_id TEXT PRIMARY KEY, path TEXT, "
                    "numbers_total INTEGER, numbers_matched INTEGER)")
        for aid in arxiv_ids:
            con.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?)",
                        (aid, "", 1, 1))
    monkeypatch.setattr(server, "DB_PATH", sdb)


def _mock_arxiv_pages(monkeypatch, papers):
    starts_seen = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    def fake_parse(_xml):
        return papers if starts_seen[-1] == 0 else []

    monkeypatch.setattr(server, "_throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(server, "_parse_arxiv_feed", fake_parse)


def _agent_paper(aid, days_ago):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"arxiv_id": aid, "title": f"An agent paper {aid}", "abstract": "", "published": ts}


def test_already_summarized_papers_are_dropped_before_ranking(tmp_path, monkeypatch):
    """실측 배경(2026-09-01): 색인 지연 때문에 매 실행이 최근 며칠을 다시
    조회하게 됐다(REINDEX_SAFETY_DAYS). 이미 요약한 논문을 안 빼면 어제
    메일에 나간 논문이 오늘 또 나간다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, ["p1", "p3"])
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in (1, 2, 3, 4)])

    async def main():
        return await rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=2)

    result = asyncio.run(main())

    assert [p["arxiv_id"] for p in result["papers"]] == ["p2", "p4"]
    assert result["already_seen_count"] == 2
    assert result["retrieved_count"] == 4      # arXiv 가 준 원본 건수
    assert result["candidates_found"] == 2     # 걸러진 뒤 실제 후보


def test_unsummarized_backlog_still_competes(tmp_path, monkeypatch):
    """아직 요약 안 된 논문은 그대로 둔다 — 어제 7위가 오늘 3위가 되는 건
    정상이고, 상위권이 빠지면서 밀린 후보가 며칠에 걸쳐 소진되는 구조다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])          # 아무것도 요약 안 됨
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in (1, 2)])

    async def main():
        return await rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=2)

    result = asyncio.run(main())
    assert {p["arxiv_id"] for p in result["papers"]} == {"p1", "p2"}
    assert result["already_seen_count"] == 0


def test_digest_reports_already_seen_count(tmp_path, monkeypatch):
    """후보 수가 왜 줄었는지 메일에서 설명이 돼야 한다."""
    import digest
    text = digest.generate_digest(
        {"papers": [], "candidates_found": 0, "already_seen_count": 7,
         "excluded_count": 1, "unmatched_count": 3}, "우리팀")
    assert "이미 보낸 논문 7건" in text
    assert "제외 규칙 1건" in text
    assert "조건 불일치 3건" in text


# ------------------------------------------- Deep Layer 시간 예산 (§8-14)


def test_deep_layer_stops_when_budget_is_exceeded(tmp_path, monkeypatch):
    """실측 배경: Gemini 가 막힌 날 Groq 폴백이 편당 약 25분이라(§8-15)
    max_items=6 이면 새벽 배치가 아침까지 안 끝난다. 편수가 아니라 시간으로
    자르는 이유는, 편당 비용이 엔진에 따라 24배까지 벌어지기 때문이다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in (1, 2, 3)])

    processed = []
    clock = {"t": 0.0}

    async def fake_process(client, arxiv_id):
        processed.append(arxiv_id)
        clock["t"] += 1000.0          # 논문 한 편에 1000초씩 걸린다고 치자
        return {"status": "done"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)
    monkeypatch.setattr(rps.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rps, "DEEP_LAYER_BUDGET_SECONDS", 1500.0)

    async def main():
        return await rps.scan_and_digest(db_path, "team_ai", None, max_pages=2)

    result, _digest = asyncio.run(main())

    # 1편(0초)·2편(1000초)까지는 시작하고, 3편째(2000초)에서 예산 초과
    assert processed == ["p1", "p2"]
    assert result["deferred_count"] == 1


def test_budget_is_checked_before_starting_not_mid_paper(tmp_path, monkeypatch):
    """처리 중간에 끊으면 요약을 반쯤 만들고 버리게 되는데, 그 호출은 이미
    무료 한도를 쓴 뒤다. 시작 전에만 본다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    started = []

    async def fake_process(client, arxiv_id):
        started.append(arxiv_id)
        return {"status": "done"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)
    monkeypatch.setattr(rps, "DEEP_LAYER_BUDGET_SECONDS", 0.0)   # 예산 0
    monkeypatch.setattr(rps.time, "monotonic", lambda: 0.0)      # 경과도 0

    async def main():
        return await rps.scan_and_digest(db_path, "team_ai", None, max_pages=2)

    result, _ = asyncio.run(main())
    # 경과 0 은 예산 0 을 "초과"하지 않는다 — 첫 편은 반드시 시작한다
    assert started == ["p1"]
    assert result.get("deferred_count", 0) == 0


def test_deferred_papers_are_not_listed_in_the_digest(tmp_path, monkeypatch):
    """요약이 없어 보여줄 내용이 없고, 내일 다시 후보로 올라와 그때 제대로
    실린다 — 오늘 제목만 내보내면 같은 논문이 이틀 연속 나간다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in (1, 2)])

    clock = {"t": 0.0}

    async def fake_process(client, arxiv_id):
        clock["t"] += 9999.0
        return {"status": "done"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)
    monkeypatch.setattr(rps.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rps, "DEEP_LAYER_BUDGET_SECONDS", 100.0)

    async def main():
        return await rps.scan_and_digest(db_path, "team_ai", None, max_pages=2)

    result, digest_text = asyncio.run(main())

    assert [p["arxiv_id"] for p in result["papers"]] == ["p1"]
    assert "시간 예산으로 내일로 미룸 1건" in digest_text


def test_budget_default_is_generous_enough_for_a_healthy_run(tmp_path, monkeypatch):
    """Gemini 가 정상이면 편당 1분 내외다(§8-15) — 기본 예산이 max_items 를
    한참 넘게 소화해야 평시에 아무것도 안 잘린다."""
    healthy_seconds_per_paper = 60
    assert rps.DEEP_LAYER_BUDGET_SECONDS / healthy_seconds_per_paper >= 20
