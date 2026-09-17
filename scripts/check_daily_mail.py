"""⑨ 배달 — 새벽 스캔과 다이제스트 발송의 부재를 한 번만 알린다.

cron 자체가 절전·종료로 건너뛰면 스캔 로그와 DB 모두 조용히 멈춘다. 이
검사는 같은 운영일의 종료·저장·발송 세 기록을 서로 대조하고, 수신자에게
알림을 보낸 뒤 날짜 stamp로 재알림을 막는다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from time_policy import KST, kst_date as _tp_kst_date, operating_day_start_utc   # 2026-09-17: 시각 정책 통합
UTC = timezone.utc
DEFAULT_LOG = ROOT / "logs" / "daily_scan.log"
DEFAULT_DB = ROOT / "data" / "papers.db"
DEFAULT_STAMP = ROOT / "logs" / "daily_mail_alert.stamp"
DEFAULT_LOCK = ROOT / "logs" / "daily_scan.lock"


def scan_in_progress(lock_path: Path) -> bool:
    """새벽 스캔이 지금 도는 중인가 — run_daily_scan.sh 가 잡는 flock 을 비차단으로 잡아 본다. 2026-09-16: 프로필이 넷이 되어 실행이
    06:30 을 넘길 수 있다. 도는 중에 "종료 줄 없음"으로 알리면 거짓 경보다 — 판정을 미루고 다음 점검(10:30)에 맡긴다."""
    import fcntl
    if not lock_path.exists():
        return False
    with open(lock_path, "a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False
ALERT_SUBJECT = "[연구 동향 브리핑] 오늘 브리핑이 나가지 않았습니다"

_START_RE = re.compile(r"^=== (?P<timestamp>\S+) 시작 \(pid \d+\) ===$")
_END_RE = re.compile(r"^=== (?P<timestamp>\S+) 종료 \(exit -?\d+\) ===$")


def _parse_utc(value: str) -> datetime:
    """로그의 Z 시각을 비교 가능한 UTC 시각으로 바꾼다."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _operational_day(now: datetime) -> tuple[date, datetime]:
    """05:00 KST 경계를 UTC 전날 20:00 으로 환산해 운영일을 정한다(time_policy)."""
    local_day = now.astimezone(KST).date()
    return local_day, operating_day_start_utc(local_day)


def _completed_runs(log_text: str) -> list[tuple[datetime, datetime, str]]:
    """시작과 종료가 짝을 이룬 로그 블록만 반환한다."""
    start_at: datetime | None = None
    block: list[str] = []
    completed: list[tuple[datetime, datetime, str]] = []
    for line in log_text.splitlines():
        start_match = _START_RE.match(line)
        if start_match:
            start_at = _parse_utc(start_match.group("timestamp"))
            block = [line]
            continue
        if start_at is None:
            continue
        block.append(line)
        end_match = _END_RE.match(line)
        if end_match:
            completed.append((start_at, _parse_utc(end_match.group("timestamp")), "\n".join(block)))
            start_at = None
            block = []
    return completed


def _run_starts(log_text: str) -> list[datetime]:
    """운영일에 시작된 실행이 끝나지 않은 경우를 찾기 위해 시작 시각을 모은다."""
    starts = []
    for line in log_text.splitlines():
        match = _START_RE.match(line)
        if match:
            starts.append(_parse_utc(match.group("timestamp")))
    return starts


def _summary_from_block(block: str) -> dict:
    """종료 직전 JSON 출력에서 delivery 필드를 읽는다."""
    decoder = json.JSONDecoder()
    lines = block.splitlines()
    for line_number, line in enumerate(lines):
        if line.strip() != "{":
            continue
        payload = "\n".join(lines[line_number:]).lstrip()
        try:
            summary, _ = decoder.raw_decode(payload)
        except json.JSONDecodeError:
            return {}
        return summary if isinstance(summary, dict) else {}
    return {}


def _kst_date(value: str | None) -> date | None:
    """DB의 UTC ISO 시각을 KST 날짜로 바꿔 운영일과 비교한다(time_policy)."""
    return _tp_kst_date(value)


