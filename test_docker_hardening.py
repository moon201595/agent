"""⑦ 실행 컨테이너 격리 인자 구성 테스트(M7) — Docker 없이 돈다.

subprocess.run 을 가로채 실제로 조립된 `docker run` 명령줄만 검사한다.
컨테이너를 띄우지 않으므로 CI·오프라인에서도 그대로 돈다.
"""

import subprocess

import docker_runner


def _capture_run_cmd(monkeypatch, **kwargs):
    """_run_container 가 만드는 docker run 인자를 잡아낸다."""
    captured = {}

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["docker", "run"]:
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["docker", "wait"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    docker_runner._run_container("tag", "echo hi", 10, network=False, **kwargs)
    return captured["cmd"]


def test_drops_all_capabilities(monkeypatch):
    cmd = _capture_run_cmd(monkeypatch)
    assert "--cap-drop" in cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"


def test_blocks_privilege_escalation(monkeypatch):
    cmd = _capture_run_cmd(monkeypatch)
    assert "--security-opt" in cmd
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"


def test_limits_pids_against_fork_bomb(monkeypatch):
    cmd = _capture_run_cmd(monkeypatch)
    assert "--pids-limit" in cmd


def test_root_filesystem_is_read_only(monkeypatch):
    cmd = _capture_run_cmd(monkeypatch)
    assert "--read-only" in cmd
    # 쓰기가 필요한 곳은 tmpfs 하나로 한정한다 — 실측으로 /tmp 만 쓰기 가능,
    # / 는 차단인 것을 확인했다.
    assert "--tmpfs" in cmd


def test_runs_as_non_root(monkeypatch):
    """실측: 컨테이너 안에서 id -u 가 65534(nobody)로 나온다."""
    cmd = _capture_run_cmd(monkeypatch)
    assert "--user" in cmd
    assert cmd[cmd.index("--user") + 1].startswith("65534")


def test_python_does_not_write_bytecode(monkeypatch):
    """read-only + 비root 조합에서 __pycache__ 쓰기 실패로 정상 repo 가
    깨지는 걸 막는 환경변수 — 격리와 세트로 붙어야 한다."""
    cmd = _capture_run_cmd(monkeypatch)
    assert "PYTHONDONTWRITEBYTECODE=1" in cmd
    assert "HOME=/tmp" in cmd  # ~/.cache 쓰기도 tmpfs 로 돌린다


def test_network_disabled_by_default(monkeypatch):
    cmd = _capture_run_cmd(monkeypatch)
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"


def test_resource_limits_still_present(monkeypatch):
    """기존 보호(메모리·CPU)가 하드닝으로 사라지지 않았는지."""
    cmd = _capture_run_cmd(monkeypatch)
    for flag in ("--memory", "--memory-swap", "--cpus"):
        assert flag in cmd


def test_security_flags_can_be_overridden_for_regression_experiments(monkeypatch):
    """회귀 실험에서 플래그를 하나씩 켜보려고 뚫어둔 구멍 — 평상시엔 기본값."""
    cmd = _capture_run_cmd(monkeypatch, security_flags=["--cap-drop", "ALL"])
    assert "--read-only" not in cmd
    assert "--cap-drop" in cmd
