"""run_profile_scan.py 통합 테스트 — server._throttled_arxiv_get만 모킹,
research_profile은 임시 SQLite로 실제 로직 그대로 돈다. 네트워크 없음."""

import http_client
import storage
import asyncio
import sqlite3
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

    assert "연구 동향 브리핑" in digest_text
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    _run_scan_and_digest(db_path)

    assert lb_calls == []


def test_deep_layer_skips_already_summarized_paper(tmp_path, monkeypatch):
    """저장 요약도 기존 처리 지점에서 ⑦ 완료를 기다린 뒤 같은 메일에 싣는다.
    요약 API 재호출 차단은 test_process_paper에서 실제 함수를 검사한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda aid: aid == "p2")

    calls = []

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
        calls.append(arxiv_id)
        return {"arxiv_id": arxiv_id, "status": "done", "engine": "gemini"}

    monkeypatch.setattr(rps.batch_summarize, "_process_paper", fake_process)

    result, _digest_text = _run_scan_and_digest(db_path)

    assert calls == ["p1", "p2", "p3"]  # 요약 재사용과 ⑦ 완료 확인은 별개다
    statuses = {p["arxiv_id"]: p["deep_status"] for p in result["papers"]}
    assert statuses["p2"].startswith("skipped:")


def test_deep_layer_records_fetch_failed_dict_as_failure(tmp_path, monkeypatch):
    """fetch 실패는 예외가 아니라 status="fetch_failed" dict로 온다(실측
    확인) — 이 경로도 failed로 기록돼야 한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_arxiv_three_agent_papers(monkeypatch)
    monkeypatch.setattr(rps, "_summary_exists", lambda _aid: False)

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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


def test_delivered_papers_are_dropped_before_ranking(tmp_path, monkeypatch):
    """**계약이 바뀌었다**(2026-09-08, §8-77). 소비의 기준이 "요약됐다"에서
    **"배달됐다"**로 옮겨졌다.

    원래 실측 배경(2026-09-01)은 그대로다 — 색인 지연 때문에 매 실행이 최근
    며칠을 다시 조회하므로(REINDEX_SAFETY_DAYS) 이미 나간 논문을 안 빼면
    어제 메일의 논문이 오늘 또 나간다. **다만 "요약됨"을 그 기준으로 쓰면
    발송이 실패한 날 그 논문은 요약만 남고 메일은 못 탄 채 후보에서도 빠져
    영영 안 나간다.** 그래서 배달 기록(`profile_shown`)만 본다.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, ["p1", "p3"])
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in (1, 2, 3, 4)])

    # 요약만 있고 배달 기록이 없으면 **후보로 남는다** — 예전에는 빠졌다.
    async def main():
        return await rps.scan_profile(db_path, "team_ai", None, page_size=50, max_pages=2)

    result = asyncio.run(main())
    assert [p["arxiv_id"] for p in result["papers"]] == ["p1", "p2", "p3", "p4"]

    # 배달 기록을 넣으면 그때 빠진다.
    rp.mark_shown(db_path, "team_ai", [{"arxiv_id": "p1", "title": "An agent paper p1"},
                                       {"arxiv_id": "p3", "title": "An agent paper p3"}])
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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

    async def fake_process(client, arxiv_id, on_progress=None, paper=None, wait_for_repro=False):
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


def test_weekly_review_lands_on_the_monday_morning_mail():
    """**§8-71.** 배달은 05:00 KST = **전날 20:00 UTC** 에 일어난다. UTC 로 요일을
    세면 월요일 아침 메일에는 안 붙고(그때 UTC 로는 일요일) **화요일 아침 메일에
    붙는다.** 여태 못 본 이유는 §8-70 ① — 주간 리뷰가 HTML 에 아예 닿지 않아
    요일이 어긋난 것도 안 보였다.

    실제 배달 시각으로 시험한다. 요일은 **읽는 사람 기준**이어야 한다.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    KST = _tz(_td(hours=9))
    assert rps.is_weekly_review_day(_dt(2026, 9, 14, 5, 0, tzinfo=KST)) is True   # 월
    assert rps.is_weekly_review_day(_dt(2026, 9, 15, 5, 0, tzinfo=KST)) is False  # 화
    assert rps.is_weekly_review_day(_dt(2026, 9, 13, 5, 0, tzinfo=KST)) is False  # 일

    # 같은 순간을 UTC 로 줘도 답이 같아야 한다 — 표현이 아니라 순간이 기준이다.
    monday_kst = _dt(2026, 9, 14, 5, 0, tzinfo=KST)
    assert rps.is_weekly_review_day(monday_kst.astimezone(_tz.utc)) is True


