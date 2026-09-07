"""run_profile_scan.py 통합 테스트 — server._throttled_arxiv_get만 모킹,
research_profile은 임시 SQLite로 실제 로직 그대로 돈다. 네트워크 없음."""

import http_client
import storage
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


@pytest.fixture(autouse=True)
def no_s2_search(monkeypatch):
    """scan_profile 이 부르는 S2 검색을 테스트에서 막는다.

    2026-09-02: 철회 sweep 과 **똑같은 사고**를 한 번 더 냈다. S2 를 두 번째
    소스로 붙이자마자 테스트가 실제 API 를 치기 시작해 7분을 넘겼다.
    s2_delta 도 server._throttled_s2_get 을 거쳐 자기 요청을 만들기 때문에
    테스트가 주입한 arXiv mock 을 우회한다.

    **패턴이 반복된다**: 스캔 경로에 외부 호출을 추가할 때마다 이 파일의
    격리를 같이 늘려야 한다. 새 소스를 붙이면 여기 스텁도 같이 추가할 것.
    S2 델타 로직 자체는 test_s2_delta.py 가 네트워크 없이 따로 검증한다.
    """
    async def _empty(client, keywords, since, until, limit=100):
        return {"papers": [], "status": "skipped", "query": "(테스트 스텁)",
                "keywords_failed": 0}

    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", _empty)


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

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", fake_parse)

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

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _xml: [])


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

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _xml: papers)


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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
        calls.append(arxiv_id)
        if arxiv_id == "p2":
            raise RuntimeError("Gemini·Groq 둘 다 실패: 테스트 예외")
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    assert calls == ["p1", "p2", "p3"]  # p2 실패에도 p3가 처리됨
    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    assert statuses["p1"] == "ok"
    # 2026-09-06: 수집에 실패한 논문은 **내용 자리를 내놓고 각주로 내려간다.**
    # 예전엔 실패한 채로 번호 붙은 자리에 남아 "처리 실패" 한 줄을 실었다.
    # 실패 사유를 기록한다는 계약은 그대로이고, 실리는 자리만 바뀌었다.
    assert "p2" not in statuses
    demoted = {p["arxiv_id"]: p["deep_status"] for p in result["title_only_papers"]}
    assert demoted["p2"].startswith("failed:")
    assert "테스트 예외" in demoted["p2"]
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
        if arxiv_id == "p1":
            return {"arxiv_id": arxiv_id, "status": "fetch_failed",
                    "detail": {"error": "HTML도 PDF도 없음"}}
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    demoted = {p["arxiv_id"]: p["deep_status"] for p in result["title_only_papers"]}
    assert demoted["p1"].startswith("failed:")   # 각주로 내려간다(2026-09-06)
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
    # 실제 스키마를 쓴다(2026-09-04, §8-52) — storage 가 유일한 소유자다.
    storage.init_storage(sdb)
    with sqlite3.connect(sdb) as con:
        for aid in arxiv_ids:
            con.execute("INSERT OR REPLACE INTO summaries "
                        "(arxiv_id, path, numbers_total, numbers_matched) VALUES (?,?,?,?)",
                        (aid, "", 1, 1))
    monkeypatch.setattr(server, "DB_PATH", sdb)
    # 경로 소유자가 storage 로 옮겨갔다(2026-09-04) — 둘 다 패치해야
    # server 도구와 digest·review_core 양쪽이 같은 임시 DB 를 본다.
    monkeypatch.setattr(storage, "DB_PATH", sdb)


def _mock_arxiv_pages(monkeypatch, papers):
    starts_seen = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    def fake_parse(_xml):
        return papers if starts_seen[-1] == 0 else []

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", fake_parse)


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


def _ranked(*specs):
    """(id, priority, core_hits) 로 순위 목록을 만든다."""
    return [{"arxiv_id": a, "title": a,
             "_score": {"priority": pr, "core_hits": list(hits)}}
            for a, pr, hits in specs]


