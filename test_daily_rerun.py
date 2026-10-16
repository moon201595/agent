"""⑨ 배달 — 점검이 그날 실패를 보면 한 번만 다시 돌린다(2026-10-16).

그날 노트북을 켠 직후 WSL 인스턴스가 1~2분마다 꺼졌다 켜졌다. 09:55 에 Windows 예약 작업이 시작한 실행이 09:56:13 WSL 종료와 함께 죽었고,
예약 작업은 하루 한 번만 실행을 시작해 그 뒤로 아무도 다시 돌리지 않았다. 10:30 점검은 알림만 보냈다. 출력은 파일 블록 버퍼라
죽기 직전 몇십 초가 사라져 어디서 멈췄는지도 알 수 없었다. Codex 읽기 전용 검토(같은 날, P2 4·P3 2)의 경계를 함께 고정한다.
"""
import importlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
check = importlib.import_module("check_daily_mail")

NOW = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)          # 10/16(금) 10:30 KST 점검 회차 — 근무일
FAILED_LOG = "=== 2026-10-16T00:55:34Z 시작 (pid 2199) ===\n  [주간 따라잡기] 할 일 없음\n"   # 종료 줄 없음


@pytest.fixture(autouse=True)
def quick_confirm(monkeypatch):
    monkeypatch.setattr(check, "RERUN_CONFIRM_S", 0.0)                # 띄운 뒤 잠금 확인을 기다리지 않는다(테스트 시간)


def _db(tmp_path):
    db = tmp_path / "p.db"
    if db.exists():
        return db
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE profiles (profile_id TEXT, last_digest_at TEXT, schedule_frequency TEXT)")
        con.execute("CREATE TABLE profile_recipients (profile_id TEXT, email TEXT, active INTEGER)")
        con.execute("INSERT INTO profiles VALUES ('team_robot', '2026-10-15T02:00:00+00:00', 'daily')")   # 어제 — 오늘 안 나감
        con.execute("INSERT INTO profile_recipients VALUES ('team_robot', 'ops@example.invalid', 1)")
    return db


def _run(tmp_path, monkeypatch, *extra, launch=None, log=FAILED_LOG, now=NOW):
    sent = []
    monkeypatch.setattr(check, "_send_alert", lambda recipients, reasons, today, rerun="": sent.append((list(reasons), rerun)))
    path = tmp_path / "daily.log"
    path.write_text(log, encoding="utf-8")
    rc = check.main(["--db", str(_db(tmp_path)), "--log", str(path), "--stamp", str(tmp_path / "alert.stamp"),
                     "--lock", str(tmp_path / "none.lock"), "--rerun-stamp", str(tmp_path / "rerun.stamp"), *extra],
                    now=now, launch=launch)
    return rc, sent


def test_failed_day_reruns_once_then_alerts_again_if_the_rerun_also_fails(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실패를 본 점검이 다시 돌리지 않거나(10/16 처럼 알림만 남음), 같은 날 두 번째 점검이 또 돌리거나
    (같은 고장을 계속 태움), 알림에 재실행 사실을 쓰지 않거나, 06:30 재실행 뒤 10:30 에도 실패인데 조용하거나(Codex 검토 P3), 그 두 번째 알림을
    세 번째 점검에서 또 보내면 실패한다. 알림 표시는 지우지 않고 운영과 같은 흐름으로 본다."""
    calls = []
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: calls.append(1))
    assert rc == 2 and calls == [1] and (tmp_path / "rerun.stamp").read_text().strip() == "2026-10-16 started"
    assert len(sent) == 1 and "자동 재실행을 시작했습니다(오늘 1회)" in sent[0][1]
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: calls.append(2))
    assert calls == [1] and len(sent) == 1 and sent[0][0][0] == "자동 재실행 뒤에도 실패" and sent[0][1] == ""
    assert (tmp_path / "alert.stamp").read_text().strip() == "2026-10-16 after-rerun"
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: calls.append(3))
    assert calls == [1] and sent == []


def test_no_launcher_dry_run_no_rerun_and_launch_failure(tmp_path, monkeypatch, capsys):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: main() 을 실행기 없이 부른 경우(테스트·수동 재현)에 운영 스크립트를 띄우거나(Codex 검토 P2),
    --dry-run·--no-rerun 에서 돌리거나, 시작이 실패했는데 "오늘 했다" 표시를 남겨 다음 점검의 기회를 막거나, 하지 않은 재실행을 알림에 쓰면 실패한다."""
    calls = []
    assert _run(tmp_path, monkeypatch)[0] == 2 and not (tmp_path / "rerun.stamp").exists()
    assert "실행기 없음" in capsys.readouterr().out
    (tmp_path / "alert.stamp").unlink()
    captured = {}
    monkeypatch.setattr(check, "main", lambda argv=None, **kw: captured.update(kw) or 0)
    assert check.cli([]) == 0 and captured["launch"] is check.launch_rerun     # 명령줄 진입점만 실제 실행기를 연결한다
    monkeypatch.undo()
    monkeypatch.setattr(check, "RERUN_CONFIRM_S", 0.0)
    assert _run(tmp_path, monkeypatch, "--dry-run", launch=lambda: calls.append(1))[0] == 2 and calls == []
    assert _run(tmp_path, monkeypatch, "--no-rerun", launch=lambda: calls.append(1))[0] == 2 and calls == []
    (tmp_path / "alert.stamp").unlink(missing_ok=True)

    def broken():
        raise OSError("bash 없음")
    rc, sent = _run(tmp_path, monkeypatch, launch=broken)
    assert rc == 2 and not (tmp_path / "rerun.stamp").exists() and sent[0][1] == ""


def test_stamp_errors_never_stop_the_alert(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P2): 재실행 표시를 못 읽거나 못 쓸 때 예외로 점검이 끝나 부재 알림까지 안 나가거나,
    표시를 못 쓴 채 실행을 띄워 다음 점검이 또 띄우게 하면 실패한다."""
    calls = []
    (tmp_path / "rerun.stamp").mkdir()                                    # 읽기·쓰기 모두 실패하는 자리
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: calls.append(1))
    assert rc == 2 and calls == [] and len(sent) == 1 and sent[0][1] == ""


def test_a_rerun_that_lost_the_lock_race_keeps_todays_chance(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P2): 띄운 재실행이 다른 실행에 밀려 바로 건너뛰었는데 "오늘 했다" 를 남겨, 그 다른
    실행이 뒤에 죽었을 때 다음 점검이 다시 시도하지 못하게 하면 실패한다. 반대로 잠금을 잡은 실행은 시작으로 기록해야 한다."""
    monkeypatch.setattr(check, "RERUN_CONFIRM_S", 5.0)

    class Exited:
        def poll(self):
            return 0
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: Exited())
    assert not (tmp_path / "rerun.stamp").exists() and sent[0][1] == ""
    (tmp_path / "alert.stamp").unlink()
    state = [False]                                                       # 점검 시작 때는 안 돌고, 띄운 뒤에는 잠금을 잡았다
    monkeypatch.setattr(check, "scan_in_progress", lambda lock: state.pop(0) if state else True)
    import time
    naps = []
    monkeypatch.setattr(time, "sleep", lambda s: naps.append(s))           # 잠금을 잡았으면 기다리지 않고 바로 "시작" 이다

    class Running:
        def poll(self):
            return None
    rc, sent = _run(tmp_path, monkeypatch, launch=lambda: Running())
    assert (tmp_path / "rerun.stamp").read_text().strip() == "2026-10-16 started" and "자동 재실행" in sent[0][1]
    assert naps == []