def test_weekly_review_day_does_not_depend_on_the_machine_timezone(monkeypatch):
    """**§8-93 ①.** 예전 구현은 인자 없는 `astimezone()` 이라 **이 컴퓨터**의
    시간대로 요일을 셌다. KST 머신에서는 맞고 UTC 컨테이너에서는 같은 순간이
    일요일 20:00 이 되어 실패했다(2026-09-11 외부 검증에서 실제로 실패).
    TZ 를 UTC 로 바꿔 놓고도 월요일 05:00 KST 가 월요일이어야 한다.
    """
    import time as _time
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    monkeypatch.setenv("TZ", "UTC")
    _time.tzset()
    try:
        KST = _tz(_td(hours=9))
        monday_kst = _dt(2026, 9, 14, 5, 0, tzinfo=KST)
        # 이 컴퓨터 기준으로는 아직 일요일 20:00 — 그래도 읽는 사람에게는 월요일이다.
        assert monday_kst.astimezone().weekday() == 6
        assert rps.is_weekly_review_day(monday_kst) is True
        assert rps.is_weekly_review_day(monday_kst.astimezone(_tz.utc)) is True
        assert rps.is_weekly_review_day(_dt(2026, 9, 15, 5, 0, tzinfo=KST)) is False
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


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



# ------------------------------------------- ⑨ 종료코드 전파 (2026-09-07)
#
# run_daily_scan.sh 가 status 를 로그에만 찍고 마지막 줄이 `|| true` 라 **항상
# 0 으로 끝났다** — 실패한 날과 성공한 날을 바깥에서 구분할 수 없었다.
# 이 시스템은 "매일 오는 메일이 파이프라인이 살아 있다는 증거"라는 전제 위에
# 서 있는데(M8), 그 전제는 메일이 안 나간 날을 누군가 알아챌 때만 성립한다.


def test_all_ok_exits_zero():
    assert rps._exit_code({"team_ai": {"status": "ok", "delivery": "발송 완료 → 1명"}}) == 0


def test_profile_error_exits_nonzero():
    """프로필 하나가 예외로 죽으면 그날은 성공이 아니다."""
    assert rps._exit_code({"a": {"status": "ok"}, "b": {"status": "error"}}) != 0


def test_delivery_failure_exits_nonzero():
    """스캔은 됐는데 메일이 안 나갔다 — 사람 화면에는 아무것도 안 온 날이다."""
    assert rps._exit_code({"a": {"status": "ok", "delivery": "발송 실패: SMTP 죽음"}}) != 0


def test_no_recipient_is_not_a_failure():
    """수신자를 안 넣은 건 설정 상태지 고장이 아니다 — 매일 경보를 울리면
    경보 자체가 무시된다."""
    assert rps._exit_code({"a": {"status": "ok",
                                 "delivery": rps.DELIVERY_NO_RECIPIENT}}) == 0


def test_empty_summary_exits_nonzero():
    """cron 이 매일 도는데 아무 일도 안 했다면 조용한 날이 아니라 설정이 비었다."""
    assert rps._exit_code({}) != 0


def test_delivery_failed_reads_only_the_owned_prefix():
    assert rps.delivery_failed("발송 실패: SMTP 죽음")
    assert not rps.delivery_failed("발송 완료 → 2명")
    assert not rps.delivery_failed(None)
    assert not rps.delivery_failed("")


