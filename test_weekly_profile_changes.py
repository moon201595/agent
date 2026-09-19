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
        # 지난 회차가 실제로 검색에 쓴 프로필 — 이것이 이번 보고의 baseline 이다
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot,"
                    " policy_version) VALUES ('s1','p1',?,?,'test')",
                    (last_scan.isoformat(),
                     json.dumps({"core_topics": ["X", "Y"], "core_weights": {"X": 1.5, "Y": 1.0}})))

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


def test_baseline_is_the_profile_last_cycle_actually_searched_with(tmp_path):
    """이 테스트가 잡는 것: baseline 을 시각으로 되짚어 **지난 메일이 쓰지 않은 프로필**과 견주는 것.

    `scan_runs.profile_snapshot` 은 그 회차가 실제로 검색에 쓴 프로필이다. revision 을 `created_at <
    start` 으로 되짚으면 같은 시각 경계에서 한 칸 어긋날 수 있는데, 그 회차가 자기 스냅숏을 들고 있으므로
    되짚을 이유가 없다. 여기서는 스캔 이후에 남은 revision 을 일부러 넣어 둘이 갈리게 만든다."""
    db = tmp_path / "p.db"
    utc = lambda *a: datetime(*a, tzinfo=KST).astimezone(timezone.utc)   # noqa: E731
    now = utc(2026, 9, 28, 5, 5)
    last_scan = utc(2026, 9, 21, 5, 2)
    _snap(db, "p1", 1, utc(2026, 9, 21, 5, 1), "agent", [["X", "core", 1.5]])
    _snap(db, "p1", 2, utc(2026, 9, 27, 5, 0), "feedback", [["X", "core", 1.8]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot,"
                    " policy_version) VALUES ('s1','p1',?,?,'test')",
                    (last_scan.isoformat(),
                     json.dumps({"core_topics": ["X"], "core_weights": {"X": 1.5}})))
    out = wpc.collect(db, "p1", days=7, now=now)
    assert (out["weights"][0]["before"], out["weights"][0]["after"]) == (1.5, 1.8)
    assert out["window"][0] == last_scan.isoformat()      # 경계는 달력이 아니라 지난 회차


def test_previous_cycle_is_used_even_when_it_started_earlier_than_the_fixed_window(tmp_path):
    """이 테스트가 잡는 것: 경계를 `max(고정 창, 지난 회차)` 로 섞는 것(2026-09-19 지적).

    지난 회차가 이번 회차보다 **이른** 시각에 돌았으면 `end - 7일` 이 더 늦다. 거기서 `max` 를 쓰면
    그 사이(지난 메일 이후 ~ 고정 경계)의 변경이 **어느 주에도 안 실린 채** 사라진다."""
    db = tmp_path / "p.db"
    utc = lambda *a: datetime(*a, tzinfo=KST).astimezone(timezone.utc)   # noqa: E731
    now = utc(2026, 9, 28, 5, 25)             # 이번 회차는 05:25 에 집계
    last_scan = utc(2026, 9, 21, 5, 2)        # 지난 회차는 05:02 — 고정 경계(05:25)보다 이르다
    gap = utc(2026, 9, 21, 5, 10)             # 그 사이에 일어난 변경
    _snap(db, "p1", 1, utc(2026, 9, 21, 5, 1), "agent", [["X", "core", 1.0]])
    _snap(db, "p1", 2, gap, "user", [["X", "core", 1.0], ["gap keyword", "core", 1.0]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot,"
                    " policy_version) VALUES ('s1','p1',?,?,'test')",
                    (last_scan.isoformat(),
                     json.dumps({"core_topics": ["X"], "core_weights": {"X": 1.0}})))
    out = wpc.collect(db, "p1", days=7, now=now)
    assert [a["keyword"] for a in out["added"]] == ["gap keyword"]   # max() 였으면 사라졌다


def test_attribution_does_not_leak_between_core_and_seed(tmp_path):
    """이 테스트가 잡는 것: 귀속을 키워드만으로 잡아 core 와 s2_seed 의 주체가 서로 번지는 것.
    순변화 쪽에서 (키워드, 종류)로 가른 것이 provenance 에서 도로 합쳐지면 의미가 없다."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user",
          [["defect detection", "core", 1.0], ["defect detection", "s2_seed", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "agent",
          [["defect detection", "core", 1.4], ["defect detection", "s2_seed", 1.0],
           ["surface defect", "s2_seed", 1.0]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision,"
                    " changes_json, skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,0,3)",
                    ("p1", "2026-09-18", (NOW - timedelta(days=2)).isoformat(), 2,
                     json.dumps([{"keyword": "defect detection", "after": 1.4}])))
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, applied_json,"
                    " base_revision, new_revision) VALUES (?,?,?,?,?,?,?)",
                    ("p1", "2026-W38", (NOW - timedelta(days=1)).isoformat(), "applied",
                     json.dumps([{"op": "add_seed", "term": "surface defect", "weight": 1.0,
                                  "reason": "시드만 늘렸다"}]), 1, 2))
    out = wpc.collect(db, "p1", days=7, now=NOW)
    weight = next(w for w in out["weights"] if w["kind"] == "core")
    seed = next(a for a in out["added"] if a["kind"] == "s2_seed")
    assert weight["origins"] == ("feedback",)      # core 가중치는 반응만 건드렸다
    assert seed["origins"] == ("agent",)           # 시드는 에이전트만 — 서로 번지지 않는다


def test_uppercase_keywords_keep_their_provenance(tmp_path):
    """이 테스트가 잡는 것: 이벤트 표(소문자 정규화)와 스냅숏(표기 그대로)을 그냥 맞춰
    **대문자가 섞인 키워드의 출처가 통째로 사라지는 것**(2026-09-19 실측: 이벤트 `llm agent` · 스냅숏 `LLM agent`)."""
    db = tmp_path / "p.db"
    _snap(db, "p1", 1, NOW - timedelta(days=8), "user", [["MVTec AD", "core", 1.0]])
    _snap(db, "p1", 2, NOW - timedelta(days=1), "agent",
          [["MVTec AD", "core", 1.0], ["LLM agent", "core", 1.2]])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO profile_keyword_events (profile_id, revision, created_at, keyword,"
                    " kind, change, actor_origin) VALUES (?,?,?,?,?,?,?)",
                    ("p1", 2, (NOW - timedelta(days=1)).isoformat(), "llm agent", "core",
                     "added", "agent"))
    out = wpc.collect(db, "p1", days=7, now=NOW)
    assert out["added"][0]["keyword"] == "LLM agent"
    assert out["added"][0]["origins"] == ("agent",)


def test_every_agent_op_has_a_kind():
    """이 테스트가 잡는 것: `agent_maintenance.OPS` 에 op 을 늘리고 `OP_KIND` 를 안 늘리는 것.
    빠진 op 의 변경은 귀속 없이 조용히 지나간다 — 조용한 손실이라 눈으로는 안 보인다."""
    import agent_maintenance
    assert set(wpc.OP_KIND) == set(agent_maintenance.OPS)


def test_heading_reports_the_real_window_not_the_argument(tmp_path):
    """이 테스트가 잡는 것: 제목이 `days` 인자를 그대로 말해 실제 기간과 어긋나는 것.
    경계가 "지난 보고 이후"라 한 주를 거르면 14일이 되는데 제목만 7일이면 거짓말이다."""
    db = tmp_path / "p.db"
    utc = lambda *a: datetime(*a, tzinfo=KST).astimezone(timezone.utc)   # noqa: E731
    now = utc(2026, 9, 28, 5, 5)
    _snap(db, "p1", 1, utc(2026, 9, 13, 5, 0), "user", [["X", "core", 1.0]])
    _snap(db, "p1", 2, utc(2026, 9, 27, 5, 0), "feedback", [["X", "core", 1.4]])
    with sqlite3.connect(db) as con:           # 지난 회차가 2주 전이다 — 한 주를 걸렀다
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot,"
                    " policy_version) VALUES ('s1','p1',?,?,'test')",
                    (utc(2026, 9, 14, 5, 2).isoformat(),
                     json.dumps({"core_topics": ["X"], "core_weights": {"X": 1.0}})))
    out = wpc.collect(db, "p1", days=14, now=now)
    scan = {"profile_changes": out}
    assert "지난 14일 검색 기준 변화" in "\n".join(digest._profile_changes_section(scan))
    assert "지난 14일 검색 기준 변화" in digest._profile_changes_html(scan)
