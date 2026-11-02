"""일일 체인의 열린 파일 한도 — cron soft 한도 1024 에서 Errno 24 로 메일이 빠졌다(2026-10-23·10-30, 2026-11-02 완화)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent



def test_daily_chain_raises_open_file_limit_before_the_scan():
    """cron 의 soft 한도 1024 에서 Errno 24 로 메일이 일부 빠졌다(10/23·10/30) — 한도 올림을 지우거나 스캔 뒤로 옮기면 실패한다.
    실제 셸로 실행해 값이 바뀌는지도 본다(문자열만 보면 오타를 못 잡는다)."""
    sh = (ROOT / "run_daily_scan.sh").read_text(encoding="utf-8")
    raise_at = sh.index("ulimit -n 10240")
    assert raise_at < sh.index(".venv/bin/python run_profile_scan.py --all")
    line = sh[sh.rindex("\n", 0, raise_at) + 1:sh.index("\n", raise_at)]
    hard = subprocess.run(["bash", "-c", "ulimit -Hn"], capture_output=True, text=True).stdout.strip()
    if hard != "unlimited" and int(hard) < 10240:
        pytest.skip(f"hard 한도 {hard} < 10240 — 이 환경에서는 올릴 수 없다")
    out = subprocess.run(["bash", "-c", f"ulimit -Sn 1024; {line.strip()}; ulimit -Sn"], capture_output=True, text=True).stdout.strip()
    assert out == "10240"