def _database_state(db_path: Path, today: date) -> tuple[list[str], list[str], list[str]]:
    """daily 프로필 전체의 digest 날짜와 첫 프로필 수신자를 읽는다."""
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT profile_id, last_digest_at FROM profiles "
            "WHERE COALESCE(schedule_frequency, 'daily')='daily' ORDER BY profile_id"
        ).fetchall()
        if not rows:
            return [], [], ["daily 프로필 없음"]
        # 알림은 **모든 daily 프로필에 공통인 수신자**(운영자)에게만 — 팀원 한 명이 받는 프로필의 장애를 다른 팀원에게 알릴 이유가
        # 없다(2026-09-16). 공통 수신자가 없으면 첫 프로필의 수신자로 떨어진다.
        per_profile = [
            {row[0] for row in con.execute(
                "SELECT email FROM profile_recipients WHERE profile_id=? AND active=1", (profile_id,))}
            for profile_id, _ in rows
        ]
    common = set.intersection(*per_profile) if per_profile else set()
    recipients = sorted(common) if common else sorted(per_profile[0])
    reasons = []
    for profile_id, last_digest_at in rows:
        if _kst_date(last_digest_at) != today:
            reasons.append(f"{profile_id}: profiles.last_digest_at이 오늘이 아님")
    if not recipients:
        reasons.append(f"{rows[0][0]}: 활성 수신자 없음")
    return recipients, [row[0] for row in rows], reasons


def _check_run(
    log_path: Path, profile_ids: list[str], boundary: datetime, now: datetime,
) -> list[str]:
    """오늘 운영일의 종료 블록과 프로필별 발송 결과를 검사한다."""
    if not log_path.exists():
        return ["logs/daily_scan.log 파일이 없음"]
    text = log_path.read_text(encoding="utf-8")
    starts = [start for start in _run_starts(text) if boundary <= start <= now]
    candidates = [
        item for item in _completed_runs(text)
        if boundary <= item[0] <= now and item[1] <= now
    ]
    if not candidates or (starts and starts[-1] > candidates[-1][0]):
        return ["logs/daily_scan.log에 오늘 운영일의 종료 줄이 없음"]
    _, _, block = candidates[-1]
    summary = _summary_from_block(block)
    if not summary:
        return ["오늘 종료 블록의 delivery JSON이 없음"]
    reasons = []
    for profile_id in profile_ids:
        entry = summary.get(profile_id)
        delivery = entry.get("delivery") if isinstance(entry, dict) else None
        if not isinstance(delivery, str) or not delivery.startswith("발송 완료"):
            reasons.append(f"{profile_id}: delivery가 발송 완료가 아님")
    return reasons


def _send_alert(recipients: list[str], reasons: list[str], today: date) -> None:
    """알림 본문은 평문과 HTML에서 같은 사유를 보게 한다."""
    import email_delivery

    text = (
        f"{today.isoformat()} KST 새벽 브리핑 점검에서 문제가 발견되었습니다.\n"
        + "\n".join(f"- {reason}" for reason in reasons)
    )
    email_delivery.send_digest_email(
        text, ALERT_SUBJECT, recipients, f"<pre>{html.escape(text)}</pre>"
    )


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    """점검 결과에 따라 0(정상), 2(문제), 1(스크립트 오류)를 반환한다."""
    parser = argparse.ArgumentParser(description="새벽 연구 브리핑 발송 감시")
    parser.add_argument("--dry-run", action="store_true", help="알림을 보내지 않고 판정만 출력")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stamp", type=Path, default=DEFAULT_STAMP)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    if scan_in_progress(args.lock):
        print("보류: 새벽 스캔이 아직 도는 중 — 다음 점검에서 판정한다")
        return 0

    current = (now or datetime.now(UTC)).astimezone(UTC)
    today, boundary = _operational_day(current)
    try:
        recipients, profile_ids, reasons = _database_state(args.db, today)
        reasons.extend(_check_run(args.log, profile_ids, boundary, current))
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as error:
        print(f"[오류] 점검 자체 실패: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    if not reasons:
        print(f"정상: {today.isoformat()} KST 브리핑 종료·저장·발송 확인")
        return 0

    print(f"문제: {today.isoformat()} KST 브리핑 점검 실패")
    for reason in reasons:
        print(f"- {reason}")
    if args.dry_run:
        print("dry-run: 알림을 보내지 않음")
        return 2
    try:
        if args.stamp.exists() and args.stamp.read_text(encoding="utf-8").strip() == today.isoformat():
            print("오늘 알림은 이미 발송됨")
            return 2
        if not recipients:
            print("알림을 보낼 활성 수신자가 없음", file=sys.stderr)
            return 2
        _send_alert(recipients, reasons, today)
        args.stamp.parent.mkdir(parents=True, exist_ok=True)
        args.stamp.write_text(today.isoformat() + "\n", encoding="utf-8")
        print("부재 알림 발송 완료")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[오류] 부재 알림 처리 실패: {type(error).__name__}: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