def test_one_keyword_cannot_take_every_slot():
    """**실측 회귀**(2026-09-06). 채점을 고쳐 상위 6칸을 전부 본문 확보된 ★★
    논문으로 만들었더니, 그 6편이 **전부 같은 키워드**('defect detection')였다.
    한 키워드가 한 창에 26편을 데려오기 때문이다. 매일 한 주제만 담긴 메일은
    "이 분야가 어디로 가는가"를 못 말한다."""
    ranked = _ranked(*[(f"d{i}", 0.98 - i * 0.001, ["defect detection"]) for i in range(8)],
                     *[(f"r{i}", 0.70 - i * 0.001, ["robot learning"]) for i in range(3)])
    out = rps._spread_keywords(ranked, max_items=6)[:6]
    kinds = [p["_score"]["core_hits"][0] for p in out]
    assert kinds.count("defect detection") == 3        # 절반까지만
    assert kinds.count("robot learning") == 3


def test_spread_counts_the_heaviest_keyword_not_the_first():
    """`_spread_keywords` 는 `primary_hit` 을 쓴다 — 표적어를 맞힌 논문이
    동향어 이름으로 세어지면 그 표적어의 상한이 안 깎인다(2026-09-06 지적).
    """
    def q(key, pr, hits, primary):
        return {"arxiv_id": key, "title": key,
                "_score": {"priority": pr, "core_hits": list(hits), "primary_hit": primary}}
    # 넷 다 defect detection 이 표적어지만 core_hits 첫 번째는 제각각이다.
    ranked = [q("p0", 0.9, ["defect detection"], "defect detection"),
              q("p1", 0.8, ["embodied AI", "defect detection"], "defect detection"),
              q("p2", 0.7, ["NPU", "defect detection"], "defect detection"),
              q("p3", 0.6, ["robot learning"], "robot learning")]
    out = rps._spread_keywords(ranked, max_items=4)[:4]
    ids = [x["arxiv_id"] for x in out]
    assert ids[:2] == ["p0", "p1"]        # 상한 2칸(4의 절반)까지
    assert ids[2] == "p3"                 # p2 는 상한에 걸려 뒤로
    assert ids[3] == "p2"


def test_spread_falls_back_to_first_hit_for_old_scores():
    """`primary_hit` 이 없는 구형·수기 `_score` 는 옛 동작(첫 번째 적중)으로
    떨어진다 — 하위 호환."""
    old = {"arxiv_id": "x", "title": "x",
           "_score": {"priority": 0.9, "core_hits": ["defect detection"]}}
    assert rps._primary_keyword(old) == "defect detection"
    assert rps._primary_keyword({"_score": {"priority": 0.1}}) == ""


def test_spread_invents_no_diversity_that_is_not_there():
    """다른 키워드가 없으면 상한을 넘겨서라도 채운다 — 없는 다양성을
    지어내려고 자리를 비우지 않는다."""
    ranked = _ranked(*[(f"d{i}", 0.9 - i * 0.01, ["defect detection"]) for i in range(6)])
    out = rps._spread_keywords(ranked, max_items=6)
    assert [p["arxiv_id"] for p in out] == [f"d{i}" for i in range(6)]


def test_spread_keeps_relative_order_within_a_keyword():
    """뒤로 미룬 논문끼리의 순서는 그대로다 — 순위를 다시 매기지 않는다."""
    ranked = _ranked(*[(f"d{i}", 0.9 - i * 0.01, ["defect detection"]) for i in range(5)],
                     ("r0", 0.5, ["robot learning"]))
    out = rps._spread_keywords(ranked, max_items=4)
    assert [p["arxiv_id"] for p in out] == ["d0", "d1", "r0", "d2", "d3", "d4"]