def test_scan_all_records_the_delivery_message_verbatim(tmp_path, monkeypatch):
    """실패 판별은 _deliver 가 만든 문자열로만 한다 — summary 에 그대로 남아야
    cron 로그에서 사람이 같은 근거를 본다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _mock_empty_arxiv(monkeypatch)
    monkeypatch.setattr(rps, "_deliver", lambda *a: "발송 실패: SMTP 죽음")

    async def run():
        return await rps.scan_all_profiles(db_path, None, max_pages=2, send=True)

    summary = asyncio.run(run())
    assert summary["team_ai"]["delivery"] == "발송 실패: SMTP 죽음"
    assert rps._exit_code(summary) != 0


# ------------------------------------------- ① 선택 이전 후보 기록 (2026-09-07)
#
# 그전까지 후보는 그 실행의 메모리에만 있었다. 다이제스트에 실린 논문만
# 흔적이 남고 밀린 논문은 사라져서 "왜 이 논문이 안 뽑혔나", "저 저널 논문은
# 언제 처음 보였나", "채점 규칙을 바꾸면 뭐가 달라지나"에 답할 수 없었다.


def _candidates(db_path, **kw):
    return rp.list_candidates(db_path, "team_ai", **kw)


def test_candidates_are_recorded_before_selection(tmp_path, monkeypatch):
    """내용 자리에 못 든 논문도 남는다 — 그게 이 테이블의 이유다.

    창(7일) 밖 논문은 delta 검색이 이미 거르므로 여기 안 온다. 기준은
    "후보로 걸린 것이 전부 남는가"이지 "넣은 것이 전부 남는가"가 아니다.
    """
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper(f"p{i}", i) for i in range(1, 7)])

    result, _text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))

    rows = _candidates(db_path)
    assert len(rows) >= result["candidates_found"] > 0
    counts = rp.candidate_outcome_counts(db_path, "team_ai")
    assert sum(counts.values()) == len(rows)

    # 다이제스트에 실린 논문은 반드시 기록에 있다 — 화면과 기록이 어긋나면
    # 기록으로 아무것도 설명할 수 없다.
    recorded = {row["paper_key"] for row in rows}
    for paper in result["papers"] + (result.get("title_only_papers") or []):
        assert rp.paper_key(paper) in recorded


def test_scoring_dropouts_are_recorded_with_their_score(tmp_path, monkeypatch):
    """score_and_rank 는 제외어에 걸렸거나 키워드를 하나도 못 맞힌 논문을
    `continue` 로 버려서 **어느 목록에도 안 남는다.** "왜 이 논문이 안
    뽑혔나"가 바로 이들을 묻는 질문이므로 점수와 함께 남긴다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    unrelated = {"arxiv_id": "z1", "title": "Marine biology of coral reefs",
                 "abstract": "", "published": _agent_paper("x", 1)["published"]}
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1), unrelated])

    result, _text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))

    assert all(p.get("arxiv_id") != "z1" for p in result["papers"])   # 안 뽑혔고
    rows = {row["arxiv_id"]: row for row in _candidates(db_path)}
    assert "z1" in rows                                               # 그래도 남았다
    assert rows["z1"]["outcome"] == rp.OUTCOME_DROPPED
    assert rows["z1"]["score"] is not None                            # 점수까지


def test_recorded_candidate_keeps_the_metadata_that_costs_a_call_to_get_again(tmp_path, monkeypatch):
    """citation_count·venue 는 검색 응답에만 있다 — 후보로 걸린 순간이
    그 값을 공짜로 갖는 유일한 시점이다(비용 원칙)."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.record_candidates(db_path, "team_ai", [{
        "title": "PhyHGNet", "doi": "10.1016/x", "venue": "Solar Energy",
        "published": "2026-09-01T00:00:00Z", "citation_count": 12, "source": "s2",
        "abstract": "초록", "_score": {"priority": 1.13},
    }], rp.OUTCOME_TITLE_ONLY)

    row = _candidates(db_path)[0]
    assert row["venue"] == "Solar Energy"
    assert row["citation_count"] == 12
    assert row["doi"] == "10.1016/x"
    assert row["source"] == "s2"
    assert abs(row["score"] - 1.13) < 1e-9
    assert row["published"] == "2026-09-01T00:00:00Z"


def test_first_seen_survives_a_second_sighting(tmp_path):
    """"언제 처음 보였나"가 신규 판단의 근거다 — 다시 걸렸다고 덮어쓰면 안 된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    paper = {"title": "T", "doi": "10.1/x", "source": "s2"}
    rp.record_candidates(db_path, "team_ai", [paper], rp.OUTCOME_RESERVE)
    first = _candidates(db_path)[0]["first_seen"]

    rp.record_candidates(db_path, "team_ai", [paper], rp.OUTCOME_CONTENT)
    row = _candidates(db_path)[0]
    assert row["first_seen"] == first          # 처음 본 날은 그대로
    assert row["outcome"] == rp.OUTCOME_CONTENT  # 결말은 갱신된다
    assert len(_candidates(db_path)) == 1        # 같은 논문이 두 줄이 되지 않는다


def test_zero_score_candidate_is_kept_for_analysis(tmp_path):
    """0점 후보를 버리면 "왜 안 뽑혔나"를 영영 못 본다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.record_candidates(db_path, "team_ai", [
        {"title": "무관한 논문", "doi": "10.1/z", "_score": {"priority": 0.0}},
    ], rp.OUTCOME_DROPPED)
    row = _candidates(db_path, outcome=rp.OUTCOME_DROPPED)[0]
    assert row["score"] == 0.0
    assert row["title"] == "무관한 논문"


def test_recording_failure_does_not_break_the_scan(tmp_path, monkeypatch):
    """관측이 배달을 막으면 주객이 전도된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    def boom(*a, **kw):
        raise sqlite3.OperationalError("디스크 꽉 참")

    monkeypatch.setattr(rps.research_profile, "record_candidates", boom)
    result, text = asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert text                                  # 다이제스트는 그대로 나온다
    assert "candidates_found" in result


