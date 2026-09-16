"""⑨ 운영 화면 자료(2026-09-16) — 발송 회차 기록 · 복원 · 키워드 표 · 반응 · 변경 이력 · 시스템 상태. Streamlit 없이 돈다.

각 테스트의 docstring 에 "무엇을 망가뜨리면 실패하는가"를 적었다.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_maintenance as am
import feedback_links as fl
import mail_ledger
import ops_dashboard as od
import research_profile as rp

KST = timezone(timedelta(hours=9))


def _paper(aid, title, hits):
    return {"arxiv_id": aid, "title": title, "_score": {"priority": 1.0, "core_hits": hits, "domain_hits": [], "venue_hit": None}}


def _react(db, issue, paper_key, action, status=fl.STATUS_VALID, when=None, recipient="r1"):
    fl.init_db(db)
    when = when or datetime.now(timezone.utc)
    tid = f"{issue}-{recipient}-{paper_key}-{action}-{status}"
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO feedback_tokens (tid, issue_id, profile_id, item_no, paper_key, recipient_hash, position, "
                    "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (tid, issue, "p", "P1", paper_key, recipient, 1, when.isoformat(), 2_000_000_000))
        con.execute("INSERT INTO feedback_events (event_id, tid, action, received_at, status, imported_at) VALUES (?,?,?,?,?,?)",
                    (f"{tid}-e-{when.isoformat()}", tid, action, when.isoformat(), status, when.isoformat()))


def _observe(db, paper_key, hits, when=None):
    rp.init_db(db)
    when = when or datetime.now(timezone.utc)
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot) VALUES (?,?,?,?)",
                    (f"s-{paper_key}", "p", when.isoformat(), "{}"))
        con.execute("INSERT INTO candidate_observations (scan_id, profile_id, paper_key, observed_at, core_hits) VALUES (?,?,?,?,?)",
                    (f"s-{paper_key}", "p", paper_key, when.isoformat(), json.dumps(hits)))


def test_ledger_reconstructs_days_before_it_existed_but_not_days_it_covers(tmp_path):
    """이 테스트가 잡는 것: 표 도입 전 발송(profile_shown)을 화면에서 잃는 것, 표가 있는 날을 복원본과 이중으로 세는 것,
    복원본 링크를 arXiv 키에서 못 만드는 것."""
    db = tmp_path / "l.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    old = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)          # KST 9/10 09:30
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) VALUES ('p',?,?,?)",
                        [("2609.00001", "Old A", old.isoformat()), ("doi:10.1/x", "Old B", (old + timedelta(minutes=1)).isoformat()),
                         ("2609.00009", "Same day as ledger", datetime(2026, 9, 16, 0, 32, tzinfo=timezone.utc).isoformat())])
    mail_ledger.record_issue(db, "i-0916", "p", "[연구 동향 브리핑(P)] 2026-09-16", [_paper("2609.00009", "New", ["alpha"])], 1, 1,
                             when=datetime(2026, 9, 16, 0, 31, tzinfo=timezone.utc))
    # 표가 생긴 날 **그 전**에 나간 논문(외부 검토 2026-09-16: 날짜 단위로 가리면 사라진다)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) VALUES ('p',?,?,?)",
                    ("2609.00007", "Before ledger, same day", datetime(2026, 9, 15, 20, 30, tzinfo=timezone.utc).isoformat()))
    issues = mail_ledger.list_issues(db, "p")
    assert [(i["day"], i["source"], i["paper_count"]) for i in issues] == [
        ("2026-09-16", "ledger", 1), ("2026-09-16", "legacy", 1), ("2026-09-10", "legacy", 2)]
    legacy = issues[2]["items"]
    assert legacy[0]["link"] == "https://arxiv.org/abs/2609.00001" and legacy[1]["link"] == "https://doi.org/10.1/x"
    assert mail_ledger.counts(db, "p") == {"issues": 3, "papers": 4, "failed_issues": 0,
                                           "last_sent_at": "2026-09-16T00:31:00+00:00", "last_status": "sent"}


def test_overview_counts_mails_reactions_and_schedule(tmp_path):
    """이 테스트가 잡는 것: 격리·취소된 반응을 유효로 세는 것, 실패 회차를 발송 수에 넣는 것, manual 주기를 안 보여 주는 것."""
    db = tmp_path / "o.db"
    rp.create_profile(db, "p", "로봇 — 설명", ["alpha", "beta"], s2_seeds=["alpha"], exclude=["x"], schedule_frequency="manual")
    rp.add_recipient(db, "p", "a@x.com")
    mail_ledger.record_issue(db, "i1", "p", "s", [_paper("k1", "A", ["alpha"]), _paper("k2", "B", ["beta"])], 1, 1)
    mail_ledger.record_issue(db, "i2", "p", "s", [_paper("k3", "C", ["alpha"])], 1, 0)
    _react(db, "i1", "k1", "more")
    _react(db, "i1", "k2", "out")
    _react(db, "i1", "k2", "useful", status="quarantined_prefetch", recipient="r2")
    # 같은 수신자가 같은 논문에 두 번 누른 것은 마지막 하나만 센다 — 학습(feedback_weights)과 같은 기준(외부 검토 2026-09-16)
    _react(db, "i1", "k1", "more", when=datetime.now(timezone.utc) + timedelta(minutes=1))
    _react(db, "i1", "k1", "useful", when=datetime.now(timezone.utc) + timedelta(minutes=2))
    o = od.profile_overview(db, "p")
    assert o["field"] == "로봇" and o["schedule"] == "manual" and o["recipients"] == ["a@x.com"]
    assert o["keywords"] == {"core": 2, "seed": 1, "domain": 0, "exclude": 1}
    assert o["mails"]["issues"] == 1 and o["mails"]["papers"] == 2 and o["mails"]["failed_issues"] == 1
    assert o["reactions"] == {"more": 0, "useful": 1, "out": 1, "valid": 2, "other": 1}


def test_keyword_table_links_hits_reactions_and_weight_history(tmp_path):
    """이 테스트가 잡는 것: 키워드 적중 편수를 창 밖 관측까지 세는 것, 반응이 그 논문의 키워드에 안 붙는 것, 가중치 추이가 첫 값을
    잃는 것, 에이전트가 넣은 키워드의 출처가 사용자로 나오는 것."""
    db = tmp_path / "k.db"
    rp.create_profile(db, "p", "P", ["alpha"], core_weights={"alpha": 1.0})
    rp.create_profile(db, "p", "P", ["alpha", "beta"], core_weights={"alpha": 1.2, "beta": 0.6}, origin="agent")
    now = datetime.now(timezone.utc)
    _observe(db, "k1", ["alpha"], now - timedelta(days=1))
    _observe(db, "k2", ["alpha", "beta"], now - timedelta(days=2))
    _observe(db, "k-old", ["alpha"], now - timedelta(days=40))
    _react(db, "i1", "k1", "more")
    _react(db, "i1", "k2", "out")
    # 반응 **뒤** 관측에서 k2 의 적중이 beta 만으로 바뀌어도, 반응은 반응 당시 적중(alpha·beta)에 붙는다(외부 검토 2026-09-16)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO candidate_observations (scan_id, profile_id, paper_key, observed_at, core_hits) VALUES (?,?,?,?,?)",
                    ("s-later", "p", "k2", (now + timedelta(hours=1)).isoformat(), json.dumps(["beta"])))
    rows = {r["term"]: r for r in od.keyword_table(db, "p", now=now)}
    assert rows["alpha"]["hits_28d"] == 2 and rows["alpha"]["more"] == 1 and rows["alpha"]["out"] == 1
    assert rows["beta"]["hits_28d"] == 1 and rows["beta"]["out"] == 1 and rows["beta"]["more"] == 0
    assert rows["alpha"]["origin"] == "user" and rows["beta"]["origin"] == "agent"
    assert rows["alpha"]["first_weight"] == 1.0 and rows["alpha"]["weight"] == 1.2 and [h[2] for h in rows["alpha"]["history"]] == [1.0, 1.2]


def test_issue_reactions_and_reaction_log_show_status(tmp_path):
    """이 테스트가 잡는 것: 회차 논문에 그 회차의 반응이 안 붙는 것(다른 회차 반응이 섞이는 것), 반응 목록에서 격리 상태가 유효처럼 보이는 것."""
    db = tmp_path / "r.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    mail_ledger.record_issue(db, "i1", "p", "s", [_paper("k1", "A", ["alpha"])], 1, 1)
    mail_ledger.record_issue(db, "i2", "p", "s", [_paper("k1", "A", ["alpha"])], 1, 1)
    _react(db, "i1", "k1", "more")
    _react(db, "i2", "k1", "out", status="quarantined_burst")
    issues = {i["issue_id"]: i for i in od.issues_with_reactions(db, "p")}
    assert issues["i1"]["items"][0]["reactions"] == {"more": 1, "useful": 0, "out": 0} and issues["i1"]["reactions"] == 1
    assert issues["i2"]["items"][0]["reactions"] == {"more": 0, "useful": 0, "out": 0}
    log = od.reaction_log(db, "p")
    assert [(l["title"], l["action"], l["valid"]) for l in log] == [("A", "관심 밖", False), ("A", "더 보고 싶음", True)]
    assert log[0]["status"] == "연속 클릭 격리"


def test_revision_history_explains_each_change(tmp_path):
    """이 테스트가 잡는 것: 이력에서 추가·삭제·가중치 변경 중 하나를 빠뜨리는 것, 출처(사용자/반응/에이전트)를 잃는 것, 최신이 먼저 오지 않는 것."""
    db = tmp_path / "h.db"
    rp.create_profile(db, "p", "P", ["alpha"], core_weights={"alpha": 1.0}, s2_seeds=["alpha"])
    rp.create_profile(db, "p", "P", ["alpha"], core_weights={"alpha": 1.1}, origin="feedback", note="feedback: alpha 1.0->1.1")
    rp.create_profile(db, "p", "P", ["alpha", "beta"], core_weights={"alpha": 1.1, "beta": 0.6}, exclude=["noise"], origin="agent")
    rp.create_profile(db, "p", "P", ["beta"], core_weights={"beta": 0.6}, exclude=["noise"], origin="user")
    hist = od.revision_history(db, "p")
    assert [h["revision"] for h in hist] == [4, 3, 2, 1]
    assert hist[0]["origin_label"] == "사용자" and hist[0]["changes"] == ["− 키워드 alpha"]
    assert hist[1]["origin_label"] == "에이전트" and hist[1]["changes"] == ["+ 키워드 beta 0.6", "+ 제외어 noise"]
    assert hist[2]["origin_label"] == "반응" and hist[2]["changes"] == ["가중치 alpha 1 → 1.1"]
    assert hist[3]["changes"] == ["초기 상태 · 키워드 1개"]


def test_agent_history_and_status_label(tmp_path):
    """이 테스트가 잡는 것: 에이전트 실행의 제안·판정·적용·기각을 화면에 못 싣는 것, 실패 사유가 코드 그대로 보이는 것."""
    db = tmp_path / "a.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    am.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, error, base_revision, new_revision, proposal_json, "
                    "judge_json, applied_json, rejected_json) VALUES ('p','2026-W38',?, 'applied', NULL, 1, 2, ?, ?, ?, ?)",
                    (datetime.now(timezone.utc).isoformat(),
                     json.dumps({"actions": [{"op": "add_keyword", "term": "x", "weight": 0.5, "evidence": ["R1"], "reason": "a"}]}),
                     json.dumps({"reviews": [{"index": 0, "verdict": "accept", "reason": "ok"}], "actions": [{"op": "add_keyword", "term": "x", "weight": 0.5, "evidence": ["R1"], "reason": "a"}]}),
                     json.dumps([{"op": "add_keyword", "term": "x", "weight": 0.5, "evidence": ["R1"], "reason": "a"}]),
                     json.dumps([])))
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, error) VALUES ('p','2026-W39',?, 'failed', 'timeout')",
                    ((datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),))
    hist = od.agent_history(db, "p")
    assert [h["week"] for h in hist] == ["2026-W39", "2026-W38"]
    assert hist[1]["proposed"][0]["term"] == "x" and hist[1]["reviews"][0]["verdict"] == "accept" and len(hist[1]["applied"]) == 1
    assert od.agent_status_label(hist[0]) == "2026-W39 실패 — 시간 초과"
    assert od.agent_status_label({"week": "2026-W40", "status": "applied", "new_revision": 3}) == "2026-W40 변경 적용 (rev 3)"


def test_system_status_reads_last_daily_run_and_next_times(tmp_path):
    """이 테스트가 잡는 것: 로그의 마지막 실행·종료 코드를 잘못 읽는 것, 다음 새벽·금요일 시각을 KST 로 못 잡는 것, 오늘 실행 여부 오판."""
    db = tmp_path / "s.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daily_scan.log").write_text(
        "=== 2026-09-14T20:00:01Z 시작 (pid 1) ===\nblah\n=== 2026-09-14T21:09:23Z 종료 (exit 2) ===\n"
        "=== 2026-09-15T20:00:01Z 시작 (pid 2) ===\n", encoding="utf-8")
    now = datetime(2026, 9, 15, 21, 0, tzinfo=timezone.utc)          # KST 9/16 06:00 수요일
    s = od.system_status(db, tmp_path, now=now)
    assert s["daily"]["started_at"] == "2026-09-15T20:00:01+00:00" and s["daily"]["finished_at"] is None
    assert s["daily_ran_today"] is True
    assert s["next_daily_kst"] == "09-17 05:00" and s["next_weekly_kst"].startswith("09-18")
    earlier = od.system_status(db, tmp_path, now=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc))
    assert earlier["daily_ran_today"] is False       # 로그 마지막 실행은 9/16 이지만 now 가 9/15 인 경우 — 순서가 뒤집혀도 오늘로 안 센다


def test_rollback_keeps_manual_schedule(tmp_path):
    """외부 검토 2026-09-16. 이 테스트가 잡는 것: 되돌리기가 프로필 주기를 기본값 daily 로 되돌려 수동 프로필이 새벽 cron 에 들어가는 것."""
    import profile_advisor
    db = tmp_path / "rb.db"
    rp.create_profile(db, "p", "P", ["alpha"], schedule_frequency="manual", schedule_time="09:00")
    rp.create_profile(db, "p", "P", ["alpha", "beta"], schedule_frequency="manual", schedule_time="09:00")
    res = profile_advisor.rollback(db, "p", 1, reason="test")
    assert res["rolled_back"] and rp.get_profile(db, "p")["core_topics"] == ["alpha"]
    assert rp.get_schedule(db, "p") == ("manual", "09:00")


# ---------------------------------------------------------------- 시스템·논문 DB 페이지(2026-09-16)
def test_recent_runs_parses_blocks_exit_warnings_api_and_delivery(tmp_path):
    """이 테스트가 잡는 것: 실행 블록의 소요·종료 코드·경고 수·API 호출 합계·프로필별 발송 결과(끝 JSON)를 잘못 읽는 것, 스킵·수동 중지·
    종료 없는 블록을 정상으로 보이는 것, 최신이 먼저 오지 않는 것."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daily_scan.log").write_text(
        "=== 2026-09-13T20:00:01Z 시작 (pid 1) ===\n  [경고] a\n  [경고] b\n"
        "  [계측] 실행 합계: API 호출 32회 — arxiv 5(429:5) · gemini 8\n{\n  \"team_x\": {\n    \"status\": \"ok\",\n"
        "    \"delivery\": \"발송 완료 → 2명\"\n  }\n}\n=== 2026-09-13T21:07:19Z 종료 (exit 2) ===\n"
        "=== 2026-09-14T20:00:01Z 시작 (pid 2) ===\n=== 2026-09-14T20:00:02Z 스킵 — 이전 실행이 아직 진행 중 ===\n"
        "=== 2026-09-14T22:00:00Z 시작 (pid 3) ===\n  [경고] c\n=== 2026-09-14T22:30:00Z 수동 중지 — arXiv 429 지속, 사용자 요청 ===\n"
        "=== 2026-09-15T20:00:00Z 시작 (pid 4) ===\n  [경고] d\n", encoding="utf-8")
    runs = od.recent_runs(tmp_path, limit=10)
    assert [r["pid"] for r in runs] == [4, 3, None, 2, 1]
    assert (runs[0]["exit"], runs[0]["minutes"]) == (None, None)
    assert (runs[1]["exit"], runs[1]["minutes"], runs[1]["warnings"]) == ("stopped", 30.0, 1)
    assert runs[2]["exit"] == "skipped"
    # pid 2 는 스킵 줄 때문에 닫히지 않는다 — 종료 줄이 없으니 "종료 기록 없음"(2026-09-16: 스킵 줄이 실제 실행을 0분 스킵으로 보이게 했다)
    assert (runs[3]["exit"], runs[3]["minutes"]) == (None, None)
    first = runs[4]
    assert (first["exit"], first["minutes"], first["warnings"], first["api_calls"]) == (2, 67.3, 2, 32)
    assert first["profiles"] == {"team_x": {"status": "ok", "delivery": "발송 완료 → 2명"}}


