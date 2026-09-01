"""⑦ 실패 사유 판정 — Docker·네트워크 없이 돈다 (2026-09-01, §8-16 + §8-24).

배경: 다이제스트의 [재현 ✗] 하나가 서로 완전히 다른 사실 넷을 뭉개고 있었다.
그중 "실행이 네트워크 차단으로 죽었을 수 있다"는 §8-16(egress allowlist 가
필요한가)의 유일한 근거인데, 계산만 되고 저장되지 않아 29건이 쌓이는 동안
아무것도 안 모였다. 사유를 매 건 판정해 남기면 라벨도 정확해지고 그 근거도
저절로 쌓인다.

판정은 전부 결정론적이다 — git 종료 상태와 문자열 대조, exit code. LLM 없음.
"""

import subprocess
from pathlib import Path

import pytest

import docker_runner as dr


# ---------------------------------------------------------------- _clone 사유 분류


def _fake_clone(monkeypatch, *, stderr=None, timeout=False, ok=False):
    def fake_run(cmd, *a, **kw):
        if timeout:
            raise subprocess.TimeoutExpired(cmd, 120)
        if ok:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise subprocess.CalledProcessError(128, cmd, output="", stderr=stderr or "")

    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.mark.parametrize("stderr", [
    "remote: Repository not found.\nfatal: repository 'https://github.com/a/b/' not found",
    "fatal: could not read Username for 'https://github.com': No such device or address",
    "remote: Repository not found.",
    "fatal: Authentication failed for 'https://github.com/a/b/'",
])
def test_missing_repo_is_classified_as_repo_not_found(monkeypatch, tmp_path, stderr):
    """실측(2608.25176 / YOLOEZA): 저자가 논문에 적은 저장소가 404 였다.
    비공개 저장소도 GitHub 은 같은 방식으로 답하는데, 우리 입장에서는 둘 다
    "볼 수 있는 저자 코드가 없다"는 같은 사실이다."""
    _fake_clone(monkeypatch, stderr=stderr)
    path, detail = dr._clone("https://github.com/a/b", tmp_path)
    assert path is None
    assert detail == "repo_not_found"


def test_other_clone_failure_is_not_called_not_found(monkeypatch, tmp_path):
    """없는 저장소와 그냥 실패한 클론을 같게 부르면, 고치려던 뭉갬을 한
    단계 안쪽에서 다시 만드는 셈이다."""
    _fake_clone(monkeypatch, stderr="fatal: unable to access ...: SSL error")
    assert dr._clone("https://github.com/a/b", tmp_path)[1] == "clone_failed"


def test_clone_timeout_has_its_own_reason(monkeypatch, tmp_path):
    _fake_clone(monkeypatch, timeout=True)
    assert dr._clone("https://github.com/a/b", tmp_path)[1] == "clone_timeout"


def test_unsupported_host_is_rejected_before_running_git(monkeypatch, tmp_path):
    """clone 불가 호스트(project page 등)는 git 을 부르지도 않는다."""
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(1))
    path, detail = dr._clone("https://example.com/project", tmp_path)
    assert (path, detail) == (None, "unsupported_host")
    assert called == []


def test_successful_clone_reports_no_reason(monkeypatch, tmp_path):
    _fake_clone(monkeypatch, ok=True)
    path, detail = dr._clone("https://github.com/a/b", tmp_path)
    assert path is not None
    assert detail == ""


# ---------------------------------------------------------------- 실행 단계 사유


def test_network_error_in_output_is_recorded_as_suspected(monkeypatch):
    """§8-16 의 근거가 되는 값. 이게 저장돼야 "차단 때문에 실패한 적이
    있는가"에 몇 달 뒤가 아니라 매 건 답할 수 있다."""
    assert dr._NETWORK_ERROR_RE.search("urllib3.exceptions.NameResolutionError: ...")
    assert dr._NETWORK_ERROR_RE.search("Temporary failure in name resolution")
    assert not dr._NETWORK_ERROR_RE.search("AssertionError: expected 3 got 4")


@pytest.mark.parametrize("success,timed_out,suspected,expected", [
    (True,  False, False, ""),
    (True,  False, True,  ""),                       # 성공이면 사유가 없다
    (False, True,  False, "run_timeout"),
    (False, True,  True,  "run_timeout"),            # 타임아웃이 네트워크보다 먼저
    (False, False, True,  "run_network_suspected"),
    (False, False, False, "run_nonzero_exit"),
])
def test_run_fail_detail_mapping(success, timed_out, suspected, expected):
    """타임아웃을 네트워크 의심보다 먼저 보는 게 핵심이다 — 시간이 다 되어
    죽은 실행의 출력에도 네트워크 오류가 섞일 수 있는데, 그때 원인은
    타임아웃이지 차단이 아니다. 뒤집으면 §8-16 근거가 부풀려진다."""
    assert dr._run_fail_detail(success, timed_out, suspected) == expected


def test_fail_detail_codes_are_the_ones_digest_knows():
    """docker_runner 가 쓰는 코드와 digest 가 아는 코드가 어긋나면, 라벨이
    조용히 기본값으로 떨어져 사유가 다시 사라진다."""
    import digest
    produced = {
        "repo_not_found", "clone_timeout", "clone_failed", "unsupported_host",
        "no_install_target", "build_failed",
        "run_network_suspected", "run_timeout", "run_nonzero_exit",
    }
    known = {detail for _stage, detail in digest._REPRO_LABELS}
    assert produced == known