def test_arxiv_failure_does_not_kill_the_day(tmp_path, monkeypatch):
    """**실측 회귀**(2026-09-06). S2 절의 주석은 원래부터 "arXiv 가 실패해도
    S2 는 시도한다 — 한 소스가 죽었다고 그날을 통째로 버리지 않는다. 반대도
    같다" 였는데 **코드는 그렇게 안 돼 있었다.** arXiv 실패는 raise 로 나갔고
    S2 쪽은 try/except 가 아예 없었다. 실제로 arXiv 429 가 4회 재시도 끝에
    포기하자 스캔 전체가 죽고 메일이 안 나갔다.

    메일이 안 오는 날은 "무언가 고장났다"로 읽어야 하는데(_deliver 주석),
    그 신호를 일시적 429 에 태우면 진짜 고장과 구분이 안 된다.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])

    async def boom(*a, **kw):
        raise RuntimeError("arXiv API 429")

    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", boom)

    async def s2_ok(client, keywords, since, until, *a, **kw):
        return {"papers": [_journal_paper("10.1/j1", "An agent journal")],
                "status": "done", "query": "S2 keywords×1"}

    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", s2_ok)

    result = asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))
    assert [p["title"] for p in result["papers"]] == ["An agent journal"]
    assert result["run_status"] == "failed"       # arXiv 가 죽은 건 숨기지 않는다
    assert result["s2_count"] == 1


def test_s2_failure_does_not_kill_the_day(tmp_path, monkeypatch):
    """반대 방향도 같아야 한다 — 대칭이 아니면 주석이 또 거짓말이 된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    async def boom(*a, **kw):
        raise RuntimeError("S2 500")

    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", boom)

    result = asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))
    assert [p["arxiv_id"] for p in result["papers"]] == ["p1"]
    assert result["s2_status"] == "failed"
    assert result["run_status"] == "done"


def test_both_sources_failing_is_raised(tmp_path, monkeypatch):
    """둘 다 죽은 날은 올린다 — 그건 일시적 혼잡이 아니라 우리가 아무것도
    못 본 것이고, 그런 날의 "논문 0편" 메일은 "조용한 날"과 구분이 안 돼
    거짓말이 된다(규칙 8)."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])

    async def boom(*a, **kw):
        raise RuntimeError("죽음")

    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", boom)
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", boom)

    with pytest.raises(RuntimeError, match="검색 소스가 전부 실패"):
        asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))


def _journal_paper(doi, title="A journal paper"):
    """S2 경유 저널 논문 — arxiv_id 가 **없다**(실측 2026-09-06: 후보 478편 중 222편)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"arxiv_id": None, "doi": doi, "title": title,
            "abstract": "an agent", "published": ts}


def test_journal_paper_shown_yesterday_is_dropped_today(tmp_path, monkeypatch):
    """**래칫 회귀 테스트**(2026-09-06). 저널 논문은 본문을 못 받아 요약이
    저장되지 않는다. `_already_summarized` 는 summaries 를 보므로 그런 논문을
    영원히 못 거른다 — 실측으로 09-04 미리보기와 09-06 메일의 상위 3편이
    같았다(PhyHGNet · 2-D Ambipolar · Beech Sawn Timber).

    이 테스트가 지키는 것: **어제 내용 자리로 나간 논문은 오늘 후보가 아니다**,
    arxiv_id 가 있든 없든.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])          # 요약은 하나도 없다
    j1 = _journal_paper("10.1016/j.solener.2026.114822", "An agent journal one")
    j2 = _journal_paper("10.1109/LED.2026.3714911", "An agent journal two")
    _mock_arxiv_pages(monkeypatch, [j1, j2])

    async def scan():
        return await rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=2)

    first = asyncio.run(scan())
    assert len(first["papers"]) == 2                  # 첫날은 둘 다 나간다

    rp.mark_shown(db_path, "team_ai", [j1])

    second = asyncio.run(scan())
    titles = [p["title"] for p in second["papers"]]
    assert titles == ["An agent journal two"]         # 내보낸 쪽만 빠진다


def test_scan_and_digest_records_only_the_content_slots(tmp_path, monkeypatch):
    """제목만 실린 논문은 소비하지 않는다 — 내일 본문이 열리면 제대로 실릴
    자격이 있다. 소비하면 그 기회를 뺏는다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    shown = _journal_paper("10.1/shown", "An agent shown")
    footnote = _journal_paper("10.1/footnote", "An agent footnote")

    rp.mark_shown(db_path, "team_ai", [shown])
    keys = rp.already_shown(db_path, "team_ai")
    assert rp.paper_key(shown) in keys
    assert rp.paper_key(footnote) not in keys