def test_candidate_key_matches_profile_shown(tmp_path):
    """두 테이블이 같은 논문을 다른 이름으로 부르면 조인이 안 된다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    paper = {"title": "T", "doi": "10.1/x", "arxiv_id": None}
    rp.record_candidates(db_path, "team_ai", [paper], rp.OUTCOME_CONTENT)
    rp.mark_shown(db_path, "team_ai", [paper])

    assert _candidates(db_path)[0]["paper_key"] == rp.paper_key(paper)
    assert rp.paper_key(paper) in rp.already_shown(db_path, "team_ai")


def test_arxiv_parser_records_where_the_paper_came_from():
    """**라이브 실행이 잡았다**(2026-09-07). 후보 515편 중 arXiv 96편의
    source 가 빈 문자열이었다 — S2 경로(s2_delta._to_paper)만 "s2" 를 달고
    왔고 arXiv 파서는 아무것도 안 달았다. "어디서 왔나"는 후보 테이블을
    만든 이유 중 하나다.

    후보 기록 쪽에서 arxiv_id 유무로 **추론**해 채울 수도 있지만 그러지
    않는다 — 아는 곳에서 사실로 적는다. 그래서 이 테스트는 스캔 경로가
    아니라 진짜 파서를 부른다(스캔 테스트는 파서를 목으로 바꾼다).
    """
    feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2609.01234v1</id>
    <title>An agent paper</title>
    <summary>초록</summary>
    <published>2026-09-01T00:00:00Z</published>
    <author><name>홍길동</name></author>
    <category term="cs.AI"/>
  </entry>
</feed>"""
    papers = http_client.parse_arxiv_feed(feed)
    assert len(papers) == 1
    assert papers[0]["source"] == "arxiv"
    assert papers[0]["arxiv_id"] == "2609.01234"


# ------------------------------------------- §8-76 S2 가 놓친 구간 (2026-09-08)
#
# 검색 창의 시작 시각이 arXiv 이력에서만 나왔다(`next_since` 의 source 기본값).
# S2 가 partial·failed 로 끝나도 창은 arXiv 기준으로 전진해서, **S2 가 못 본
# 구간이 어디에도 남지 않았다.** 5일 안전 창이 우연히 덮어 주고 있었을 뿐이다.


def _seed_run(db_path, source, status, w_from, w_to, started):
    import sqlite3 as _sq
    rp.init_db(db_path)
    with _sq.connect(db_path) as con:
        con.execute(
            "INSERT INTO search_runs (run_id, profile_id, source, query, window_from, "
            "window_to, status, retrieved_count, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"r-{source}-{started}", "team_ai", source, "q", w_from.isoformat(), w_to.isoformat(),
             status, 0, started, started))


def test_window_follows_the_source_that_saw_less(tmp_path, monkeypatch):
    """arXiv 는 다 봤고 S2 는 못 봤으면, 창은 **S2 쪽**을 따라가야 한다 —
    안 그러면 S2 가 못 본 구간이 영영 안 조회된다."""
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    now = datetime.now(timezone.utc)
    sig = rp.topic_signature(rp.get_profile(db_path, "team_ai")["core_topics"])

    # arXiv 는 어제까지 다 봤다(done → 커서가 window_to)
    _seed_run(db_path, "arxiv", "done", now - timedelta(days=20), now - timedelta(days=1),
              (now - timedelta(days=1)).isoformat())
    # S2 는 20일 전 구간에서 멈췄다(partial → 커서가 window_from)
    _seed_run(db_path, "s2", "partial", now - timedelta(days=20), now - timedelta(days=1),
              (now - timedelta(days=1)).isoformat())

    arxiv_only = rp.next_since(db_path, "team_ai", "arxiv", signature=sig)
    s2_only = rp.next_since(db_path, "team_ai", "s2", signature=sig)
    assert s2_only < arxiv_only, "픽스처 전제: s2 가 더 뒤처져 있다"

    # **스캔이 실제로 검색에 넘긴 since 를 잡는다.** 2026-09-08 외부 검토가
    # 지적했다 — 처음 이 테스트는 테스트 안에서 min 을 다시 계산해 비교했다.
    # 그러면 운영 코드의 min 을 max 로 바꿔도 통과한다(돌연변이로 확인: 67개
    # 전부 통과했다). 통과하지만 아무것도 안 지키는 테스트였다.
    _seed_summary(monkeypatch, tmp_path, [])
    seen = {}

    async def spy_arxiv(client, query, since, **kw):
        seen["since"] = since
        from datetime import datetime as _dt, timezone as _tz
        return {"status": "done", "papers": [], "pages_used": 1, "query": query,
                "until": _dt.now(_tz.utc).isoformat()}

    async def no_s2(client, keywords, since, until, **kw):
        return {"papers": [], "status": "done", "query": "q",
                "keywords_failed": 0, "keywords_searched": 0, "keywords_truncated": 0}

    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", spy_arxiv)
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", no_s2)
    asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))

    assert seen["since"] == s2_only, (
        f"검색에 넘긴 since 가 뒤처진 쪽(s2)이 아니다: {seen['since']} != {s2_only}")
    assert seen["since"] != arxiv_only