def test_db_status_counts_tables_and_lists_newest_backups(tmp_path):
    """이 테스트가 잡는 것: 없는 표를 0 으로 속이는 것(없음 = None), 백업을 오래된 것부터 보이는 것, 마지막 정리 결과의 삭제 수를 못 읽는 것."""
    import db_retention
    db = tmp_path / "papers.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    db_retention.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO retention_runs (run_id, started_at, finished_at, applied, plan_json, result_json) VALUES (?,?,?,?,?,?)",
                    ("r", "2026-09-18T08:00:00+00:00", "2026-09-18T08:01:00+00:00", 1, "{}",
                     json.dumps({"tables": {"candidate_observations": {"deleted": 5}, "papers": {"deleted": 2}}, "files": {"count": 7}})))
    (tmp_path / "backups").mkdir()
    for name in ("papers_2026-09-12.db", "papers_2026-09-15.db"):
        (tmp_path / "backups" / name).write_bytes(b"x" * 10)
    d = od.db_status(db)
    assert d["tables"]["profile_revisions"] == 1 and d["tables"]["papers"] is None
    assert [b["name"] for b in d["backups"]] == ["papers_2026-09-15.db", "papers_2026-09-12.db"]
    assert (d["retention"]["rows_deleted"], d["retention"]["files_deleted"], d["retention"]["applied"]) == (7, 7, True)
    assert od.fmt_bytes(1536) == "1.5KB" and od.fmt_bytes(None) == "—"