def test_paper_key_falls_back_doi_then_title():
    """arxiv_id → doi → 정규화 제목. 저널 논문에 안정된 신원을 준다."""
    assert rp.paper_key({"arxiv_id": "2609.01"}) == "2609.01"
    assert rp.paper_key({"doi": "10.1/A"}) == "doi:10.1/a"
    assert rp.paper_key({"title": " Deep  Nets "}) == "title:deep nets"


def test_paper_key_prefers_doi_over_the_synthetic_pdf_id():
    """**실측 회귀**(2026-09-06). `pdf-<해시>` 는 본문을 받은 **뒤에야** 생기는
    저장용 ID 다. 검색 결과로 도착할 때 그 논문은 `arxiv_id=None` + DOI 를
    갖는데, 처리 직후의 모습으로 기록하면 두 키가 안 맞아 필터가 뚫린다.

    실측으로 오픈액세스 저널 논문이 두 실행 연속 1번 자리에 나왔다 —
    이 필터가 막으려던 **바로 그 논문**(본문이 실제로 열리는 저널)에 구멍이
    나 있었다. 게다가 본문을 다시 받고 요약을 다시 만들어 무료 한도도 태웠다.
    """
    arriving = {"arxiv_id": None, "doi": "10.1007/s11760-026-05625-7"}
    after_processing = {"arxiv_id": "pdf-c8bbaedce8", "doi": "10.1007/s11760-026-05625-7"}
    assert rp.paper_key(after_processing) == rp.paper_key(arriving)

    # DOI 가 없는 직접 업로드 PDF 는 합성 ID 가 유일한 신원이다 — 그건 그대로 쓴다.
    assert rp.paper_key({"arxiv_id": "pdf-abc", "doi": None}) == "pdf-abc"


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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None):
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


# ------------------------------------------- 주간 동향 리뷰 (2026-09-02)


def test_weekly_review_is_attached_only_on_the_review_day(tmp_path, monkeypatch):
    """매일 붙이면 어제와 거의 같은 표가 반복돼 읽히지 않고, 인용망 조회
    비용도 매일 낼 이유가 없다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    async def fake_review(db, profile, client=None, days=7, with_references=True):
        return "■ 주간 동향 리뷰\n(내용)\n"

    monkeypatch.setattr(rps.trend_report, "build", fake_review)

    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: True)
    _r, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert "주간 동향 리뷰" in text


def test_weekly_review_absent_on_other_days(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    async def fake_review(db, profile, client=None, days=7, with_references=True):
        raise AssertionError("리뷰 요일이 아닌데 불렸다")

    monkeypatch.setattr(rps.trend_report, "build", fake_review)

    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: False)
    _r, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert "주간 동향 리뷰" not in text


def test_weekly_review_failure_does_not_break_the_digest(tmp_path, monkeypatch):
    """부가 정보 때문에 메일이 안 나가면 주객이 전도된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    async def boom(db, profile, client=None, days=7, with_references=True):
        raise RuntimeError("S2 죽음")

    monkeypatch.setattr(rps.trend_report, "build", boom)

    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: True)
    _r, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    # 세 갈래 중 하나는 반드시 있다 — 논문이 실렸거나, 후보가 없었거나,
    # 본문을 받을 수 있는 논문이 없었거나(2026-09-06 문지기가 생긴 뒤의 갈래).
    assert ("오늘의 신규 논문" in text or "새로 걸린 논문이 없습니다" in text
            or "본문을 받을 수 있는 신규 논문은 없었습니다" in text)
    assert "주간" not in text          # 실패한 부가 정보는 안 붙는다


