"""ui_helpers 의 상대 시간 표시 — Streamlit 없이 돈다. review_core 시절(§8-31)의 테스트 중 살아남은 함수 것만 남겼다(2026-09-16)."""
from datetime import datetime, timedelta, timezone

import pytest

import ui_helpers as app


def _iso(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.mark.parametrize("kw,want", [
    ({"seconds": 5}, "방금 전"),
    ({"minutes": 7}, "7분 전"),
    ({"hours": 3}, "3시간 전"),
    ({"days": 2}, "2일 전"),
])
def test_relative_time_buckets(kw, want):
    assert app._relative_time(_iso(**kw)) == want


def test_relative_time_treats_naive_timestamp_as_utc():
    """DB 에 타임존 없이 저장된 값이 섞여 있다 — 로컬로 해석하면 9시간이
    어긋나 "9시간 전"이 "방금 전"으로 보인다."""
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    assert app._relative_time(naive) == "방금 전"


def test_relative_time_passes_through_unparseable():
    """못 읽는 값을 빈칸으로 만들면 화면에서 정보가 사라진다 — 원문을 그대로 둔다."""
    assert app._relative_time("언제인지 모름") == "언제인지 모름"
    assert app._relative_time("") == ""

