"""research_profile.py 단위 테스트 — 임시 SQLite DB, 네트워크 없음."""

from datetime import datetime, timedelta, timezone

import research_profile as rp


def test_create_and_get_profile_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    rp.create_profile(
        db, "team_ai", "우리팀",
        core_topics=["agent", "digital twin"],
        target_domain=["robot hand"],
        exclude=["medical"],
        venues=["IEEE TII"],
        max_items=5,
    )

    profile = rp.get_profile(db, "team_ai")

    assert profile["profile_id"] == "team_ai"
    assert profile["name"] == "우리팀"
    assert set(profile["core_topics"]) == {"agent", "digital twin"}
    assert profile["target_domain"] == ["robot hand"]
    assert profile["exclude"] == ["medical"]
    assert profile["venues"] == ["IEEE TII"]
    assert profile["max_items"] == 5


def test_get_profile_returns_none_when_missing(tmp_path):
    db = tmp_path / "t.db"
    assert rp.get_profile(db, "nope") is None


def test_create_profile_upsert_replaces_keywords_entirely(tmp_path):
    """같은 profile_id로 다시 만들면 이전 키워드는 완전히 지워지고 새 걸로
    바뀐다 — 일부만 덮어써서 "이전 키워드가 실수로 안 지워진 상태"가 남지
    않게 하기 위해서(모듈 docstring 참고)."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "이름1", core_topics=["a", "b"])
    rp.create_profile(db, "p", "이름2", core_topics=["c"])

    profile = rp.get_profile(db, "p")
    assert profile["name"] == "이름2"
    assert profile["core_topics"] == ["c"]  # a, b는 완전히 사라짐


def test_add_and_get_recipients_only_returns_active(tmp_path):
    db = tmp_path / "t.db"
    rp.add_recipient(db, "p", "a@x.com")
    rp.add_recipient(db, "p", "b@x.com")
    rp.add_recipient(db, "p", "c@x.com", active=False)

    assert rp.get_recipients(db, "p") == ["a@x.com", "b@x.com"]


def test_add_recipient_upsert_toggles_active():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        rp.add_recipient(db, "p", "a@x.com", active=True)
        assert rp.get_recipients(db, "p") == ["a@x.com"]

        rp.add_recipient(db, "p", "a@x.com", active=False)
        assert rp.get_recipients(db, "p") == []


def test_list_profiles_returns_sorted_ids(tmp_path):
    db = tmp_path / "t.db"
    rp.create_profile(db, "z_profile", "z", core_topics=["x"])
    rp.create_profile(db, "a_profile", "a", core_topics=["x"])

    assert rp.list_profiles(db) == ["a_profile", "z_profile"]


def test_next_since_defaults_to_lookback_when_no_history(tmp_path):
    db = tmp_path / "t.db"
    since = rp.next_since(db, "new_profile", default_lookback_days=7)
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    assert abs((since - expected).total_seconds()) < 5  # 실행 시각 오차만 허용


def test_next_since_uses_window_to_after_done_run(tmp_path):
    db = tmp_path / "t.db"
    window_to = datetime(2026, 8, 20, tzinfo=timezone.utc)
    rp.record_run(db, "p", "arxiv", "all:x",
                   window_from=datetime(2026, 8, 15, tzinfo=timezone.utc),
                   window_to=window_to, status="done", retrieved_count=3)

    assert rp.next_since(db, "p") == window_to


def test_next_since_retries_window_from_after_partial_run():
    """partial/failed면 이미 본 구간을 다시 앞당기지 않는다 — 다 못 봤을
    수 있는 구간을 그대로 다시 본다(설계 문서 §3, "빠진 논문" 문제 대응)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        window_from = datetime(2026, 8, 10, tzinfo=timezone.utc)
        rp.record_run(db, "p", "arxiv", "all:x", window_from=window_from,
                       window_to=datetime(2026, 8, 20, tzinfo=timezone.utc),
                       status="partial", retrieved_count=50)

        assert rp.next_since(db, "p") == window_from


def test_next_since_scoped_per_profile(tmp_path):
    db = tmp_path / "t.db"
    rp.record_run(db, "p1", "arxiv", "x",
                   window_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
                   window_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
                   status="done", retrieved_count=1)

    since_p2 = rp.next_since(db, "p2", default_lookback_days=7)
    # p1의 기록에 영향받지 않고 p2는 여전히 "이력 없음" 기본값을 씀
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    assert abs((since_p2 - expected).total_seconds()) < 5


def test_list_runs_returns_newest_first_scoped_per_profile(tmp_path):
    db = tmp_path / "t.db"
    rp.record_run(db, "p1", "arxiv", "old query",
                   window_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                   window_to=datetime(2026, 8, 10, tzinfo=timezone.utc),
                   status="done", retrieved_count=3,
                   started_at="2026-08-10T00:00:00+00:00")
    rp.record_run(db, "p1", "arxiv", "new query",
                   window_from=datetime(2026, 8, 10, tzinfo=timezone.utc),
                   window_to=datetime(2026, 8, 17, tzinfo=timezone.utc),
                   status="partial", retrieved_count=50,
                   started_at="2026-08-17T00:00:00+00:00")
    rp.record_run(db, "p2", "arxiv", "other profile",
                   window_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                   window_to=datetime(2026, 8, 17, tzinfo=timezone.utc),
                   status="done", retrieved_count=1,
                   started_at="2026-08-17T00:00:00+00:00")

    runs = rp.list_runs(db, "p1")

    assert len(runs) == 2
    assert runs[0]["query"] == "new query"  # 최신이 먼저
    assert runs[1]["query"] == "old query"
    assert all(r["profile_id"] == "p1" for r in runs)


