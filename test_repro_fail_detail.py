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
        "no_install_target", "build_failed", "install_only_no_run_target",
        "run_network_suspected", "run_timeout", "run_nonzero_exit",
    }
    known = {detail for _stage, detail in digest._REPRO_LABELS}
    assert produced == known


# ---------------------------------------------------------------- 가드 3분기 (2026-09-02)
#
# 실측 배경: ⑦ 실행 단계 도달률이 30건 중 6건(20%)이고 최대 원인이
# no_target(40%)이었다. 표본 3개를 GitHub API 로 확인하니 **셋 다 설치 파일이
# 있었다**(llm-awq/pyproject.toml, Hadamard·UrbanGround/requirements.txt).
#
# 원인은 detect_install_plan 이 아니었다 — 그 함수는 정상 동작한다. 호출부
# 가드가 **실행 대상만** 보면서 로그에는 "설치 대상도 못 찾음"이라고 적었다.
# 조건과 메시지가 어긋나 있었다.


def _plan_for(tmp_path, files: dict[str, str]):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return dr.detect_install_plan(tmp_path)


def test_detect_install_plan_is_not_the_bug(tmp_path):
    """requirements.txt 만 있어도 installer·install_cmd 는 제대로 채워진다.
    이 함수에 테스트를 써봐야 정상 동작만 고정할 뿐 결함은 안 잡힌다 —
    처음에 원인을 여기로 잘못 짚었던 것을 기록해 둔다."""
    plan = _plan_for(tmp_path, {"requirements.txt": "numpy\n", "train.py": "print(1)\n"})
    assert plan.installer == "pip-requirements"
    assert "requirements.txt" in plan.install_cmd
    assert plan.entry_point is None and plan.package_name is None   # 실행 대상만 없다


def _outcome(monkeypatch, tmp_path, files, build_ok=True):
    """run_repo_in_docker 를 Docker 없이 돌린다. _run_container 가 불렸는지도
    같이 돌려준다 — install_only 경로에서 placeholder 를 실행하면 안 된다."""
    ran = []
    monkeypatch.setattr(dr, "_write_dockerfile", lambda *a, **kw: tmp_path / "Dockerfile.x")
    monkeypatch.setattr(dr, "_build_image", lambda *a, **kw: (build_ok, "빌드 로그"))
    monkeypatch.setattr(dr, "_run_container",
                        lambda *a, **kw: ran.append(1) or dr.RunResult(True, 0, "", "", False, False, 1.0))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return dr.run_repo_in_docker(tmp_path), ran


def test_install_target_without_run_target_is_install_only(monkeypatch, tmp_path):
    """**핵심 회귀**: requirements.txt 가 있는 저장소가 "아무것도 없음"으로
    거부되던 것을 고쳤다. 실측된 Hadamard·UrbanGround 가 이 경우다."""
    out, ran = _outcome(monkeypatch, tmp_path, {"requirements.txt": "numpy\n"})
    assert out["stage"] == "install_only"
    assert out["fail_detail"] == "install_only_no_run_target"
    assert out["success"] is False          # 성공으로 세지 않는다


def test_install_only_never_runs_the_placeholder(monkeypatch, tmp_path):
    """placeholder 를 돌리면 exit 0 이 나오고 그게 성공으로 세어져 검증기와
    같은 거짓 통과가 된다(2026-08-03 TSPulse 실측). build 에서 멈추면 exit 0 이
    나올 자리 자체가 없어 그 문제가 구조적으로 사라진다."""
    _out, ran = _outcome(monkeypatch, tmp_path, {"requirements.txt": "numpy\n"})
    assert ran == []


def test_nothing_at_all_is_still_no_target(monkeypatch, tmp_path):
    """설치 대상도 실행 대상도 없으면 예전 판정 그대로 — 가중치·문서 전용
    저장소가 여기 해당한다(실측: HuggingFace 모델 페이지)."""
    out, ran = _outcome(monkeypatch, tmp_path, {"README.md": "weights only\n"})
    assert out["stage"] == "no_target"
    assert ran == []


def test_build_failure_still_reports_build_not_install_only(monkeypatch, tmp_path):
    """설치가 실패했으면 "설치만 확인"이라고 하면 안 된다."""
    out, _ran = _outcome(monkeypatch, tmp_path, {"requirements.txt": "nope\n"}, build_ok=False)
    assert out["stage"] == "build"
    assert out["fail_detail"] == "build_failed"


def test_run_target_present_still_runs(monkeypatch, tmp_path):
    """임포트 대상이 있으면 종전대로 실행까지 간다 — 회귀 방지."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    out, ran = _outcome(monkeypatch, tmp_path, {"requirements.txt": "numpy\n"})
    assert out["stage"] == "run"
    assert ran == [1]