def test_both_done_keeps_the_window_unchanged(tmp_path):
    """둘 다 다 봤으면 창이 길어질 이유가 없다 — 공짜로 검색량을 늘리지 않는다."""
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    now = datetime.now(timezone.utc)
    sig = rp.topic_signature(rp.get_profile(db_path, "team_ai")["core_topics"])
    for src in ("arxiv", "s2"):
        _seed_run(db_path, src, "done", now - timedelta(days=20), now - timedelta(days=1),
                  (now - timedelta(days=1)).isoformat())
    a = rp.next_since(db_path, "team_ai", "arxiv", signature=sig)
    s = rp.next_since(db_path, "team_ai", "s2", signature=sig)
    # 둘 다 5일 안전 창(now - REINDEX_SAFETY_DAYS)에 걸리므로 호출 시각 차이만큼
    # 마이크로초가 다르다. 창이 "길어지지 않는다"가 지키려는 것이므로 초 단위로 본다.
    assert abs((a - s).total_seconds()) < 1
    assert abs((min(a, s) - a).total_seconds()) < 1


def test_scan_uses_the_combined_window(tmp_path, monkeypatch):
    """스캔 경로가 실제로 두 소스를 다 보는지 — 호출을 세서 확인한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    seen = []
    real = rp.next_since

    def spy(db, pid, source="arxiv", **kw):
        seen.append(source)
        return real(db, pid, source, **kw)

    monkeypatch.setattr(rps.research_profile, "next_since", spy)
    asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert "arxiv" in seen and "s2" in seen, f"본 소스: {seen}"


# ------------------------------------------- §8-77 발송 실패 시 소비 (2026-09-08)
#
# `mark_shown` 이 scan_and_digest 안에서 불리고 메일은 그 뒤 _deliver 가 보냈다.
# _deliver 는 SMTP 실패를 예외로 안 올리고 문자열만 돌려주며 되돌리는 코드가
# 없어서, **메일이 안 나간 날에도 그날 논문은 소비돼 다음 다이제스트에서 영영
# 빠졌다.** 소비의 기준을 "배달됐다"로 옮겨 고쳤다.


def test_failed_delivery_leaves_papers_for_tomorrow(tmp_path, monkeypatch):
    """**§8-77 의 핵심.** SMTP 가 죽은 날의 논문은 내일 다시 나갈 기회를 가져야 한다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])
    monkeypatch.setattr(rps, "_deliver", lambda *a: "발송 실패: SMTP 죽음")
    # 소비 게이트 자체를 본다 — 그날 내용 자리에 무엇이 들어갔는지(스코어링
    # 결과)에 테스트가 흔들리면 안 된다.
    calls = []
    monkeypatch.setattr(rps.research_profile, "mark_shown",
                        lambda db, pid, papers: calls.append(papers))

    async def run():
        return await rps.scan_all_profiles(db_path, None, max_pages=2, send=True)

    summary = asyncio.run(run())
    assert rps.delivery_failed(summary["team_ai"]["delivery"])
    assert calls == [], "발송이 실패했는데 소비 처리를 했다"


def test_successful_delivery_consumes_the_papers(tmp_path, monkeypatch):
    """성공한 날은 소비해야 한다 — 안 그러면 내일 같은 논문이 또 나간다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])
    monkeypatch.setattr(rps, "_deliver", lambda *a: "발송 완료 → 1명")
    calls = []
    monkeypatch.setattr(rps.research_profile, "mark_shown",
                        lambda db, pid, papers: calls.append(papers))

    async def run():
        return await rps.scan_all_profiles(db_path, None, max_pages=2, send=True)

    summary = asyncio.run(run())
    assert summary["team_ai"]["delivery"] == "발송 완료 → 1명"
    assert len(calls) == 1, "발송에 성공했는데 소비 처리를 안 했다"
    # 무엇을 소비했는지는 아래 전용 테스트가 본다 — summary 에는 papers 가
    # 없어서 여기서 대조할 수 없다.


def test_successful_delivery_consumes_the_papers_that_were_sent(tmp_path, monkeypatch):
    """소비 인자가 **그날 실린 논문 그대로**여야 한다. 빈 목록을 넘기면
    아무것도 소비되지 않아 다음 날 같은 메일이 또 나간다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    rp.add_recipient(db_path, "team_ai", "a@example.com")
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])
    monkeypatch.setattr(rps, "_deliver", lambda *a: "발송 완료 → 1명")

    # 그날 실린 논문을 고정해 소비 인자와 대조한다.
    sent = [{"arxiv_id": "px", "title": "실린 논문"}]
    real = rps.scan_and_digest

    async def stub(db, pid, client, **kw):
        result, text = await real(db, pid, client, **kw)
        result["papers"] = sent
        return result, text

    monkeypatch.setattr(rps, "scan_and_digest", stub)
    got = []
    monkeypatch.setattr(rps.research_profile, "mark_shown",
                        lambda db, pid, papers: got.append(list(papers)))

    asyncio.run(rps.scan_all_profiles(db_path, None, max_pages=2, send=True))
    assert got == [sent], f"실린 논문과 소비한 논문이 다르다: {got}"