def test_list_runs_respects_limit(tmp_path):
    db = tmp_path / "t.db"
    for i in range(5):
        rp.record_run(db, "p", "arxiv", f"q{i}",
                       window_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                       window_to=datetime(2026, 8, 2, tzinfo=timezone.utc),
                       status="done", retrieved_count=1,
                       started_at=f"2026-08-{i+1:02d}T00:00:00+00:00")

    assert len(rp.list_runs(db, "p", limit=3)) == 3


def test_get_latest_digest_returns_none_when_never_saved(tmp_path):
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "이름", core_topics=["a"])
    assert rp.get_latest_digest(db, "p") is None


def test_save_and_get_latest_digest_roundtrip(tmp_path):
    """cron이든 review_app.py "지금 스캔 실행" 버튼이든 누가 저장했는지와
    무관하게 여기 하나만 본다는 게 핵심 — session_state가 아니라 DB에
    남는다(2026-08-24, "cron이 새벽에 돌려도 화면에 안 남는다" 문제)."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "이름", core_topics=["a"])

    rp.save_digest(db, "p", "첫 번째 다이제스트")
    text, at = rp.get_latest_digest(db, "p")
    assert text == "첫 번째 다이제스트"
    assert at  # 타임스탬프가 채워져 있음

    rp.save_digest(db, "p", "두 번째 다이제스트")  # 덮어쓰기 — 이력 아님
    text2, _ = rp.get_latest_digest(db, "p")
    assert text2 == "두 번째 다이제스트"


def test_save_digest_scoped_per_profile(tmp_path):
    db = tmp_path / "t.db"
    rp.create_profile(db, "p1", "이름1", core_topics=["a"])
    rp.create_profile(db, "p2", "이름2", core_topics=["b"])

    rp.save_digest(db, "p1", "p1 다이제스트")

    assert rp.get_latest_digest(db, "p1")[0] == "p1 다이제스트"
    assert rp.get_latest_digest(db, "p2") is None


# ------------------------------------------- 키워드가 바뀌면 델타 커서 무효화 (§8-21)


def _record(db, profile_id, *, status, window_from, window_to, signature=None):
    rp.record_run(db, profile_id, "arxiv", "q", window_from, window_to,
                  status, 10, signature=signature)


def test_topic_signature_ignores_order_case_and_whitespace():
    """같은 키워드를 순서만 바꿔 저장한 걸 "바뀌었다"로 보면, 프로필을 손댈
    때마다 과거 열흘을 다시 훑게 된다."""
    a = rp.topic_signature(["defect detection", "NPU", " sim-to-real "])
    b = rp.topic_signature(["npu", "sim-to-real", "Defect Detection"])
    assert a == b


def test_topic_signature_changes_when_a_keyword_is_added():
    base = rp.topic_signature(["a", "b"])
    assert rp.topic_signature(["a", "b", "c"]) != base


def test_cursor_is_inherited_when_keywords_are_unchanged(tmp_path):
    db = tmp_path / "t.db"
    sig = rp.topic_signature(["agent"])
    now = datetime.now(timezone.utc)
    _record(db, "p", status="done", window_from=now - timedelta(days=10),
            window_to=now - timedelta(hours=1), signature=sig)

    since = rp.next_since(db, "p", signature=sig)
    assert since == now - timedelta(hours=1)


def test_cursor_is_reset_when_keywords_changed(tmp_path):
    """실측(2026-08-31): 키워드를 12→27 개로 넓힌 날 커서가 90분 전을 가리켜
    새 키워드가 과거를 못 볼 뻔했다. 손으로 되돌려야 했던 그 상황이다."""
    db = tmp_path / "t.db"
    now = datetime.now(timezone.utc)
    _record(db, "p", status="done", window_from=now - timedelta(days=10),
            window_to=now - timedelta(hours=1),
            signature=rp.topic_signature(["agent"]))

    since = rp.next_since(db, "p", default_lookback_days=7,
                          signature=rp.topic_signature(["agent", "physical AI"]))

    assert since < now - timedelta(days=6)   # 커서를 안 이어받고 과거로 되돌아갔다
    assert since > now - timedelta(days=8)


def test_old_rows_without_signature_behave_as_before(tmp_path):
    """구형 DB 행에는 지문이 없다. "모르는 것"을 "바뀌었다"로 단정해 매번
    과거를 다시 훑으면 그것대로 낭비다."""
    db = tmp_path / "t.db"
    now = datetime.now(timezone.utc)
    _record(db, "p", status="done", window_from=now - timedelta(days=10),
            window_to=now - timedelta(hours=1), signature=None)

    since = rp.next_since(db, "p", signature=rp.topic_signature(["무엇이든"]))
    assert since == now - timedelta(hours=1)


def test_call_without_signature_is_unchanged_behaviour(tmp_path):
    db = tmp_path / "t.db"
    now = datetime.now(timezone.utc)
    _record(db, "p", status="done", window_from=now - timedelta(days=10),
            window_to=now - timedelta(hours=1),
            signature=rp.topic_signature(["agent"]))
    assert rp.next_since(db, "p") == now - timedelta(hours=1)


def test_partial_run_still_wins_over_signature_match(tmp_path):
    """키워드가 그대로여도 지난 실행이 partial 이면 창을 다 못 본 것이므로
    window_from 을 그대로 다시 본다 — 기존 규칙이 깨지면 안 된다."""
    db = tmp_path / "t.db"
    sig = rp.topic_signature(["agent"])
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=10)
    _record(db, "p", status="partial", window_from=start,
            window_to=now - timedelta(hours=1), signature=sig)
    assert rp.next_since(db, "p", signature=sig) == start
