"""⑧⑨ M9 — 실제 임시 DB와 기존 발송 진입점에서 실패·중복·상태 비교를 검증한다."""
import asyncio
import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import digest
import evidence_state as es
import research_profile as rp
import run_profile_scan as rps
import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    for name in ("DATA_DIR", "TEXT_DIR", "PDF_DIR", "SUMMARY_DIR", "IMAGE_DIR", "REPRO_DIR"):
        monkeypatch.setattr(storage, name, tmp_path / name)
    path = tmp_path / "m9.db"
    monkeypatch.setattr(storage, "DB_PATH", path)
    storage.init_storage(path)
    rp.create_profile(path, "team", "팀", ["robot"])
    es.init_db(path)
    return path


def seed(db, aid="2609.00001", status=0, source="arxiv", days=1):
    p = {"arxiv_id": aid, "title": "Robot " + aid, "source": source,
         "_score": {"priority": 1, "core_hits": ["robot"]}}
    if aid.startswith("pdf-"):
        p["doi"] = "10.1234/robot"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers(arxiv_id,title,source,is_retracted) VALUES (?,?,?,?)",
                    (aid, p["title"], source, status))
    rp.mark_shown(db, "team", [p])
    with sqlite3.connect(db) as con:
        con.execute("UPDATE profile_shown SET shown_at=? WHERE paper_key=?",
                    ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), rp.paper_key(p)))
    return p


def snap(db, p):
    return es.capture(db, p, datetime.now(timezone.utc))


def test_semantic_diff_ignores_other_axes_and_formatting(db):
    state = snap(db, seed(db))["state"]
    changed = copy.deepcopy(state)
    changed.update(coverage={"ratio": 0.1}, verification={"total": 4}, injection="flag", label="새 문구")
    assert es.changed_axes(state, changed) == []
    changed["retraction"] = 1
    assert es.changed_axes(state, changed) == ["retraction"]


def test_generation_does_not_advance_delivery_baseline(db):
    p = seed(db)
    current = snap(db, p)
    first = es.pending_updates(db, "team", "a@example.com", [current], set())
    assert first[0]["before"] is None
    assert es.pending_updates(db, "team", "a@example.com", [current], set()) == first
    es.acknowledge(db, "team", "a@example.com", [current], "delivery1")
    es.acknowledge(db, "team", "a@example.com", [current], "delivery1")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM evidence_notifications").fetchone()[0] == 1
    assert es.pending_updates(db, "team", "a@example.com", [current], set()) == []
    assert es.pending_updates(db, "team", "b@example.com", [current], set())[0]["before"] is None
    assert es.pending_updates(db, "other", "a@example.com", [current], set())[0]["before"] is None


def test_retraction_changes_survive_failed_send_and_clear_after_success(db, monkeypatch):
    import email_delivery
    p = seed(db)
    rp.add_recipient(db, "team", "a@example.com")
    es.acknowledge(db, "team", "a@example.com", [snap(db, p)])
    with sqlite3.connect(db) as con:
        con.execute("UPDATE papers SET is_retracted=1")
    changed = snap(db, p)
    p["_delivered_state"] = changed["state"]
    result = {"papers": [p], "_evidence_states": [changed]}
    def fail(*args):
        raise RuntimeError("SMTP unavailable")
    monkeypatch.setattr(email_delivery, "send_digest_email", fail)
    assert rps.delivery_failed(rps._deliver(db, "team", result, "旧본문"))
    assert es.pending_updates(db, "team", "a@example.com", [changed], set())[0]["axes"] == ["retraction"]
    mails = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: mails.append(args))
    assert rps._deliver(db, "team", result, "본문") == "발송 완료 → 1명"
    assert "철회된 논문" in mails[0][0] and "철회된 논문" in mails[0][3]
    assert "이전에 보낸 논문의 상태 소식" not in mails[0][0]
    assert es.pending_updates(db, "team", "a@example.com", [changed], set()) == []
    rps._deliver(db, "team", result, "본문")
    assert "상태 변경" not in mails[-1][0]


