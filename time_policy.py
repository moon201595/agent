"""⑧⑨ 공용 시각 정책 — 저장은 UTC ISO, 표시·운영일·요일은 KST. 2026-09-17 에 다섯 곳(mail_ledger·ops_dashboard·feedback_weights·
scripts/check_daily_mail·scripts/morning_report·trend_report·run_profile_scan)에 흩어져 있던 같은 변환을 여기 하나로 모았다(Codex 구조 검토 §8-150).

왜 하나여야 하는가: `scripts/morning_report._kst` 는 `astimezone()`(인자 없음)이라 **호스트 시간대**를 따랐다 — WSL 이 UTC 면 화면·메일과 날짜가
달랐다. 나머지는 각자 `timezone(timedelta(hours=9))` 를 선언했다. 규칙은 두 줄이다:
- DB·로그에 쓰는 시각은 항상 UTC ISO(`+00:00`). 시간대 없는 값은 UTC 로 해석한다(옛 저장 관행).
- 사람에게 보이는 시각·운영일(05:00 경계)·요일은 KST(Asia/Seoul).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc
DAILY_BOUNDARY = time(5)        # 새벽 배달 시각 — "오늘의 운영일"은 이 경계로 정한다


def as_utc(value: datetime) -> datetime:
    """시간대 없는 값은 UTC 로 본다(저장소 관행). 있으면 UTC 로 바꾼다."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_iso(value: str | None) -> datetime | None:
    """ISO 문자열(`Z` 포함) → aware datetime(UTC). 못 읽으면 None."""
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def to_kst(value: datetime) -> datetime:
    return as_utc(value).astimezone(KST)


def kst_hm(value: str | datetime | None, missing: str = "—") -> str:
    """'09-16 11:13' — 화면·메일의 시각 표시. 못 읽으면 원문 앞부분(디버그에 낫다)."""
    if value is None or value == "":
        return missing
    dt = value if isinstance(value, datetime) else parse_iso(value)
    if dt is None:
        return str(value)[:16]
    return to_kst(dt).strftime("%m-%d %H:%M")


def kst_day(value: str | datetime | None, missing: str = "—") -> str:
    """'2026-09-16' — KST 날짜 문자열."""
    if value is None or value == "":
        return missing
    dt = value if isinstance(value, datetime) else parse_iso(value)
    if dt is None:
        return str(value)[:10]
    return to_kst(dt).strftime("%Y-%m-%d")


def kst_date(value: str | datetime | None) -> date | None:
    dt = value if isinstance(value, datetime) else parse_iso(value)
    return to_kst(dt).date() if dt else None


def kst_weekday(value: datetime | None = None) -> int:
    """읽는 사람 기준 요일(월=0)."""
    return to_kst(value or datetime.now(UTC)).weekday()


def operating_day(now: datetime | None = None) -> date:
    """운영일 — 05:00 KST 경계 이전이면 어제. 새벽 점검·발송 대장이 같은 날짜를 보게 한다."""
    local = to_kst(now or datetime.now(UTC))
    return local.date() if local.time() >= DAILY_BOUNDARY else (local - timedelta(days=1)).date()


def operating_day_start_utc(day: date) -> datetime:
    """운영일의 시작(05:00 KST)을 UTC 로."""
    return datetime.combine(day, DAILY_BOUNDARY, tzinfo=KST).astimezone(UTC)
