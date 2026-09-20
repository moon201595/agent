"""⑨ 배달 — 오늘 돌릴 날인가. 주말과 한국 공휴일에는 스캔도 메일도 쉰다.

2026-09-20 사용자 결정. 그전에는 **매일** 돌았다. 근거는 두 가지였다 — arXiv 주말 신규가 거의 없고
(8/29~31 실측: cs.CV 전체 0편), 읽지 않는 메일이 그날 하루를 대표할 이유가 없다.

**건너뛴 날의 논문은 사라지지 않는다.** 검색 창은 달력이 아니라 지난 실행에서 이어받는 델타다
(`research_profile.next_since`) — 금요일에 멈췄다 월요일에 돌면 그 창이 금~월을 덮는다. 이것을
확인하고 결정했다.

**실패 방향은 열어 둔다.** 휴일 판정이 어떤 이유로든 안 되면 **돌리는 쪽**을 고른다 — 메일이 한 번
더 가는 것보다 안 가는 게 나쁘다(CLAUDE.md 규칙 6). `holidays` 를 못 읽으면 주말만 쉬고, 그마저
실패하면 부르는 쪽(`run_daily_scan.sh`)이 그냥 돌린다.

공휴일은 `holidays` 패키지가 음력 명절(설·추석·부처님오신날)과 **대체공휴일**까지 계산한다. 직접
표를 적으면 매년 손으로 고쳐야 하고, 고치는 것을 잊으면 조용히 틀린다(2026-10-05 개천절 대체 휴일이
바로 그런 날이다). 네트워크를 쓰지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from time_policy import KST

COUNTRY = "KR"
_MISSING: set = set()      # holidays 를 못 읽었을 때 — 주말만 쉰다


def _holidays(year: int):
    """그 해의 공휴일 집합. 읽지 못하면 빈 집합 — 주말만 쉬는 쪽으로 물러난다."""
    try:
        import holidays
        return holidays.country_holidays(COUNTRY, years=[year])
    except Exception:      # noqa: BLE001 — 달력을 못 읽어도 스캔은 돈다
        return _MISSING


def today(moment: datetime | None = None) -> date:
    """**읽는 사람 날짜(KST)**. 05:00 KST 실행은 UTC 로 전날 20:00 이라 UTC 날짜로 보면 하루가 밀린다."""
    return (moment or datetime.now(tz=KST)).astimezone(KST).date()


def holiday_name(day: date) -> str | None:
    table = _holidays(day.year)
    return table.get(day) if table else None


def is_work_day(day: date | None = None) -> bool:
    """돌리는 날인가. 토·일과 공휴일이면 False."""
    day = day or today()
    if day.weekday() >= 5:
        return False
    return holiday_name(day) is None


def skip_reason(day: date | None = None) -> str | None:
    """왜 쉬는지 한 마디. 로그에 그대로 적는다 — 왜 안 돌았는지가 로그에 없으면 고장과 구별이 안 된다."""
    day = day or today()
    if day.weekday() >= 5:
        return "주말"
    name = holiday_name(day)
    return f"공휴일 · {name}" if name else None


def is_weekly_day(day: date | None = None) -> bool:
    """주간 관리를 돌리는 날인가 — **그 주의 첫 근무일**.

    "월요일"로 못 박지 않는 이유(2026-09-20): 월요일이 공휴일인 주가 실제로 있다(2026-10-05 개천절
    대체 휴일). 그때 월요일 고정이면 그 주는 키워드 조정이 통째로 건너뛴다. 주의 첫 근무일이면
    보통은 월요일이고, 월요일이 쉬면 화요일이 그 자리를 받는다.
    """
    day = day or today()
    if not is_work_day(day):
        return False
    monday = day - timedelta(days=day.weekday())
    return not any(is_work_day(monday + timedelta(days=i)) for i in range(day.weekday()))


def next_work_day(after: date | None = None) -> date:
    """`after` **다음**의 첫 근무일. 화면이 "다음 실행"을 적을 때 쓴다."""
    day = (after or today()) + timedelta(days=1)
    for _ in range(30):          # 연휴가 아무리 길어도 한 달을 넘지 않는다 — 무한 루프를 만들지 않는다
        if is_work_day(day):
            return day
        day += timedelta(days=1)
    return day


def next_weekly_day(after: date | None = None) -> date:
    """`after` 다음의 첫 '주간 관리' 날. 근무일을 따라가다 그 주 첫 근무일을 만나면 거기다."""
    day = (after or today()) + timedelta(days=1)
    for _ in range(30):
        if is_weekly_day(day):
            return day
        day += timedelta(days=1)
    return day


def day_kind(day: date | None = None) -> str:
    """`run_daily_scan.sh` 가 읽는 한 낱말 — `skip` · `weekly` · `run`.

    셸이 요일을 직접 세지 않게 한다. 셸에서 세면 그 판정을 테스트가 실행해 볼 수 없고,
    문자열만 맞춰 보는 테스트가 된다(2026-09-20 Codex 지적).
    """
    day = day or today()
    if not is_work_day(day):
        return "skip"
    return "weekly" if is_weekly_day(day) else "run"


if __name__ == "__main__":       # `python -m work_calendar` — 셸이 이것만 읽는다
    print(day_kind())