def test_partial_recipient_failure_does_not_acknowledge_the_failed_recipient(db, monkeypatch):
    import email_delivery
    p = seed(db)
    for recipient in ("a@example.com", "b@example.com"):
        rp.add_recipient(db, "team", recipient)
    def send(text, subject, recipients, html):
        assert len(recipients) == 1
        if recipients == ["b@example.com"]:
            raise RuntimeError("refused")
    monkeypatch.setattr(email_delivery, "send_digest_email", send)
    current = snap(db, p)
    assert rps.delivery_failed(rps._deliver(db, "team", {"papers": [p], "_evidence_states": [current]}, "본문"))
    assert es.pending_updates(db, "team", "a@example.com", [current], set()) == []
    assert es.pending_updates(db, "team", "b@example.com", [current], set())


def test_no_recipient_does_not_acknowledge(db):
    p = seed(db)
    rps._deliver(db, "team", {"papers": [p], "_evidence_states": [snap(db, p)]}, "본문")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM evidence_notifications").fetchone()[0] == 0


def test_old_updates_are_neither_sent_nor_acknowledged(db, monkeypatch):
    import email_delivery
    monkeypatch.setattr(es, "NOTICE_LIMIT", 1)
    states = [snap(db, seed(db, aid)) for aid in ("2609.00001", "2609.00002")]
    rp.add_recipient(db, "team", "a@example.com")
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: None)
    rps._deliver(db, "team", {"papers": [], "_evidence_states": states}, "본문")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM evidence_notifications").fetchone()[0] == 0


def test_new_content_is_not_duplicated_as_an_update(db, monkeypatch):
    import email_delivery
    p = seed(db)
    current = snap(db, p)
    rp.add_recipient(db, "team", "a@example.com")
    assert es.pending_updates(db, "team", "a@example.com", [current], {rp.paper_key(p)}) == []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: None)
    rps._deliver(db, "team", {"papers": [p], "_evidence_states": [current]}, "본문")
    assert es.pending_updates(db, "team", "a@example.com", [current], set()) == []


def test_late_repro_success_is_detected_without_starting_docker(db, monkeypatch):
    import docker_runner
    monkeypatch.setattr(docker_runner, "launch_background", lambda *args: pytest.fail("new trigger"))
    p = seed(db)
    previous = snap(db, p)
    es.acknowledge(db, "team", "a@example.com", [previous])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO repro_results(arxiv_id,repo_url,attempt,success,exit_code,stage) "
                    "VALUES (?,?,1,1,0,'run')", (p["arxiv_id"], "https://example.com/code"))
    changed = snap(db, p)
    updates = es.pending_updates(db, "team", "a@example.com", [changed], set())
    assert updates[0]["axes"] == ["repro"]
    text = digest.generate_digest({"papers": [], "state_updates": updates}, "팀")
    assert "이전에 보낸 논문의 상태 소식" not in text
    assert "재현 성공" not in text


def test_recheck_includes_suspect_and_preserves_previous_value_on_failure(db, monkeypatch):
    seed(db, status=2)
    p = es.tracked_papers(db, "team", datetime.now(timezone.utc))[0]
    async def unavailable(*args):
        return None
    monkeypatch.setattr(es.retraction, "openalex_is_retracted", unavailable)
    result = asyncio.run(es.recheck_retractions(db, [p], object(), "test-key"))
    assert result["checked"] == 1 and result["resolved"] == 0
    assert snap(db, p)["state"]["retraction"] == 2
    assert asyncio.run(es.recheck_retractions(db, [p], object(), "test-key"))["checked"] == 0


def test_zero_can_become_retracted_after_cross_check_and_doi_is_supported(db, monkeypatch):
    seed(db, aid="pdf-abc", source="open-access: https://doi.org/10.1234/robot")
    papers = es.tracked_papers(db, "team", datetime.now(timezone.utc))
    assert len(papers) == 1 and papers[0]["paper_key"] == "doi:10.1234/robot"
    async def flagged(client, doi, key):
        assert doi == "10.1234/robot"
        return True
    async def confirmed(*args):
        return ["retraction"]
    monkeypatch.setattr(es.retraction, "openalex_is_retracted", flagged)
    monkeypatch.setattr(es.retraction, "crossref_update_types", confirmed)
    result = asyncio.run(es.recheck_retractions(db, papers, object(), "test-key"))
    assert result["resolved"] == 1
    assert snap(db, papers[0])["state"]["retraction"] == 1


