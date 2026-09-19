"""월요일 메일의 "지난 7일 검색 기준 변화" — 동향(분야가 어떻게 움직였나)과 **다른 질문**에 답한다.

2026-09-19 사용자 결정으로 `주간 동향 리뷰` 자리를 이 절이 대신한다. 그 절은 기간 비교가
`window_movement` 와 겹쳤고 나머지는 운영 지표였다. 여기서 지키는 계약:
순변화는 스냅숏 두 개로 재고, 누가 바꿨는지를 빠짐없이 적고, 바뀐 게 없으면 절 자체가 없다."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import digest
import research_profile
from time_policy import KST
import weekly_profile_changes as wpc

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _snap(db, pid, revision, created, origin, keywords):
    research_profile.init_db(db)
    # 가중치·에이전트 기록은 다른 모듈이 소유한다 — 귀속을 보려면 그 표도 있어야 한다
    import agent_maintenance, feedback_weights
    feedback_weights.init_db(db)
    agent_maintenance.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO profile_revisions "
                    "(profile_id, revision, created_at, origin, content_sha, snapshot, note) "
                    "VALUES (?,?,?,?,?,?,'')",
                    (pid, revision, created.isoformat(), origin, f"sha{revision}",
                     json.dumps({"keywords": keywords, "max_items": 5})))


def test_net_change_comes_from_snapshots_not_event_replay(tmp_path):
    """이 테스트가 잡는 것: 이벤트를 더해 순변화를 내는 것. 한 주 안에 user·feedback·agent 가 섞여 돌면
    중간 경로(1.0→1.4→1.2→1.6)가 결과처럼 보인다 — 시작·끝 스냅숏만 견줘야 순이동이 정확하다."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=9), "user", [["defect detection", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=5), "feedback", [["defect detection", "core", 1.4]])
    _snap(db, "p1", 3, NOW - timedelta(days=3), "user", [["defect detection", "core", 1.2]])
    _snap(db, "p1", 4, NOW - timedelta(days=1), "agent", [["defect detection", "core", 1.6]])
    out = wpc.collect(db, "p1", days=7, now=NOW)
    assert len(out["weights"]) == 1
    w = out["weights"][0]
    assert (w["before"], w["after"], w["delta"]) == (1.0, 1.6, 0.6)   # 중간 경로가 아니라 순이동


def test_same_word_as_core_and_seed_is_not_collapsed(tmp_path):
    """이 테스트가 잡는 것: 키를 키워드만으로 잡아 core 와 s2_seed 가 서로 덮는 것
    (2026-09-19 실측: `defect detection` 이 둘 다였고, core 1.6 이 seed 0.6 으로 뒤바뀌어 보였다)."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user",
          [["defect detection", "core", 1.0], ["defect detection", "s2_seed", 0.6]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "agent",
          [["defect detection", "core", 1.6], ["defect detection", "s2_seed", 0.6]])
    out = wpc.collect(db, "p1", days=7, now=NOW)
    kinds = {(w["keyword"], w["kind"]): w for w in out["weights"]}
    assert ("defect detection", "core") in kinds
    assert kinds[("defect detection", "core")]["after"] == 1.6
    assert ("defect detection", "s2_seed") not in kinds        # 안 움직인 쪽은 안 싣는다


def test_every_actor_that_touched_a_keyword_is_named(tmp_path):
    """이 테스트가 잡는 것: 마지막으로 건드린 주체 하나만 적어 나머지 기여를 지우는 것.
    실측으로 `defect detection` 을 반응과 에이전트가 함께 움직인 주가 있었다."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["defect detection", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "agent", [["defect detection", "core", 1.7]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision,"
                    " changes_json, skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,0,3)",
                    ("p1", "2026-09-18", (NOW - timedelta(days=2)).isoformat(), 2,
                     json.dumps([{"keyword": "defect detection", "before": 1.0, "after": 1.6}])))
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, applied_json,"
                    " base_revision, new_revision) VALUES (?,?,?,?,?,?,?)",
                    ("p1", "2026-W38", (NOW - timedelta(days=1)).isoformat(), "applied",
                     json.dumps([{"op": "set_weight", "term": "defect detection", "weight": 1.7,
                                  "reason": "좋다고 반응한 논문 두 편에 걸렸다"}]), 1, 2))
    out = wpc.collect(db, "p1", days=7, now=NOW)
    assert set(out["weights"][0]["origins"]) == {"agent", "feedback"}
    text = "\n".join(digest._profile_changes_section({"profile_changes": out}))
    assert "에이전트·반응" in text
    assert "좋다고 반응한 논문 두 편에 걸렸다" in text          # 사유를 요약하지 않고 그대로


def test_no_change_means_no_section(tmp_path):
    """이 테스트가 잡는 것: "이번 주 변경 없음"을 매주 출력하는 것 — 소음이다
    (`agent_maintenance.pending_report` 와 같은 철학)."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["defect detection", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "user", [["defect detection", "core", 1.0]])
    assert wpc.collect(db, "p1", days=7, now=NOW) is None
    assert digest._profile_changes_section({}) == []
    assert digest._profile_changes_html({}) == ""


def test_user_only_changes_are_summarised_not_listed(tmp_path):
    """이 테스트가 잡는 것: 사용자가 직접 한 변경을 전부 펼쳐 이 절의 주인공(시스템이 배운 것)을 묻는 것.
    실측 한 주에 사용자 revision 이 19건이었다 — 다 펼치면 자동 변경이 안 보인다."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["a", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=2), "user",
          [["a", "core", 1.0], ["b", "core", 1.0], ["c", "core", 1.0]])
    out = wpc.collect(db, "p1", days=7, now=NOW)
    text = "\n".join(digest._profile_changes_section({"profile_changes": out}))
    assert "revision 1건" in text                 # 건수만
    assert "\n      + b" not in text              # 항목을 펼치지 않는다