def test_scan_without_send_does_not_consume(tmp_path, monkeypatch):
    """review_app 의 수동 스캔은 메일을 안 보낸다. 화면에서 본 것과 메일로 받은
    것은 다르고, 이 기록의 이름은 "내보냈다"이다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])

    asyncio.run(rps.scan_and_digest(db_path, "team_ai", None, max_pages=2))
    assert rp.already_shown(db_path, "team_ai") == set()


def test_backfill_marks_old_summaries_as_delivered(tmp_path, monkeypatch):
    """**이행 장치.** 소비 기준을 옮기면 예전에 요약만 되고 배달 기록이 없는
    논문이 한꺼번에 되살아난다. 실측(2026-09-08): 요약 132편 중 기록 없는 것이
    101편이고 그중 9편이 최근 5일 창에 들어와 내일 메일에 다시 나갈 참이었다.
    그 논문들은 실제로는 이미 나갔고 기록만 없던 것이라 소급해 채운다."""
    import sqlite3 as _sq
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    with _sq.connect(db_path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS papers "
                    "(arxiv_id TEXT PRIMARY KEY, title TEXT, source TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS summaries (arxiv_id TEXT PRIMARY KEY, path TEXT)")
        con.execute("INSERT INTO papers VALUES ('p1','A',NULL), ('p2','B',NULL)")
        con.execute("INSERT INTO summaries VALUES ('p1',''), ('p2','')")

    assert rp.already_shown(db_path, "team_ai") == set()
    n = rp.backfill_shown_from_summaries(db_path, "team_ai")
    assert n == 2
    assert len(rp.already_shown(db_path, "team_ai")) == 2
    assert rp.backfill_shown_from_summaries(db_path, "team_ai") == 0   # 두 번 불러도 안전


def test_skipped_s2_does_not_hold_the_window(tmp_path, monkeypatch):
    """**§8-78 ①.** S2 커서를 무조건 합치면, 가중치를 낮춰 S2 를 끈 프로필에서
    **옛 커서가 영원히 남아 arXiv 창을 끈다** — 그 소스는 앞으로 갱신되지
    않으므로 커서가 늙기만 한다. 이번 실행에서 실제로 질의할 소스만 센다."""
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    now = datetime.now(timezone.utc)

    # arXiv 는 어제까지 다 봤고, S2 는 30일 전에서 멈춘 뒤 다시 안 돌았다.
    _seed_run(db_path, "arxiv", "done", now - timedelta(days=40), now - timedelta(days=1),
              (now - timedelta(days=1)).isoformat())
    _seed_run(db_path, "s2", "partial", now - timedelta(days=40), now - timedelta(days=30),
              (now - timedelta(days=30)).isoformat())

    seen = {}

    async def spy_arxiv(client, query, since, **kw):
        seen["since"] = since
        return {"status": "done", "papers": [], "pages_used": 1, "query": query,
                "until": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", spy_arxiv)
    # **S2 를 끈다** — 이 프로필은 이번 실행에서 S2 를 질의하지 않는다.
    monkeypatch.setattr(rps.s2_delta, "keywords_for_s2", lambda profile, **kw: [])

    asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))

    age = (datetime.now(timezone.utc) - seen["since"]).days
    assert age <= 7, f"질의도 안 하는 S2 커서가 창을 {age}일로 끌었다"


def test_active_s2_still_holds_the_window(tmp_path, monkeypatch):
    """반대로 S2 를 실제로 질의하는 날은 그 커서를 따라가야 한다 — §8-76 의 요지."""
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    _seed_summary(monkeypatch, tmp_path, [])
    now = datetime.now(timezone.utc)
    _seed_run(db_path, "arxiv", "done", now - timedelta(days=40), now - timedelta(days=1),
              (now - timedelta(days=1)).isoformat())
    _seed_run(db_path, "s2", "partial", now - timedelta(days=40), now - timedelta(days=30),
              (now - timedelta(days=30)).isoformat())

    seen = {}

    async def spy_arxiv(client, query, since, **kw):
        seen["since"] = since
        return {"status": "done", "papers": [], "pages_used": 1, "query": query,
                "until": datetime.now(timezone.utc).isoformat()}

    async def no_s2(client, keywords, since, until, **kw):
        return {"papers": [], "status": "done", "query": "q",
                "keywords_failed": 0, "keywords_searched": 1, "keywords_truncated": 0}

    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", spy_arxiv)
    monkeypatch.setattr(rps.s2_delta, "keywords_for_s2", lambda profile, **kw: ["defect detection"])
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", no_s2)

    asyncio.run(rps.scan_profile(db_path, "team_ai", None, max_pages=2))
    age = (datetime.now(timezone.utc) - seen["since"]).days
    assert age >= 29, f"질의하는 S2 가 뒤처졌는데 창이 안 따라갔다({age}일)"


def test_backfill_also_records_the_arrival_identity_of_synthetic_ids(tmp_path):
    """**합성 ID 는 도착할 때의 신원이 아니다**(AGENTS.md 함정 목록).
    `pdf-<해시>` 는 본문을 받은 뒤 생기는 저장용 ID이고, 그 논문이 검색으로
    다시 도착할 때의 키는 `doi:...` 다. 합성 ID 로만 소급하면 다음 날 조회 키와
    안 맞아 필터를 그냥 통과한다 — 실측(2026-09-08): 합성 ID 요약 9편 중 6편이
    도착 키로는 기록에 없었다."""
    import sqlite3 as _sq
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)
    with _sq.connect(db_path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS papers "
                    "(arxiv_id TEXT PRIMARY KEY, title TEXT, source TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS summaries (arxiv_id TEXT PRIMARY KEY, path TEXT)")
        con.execute("INSERT INTO papers VALUES ('pdf-abc','저널 논문','open-access: 10.1/x')")
        con.execute("INSERT INTO summaries VALUES ('pdf-abc','')")

    rp.backfill_shown_from_summaries(db_path, "team_ai")
    shown = rp.already_shown(db_path, "team_ai")

    # 저장 시 신원과 도착 시 신원 **둘 다** 기록돼야 한다
    assert rp.paper_key({"arxiv_id": "pdf-abc", "title": "저널 논문"}) in shown
    arrival = rp.paper_key({"arxiv_id": None, "doi": "10.1/x", "title": "저널 논문"})
    assert arrival in shown, "도착 시 키가 없어 내일 다시 후보로 올라온다"


def test_no_recipient_does_not_consume(tmp_path, monkeypatch):
    """**수신자가 없으면 메일이 안 간 것이다**(2026-09-08, 외부 검토).
    고장은 아니라 경보는 안 울리지만, 소비 처리도 하면 안 된다 — 나중에
    수신자를 등록해도 그 논문은 후보에서 이미 빠져 있다."""
    db_path = tmp_path / "t.db"
    _setup_profile(db_path)          # 수신자를 넣지 않는다
    _seed_summary(monkeypatch, tmp_path, [])
    _mock_arxiv_pages(monkeypatch, [_agent_paper("p1", 1)])
    calls = []
    monkeypatch.setattr(rps.research_profile, "mark_shown",
                        lambda db, pid, papers: calls.append(papers))

    summary = asyncio.run(rps.scan_all_profiles(db_path, None, max_pages=2, send=True))
    assert summary["team_ai"]["delivery"] == rps.DELIVERY_NO_RECIPIENT
    assert not rps.delivery_failed(summary["team_ai"]["delivery"])   # 경보는 안 울린다
    assert calls == [], "메일이 안 갔는데 소비 처리했다"


def test_s2_실행기록이_씨앗_지문을_남긴다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: S2 에 core 지문을 넘기는 것(운영 경로를 실제로 부른다).

    씨앗만 바꿨는데 S2 커서가 리셋되지 않으면 **새 씨앗이 과거를 영영 못
    본다** — §8-21 이 core 에서 막았던 사고가 씨앗에서 재발한다.

    앞선 판의 이 테스트는 `next_since` 를 테스트 안에서 직접 불러서, 스캔이
    어떤 지문을 넘기는지는 보지 않았다. 돌연변이(`"s2": s2_signature` →
    `"s2": signature`)를 넣었더니 **783개가 전부 통과했다.** 그래서 기록된
    search_runs 행을 읽는 쪽으로 바꿨다.
    """
    db_path = tmp_path / "t.db"
    rp.create_profile(db_path, "team_ai", "우리팀",
                      core_topics=["agent", "digital twin"],
                      core_weights={"agent": 0.6, "digital twin": 0.6},
                      s2_seeds=["world model"], max_items=5)

    async def _s2(client, keywords, since, until, limit=100):
        return {"papers": [], "status": "done", "query": f"S2×{len(keywords)}",
                "keywords_failed": 0}

    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", _s2)

    async def fake_throttled(client, params):
        class FakeResp:
            text = "<fake/>"
        return FakeResp()

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _x: [])

    asyncio.run(rps.scan_profile(db_path, "team_ai", client=None))

    with sqlite3.connect(db_path) as con:
        sigs = dict(con.execute(
            "SELECT source, topic_signature FROM search_runs WHERE profile_id='team_ai'"))

    core_sig = rp.topic_signature(["agent", "digital twin"])
    seed_sig = rp.topic_signature(["world model"])
    assert sigs["arxiv"] == core_sig, "arXiv 는 core 전부를 OR 로 던지니 core 지문이어야 한다"
    assert sigs["s2"] == seed_sig, "S2 가 씨앗이 아니라 core 지문을 남겼다"
    assert sigs["s2"] != sigs["arxiv"], "두 소스가 같은 지문을 쓰면 분리가 무의미하다"