def test_recheck_honors_count_limit_and_lookback(db, monkeypatch):
    for i in range(5):
        seed(db, f"2609.0000{i}", days=1)
    seed(db, "2609.00009", days=31)
    papers = es.tracked_papers(db, "team", datetime.now(timezone.utc))
    assert len(papers) == 5
    async def healthy(*args):
        return False
    monkeypatch.setattr(es.retraction, "openalex_is_retracted", healthy)
    monkeypatch.setattr(es, "RECHECK_LIMIT", 2)
    assert asyncio.run(es.recheck_retractions(db, papers, object(), "test-key"))["checked"] == 2


def test_recheck_timeout_does_not_clear_state(db, monkeypatch):
    p = seed(db, status=2)
    async def slow(*args):
        await asyncio.sleep(10)
    monkeypatch.setattr(es.retraction, "openalex_is_retracted", slow)
    monkeypatch.setattr(es, "RECHECK_SECONDS", 0.01)
    result = asyncio.run(es.recheck_retractions(db, [dict(p, is_retracted=2)], object(), "test-key"))
    assert result["resolved"] == 0
    assert snap(db, p)["state"]["retraction"] == 2


def test_repro_retry_advice_is_not_automatic_execution(db):
    p = seed(db, days=15)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO repro_results(arxiv_id,repo_url,attempt,success,stage,fail_detail) "
                    "VALUES (?,?,1,0,'clone','repo_not_found')", (p["arxiv_id"], "https://example.com/code"))
    tracked = es.tracked_papers(db, "team", datetime.now(timezone.utc))[0]
    assert snap(db, tracked)["state"]["repro"]["review_due"] is True
    rp.add_recipient(db, "team", "a@example.com")
    result = {"papers": []}
    asyncio.run(es.prepare(db, "team", result, None, None))
    assert "재시도 버튼" not in digest.generate_digest(result, "팀")
    assert result["_evidence_states"] == []


def test_snapshot_is_frozen_before_rendering(db):
    p = seed(db, status=1)
    p["_delivered_state"] = snap(db, p)["state"]
    with sqlite3.connect(db) as con:
        con.execute("UPDATE papers SET is_retracted=0")
    assert "철회된 논문" in digest.generate_digest({"papers": [p]}, "팀")
    assert "철회된 논문" in digest.generate_digest_html({"papers": [p]}, "팀")


def test_failed_state_lookup_does_not_block_regular_email(db, monkeypatch):
    import email_delivery
    p = seed(db)
    rp.add_recipient(db, "team", "a@example.com")
    current = snap(db, p)
    def unavailable(*args):
        raise sqlite3.OperationalError("state store unavailable")
    monkeypatch.setattr(es, "pending_updates", unavailable)
    mails = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: mails.append(args))
    result = {"papers": [p], "_evidence_states": [current]}
    assert rps._deliver(db, "team", result, "본문") == "발송 완료 → 1명"
    assert "이전에 보낸 논문의 상태 소식" not in mails[0][0]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM evidence_notifications").fetchone()[0] == 1



def test_prepare_observes_only_current_papers_and_handles_abstract_only(db, monkeypatch):
    seed(db, "2609.00001")
    current = seed(db, "2609.00002", status=1)
    abstract = {"title": "Abstract only", "doi": "10.1234/abstract"}
    async def forbidden(*args):
        pytest.fail("old or confirmed-retracted paper must not be queried")
    monkeypatch.setattr(es.retraction, "openalex_is_retracted", forbidden)
    result = {"papers": [current, abstract], "state_updates": [{"title": "stale"}]}
    asyncio.run(es.prepare(db, "team", result, object(), "test-key"))
    assert result["state_recheck"]["checked"] == 0
    assert {i["paper_key"] for i in result["_evidence_states"]} == {rp.paper_key(current), rp.paper_key(abstract)}
    assert "state_updates" not in result
    assert current["_delivered_state"]["retraction"] == 1