def test_plain_and_html_carry_the_same_facts(tmp_path):
    """이 테스트가 잡는 것: 두 판이 갈라지는 것(§8-70 이 그 사고였다 — 한쪽에만 절이 있었다)."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["defect detection", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "feedback", [["defect detection", "core", 1.4]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision,"
                    " changes_json, skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,0,2)",
                    ("p1", "2026-09-18", (NOW - timedelta(days=1)).isoformat(), 2,
                     json.dumps([{"keyword": "defect detection", "after": 1.4}])))
    out = wpc.collect(db, "p1", days=7, now=NOW)
    scan = {"profile_changes": out}
    text = "\n".join(digest._profile_changes_section(scan))
    html = digest._profile_changes_html(scan)
    for needle in ("defect detection", "1.4", "반응"):
        assert needle in text, needle
        assert needle in html, needle


def test_last_reports_changes_do_not_come_back_next_week(tmp_path):
    """이 테스트가 잡는 것: 기간을 고정 7일로 잡아 **지난 보고분이 다음 주에 다시 실리는 것**.

    PC 가 자면 그날 스캔이 늦게 뜬다(2026-09-18 실측 — 05:00 을 건너뛰고 09:45 에 떴다). 지난 월요일이
    늦고 이번 월요일이 제때면 `end - 7일` 창이 지난주 에이전트 변경을 통째로 삼킨다. 경계는 달력이
    아니라 **지난 회차 스캔 시작 시각**이어야 한다."""
    db = tmp_path / "p.db"
    utc = lambda *a: datetime(*a, tzinfo=KST).astimezone(timezone.utc)   # noqa: E731
    now = utc(2026, 9, 28, 5, 5)             # 이번 월요일, 제때
    last_scan = utc(2026, 9, 21, 9, 45)      # 지난 월요일, 절전에서 깨어 늦게
    last_agent = utc(2026, 9, 21, 9, 40)     # 그 스캔 직전에 돈 주간 관리 — 지난주 메일에 이미 실렸다
    _snap(db, "p1", 1, utc(2026, 9, 21, 5, 0), "user", [["X", "core", 1.0], ["Y", "core", 1.0]])
    _snap(db, "p1", 2, last_agent, "agent", [["X", "core", 1.5], ["Y", "core", 1.0]])
    _snap(db, "p1", 3, utc(2026, 9, 24, 5, 0), "feedback", [["X", "core", 1.5], ["Y", "core", 1.3]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, applied_json,"
                    " base_revision, new_revision) VALUES (?,?,?,?,?,?,?)",
                    ("p1", "2026-W39", last_agent.isoformat(), "applied",
                     json.dumps([{"op": "set_weight", "term": "X", "weight": 1.5,
                                  "reason": "지난주에 이미 보고한 이유"}]), 1, 2))
        con.execute("INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision,"
                    " changes_json, skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,0,2)",
                    ("p1", "2026-09-24", utc(2026, 9, 24, 5, 0).isoformat(), 3,
                     json.dumps([{"keyword": "Y", "before": 1.0, "after": 1.3}])))
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot,"
                    " policy_version) VALUES ('s1','p1',?,'{}','test')", (last_scan.isoformat(),))

    out = wpc.collect(db, "p1", days=7, now=now)
    assert [(w["keyword"], w["before"], w["after"]) for w in out["weights"]] == [("Y", 1.0, 1.3)]
    assert out["agent"]["applied"] == []
    assert "지난주에 이미 보고한 이유" not in "\n".join(
        digest._profile_changes_section({"profile_changes": out}))

    # 경계를 지우면(= 지난 회차 기록이 없으면) 고정 7일로 물러나고, 그때는 지난주 것이 다시 걸린다.
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scan_runs")
    back = wpc.collect(db, "p1", days=7, now=now)
    assert {w["keyword"] for w in back["weights"]} == {"X", "Y"}
    assert back["agent"]["applied"][0]["term"] == "X"


def test_origins_name_who_touched_it_not_how_much_each_moved(tmp_path):
    """이 테스트가 잡는 것: `origins` 를 기여 분해(`에이전트 +0.9 · 반응 +0.2`)로 "발전"시키는 것.

    스냅숏 두 개가 아는 것은 순이동뿐이다. 누가 얼마를 만들었는지는 재지 않았고, 재지 않은 값은
    쓰지 않는다(규칙 7). 그래서 항목에는 주체 **이름**만 있고 주체별 숫자 칸이 없어야 한다."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["defect detection", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "agent", [["defect detection", "core", 2.1]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision,"
                    " changes_json, skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,0,4)",
                    ("p1", "2026-09-16", (NOW - timedelta(days=3)).isoformat(), 2,
                     json.dumps([{"keyword": "defect detection", "before": 1.0, "after": 1.2}])))
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, applied_json,"
                    " base_revision, new_revision) VALUES (?,?,?,?,?,?,?)",
                    ("p1", "2026-W38", (NOW - timedelta(days=1)).isoformat(), "applied",
                     json.dumps([{"op": "set_weight", "term": "defect detection", "weight": 2.1,
                                  "reason": "반응이 걸린 자리와 이번 주 상승어가 같았다"}]), 1, 2))
    w = wpc.collect(db, "p1", days=7, now=NOW)["weights"][0]
    assert w["delta"] == 1.1                       # 전체 기간 순변화 하나
    assert w["origins"] == ("agent", "feedback")   # 건드린 주체들 — 몫이 아니다
    assert set(w) == {"keyword", "kind", "before", "after", "delta", "origins"}
