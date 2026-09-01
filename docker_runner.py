"""docker_runner.py — ⑦ 코드 재현: code_finder.py 가 찾은 후보 저장소를
Docker로 격리 실행해서 "설치+실행이 에러 없이 도는가"를 판정한다.

논문 수치 재현이 아니라 설치+실행 성공 여부만 본다(docs/PROGRESS.md §5, §8-6
결정) — 성공 판정은 exit code 뿐이다. 검증기(verify.py)처럼 게임 가능한 점수를
판단 기준으로 쓰지 않는다는 프로젝트 원칙(Goodhart 가드)을 여기서도 지킨다.

2026-08-03 SWE-agent·TSPulse 수동 시행착오(docs/PROGRESS.md §5)로 확인된
설계 결론 4개를 그대로 반영한다:
  1. 베이스 이미지에 git 을 기본 포함한다 — GitPython 처럼 pip 의존성엔 없지만
     시스템 git 바이너리를 import 시점에 요구하는 패키지가 흔하다.
  2. 설치 타임아웃과 실행 타임아웃을 분리한다 — torch류 무거운 의존성은 설치에
     6분+ 걸린다(실측). 실행(스모크 테스트)은 그보다 훨씬 짧아야 정상이다.
  3. `--network none` 은 기본이되, 실행이 네트워크 관련 에러로 실패하면(예:
     사전학습 가중치 다운로드) 네트워크를 열고 1회만 더 시도한다 — 라이브러리
     자체 재시도(지수 백오프, ~90초)를 다 태우고 나서야 실패로 확정되므로
     무한정 기다리지 않는다.
  4. 타임아웃은 셸 timeout 으로 `docker run` 클라이언트를 감싸지 않는다.
     클라이언트를 죽여도 컨테이너 자체는 안 죽는 것을 실측으로 확인했다 —
     컨테이너 이름을 직접 추적해 `docker stop` 을 컨테이너에 건다.

⑦ 는 프로젝트 전체에서 유일하게 자율 재시도 루프(최대 3회)가 있는 단계다
(docs/PROGRESS.md §4). reproduce() 가 그 루프다 — 후보 저장소를 신뢰도 순으로
최대 3개까지 시도하고, 하나라도 성공하면 멈춘다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import code_finder
import server

DEFAULT_BASE_IMAGE = "python:3.11-slim"
INSTALL_TIMEOUT = 900   # 15분 — pip 패키지 설치는 6분대였지만, from-source
                        # editable(-e .) 설치는 granite-tsfm 실측에서 600초를
                        # 넘겼다(2026-08-03). 소스 설치는 더 오래 걸릴 수 있다.
RUN_TIMEOUT = 120       # 스모크 테스트 자체는 짧아야 정상
MEM_LIMIT = "2g"
CPUS = "2"
MAX_ATTEMPTS = 3

# 시스템 패키지가 아닌 것 같은 최상위 디렉터리 이름 — 임포트 대상 추정 시 제외.
# "services"·"common"·"utils" 류는 __init__.py 를 갖고 있어도 알파벳 순으로
# 먼저 걸려 진짜 메인 패키지보다 앞서 뽑히는 걸 실측으로 확인했다(2026-08-03,
# granite-tsfm — 진짜 패키지는 tsfm_public 인데 services 가 먼저 잡힘).
_NON_PACKAGE_DIRS = {
    "tests", "test", "docs", "doc", "examples", "example", "notebooks",
    "scripts", "assets", "trajectories", "config", "data", "tools",
    "services", "common", "utils", "core", "shared",
}

# 2026-08-03 TSPulse 시행착오로 확인된 패턴 — 이 중 하나라도 stderr/stdout에
# 보이면 "설치는 됐는데 실행 시점에 네트워크가 필요해서 실패"로 진단한다.
_NETWORK_ERROR_RE = re.compile(
    r"NameResolutionError|Temporary failure in name resolution|"
    r"Failed to resolve|ConnectionError|Network is unreachable|"
    r"Could not resolve host|Max retries exceeded",
    re.I,
)


@dataclass
class InstallPlan:
    installer: str        # "pip-editable" | "pip-requirements" | "none"
    install_cmd: str
    run_cmd: str           # 결정론적으로 추정한 스모크 테스트 명령
    entry_point: str | None = None
    package_name: str | None = None


@dataclass
class RunResult:
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    network_enabled: bool
    duration_s: float


def _safe_import_name(package_name: str) -> str:
    return package_name.replace("-", "_")


def _guess_importable_package(repo_dir: Path) -> str | None:
    """pyproject.toml/setup.py 가 없을 때 쓰는 최후 수단 — __init__.py 를 가진
    최상위 디렉터리를 찾는다. tests/docs/examples 류는 제외한다.
    """
    for child in sorted(repo_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if child.name.lower() in _NON_PACKAGE_DIRS:
            continue
        if (child / "__init__.py").exists():
            return child.name
    return None


def _find_real_package_name(repo_dir: Path, declared_name: str | None) -> str | None:
    """pyproject.toml 의 배포 이름(distribution name)과 실제 `import` 이름이
    다른 경우가 흔하다(scikit-learn→sklearn, beautifulsoup4→bs4 류) — 이
    프로젝트에서도 granite-tsfm→tsfm_public 로 실측 확인했다(2026-08-03).
    선언된 이름의 디렉터리가 실제로 존재하는지 먼저 확인하고, 없으면(혹은
    애초에 선언이 없으면) __init__.py 를 가진 최상위 디렉터리를 직접 찾는다.
    flat 레이아웃과 `src/` 레이아웃 둘 다 본다.
    """
    if declared_name:
        safe = _safe_import_name(declared_name)
        for base in (repo_dir, repo_dir / "src"):
            if (base / safe / "__init__.py").exists():
                return safe
    for base in (repo_dir, repo_dir / "src"):
        if base.exists():
            guess = _guess_importable_package(base)
            if guess:
                return guess
    return None


def detect_install_plan(repo_dir: Path) -> InstallPlan:
    """레포 구조를 보고 설치·실행 명령을 결정론적으로 추정한다.

    2026-08-03 SWE-agent 시행착오: pyproject.toml 의 [project.scripts] 를 읽으면
    `<entry> --help` 가 신뢰할 만한 범용 스모크 테스트가 된다 — 실제로 이
    방식으로 격리 실행 성공을 확인했다.
    """
    pyproject = repo_dir / "pyproject.toml"
    setup_py = repo_dir / "setup.py"
    requirements = repo_dir / "requirements.txt"

    entry_point: str | None = None
    declared_name: str | None = None

    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        scripts = project.get("scripts", {})
        if scripts:
            entry_point = next(iter(scripts))
        declared_name = project.get("name")
        install_cmd = "pip install --no-cache-dir -e ."
        installer = "pip-editable"
    elif setup_py.exists():
        install_cmd = "pip install --no-cache-dir -e ."
        installer = "pip-editable"
    elif requirements.exists():
        install_cmd = "pip install --no-cache-dir -r requirements.txt"
        installer = "pip-requirements"
    else:
        install_cmd = ""
        installer = "none"

    package_name = _find_real_package_name(repo_dir, declared_name)

    if entry_point:
        run_cmd = f"{entry_point} --help"
    elif package_name:
        run_cmd = f'python -c "import {package_name}"'
    else:
        run_cmd = "python -c \"print('install only — 임포트 대상 추정 실패')\""

    return InstallPlan(
        installer=installer, install_cmd=install_cmd, run_cmd=run_cmd,
        entry_point=entry_point, package_name=package_name,
    )


def _write_dockerfile(repo_dir: Path, install_cmd: str) -> Path:
    # 2026-08-03 실측(granite-tsfm): clone 할 때마다 .git 내부 팩파일이 미세하게
    # 달라져 COPY 레이어의 내용 해시가 매번 바뀌고, 이미 한 번 성공한 무거운
    # pip install RUN 레이어까지 캐시가 매번 깨져 매 시도마다 처음부터 다시
    # 설치했다. 실제 코드와 무관한 .git 은 빌드 컨텍스트에서 아예 뺀다.
    (repo_dir.parent / ".dockerignore").write_text("*/.git\n", encoding="utf-8")

    dockerfile = repo_dir.parent / f"Dockerfile.{repo_dir.name}"
    lines = [
        f"FROM {DEFAULT_BASE_IMAGE}",
        # 설계 결론 1: git 을 기본 포함한다.
        "RUN apt-get update && apt-get install -y --no-install-recommends git "
        "&& rm -rf /var/lib/apt/lists/*",
        "WORKDIR /repo",
        f"COPY {repo_dir.name} /repo",
    ]
    if install_cmd:
        lines.append(f"RUN {install_cmd}")
    dockerfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dockerfile


def _build_image(repo_dir: Path, dockerfile: Path, tag: str,
                  timeout: int = INSTALL_TIMEOUT) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["docker", "build", "-t", tag, "-f", str(dockerfile), "."],
            cwd=repo_dir.parent, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return False, f"설치 타임아웃({timeout}s 초과): {e}"
    ok = proc.returncode == 0
    return ok, (proc.stdout[-4000:] + proc.stderr[-4000:])


# 실행 컨테이너 격리 플래그(M7, 2026-08-28). ⑦은 검증 안 된 남의 코드를
# 자동 실행하므로 악성 setup.py·마이닝·호스트 접근이 현실적 위협이다.
# 단계적으로 넣으면서 매 단계 기존 성공 사례(LF-YOLO)로 회귀를 돌려 확인했다.
_SECURITY_FLAGS = [
    "--cap-drop", "ALL",                    # 모든 리눅스 capability 제거
    "--security-opt", "no-new-privileges",  # setuid 로 권한 상승 차단
    "--pids-limit", "256",                  # fork 폭탄 차단
    "--read-only",                          # 루트 파일시스템 쓰기 금지
    "--tmpfs", "/tmp:rw,size=512m,exec",    # 쓰기가 필요한 곳은 여기로 한정
    "--user", "65534:65534",                # nobody — root 로 안 돌린다
]
# read-only + 비root 조합에서 파이썬이 쓰기를 시도하는 지점들을 tmpfs 로 돌린다.
# 이게 없으면 __pycache__·~/.cache 쓰기 실패로 정상 repo 도 깨진다.
_SECURITY_ENV = [
    "-e", "PYTHONDONTWRITEBYTECODE=1",
    "-e", "HOME=/tmp",
    "-e", "TMPDIR=/tmp",
    "-e", "XDG_CACHE_HOME=/tmp/.cache",
]


def _run_container(tag: str, run_cmd: str, timeout: int, network: bool,
                    mem_limit: str = MEM_LIMIT, cpus: str = CPUS,
                    security_flags: list[str] | None = None) -> RunResult:
    """설계 결론 4: `docker run` 을 셸 timeout 으로 감싸지 않는다. 컨테이너를
    이름으로 띄워두고(-d), `docker wait` 자체에 타임아웃을 걸어 넘으면
    컨테이너 이름을 직접 지정해 `docker stop` 한다. 클라이언트 프로세스가
    죽어도 컨테이너는 살아남는다는 것을 TSPulse 시행착오로 실측했기 때문이다.

    security_flags 를 명시하면 그걸 쓰고, 안 주면 _SECURITY_FLAGS 를 쓴다 —
    회귀 실험에서 플래그를 하나씩 켜보려고 뚫어둔 구멍이지 평상시엔 기본값을 쓴다.
    """
    name = f"repro-{uuid.uuid4().hex[:8]}"
    flags = _SECURITY_FLAGS if security_flags is None else security_flags
    start_cmd = [
        "docker", "run", "-d", "--name", name,
        "--memory", mem_limit, "--memory-swap", mem_limit, "--cpus", cpus,
        *flags, *_SECURITY_ENV,
    ]
    if not network:
        start_cmd += ["--network", "none"]
    start_cmd += [tag, "sh", "-c", run_cmd]

    start = time.monotonic()
    try:
        subprocess.run(start_cmd, check=True, capture_output=True, text=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
        return RunResult(False, None, "", stderr, False, network, 0.0)

    timed_out = False
    exit_code: int | None = None
    try:
        wait = subprocess.run(
            ["docker", "wait", name], capture_output=True, text=True, timeout=timeout,
        )
        exit_code = int(wait.stdout.strip())
    except subprocess.TimeoutExpired:
        timed_out = True
        # 컨테이너 이름을 직접 지정해서 stop — 여기가 설계 결론 4의 핵심.
        subprocess.run(["docker", "stop", "-t", "5", name], capture_output=True, text=True)

    duration = time.monotonic() - start
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)

    success = (exit_code == 0) and not timed_out
    return RunResult(success, exit_code, logs.stdout[-4000:], logs.stderr[-4000:],
                      timed_out, network, duration)


def run_repo_in_docker(repo_dir: Path, run_cmd: str | None = None) -> dict:
    """clone 된 저장소 하나를 빌드+실행한다. 후보 랭킹이나 여러 저장소 시도는
    reproduce() 가 담당 — 이 함수는 저장소 하나에 대한 결정론적 실행만 한다.
    """
    plan = detect_install_plan(repo_dir)
    cmd = run_cmd or plan.run_cmd
    tag = f"repro-{repo_dir.name.lower()}"[:60]

    # 2026-08-03 실측(TSPulse의 HuggingFace 후보): 설치할 것도 임포트할 대상도
    # 못 찾으면 스모크 테스트가 아무것도 검증하지 않는 공허한 명령(placeholder
    # print)만 돈다. exit 0 이 나온다고 "성공"으로 세면 검증기와 같은 방식의
    # 거짓 통과가 된다 — 프로젝트가 지키는 Goodhart 가드 원칙을 여기서도 지켜야
    # 하므로, 아예 시도하지 않고 명시적으로 "판정 불가"로 남긴다.
    if run_cmd is None and not plan.entry_point and not plan.package_name:
        return {"success": False, "stage": "no_target", "plan": asdict(plan),
                "fail_detail": "no_install_target",
                "log": "설치 대상(pyproject/setup.py/requirements.txt)도 임포트 가능한 "
                       "패키지도 못 찾음 — 이 저장소는 설치+실행 스모크 테스트를 구성할 "
                       "수 없다(코드가 아니라 가중치·문서 전용 저장소일 수 있음).",
                "attempts": []}

    dockerfile = _write_dockerfile(repo_dir, plan.install_cmd)
    try:
        build_ok, build_log = _build_image(repo_dir, dockerfile, tag)
        if not build_ok:
            return {"success": False, "stage": "build", "plan": asdict(plan),
                    "fail_detail": "build_failed",
                    "log": build_log, "attempts": []}

        # 실행 단계는 네트워크 없음으로 **고정**한다(M7, 2026-08-28).
        #
        # 예전엔 "출력에 네트워크 에러 문자열이 보이면 네트워크를 열고 1회
        # 재시도"였다. 그 게이트의 입력(컨테이너 stdout/stderr)은 **임의 코드가
        # 실행된 뒤 그 코드가 만들어내는 값**이라 공격자가 마음대로 조작할 수
        # 있었다 — 악성 repo 는 그 문자열을 출력하기만 하면 네트워크 실행을
        # 얻어냈다. "1회로 제한"은 공격자에게 제약이 아니라 정직한 repo 에만
        # 제약이었다.
        #
        # 제거해도 잃는 게 없다는 것을 데이터로 확인했다: repro_results 13건
        # 전부 network_used=0 이고, 성공한 2건(SWE-agent·LF-YOLO)도 네트워크
        # 없이 성공했다 — 이 재시도 경로는 한 번도 발동한 적이 없다. 구조적
        # 이유도 명확하다: 의존성 설치는 `docker build` 에서 끝나고 build 는
        # 원래 네트워크가 열려 있어서, 실행 단계에서 네트워크가 필요할 일이
        # 애초에 드물다.
        #
        # 실행 단계 네트워크가 정말 필요한 repo 가 나타나면 그때 egress
        # allowlist(프록시로 pypi·github·huggingface 만 허용)를 붙인다 —
        # "1회 무제한 인터넷"과 "완전 차단" 사이의 실제 중간항이다. §8 참고.
        result = _run_container(tag, cmd, RUN_TIMEOUT, network=False)

        # _NETWORK_ERROR_RE 는 남긴다 — 다만 **판정이 아니라 주석 전용**이다.
        # "네트워크가 필요했을 실패"가 얼마나 나오는지 세어야 위 egress
        # allowlist 판단의 근거가 쌓인다. 이 값으로 아무것도 허가하지 않으므로
        # 컨테이너가 조작해도 얻는 게 없다(통계만 부풀 뿐).
        network_suspected = bool(
            not result.success and not result.timed_out
            and _NETWORK_ERROR_RE.search(result.stdout + result.stderr)
        )

        return {
            "success": result.success, "stage": "run", "plan": asdict(plan),
            "network_suspected": network_suspected,
            "fail_detail": _run_fail_detail(result.success, result.timed_out,
                                            network_suspected),
            "attempts": [asdict(result)],
        }
    finally:
        dockerfile.unlink(missing_ok=True)
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, text=True)


_CLONABLE_HOSTS = ("https://github.com/", "https://gitlab.com/",
                    "https://bitbucket.org/", "https://huggingface.co/")


def _rank_candidates(found: dict) -> list[dict]:
    """저자가 직접 언급한 링크(in_text, 알려진 호스트)를 최우선으로, 그 다음
    GitHub 검색 결과를 별점 내림차순으로 둔다 — code_finder.py 의 신뢰도
    라벨을 그대로 신뢰 순서로 쓴다.

    project_page(알려진 호스트가 아닌 저자 링크)는 애초에 clone 불가능하므로
    여기서 제외한다 — 최대 3회뿐인 시도를 clone 실패가 확실한 후보에 쓰지 않는다.
    """
    in_text = [c for c in found.get("in_text", []) if c["confidence"] == "author-stated"]
    gh = sorted(found.get("github_search", []), key=lambda c: c.get("stars") or 0, reverse=True)
    ordered = in_text + gh
    return [c for c in ordered if c["url"].startswith(_CLONABLE_HOSTS)]


# git 이 "그런 저장소 없다"를 말하는 방식들. 비공개 저장소도 GitHub 은 404 로
# 답하면서 인증을 요구하므로 같은 부류로 묶는다 — 둘 다 "우리가 볼 수 있는
# 저자 코드가 없다"는 같은 사실이다.
_REPO_MISSING_RE = re.compile(
    r"not found|repository not found|could not read username|"
    r"authentication failed|does not appear to be a git repository",
    re.IGNORECASE,
)


def _run_fail_detail(success: bool, timed_out: bool, network_suspected: bool) -> str:
    """실행 단계 실패를 한 단계 더 나눈 코드.

    새 판정을 만들지 않는다 — 이미 있는 세 신호(exit code 기반 success,
    docker wait 타임아웃, 네트워크 오류 문자열)에서 그대로 파생한다.
    타임아웃을 네트워크 의심보다 먼저 보는 이유: 시간이 다 되어 죽은 실행의
    출력에도 네트워크 오류가 섞여 있을 수 있는데, 그때 원인은 타임아웃이다.

    이 값이 있어야 다이제스트가 "네트워크 차단 때문일 수 있다"를 매 건 말할
    수 있고, 그 말을 하는 순간 §8-16(egress allowlist 가 필요한가)의 근거가
    저절로 쌓인다 — 따로 통계를 모을 필요가 없다.
    """
    if success:
        return ""
    if timed_out:
        return "run_timeout"
    if network_suspected:
        return "run_network_suspected"
    return "run_nonzero_exit"


def _clone(url: str, dest_parent: Path) -> tuple[Path | None, str]:
    """returns (클론된 경로 | None, 실패 사유 코드). 성공이면 사유는 빈 문자열.

    사유를 같이 돌려주는 이유(2026-09-01): 실패를 전부 하나로 뭉개면
    다이제스트가 "저자가 코드를 안 올렸다(404)"와 "저자 코드가 안 돈다"를
    같은 `[재현 ✗]` 로 보고한다. 실측(2608.25176)에서 1순위 후보가 저자가
    논문에 적은 저장소였는데 404 였고, 그 사실이 메일에서 사라졌다.

    분류에 쓰는 문자열은 **git 자신의 stderr** 다 — 컨테이너에서 실행된 임의
    코드의 출력이 아니다(M7 에서 폐기한 "컨테이너 출력으로 재시도를 결정하던"
    게이트와 성격이 다르다). 게다가 이 값은 아무것도 허가하지 않는 주석
    전용이라, 조작돼도 얻을 게 없다.
    """
    if not url.startswith(_CLONABLE_HOSTS):
        return None, "unsupported_host"
    name = re.sub(r"[^\w.-]", "_", url.rstrip("/").rsplit("/", 1)[-1]) or uuid.uuid4().hex[:8]
    dest = dest_parent / f"{name}-{uuid.uuid4().hex[:6]}"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=120, check=True,
        )
    except subprocess.TimeoutExpired:
        return None, "clone_timeout"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if _REPO_MISSING_RE.search(stderr):
            return None, "repo_not_found"
        return None, "clone_failed"
    return dest, ""


def reproduce(arxiv_id: str, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """⑦ 의 유일한 자율 재시도 루프. 후보 저장소를 신뢰도 순으로 최대
    max_attempts 개 시도해서 하나라도 설치+실행에 성공하면 멈춘다.

    성공 판정은 exit code 뿐이다 — 논문 수치 재현이 아니다. 각 시도는
    server.save_repro_result() 로 그대로 축적된다(⑧, 성공이든 실패든).
    """
    arxiv_id = server._clean_arxiv_id(arxiv_id)
    found = code_finder.find_repo_candidates(arxiv_id)
    ordered = _rank_candidates(found)

    if not ordered:
        return {"arxiv_id": arxiv_id, "success": False, "reason": "저장소 후보 없음", "log": []}

    workdir = server.REPRO_DIR / arxiv_id.replace("/", "_")
    workdir.mkdir(parents=True, exist_ok=True)

    attempts_log = []
    for i, cand in enumerate(ordered[:max_attempts], start=1):
        repo_dir, clone_detail = _clone(cand["url"], workdir)
        if repo_dir is None:
            attempts_log.append({"candidate": cand, "success": False, "stage": "clone",
                                 "fail_detail": clone_detail})
            server.save_repro_result(
                arxiv_id, cand["url"], cand["source"], cand["confidence"],
                False, None, "clone", i, False, 0.0, "",
                fail_detail=clone_detail,
            )
            continue

        outcome = run_repo_in_docker(repo_dir)
        attempts_log.append({"candidate": cand, **outcome})

        # 성공한 clone만 남긴다 — "설치+실행 성공 여부만 본다"는 원칙은 그대로
        # 지키되(코드 내용을 판단하지 않는다), 성공했을 때 그 코드를 review_app.py
        # 에서 실제로 열어볼 수 있어야 한다는 지적을 받아들였다(2026-08-12).
        # 실패한 시도는 그대로 지운다 — 실패 이유는 stage/exit_code로 이미
        # 충분히 남고, 코드까지 보관할 필요는 없다(디스크 낭비).
        local_path = ""
        if outcome["success"]:
            persist_dir = server.REPRO_DIR / "code" / arxiv_id.replace("/", "_")
            shutil.rmtree(persist_dir, ignore_errors=True)  # 이전 성공 잔재가 있으면 교체
            persist_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(repo_dir), str(persist_dir))
            local_path = str(persist_dir)
        else:
            shutil.rmtree(repo_dir, ignore_errors=True)

        last_attempt = outcome["attempts"][-1] if outcome["attempts"] else None
        server.save_repro_result(
            arxiv_id, cand["url"], cand["source"], cand["confidence"],
            outcome["success"], (last_attempt or {}).get("exit_code"),
            outcome["stage"], i, (last_attempt or {}).get("network_enabled", False),
            (last_attempt or {}).get("duration_s", 0.0), "", local_path,
            fail_detail=outcome.get("fail_detail", ""),
        )

        if outcome["success"]:
            return {"arxiv_id": arxiv_id, "success": True, "attempt": i,
                    "local_path": local_path, "log": attempts_log}

    return {"arxiv_id": arxiv_id, "success": False, "attempt": len(attempts_log), "log": attempts_log}


def launch_background(arxiv_id: str) -> str:
    """④⑤가 끝나는 즉시 ⑦을 별도 프로세스로 무인 실행한다 — 사람의 승인
    클릭을 기다리지 않는다.

    2026-08-24: 원래 이 트리거는 review_app.py의 "✅ 승인" 버튼 클릭
    안에 있었다("⑥→⑦ 연결" 주석 참고). "코드 재현까지 다 끝난 상태로
    자동으로 이메일을 보내야 하는데, 그 사이에 승인 버튼을 누가 언제
    누르냐"는 지적을 받아 하네스 전체에서 승인/반려 게이트를 없앴다 —
    이제 요약이 저장되면(④⑤ 완료) 예외 없이 곧바로 ⑦로 넘어간다("없으면
    없는대로, 있으면 코드재현까지 다 한 상태로"). 이 함수를 docker_runner.py
    (⑦을 소유한 모듈)로 옮긴 이유: review_app.py(수동 검색·PDF 업로드
    흐름)와 batch_summarize.py(자동 delta 스캔 흐름) 둘 다 트리거해야
    하는데, "언제·어떻게 실행할지"가 두 곳에 따로 있으면 한쪽만 고치고
    다른 쪽을 놓치는 사고가 나기 쉽다 — 한 곳에만 있어야 한다.

    reproduce()는 Docker clone+install+run을 최대 3회 재시도하는 무거운
    작업이라(최악의 경우 후보당 install 15분+run 2분) 호출부를 막지 않고
    별도 프로세스로 띄운다 — batch_summarize.py와 같은 "실행은 트리거하지만
    그다음은 무인으로 돈다" 패턴. 이미 성공 기록이 있으면 다시 안 돌리고,
    이미 실행 중이면(마커 파일) 중복 실행하지 않는다.
    """
    with server._db() as con:
        rows = con.execute(
            "SELECT success FROM repro_results WHERE arxiv_id=?", (arxiv_id,)
        ).fetchall()
    if any(r["success"] for r in rows):
        return "이미 성공 기록이 있어 재실행하지 않음"

    server.REPRO_DIR.mkdir(parents=True, exist_ok=True)
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    if marker.exists():
        return "이미 실행 중"
    marker.write_text(server._now(), encoding="utf-8")
    log_path = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.log"

    # docker_runner.py 자체의 __main__은 마커 파일을 모르므로, 마커 정리까지
    # 포함한 짧은 래퍼를 셸로 실행한다 — 이 파일의 다른 코드는 안 건드림.
    wrapper = f'"{sys.executable}" docker_runner.py "{arxiv_id}"; rm -f "{marker}"'
    with open(log_path, "w", encoding="utf-8") as f:
        subprocess.Popen(
            ["/bin/bash", "-c", wrapper],
            cwd=str(Path(__file__).resolve().parent),
            stdout=f, stderr=subprocess.STDOUT,
            start_new_session=True,  # 호출한 프로세스(streamlit/batch_summarize.py)가 끝나도 안 죽게
        )
    return "코드 재현을 백그라운드에서 시작함"


if __name__ == "__main__":
    import json

    for aid in sys.argv[1:]:
        print(json.dumps(reproduce(aid), ensure_ascii=False, indent=2, default=str))
