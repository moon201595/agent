"""feedback_weights.py의 결정적 반응 학습 계약을 임시 SQLite DB로 검증한다."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import feedback_links as fl
import feedback_weights as fw
import research_profile as rp


UTC = timezone.utc


def _profile(
    db, core=("topic",), weights=None, *, schedule_frequency="daily",
    domain=("robotics",), exclude=("banned",), venues=("Venue",), seeds=("seed",),
):
    weights = weights or {keyword: 1.0 for keyword in core}
    return rp.create_profile(
        db, "p", "프로필", core_topics=list(core), target_domain=list(domain),
        exclude=list(exclude), venues=list(venues), max_items=5,
        schedule_frequency=schedule_frequency, schedule_time="07:30",
        core_weights=weights, s2_seeds=list(seeds),
    )


def _observation(db, paper_key, observed_at, hits, scan_id=None):
    rp.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO candidate_observations (scan_id, profile_id, paper_key, observed_at, core_hits) "
            "VALUES (?,?,?,?,?)",
            (scan_id or f"scan-{paper_key}-{observed_at}", "p", paper_key, observed_at,
             json.dumps(hits, ensure_ascii=False)),
        )


def _reaction(db, paper_key, received_at, action, *, issue_id="issue", recipient_hash="recipient", suffix=""):
    fl.init_db(db)
    tid = f"tid-{paper_key}-{received_at}-{action}-{suffix}"
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO feedback_tokens (tid, issue_id, profile_id, item_no, paper_key, recipient_hash, "
            "position, created_at, expires_at, delivered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, issue_id, "p", "P1", paper_key, recipient_hash, 1, received_at, 9999999999, received_at),
        )
        con.execute(
            "INSERT INTO feedback_events (event_id, tid, action, received_at, status, imported_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"event-{tid}", tid, action, received_at, fl.STATUS_VALID, received_at),
        )


def _weight(db, keyword="topic"):
    profile = rp.get_profile(db, "p")
    return profile["core_weights"][keyword]


def _run_rows(db):
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM feedback_weight_runs ORDER BY run_date")]


def test_more_reactions_reach_target_with_daily_limit(tmp_path):
    """이 테스트가 잡는 것: 반응을 한 번에 더하거나 하루 0.1 이동 제한을 넘기는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    for index in range(3):
        received = start + timedelta(days=index)
        stamp = received.isoformat()
        _observation(db, f"p{index}", (received - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, f"p{index}", stamp, "more", suffix=str(index))

    weights = []
    for day in range(1, 6):
        result = fw.update_profile(db, "p", now=start + timedelta(days=day, hours=1))
        if result["changes"]:
            weights.append(result["changes"][0]["after"])
    assert weights[0] == 1.1
    assert all(after - before <= 0.100001 for before, after in zip([1.0] + weights, weights))
    assert weights[-1] == pytest.approx(1.34, abs=0.01)


def test_old_reactions_do_not_drift_past_target_without_new_reactions(tmp_path):
    """이 테스트가 잡는 것: 새 반응이 없는데 같은 신호를 매일 다시 더해 목표값을 지나치는 누적 표류."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    for index in range(3):
        _observation(db, f"p{index}", (start - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, f"p{index}", start.isoformat(), "more", suffix=str(index))

    values = [1.0]
    for day in range(1, 16):
        fw.update_profile(db, "p", now=start + timedelta(days=day, hours=1))
        values.append(_weight(db))
    assert max(values) <= 1.34
    assert values[-1] <= max(values[5:])


def test_out_reactions_lower_weight_and_respect_floor(tmp_path):
    """이 테스트가 잡는 것: 음수 목표값을 하한 0.35로 자르지 않거나 음수 반응을 상승 신호로 읽는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 3, 1, 12, tzinfo=UTC)
    for index in range(24):
        key = f"out-{index}"
        _observation(db, key, (start - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, key, start.isoformat(), "out", suffix=str(index))
    for day in range(1, 10):
        fw.update_profile(db, "p", now=start + timedelta(days=day, hours=1))
    assert _weight(db) == 0.35
    assert _weight(db) >= 0.35


def test_same_kst_day_is_applied_once(tmp_path):
    """이 테스트가 잡는 것: 같은 KST 날짜의 두 호출이 두 revision과 두 가중치 변경을 만드는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    now = datetime(2026, 4, 1, 12, tzinfo=UTC)
    for key in ("p1", "p2"):          # 2편 이상이어야 움직인다(2026-09-16)
        _observation(db, key, (now - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, key, now.isoformat(), "more", suffix=key)
    first = fw.update_profile(db, "p", now=now + timedelta(hours=1))
    second = fw.update_profile(db, "p", now=now + timedelta(hours=2))
    assert first["status"] == "updated"
    assert second["status"] == "already_ran"
    assert second["revision"] == first["revision"]
    assert rp.current_revision(db, "p") == 2
    assert len(_run_rows(db)) == 1


def test_latest_reaction_replaces_earlier_action_for_same_identity(tmp_path):
    """이 테스트가 잡는 것: 같은 회차·수신자·논문의 이전 버튼과 최신 버튼을 모두 누적하는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 5, 1, 12, tzinfo=UTC)
    _observation(db, "p1", (start - timedelta(hours=1)).isoformat(), ["topic"])
    _reaction(db, "p1", start.isoformat(), "more", suffix="old")
    _reaction(db, "p1", (start + timedelta(hours=1)).isoformat(), "out", suffix="new")
    _observation(db, "p2", (start - timedelta(hours=1)).isoformat(), ["topic"])       # 둘째 논문(2026-09-16)
    _reaction(db, "p2", (start + timedelta(hours=1)).isoformat(), "out", suffix="p2")
    result = fw.update_profile(db, "p", now=start + timedelta(days=1))
    assert result["changes"][0]["after"] == 0.9
    assert result["changes"][0]["pos"] == 0                   # p1 의 앞선 more 는 세지 않는다
    assert result["changes"][0]["neg"] == pytest.approx(0.9923 + 0.9926, abs=0.002)