def test_weekly_review_lands_in_the_result_so_html_mail_gets_it(tmp_path, monkeypatch):
    """**§8-70 회귀**(2026-09-07). 예전에는 주간 리뷰를 `digest_text` 에 문자열로
    이어붙였는데, _deliver 는 HTML 을 `result` 로 **다시 만든다** — 그래서 절이
    HTML 메일에만 통째로 없었다. 메일은 multipart/alternative 고 Gmail 은 HTML 을
    보여주므로 사용자 화면에 닿은 적이 없다.

    이 테스트가 지키는 것은 "평문에 있다"가 아니라 **"배달되는 두 판 모두에
    있다"** 이다 — 그게 그때 놓친 주장이다.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    async def fake_review(db, profile, client=None, days=7, with_references=True):
        return "■ 주간 동향 리뷰\n\n처리한 논문 12편 (지난주 9편)\n"

    monkeypatch.setattr(rps.trend_report, "build", fake_review)
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: True)

    result, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))

    assert "처리한 논문 12편" in result.get("weekly_review", "")
    assert "처리한 논문 12편" in text                       # 평문 판
    html = rps.digest.generate_digest_html(result, "team_ai")
    assert "처리한 논문 12편" in html                       # _deliver 가 보내는 판
    assert "주간 동향 리뷰" in html


def test_weekly_review_missing_leaves_both_renderers_clean(tmp_path, monkeypatch):
    """리뷰 요일이 아니면 두 판 어디에도 빈 절이 생기지 않는다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: False)

    result, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert "weekly_review" not in result
    assert "주간 동향 리뷰" not in text
    assert "주간 동향 리뷰" not in rps.digest.generate_digest_html(result, "team_ai")


def test_weekly_review_day_is_computed_from_the_weekday():
    from datetime import datetime as _dt, timezone as _tz
    monday = _dt(2026, 9, 7, tzinfo=_tz.utc)      # 월요일
    tuesday = _dt(2026, 9, 8, tzinfo=_tz.utc)
    assert rps.is_weekly_review_day(monday) is True
    assert rps.is_weekly_review_day(tuesday) is False


def test_scan_profile_ranks_by_relevance_not_text_availability(tmp_path, monkeypatch):
    """**주장이 뒤집혔다**(2026-09-04). 하루 전 이 테스트는 "본문 확보 여부로
    가른다"였다 — 본문 없는 논문이 요약 자리를 먹는 걸 막으려던 것이었다.

    그런데 09-04 메일 실측에서 그게 더 나쁜 걸 만들었다: ★★★ 팀 표적 논문
    두 편(PhyHGNet, 2-D Ambipolar)이 ★★ 여섯 편 **아래**에 묻혔다.
    "본문을 받을 수 있나"를 "우리 분야인가"보다 먼저 놓았기 때문이다.

    원래 걱정은 §8-41(초록 정리)로 사라졌다 — 본문을 못 받아도 칸이 안 빈다.
    이제 **자리는 관련도가 정하고 깊이만 확보한 것이 정한다.**
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    now = datetime.now(timezone.utc)
    published = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 셋 다 프로필에 걸리지만 본문 확보 수단이 다르다
    pages = {0: [
        {"arxiv_id": "a1", "title": "An agent for robot hand control",
         "abstract": "", "published": published},
        {"arxiv_id": None, "doi": "10.1/oa", "open_access_pdf": "http://x/y.pdf",
         "title": "An agent for robot arm grasping", "abstract": "", "published": published},
        {"arxiv_id": None, "doi": "10.1/paywalled",
         "title": "An agent for robot locomotion", "abstract": "", "published": published},
    ]}
    starts_seen = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _x: pages[starts_seen[-1]])

    result = asyncio.run(
        rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=3))

    # 본문 확보 여부와 무관하게 셋 다 상위 목록에 있다(max_items 안이므로)
    got = {p.get("arxiv_id") or p.get("doi") for p in result["papers"]}
    assert got == {"a1", "10.1/oa", "10.1/paywalled"}
    assert result["title_only_papers"] == []     # 순위 밖이 없다
    assert result["candidates_found"] == 3


def test_high_relevance_paper_without_text_outranks_low_relevance_with_text(tmp_path, monkeypatch):
    """핵심 회귀 — 09-04 메일에서 실제로 뒤집혀 있었다.

    ★★★ PhyHGNet(핵심 키워드 2개, 본문 없음)이 ★★ 여섯 편(핵심 키워드 1개,
    본문 있음) **아래**에 묻혔다. 읽는 사람이 먼저 알아야 할 건 관련도지
    우리 수집 사정이 아니다.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    now = datetime.now(timezone.utc)
    published = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pages = {0: [
        # 본문은 받을 수 있지만 핵심 키워드 하나(+도메인 보너스까지 얹힌다)
        {"arxiv_id": "weak", "title": "An agent for robot hand control",
         "abstract": "", "published": published},
        # 본문이 없지만 핵심 키워드 둘 — 이게 위에 와야 한다
        {"arxiv_id": None, "doi": "10.1/strong",
         "title": "A digital twin agent for factory lines",
         "abstract": "", "published": published},
    ]}
    starts_seen = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _x: pages[starts_seen[-1]])

    result = asyncio.run(
        rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=3))

    top = result["papers"][0]
    assert (top.get("doi") or top.get("arxiv_id")) == "10.1/strong"
    assert len(top["_score"]["core_hits"]) > len(result["papers"][1]["_score"]["core_hits"])


