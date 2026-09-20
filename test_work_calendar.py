"""주말·공휴일 휴무 — 2026-09-20 사용자 결정.

여기서 지키는 계약: 쉬는 날은 안 돌고, 그 주 주간 관리는 **첫 근무일**이 받고,
달력을 못 읽으면 **돌리는 쪽**으로 물러난다.
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

import work_calendar as wc

ROOT = Path(__file__).resolve().parent


def test_weekends_and_korean_holidays_are_off():
    """이 테스트가 잡는 것: 음력 명절이나 대체공휴일을 놓치는 것.

    직접 표를 적으면 매년 손으로 고쳐야 하고 잊으면 조용히 틀린다 — 2026-10-05 개천절 대체 휴일이
    바로 그런 날이다(토요일 개천절이 월요일로 넘어온다)."""
    assert wc.is_work_day(date(2026, 9, 21))            # 월
    assert not wc.is_work_day(date(2026, 9, 26))        # 토
    assert not wc.is_work_day(date(2026, 9, 25))        # 추석
    assert not wc.is_work_day(date(2026, 9, 24))        # 추석 전날
    assert not wc.is_work_day(date(2026, 10, 5))        # 개천절 대체 휴일(월)
    assert wc.is_work_day(date(2026, 10, 6))
    assert wc.skip_reason(date(2026, 9, 26)) == "주말"
    assert "추석" in wc.skip_reason(date(2026, 9, 25))
    assert wc.skip_reason(date(2026, 9, 21)) is None


def test_weekly_management_moves_to_the_first_working_day():
    """이 테스트가 잡는 것: 주간 관리를 "월요일"로 못 박는 것.

    월요일이 공휴일인 주가 실제로 있다(2026-10-05). 고정이면 그 주는 키워드 조정이 통째로 건너뛴다 —
    바꾼 기준으로 그 주 논문을 골라야 하는데 한 주를 통째로 놓친다."""
    assert wc.is_weekly_day(date(2026, 9, 21))          # 보통 주는 월요일
    assert not wc.is_weekly_day(date(2026, 9, 22))      # 화요일은 아니다
    assert not wc.is_weekly_day(date(2026, 10, 5))      # 그 월요일은 휴일
    assert wc.is_weekly_day(date(2026, 10, 6))          # 화요일이 그 자리를 받는다
    assert not wc.is_weekly_day(date(2026, 10, 7))      # 그 주에 두 번 돌지 않는다
    assert wc.next_weekly_day(date(2026, 10, 2)) == date(2026, 10, 6)


def test_a_long_break_does_not_lose_the_next_run():
    """이 테스트가 잡는 것: 연휴를 건너뛰다 무한 루프에 빠지거나 다음 근무일을 못 찾는 것."""
    assert wc.next_work_day(date(2026, 9, 23)) == date(2026, 9, 28)   # 목·금 추석 + 주말
    assert wc.next_work_day(date(2026, 10, 2)) == date(2026, 10, 6)   # 토·일 + 대체 휴일


def test_an_unreadable_calendar_still_runs_on_weekdays(monkeypatch):
    """이 테스트가 잡는 것: 달력을 못 읽었을 때 **안 돌리는 쪽**으로 물러나는 것.

    메일이 한 번 더 가는 것보다 안 가는 게 나쁘다(규칙 6). `holidays` 가 없으면 주말만 쉰다."""
    monkeypatch.setattr(wc, "_holidays", lambda year: wc._MISSING)
    assert wc.is_work_day(date(2026, 9, 25))            # 추석인데도 돈다 — 모르면 돌린다
    assert not wc.is_work_day(date(2026, 9, 26))        # 주말은 달력 없이도 안다
    assert wc.skip_reason(date(2026, 9, 25)) is None


def test_the_shell_reads_one_word_and_falls_back_to_run():
    """이 테스트가 잡는 것: 셸이 요일을 직접 세게 두는 것(그러면 판정을 실행해 볼 수 없다) ·
    판정이 실패했을 때 건너뛰는 쪽으로 넘어가는 것.

    `python -m work_calendar` 를 **실제로 실행**해 한 낱말이 나오는지 보고, 셸이 그 실패를
    `|| echo run` 으로 받는지 본다."""
    out = subprocess.run([sys.executable, "-m", "work_calendar"], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert out.stdout.strip() in ("skip", "weekly", "run")

    sh = (ROOT / "run_daily_scan.sh").read_text(encoding="utf-8")
    # 판정이 실패하면 **옛 규칙**으로 물러난다 — 그냥 run 이면 달력이 죽은 월요일에 주간 관리가
    # 통째로 생략된다(2026-09-20 Codex 재검토). 어느 쪽이든 "안 돌린다"로는 물러나지 않는다.
    block = sh[sh.index("DAY_KIND="):sh.index("DAY_KIND=") + 260]
    assert "echo weekly" in block and "echo run" in block and "skip" not in block.split("\n")[0]
    assert 'if [ "$DAY_KIND" = "skip" ]' in sh and 'if [ "$DAY_KIND" = "weekly" ]' in sh


def test_day_kind_names_the_three_cases():
    assert wc.day_kind(date(2026, 9, 21)) == "weekly"    # 그 주 첫 근무일
    assert wc.day_kind(date(2026, 9, 22)) == "run"       # 보통 근무일
    assert wc.day_kind(date(2026, 9, 26)) == "skip"      # 주말
    assert wc.day_kind(date(2026, 10, 6)) == "weekly"    # 월요일이 쉬면 화요일


def test_the_mail_watchdog_stays_quiet_on_days_off(tmp_path, capsys):
    """이 테스트가 잡는 것: 쉬는 날에도 "브리핑이 안 왔다"고 알리는 것.

    토·일·연휴마다 경보가 오면 **정말 고장난 날을 못 알아챈다** — 경보가 늑대소년이 된다."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    check = importlib.import_module("check_daily_mail")
    from datetime import datetime, timezone
    saturday = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)      # KST 9/27 07:00 (일)
    rc = check.main(["--dry-run", "--db", str(tmp_path / "none.db"),
                     "--log", str(tmp_path / "none.log"), "--stamp", str(tmp_path / "s"),
                     "--lock", str(tmp_path / "l")], now=saturday)
    assert rc == 0
    assert "발송하지 않는 날" in capsys.readouterr().out


def test_the_mail_report_day_follows_the_same_calendar_as_the_shell():
    """이 테스트가 잡는 것: 셸과 메일이 **다른 달력**을 보는 것(2026-09-20 Codex 재검토 A).

    셸은 "그 주 첫 근무일"에 주간 관리를 돌리는데 메일 쪽이 KST 월요일만 보고 있었다 — 월요일이
    공휴일인 주(10/5)에는 **기준은 바뀌는데 그 변경이 메일에 안 실린다.** 실측으로 10/6 에
    `day_kind=weekly` 인데 `is_weekly_review_day=False` 였다."""
    from datetime import datetime, timezone
    import run_profile_scan as rps
    from time_policy import KST
    for day in (date(2026, 9, 21), date(2026, 10, 6)):               # 보통 주의 월요일 · 휴일 밀린 화요일
        moment = datetime(day.year, day.month, day.day, 5, 5, tzinfo=KST).astimezone(timezone.utc)
        assert wc.day_kind(day) == "weekly"
        assert rps.is_weekly_review_day(moment), f"{day} 는 셸이 weekly 인데 메일이 아니라고 한다"
    ordinary = datetime(2026, 10, 7, 5, 5, tzinfo=KST).astimezone(timezone.utc)
    assert not rps.is_weekly_review_day(ordinary)                    # 그 주에 두 번 싣지 않는다