def test_reaction_without_prior_observation_is_skipped_and_counted(tmp_path):
    """이 테스트가 잡는 것: 반응 시각 뒤의 관측을 소급해 사용하거나 미관측 반응의 skip 수를 누락하는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    _observation(db, "late", (now + timedelta(hours=1)).isoformat(), ["topic"])
    _reaction(db, "late", now.isoformat(), "more")
    result = fw.update_profile(db, "p", now=now + timedelta(days=1))
    assert result["status"] == "no_reactions"
    assert result["skipped_no_observation"] == 1
    assert result["reactions_used"] == 0
    assert rp.current_revision(db, "p") == 1


def test_multiple_core_hits_split_reaction_evenly(tmp_path):
    """이 테스트가 잡는 것: 여러 적중 논문이 반응값 전체를 첫 키워드 하나에 몰아주는 것."""
    db = tmp_path / "weights.db"
    _profile(db, core=("alpha", "beta"), weights={"alpha": 1.0, "beta": 1.0})
    now = datetime(2026, 7, 1, 12, tzinfo=UTC)
    for key in ("p1", "p2"):          # 2편 이상이어야 움직인다(2026-09-16)
        _observation(db, key, (now - timedelta(hours=1)).isoformat(), ["alpha", "beta"])
        _reaction(db, key, now.isoformat(), "more", suffix=key)
    result = fw.update_profile(db, "p", now=now + timedelta(hours=1))
    changes = {change["keyword"]: change for change in result["changes"]}
    assert changes["alpha"]["pos"] == pytest.approx(2 * 0.49984, abs=0.00002)     # 편당 0.5 씩, 두 편
    assert changes["beta"]["pos"] == pytest.approx(2 * 0.49984, abs=0.00002)
    assert changes["alpha"]["after"] == changes["beta"]["after"] == 1.1


def test_manual_profile_settings_survive_feedback_revision(tmp_path):
    """이 테스트가 잡는 것: feedback revision이 manual 주기, 시드, 도메인, 제외어를 기본값으로 덮는 것."""
    db = tmp_path / "weights.db"
    _profile(db, schedule_frequency="manual", domain=("my domain",), exclude=("do not use",), seeds=("my seed",))
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    for key in ("p1", "p2"):          # 2편 이상이어야 움직인다(2026-09-16)
        _observation(db, key, (now - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, key, now.isoformat(), "more", suffix=key)
    result = fw.update_profile(db, "p", now=now + timedelta(hours=1))
    assert result["status"] == "updated"
    profile = rp.get_profile(db, "p")
    assert profile["core_topics"] == ["topic"]
    assert profile["target_domain"] == ["my domain"]
    assert profile["exclude"] == ["do not use"]
    assert profile["s2_seeds"] == ["my seed"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT schedule_frequency FROM profiles WHERE profile_id='p'").fetchone()[0] == "manual"


def test_non_feedback_revision_resets_base_weight(tmp_path):
    """이 테스트가 잡는 것: 사람이 직접 바꾼 가중치를 이전 base에 계속 묶어 feedback이 되돌리는 것."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    for key in ("p1", "p2"):          # 2편 이상이어야 움직인다(2026-09-16)
        _observation(db, key, (start - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, key, start.isoformat(), "more", suffix=key)
    fw.update_profile(db, "p", now=start + timedelta(hours=1))
    rp.create_profile(db, "p", "프로필", core_topics=["topic"], target_domain=["robotics"],
                      exclude=["banned"], venues=["Venue"], max_items=5, schedule_frequency="daily",
                      schedule_time="07:30", core_weights={"topic": 1.5}, s2_seeds=["seed"], origin="user")
    result = fw.update_profile(db, "p", now=start + timedelta(days=1, hours=1))
    assert result["changes"][0]["before"] == 1.5
    assert result["changes"][0]["after"] == 1.6
    with sqlite3.connect(db) as con:
        base = con.execute(
            "SELECT base_weight FROM feedback_weight_base WHERE profile_id='p' AND keyword='topic'"
        ).fetchone()[0]
    assert base == 1.5


def test_ninety_day_half_life_reduces_numeric_influence(tmp_path):
    """이 테스트가 잡는 것: 반응 나이를 무시해 90일 전 신호를 최신 신호와 같은 크기로 적용하는 것."""
    recent_db = tmp_path / "recent.db"
    old_db = tmp_path / "old.db"
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    targets = {}
    for db, received in ((recent_db, now), (old_db, now - timedelta(days=90))):
        _profile(db)
        for key in ("p1", "p2"):          # 2편 이상이어야 움직인다(2026-09-16)
            _observation(db, key, (received - timedelta(hours=1)).isoformat(), ["topic"])
            _reaction(db, key, received.isoformat(), "more", suffix=key)
        targets[db] = fw.update_profile(db, "p", now=now)["changes"][0]["target"]
    # 하루 ±0.1 상한 때문에 첫날 가중치는 둘 다 1.1 이다 — 나이가 깎는 것은 목표값이다.
    assert _weight(recent_db) == _weight(old_db) == 1.1
    assert targets[recent_db] == pytest.approx(1.0 + 0.8 * 2 / 6, abs=0.001)          # 감쇠 없음: pos 2
    assert targets[old_db] == pytest.approx(1.0 + 0.8 * 1 / 5, abs=0.001)             # 90일 = 반감: pos 1
    assert targets[recent_db] > targets[old_db]


def test_unrelated_direct_revision_does_not_rebase_a_feedback_moved_weight(tmp_path):
    """이 테스트가 잡는 것: 사람·에이전트가 **다른** 키워드만 고친 revision 에서, 피드백으로 이미 올라간 값을 새 기준선으로
    다시 잡아 같은 반응을 한 번 더 더하는 래칫(2026-09-15 검토 — 주간 에이전트가 매주 revision 을 만들면 매주 오른다).
    그 키워드의 가중치를 직접 바꾼 revision 만 기준선을 다시 잡는다."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 4, 1, 12, tzinfo=UTC)
    for index in range(3):
        _observation(db, f"p{index}", (start - timedelta(hours=1)).isoformat(), ["topic"])
        _reaction(db, f"p{index}", start.isoformat(), "more", suffix=str(index))
    for day in range(1, 8):
        fw.update_profile(db, "p", now=start + timedelta(days=day, hours=1))
    settled = _weight(db)
    assert settled == pytest.approx(1.34, abs=0.01)
    profile = rp.get_profile(db, "p")
    rp.create_profile(db, "p", "프로필", core_topics=profile["core_topics"] + ["other"], target_domain=profile["target_domain"],
                      exclude=profile["exclude"], venues=profile["venues"], max_items=5, schedule_frequency="daily",
                      schedule_time="07:30", core_weights={**profile["core_weights"], "other": 1.0}, origin="agent")
    for day in range(8, 16):
        fw.update_profile(db, "p", now=start + timedelta(days=day, hours=1))
    assert _weight(db) <= settled + 0.005

    profile = rp.get_profile(db, "p")
    rp.create_profile(db, "p", "프로필", core_topics=profile["core_topics"], target_domain=profile["target_domain"],
                      exclude=profile["exclude"], venues=profile["venues"], max_items=5, schedule_frequency="daily",
                      schedule_time="07:30", core_weights={**profile["core_weights"], "topic": 0.6}, origin="user")
    fw.update_profile(db, "p", now=start + timedelta(days=16, hours=1))
    assert _weight(db) == pytest.approx(0.7, abs=0.001)     # 직접 내린 0.6 이 새 기준선 — 거기서 다시 따라간다


def test_one_paper_alone_does_not_move_a_keyword(tmp_path):
    """2026-09-16 사용자 결정. 이 테스트가 잡는 것: 논문 한 편의 반응(클릭 한 번)으로 키워드가 움직여 계층이 바뀌는 것 — 두 번째
    논문에 반응이 오면 그때 움직여야 한다. 같은 논문을 두 수신자가 눌러도 한 편이다."""
    db = tmp_path / "weights.db"
    _profile(db)
    start = datetime(2026, 5, 1, 12, tzinfo=UTC)
    _observation(db, "p0", (start - timedelta(hours=1)).isoformat(), ["topic"])
    _reaction(db, "p0", start.isoformat(), "more", suffix="a")
    _reaction(db, "p0", start.isoformat(), "more", recipient_hash="other", suffix="b")
    result = fw.update_profile(db, "p", now=start + timedelta(days=1, hours=1))
    assert result["changes"] == [] and result["reactions_used"] == 2 and _weight(db) == 1.0
    _observation(db, "p1", (start - timedelta(hours=1)).isoformat(), ["topic"])
    _reaction(db, "p1", (start + timedelta(days=1)).isoformat(), "useful", suffix="c")
    result = fw.update_profile(db, "p", now=start + timedelta(days=2, hours=1))
    assert result["changes"] and _weight(db) > 1.0