def test_paper_without_link_reaches_process_paper(tmp_path, monkeypatch):
    """핵심 회귀 — 조기 반환이 **세 곳**에 있었다(§8-50).

    batch_summarize 안의 둘을 한 곳으로 모았는데 run_profile_scan 에 세 번째가
    남아 있어서, 링크 없는 논문이 `_process_paper` 에 닿지도 못하고
    `failed: 식별자도 오픈액세스 링크도 없음` 으로 잘렸다. 초록이 있어도.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    now = datetime.now(timezone.utc)
    published = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pages = {0: [{"arxiv_id": None, "doi": "10.1/x",
                  "title": "A digital twin agent for factory lines",
                  "abstract": "We detect defects.", "published": published}]}
    starts_seen = []
    reached = []

    async def fake_throttled(client, params):
        starts_seen.append(params["start"])

        class FakeResp:
            text = "<fake/>"

        return FakeResp()

    async def fake_process(client, arxiv_id, paper=None, **kw):
        reached.append((paper or {}).get("doi"))
        return {"arxiv_id": "", "status": "abstract_only",
                "brief": "- 무엇을 하려 했는가 : 결함을 검출한다."}

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _x: pages[starts_seen[-1]])
    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, digest_text = asyncio.run(
        rps.scan_and_digest(db_path, "team_ai", None, max_pages=3))

    assert reached == ["10.1/x"]                      # 잘리지 않고 닿았다
    top = result["papers"][0]
    assert top["deep_status"] == "abstract_only"
    assert "결함을 검출한다" in top["abstract_brief"]
    assert "식별자도 오픈액세스 링크도 없음" not in digest_text
    assert "본문 비공개 — 초록만 보고 정리한 것이다" in digest_text


# ---------------------------------------------------------------- 본문 문지기 (2026-09-06)
#
# **"자리 보장"에서 "문지기"로 바꿨다.** 2026-09-05 에는 본문 되는 논문에
# 최소 2칸만 보장했는데, 실측이 그 절충을 못 버티게 했다:
#
#   · 후보 478편 중 arXiv 밖 222편, 그중 오픈액세스 링크 있음 78편(35%)
#   · 그 링크를 실제로 열어보니 20편 중 **6편(30%)** 만 PDF 를 줬다
#   · 출판사별로 갈린다 — Springer·Wiley·Elsevier·SSRN·IOP **0/12**
#     (링크는 주는데 열면 HTML 로그인 페이지이거나 403),
#     소형·지역 OA 저널 **6/8**
#
# 즉 저널 222편 중 본문이 실제로 열리는 건 열 편 남짓이고, 나머지는 자리를
# 먹고 "처리 실패" 한 줄을 싣는다. 아래 테스트들은 옛 예약 계약(최소 2칸)을
# **더 강한 계약**(본문 되는 논문만 내용 자리)으로 갈아끼운 것이다 —
# 완화가 아니라 강화다(규칙 9, 사유·날짜 기록).

def _p(key, score, arxiv=False, oa=None):
    return {"arxiv_id": key if arxiv else None,
            "doi": None if arxiv else key,
            "open_access_pdf": oa,
            "title": f"paper {key}",
            "_score": {"priority": score, "core_hits": [], "domain_hits": [],
                       "venue_hit": None, "top_core_weight": 1.0}}


def test_only_papers_we_can_fetch_are_eligible_for_content_slots():
    """09-04·09-06 메일이 이걸로 망가졌다 — 본문 못 받는 저널이 번호 붙은
    자리를 먹고 "처리 실패" 를 실었다."""
    ranked = [_p("j1", 0.9), _p("j2", 0.8), _p("a1", 0.7, arxiv=True),
              _p("j3", 0.6), _p("a2", 0.5, arxiv=True)]
    top, rest = rps._eligible_for_content(ranked, max_items=2)
    assert [x["arxiv_id"] for x in top] == ["a1", "a2"]
    assert {x["doi"] for x in rest} == {"j1", "j2", "j3"}


def test_gate_runs_before_the_keyword_spread():
    """**실측 회귀**(2026-09-06). 처음엔 다양성 → 문지기 순으로 불렀는데,
    문지기가 전체 목록에서 자격자를 다시 뽑으면서 다양성 제한을 되돌렸다 —
    3칸 상한인 'defect detection' 이 6칸 중 4칸을 먹었다.
    순서는 **자격 → 다양성 → 자르기** 다."""
    def q(key, score, kw):
        return {"arxiv_id": key, "doi": None, "open_access_pdf": None,
                "title": key, "_score": {"priority": score, "core_hits": [kw],
                                         "domain_hits": [], "venue_hit": None,
                                         "top_core_weight": 1.0}}
    ranked = ([q(f"d{i}", 0.9 - i * 0.001, "defect detection") for i in range(6)]
              + [q(f"s{i}", 0.8 - i * 0.001, "surface inspection") for i in range(3)])
    eligible, _dropped = rps._eligible_for_content(ranked, 4)
    top = rps._spread_keywords(eligible, 4)[:4]
    kinds = [x["_score"]["core_hits"][0] for x in top]
    assert kinds.count("defect detection") == 2      # 4칸의 절반까지만
    assert kinds.count("surface inspection") == 2


def test_open_access_link_counts_as_a_route():
    """저널이라고 무조건 빼지 않는다 — 소형 OA 저널은 실측 6/8 로 열린다."""
    ranked = [_p("oa1", 0.9, oa="https://dergipark.org.tr/x.pdf"), _p("j1", 0.8)]
    top, rest = rps._eligible_for_content(ranked, max_items=1)
    assert top[0]["doi"] == "oa1"
    assert rest[0]["doi"] == "j1"


def test_order_inside_the_slots_is_still_relevance():
    """§8-44 의 교훈 — 자리는 걸러도 **순서는 관련도 그대로**."""
    ranked = [_p("a1", 0.9, arxiv=True), _p("j1", 0.85), _p("a2", 0.8, arxiv=True)]
    top, _rest = rps._eligible_for_content(ranked, max_items=2)
    assert [x["_score"]["priority"] for x in top] == [0.9, 0.8]


def test_empty_slots_are_filled_rather_than_left_blank():
    """본문 되는 논문이 모자란 날은 관련도 순으로 채운다 — 매일 오는 메일
    자체가 파이프라인 생존 신호다(_deliver 주석). 빈 메일을 만들지 않는다."""
    ranked = [_p("j1", 0.9), _p("j2", 0.8), _p("a1", 0.3, arxiv=True)]
    top, rest = rps._eligible_for_content(ranked, max_items=3)
    assert len(top) == 3
    assert [x["_score"]["priority"] for x in top] == [0.9, 0.8, 0.3]
    assert rest == []

