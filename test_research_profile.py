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