def test_profiles_skipped_because_already_sent_today_are_not_failures(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P2): 재실행이 장부를 보고 "오늘 이미 발송 — 건너뜀" 으로 넘긴 프로필을 실패로 판정해
    불필요한 재실행·알림을 만들면 실패한다. 장부에 그날 성공 회차가 없으면 여전히 실패다."""
    db = _db(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE profiles SET last_digest_at='2026-10-16T01:00:00+00:00'")
        con.execute("CREATE TABLE mail_issues (issue_id TEXT, profile_id TEXT, sent_at TEXT, subject TEXT, recipients_total INT,"
                    " recipients_sent INT, paper_count INT, status TEXT)")
    log = ("=== 2026-10-16T01:40:00Z 시작 (pid 9) ===\n{\n  \"team_robot\": {\"delivery\": \"오늘 이미 발송 — 건너뜀\"}\n}\n"
           "=== 2026-10-16T01:50:00Z 종료 (exit 0) ===\n")
    later = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
    rc, sent = _run(tmp_path, monkeypatch, "--no-rerun", log=log, now=later)
    assert rc == 2 and any("delivery" in r for r in sent[0][0])           # 장부에 성공 회차 없음 — 실패
    (tmp_path / "alert.stamp").unlink()
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO mail_issues VALUES ('x', 'team_robot', '2026-10-16T00:30:00+00:00', 's', 1, 1, 5, 'sent')")
    rc, sent = _run(tmp_path, monkeypatch, "--no-rerun", log=log, now=later)
    assert rc == 0 and sent == []


def test_launch_runs_the_daily_script_detached(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P3 — 소스 문자열 검사로는 못 막는다): 재실행이 다른 스크립트를 부르거나, 점검 프로세스에
    묶여(새 세션 아님) 같이 죽거나, 표준입력을 물려받거나, 저장소 밖에서 돌거나, 테스트 중에 진짜로 띄우면 실패한다."""
    with pytest.raises(OSError):
        check.launch_rerun(tmp_path / "cron.log")                          # pytest 중에는 거부
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    seen = {}

    def fake_popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return "proc"
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert check.launch_rerun(tmp_path / "cron.log") == "proc"
    assert seen["argv"] == ["/bin/bash", str(ROOT / "run_daily_scan.sh")] and seen["cwd"] == str(ROOT)
    assert seen["start_new_session"] is True and seen["stdin"] is subprocess.DEVNULL and seen["stderr"] is subprocess.STDOUT


def test_daily_script_writes_python_output_unbuffered():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실행 스크립트에서 PYTHONUNBUFFERED 를 빼 파이썬 출력이 블록 버퍼로 돌아가면 실패한다 —
    그러면 실행이 중간에 죽은 날 마지막 출력이 사라진다(10/16)."""
    script = (ROOT / "run_daily_scan.sh").read_text(encoding="utf-8")
    assert "export PYTHONUNBUFFERED=1" in script
    assert script.index("export PYTHONUNBUFFERED=1") < script.index(".venv/bin/python run_profile_scan.py --all")