def test_cron_entries_only_this_repo_and_none_when_unreadable(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 다른 저장소·주석 줄까지 이 시스템의 예약 작업으로 보이는 것, crontab 을 못 읽었는데 "예약 없음"으로 보이는 것."""
    import subprocess

    class R:
        returncode = 0
        stdout = f"# comment {tmp_path}\n0 5 * * * {tmp_path}/run_daily_scan.sh\n0 1 * * * /other/repo/job.sh\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert od.cron_entries(tmp_path) == [f"0 5 * * * {tmp_path}/run_daily_scan.sh"]
    R.returncode = 1
    assert od.cron_entries(tmp_path) is None


def test_paper_catalog_joins_delivery_by_id_or_title_and_filters(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 저널 논문(pdf-* 합성 ID, paper_key 가 title:/doi:)의 발송 프로필을 못 잇는 것, 검색어·프로필 필터가 안 먹는 것,
    재현 성공·실패·코드 단계를 잘못 보이는 것."""
    import code_ladder
    import server, storage
    db = tmp_path / "p.db"
    storage.init_storage(db)
    rp.init_db(db)
    code_ladder.init_db(db)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers (arxiv_id, title, abstract, fetched_at, source) VALUES ('2609.00001','Robot Grasping Now','tactile',?,NULL)", (now,))
        con.execute("INSERT INTO papers (arxiv_id, title, abstract, fetched_at, source) VALUES ('pdf-abc','Journal: Defect Detection!','x',?,'open-access: 10.1/y')", (now,))
        con.execute("INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) VALUES ('robot','2609.00001','Robot Grasping Now',?)", (now,))
        con.execute("INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) VALUES ('vision','title:journal defect detection','Journal  Defect Detection',?)", (now,))
        con.execute("INSERT INTO repro_results (arxiv_id, repo_url, success, stage, created_at) VALUES ('2609.00001','u1',0,'clone',?)", (now,))
        con.execute("INSERT INTO repro_results (arxiv_id, repo_url, success, stage, created_at) VALUES ('2609.00001','u2',1,'run',?)", (now,))
        con.execute("INSERT INTO code_ladder (arxiv_id, tier, full_name, query, searched_at) VALUES ('pdf-abc','analogous','o/r','[]',?)", (now,))
    rows = {r["arxiv_id"]: r for r in od.paper_catalog(db)}
    assert rows["2609.00001"]["profiles"] == ["robot"] and rows["2609.00001"]["repro"] == "성공"
    assert rows["pdf-abc"]["profiles"] == ["vision"] and rows["pdf-abc"]["source"] == "저널(OA)" and rows["pdf-abc"]["code_tier"] == "analogous"
    assert [r["arxiv_id"] for r in od.paper_catalog(db, query="tactile")] == ["2609.00001"]
    assert [r["arxiv_id"] for r in od.paper_catalog(db, profile_id="vision")] == ["pdf-abc"]
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(server, "DB_PATH", db)
    d = od.paper_detail(db, "2609.00001")
    assert d["link"] == "https://arxiv.org/abs/2609.00001" and len(d["repro"]) == 2 and d["summary_md"] == ""
    assert od.paper_detail(db, "nope") is None


def test_update_core_weights_is_a_user_revision_that_keeps_everything_else(tmp_path):
    """관리자 화면 가중치 편집. 이 테스트가 잡는 것: 가중치만 바꿨는데 검색어·제외어·주기를 잃는 것, 범위(0.35~2.0) 밖 값을 받는 것,
    바뀐 게 없는데 revision 을 만드는 것, 모르는 키워드를 새로 넣는 것."""
    db = tmp_path / "w.db"
    rp.create_profile(db, "p", "P", ["alpha", "beta"], core_weights={"alpha": 1.0, "beta": 0.6}, exclude=["x"],
                      s2_seeds=["alpha"], schedule_frequency="manual", schedule_time="09:00")
    before = rp.current_revision(db, "p")
    assert rp.update_core_weights(db, "p", {"alpha": 1.0}) == before
    rev = rp.update_core_weights(db, "p", {"alpha": 5.0, "beta": 0.1, "ghost": 1.0})
    prof = rp.get_profile(db, "p")
    assert rev == before + 1 and prof["core_weights"] == {"alpha": 2.0, "beta": 0.35}
    assert prof["exclude"] == ["x"] and prof["s2_seeds"] == ["alpha"] and rp.get_schedule(db, "p") == ("manual", "09:00")
    with sqlite3.connect(db) as con:
        origin, note = con.execute("SELECT origin, note FROM profile_revisions WHERE profile_id='p' ORDER BY revision DESC").fetchone()
    assert origin == "user" and "alpha 1->2" in note


def test_short_error_keeps_code_and_host_not_the_whole_url():
    """2026-09-16 사용자 지적. 이 테스트가 잡는 것: 검색 실패 사유에 수백 자 URL 을 그대로 실어 설정 탭 아래 메뉴를 밀어내는 것."""
    detail = ("Client error '429 Unknown Error' for url 'https://export.arxiv.org/api/query?search_query=" + "all%3Aa+OR+" * 80
              + "'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429")
    assert od.short_error(detail) == "429 Unknown Error · export.arxiv.org"
    assert od.short_error("x" * 200).endswith("…") and len(od.short_error("x" * 200)) == 81
    assert od.short_error(None) == ""


def test_display_text_renames_seed_with_particles_and_history_uses_it(tmp_path):
    """2026-09-16 사용자 결정("씨앗" → "시드"). 이 테스트가 잡는 것: DB 에 남은 옛 메모가 화면 변경 이력에 "씨앗" 그대로 보이는 것, 조사가
    어긋나는 것(시드을·시드이), 기록 자체를 고쳐 버리는 것."""
    assert od.display_text("씨앗 교체: 씨앗을 바꿨다, 씨앗이 없었다, 씨앗은 둘, 씨앗으로 묻는다") == \
        "시드 교체: 시드를 바꿨다, 시드가 없었다, 시드는 둘, 시드로 묻는다"
    assert od.display_text(None) == "" and od.display_text("변화 없음") == "변화 없음"
    db = tmp_path / "h.db"
    rp.create_profile(db, "p", "P", ["alpha"], note="씨앗 교체: a → b")
    assert od.revision_history(db, "p")[0]["note"] == "시드 교체: a → b"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT note FROM profile_revisions").fetchone()[0] == "씨앗 교체: a → b"


def test_skip_line_from_a_second_trigger_does_not_close_the_running_block(tmp_path):
    """이 테스트가 잡는 것: cron 과 Windows 작업이 같은 시각에 스크립트를 부를 때 두 번째의 스킵 줄이 첫 번째 실행을 "0분 스킵"으로 닫는 것."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daily_scan.log").write_text(
        "=== 2026-09-15T20:00:01Z 시작 (pid 7) ===\n=== 2026-09-15T20:00:02Z 스킵 — 이전 실행이 아직 진행 중 ===\n"
        "  [경고] x\n=== 2026-09-15T21:00:01Z 종료 (exit 0) ===\n", encoding="utf-8")
    runs = od.recent_runs(tmp_path)
    real = next(r for r in runs if r["pid"] == 7)
    assert (real["exit"], real["minutes"], real["warnings"]) == (0, 60.0, 1)
    assert [r["exit"] for r in runs] == ["skipped", 0]
