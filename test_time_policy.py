"""공용 시각 정책(2026-09-17) — 저장은 UTC, 표시·운영일·요일은 KST. 각 테스트: 무엇을 망가뜨리면 실패하는가."""
from datetime import date, datetime, timezone

import time_policy as tp


def test_display_is_kst_regardless_of_host_timezone(monkeypatch):
    """이 테스트가 잡는 것: `astimezone()`(호스트 시간대)로 되돌리는 것 — WSL 이 UTC 면 morning_report 가 화면·메일과 다른 날짜를 찍었다."""
    monkeypatch.setenv("TZ", "UTC")
    assert tp.kst_hm("2026-09-16T20:05:00+00:00") == "09-17 05:05"
    assert tp.kst_day("2026-09-16T20:05:00Z") == "2026-09-17"
    assert tp.kst_hm("2026-09-16T20:05:00") == "09-17 05:05", "시간대 없는 값은 UTC 로 본다(저장 관행)"


def test_unparseable_and_missing_values_degrade_visibly():
    """이 테스트가 잡는 것: 못 읽는 값을 예외로 올리거나 빈 문자열로 삼켜 화면에서 사라지게 하는 것."""
    assert tp.kst_hm(None) == "—" and tp.kst_hm("", missing="?") == "?"
    assert tp.kst_hm("garbage-value") == "garbage-value"[:16] and tp.kst_day("garbage") == "garbage"
    assert tp.parse_iso("nope") is None and tp.kst_date(None) is None


def test_operating_day_uses_the_0500_kst_boundary():
    """이 테스트가 잡는 것: 운영일을 자정 기준으로 바꾸는 것 — 새벽 4시의 점검이 '오늘' 브리핑이 없다고 오판한다."""
    assert tp.operating_day(datetime(2026, 9, 16, 19, 30, tzinfo=timezone.utc)) == date(2026, 9, 16)   # 04:30 KST → 아직 어제
    assert tp.operating_day(datetime(2026, 9, 16, 20, 30, tzinfo=timezone.utc)) == date(2026, 9, 17)   # 05:30 KST → 오늘
    assert tp.operating_day_start_utc(date(2026, 9, 17)) == datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
    assert tp.kst_weekday(datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)) == 4                     # 목요일 밤 UTC = 금요일 KST