def test_씨앗을_바꾸면_S2_창이_과거로_돌아간다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: next_since 에 소스별 지문 대신 core 지문을 넘기는 것.

    앞 테스트는 **기록된** 지문만 본다. 지문은 두 곳에 쓰인다 — 기록과
    `next_since` 판정이고, 실제로 창을 움직이는 것은 후자다. 그래서 창이
    어디서 시작했는지를 본다.

    두 소스 모두 최근 커서를 갖고 있는데 **S2 씨앗만 바뀐** 상황을 만든다.
    지문이 소스별이면 S2 는 불일치로 과거 7일까지 돌아가고, 창은 둘 중 더
    뒤처진 쪽을 따라가므로(§8-76) `since` 가 7일 전이 된다. core 지문을
    S2 에도 쓰면 일치로 판정해 최근 커서를 이어받는다.
    """
    db_path = tmp_path / "t.db"
    rp.create_profile(db_path, "team_ai", "우리팀",
                      core_topics=["agent", "digital twin"],
                      core_weights={"agent": 0.6, "digital twin": 0.6},
                      s2_seeds=["world model"], max_items=5)

    now = datetime.now(timezone.utc)
    core_sig = rp.topic_signature(["agent", "digital twin"])
    # 두 소스 다 "한 시간 전까지 봤다"는 이력을 남긴다. S2 이력의 지문은
    # **옛 씨앗**(=core 지문)이다 — 씨앗을 막 바꾼 직후의 모습이다.
    for src in ("arxiv", "s2"):
        rp.record_run(db_path, "team_ai", src, "q", now - timedelta(hours=3),
                      now - timedelta(hours=1), "done", 5, signature=core_sig)

    async def _s2(client, keywords, since, until, limit=100):
        return {"papers": [], "status": "done", "query": "S2", "keywords_failed": 0}

    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", _s2)

    async def fake_throttled(client, params):
        class FakeResp:
            text = "<fake/>"
        return FakeResp()

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_throttled)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _x: [])

    asyncio.run(rps.scan_profile(db_path, "team_ai", client=None))

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT window_from FROM search_runs WHERE profile_id='team_ai' "
            "ORDER BY rowid DESC LIMIT 2").fetchall()
    starts = [datetime.fromisoformat(r[0]) for r in rows]
    assert all(s < now - timedelta(days=6) for s in starts), (
        f"씨앗이 바뀌었는데 창이 최근 커서를 이어받았다: {starts}")



def test_pending_reproduction_sends_reason_in_same_email(tmp_path, monkeypatch):
    """대기 상한 뒤 발송을 막거나 시간 초과 사유를 숨기면 실패한다."""
    import email_delivery
    import sqlite3
    db = tmp_path / "mail.db"
    _setup_profile(db)
    _mock_arxiv_three_agent_papers(monkeypatch)
    rps.research_profile.add_recipient(db, "team_ai", "reader@example.com")
    monkeypatch.setattr(rps, "_summary_exists", lambda _: False)
    async def pending(client, aid, **kwargs):
        assert kwargs["wait_for_repro"] is True
        return {"status": "done", "arxiv_id": aid,
                "reproduction": {"status": "timeout", "success": None,
                    "reason": "대기 상한(60분) 초과 — 작업 완료 미확인"}}
    monkeypatch.setattr(rps.batch_summarize, "_process_paper", pending)
    mails = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *a: mails.append(a))
    summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
    assert summary["team_ai"]["status"] == "ok"
    assert rps._exit_code(summary) == 0
    assert len(mails) == 1
    for mail in (mails[0][0], mails[0][3]):
        assert "대기 상한(60분) 초과" in mail
        assert "작업 완료 미확인" in mail
        assert "이전에 보낸 논문의 상태 소식" not in mail
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT last_digest FROM profiles WHERE profile_id='team_ai'").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM profile_shown").fetchone()[0] == 3
