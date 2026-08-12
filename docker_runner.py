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


def _run_container(tag: str, run_cmd: str, timeout: int, network: bool,
                    mem_limit: str = MEM_LIMIT, cpus: str = CPUS) -> RunResult:
    """설계 결론 4: `docker run` 을 셸 timeout 으로 감싸지 않는다. 컨테이너를
    이름으로 띄워두고(-d), `docker wait` 자체에 타임아웃을 걸어 넘으면
    컨테이너 이름을 직접 지정해 `docker stop` 한다. 클라이언트 프로세스가
    죽어도 컨테이너는 살아남는다는 것을 TSPulse 시행착오로 실측했기 때문이다.
    """
    name = f"repro-{uuid.uuid4().hex[:8]}"
    start_cmd = [
        "docker", "run", "-d", "--name", name,
        "--memory", mem_limit, "--memory-swap", mem_limit, "--cpus", cpus,
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
                "log": "설치 대상(pyproject/setup.py/requirements.txt)도 임포트 가능한 "
                       "패키지도 못 찾음 — 이 저장소는 설치+실행 스모크 테스트를 구성할 "
                       "수 없다(코드가 아니라 가중치·문서 전용 저장소일 수 있음).",
                "attempts": []}

    dockerfile = _write_dockerfile(repo_dir, plan.install_cmd)
    try:
        build_ok, build_log = _build_image(repo_dir, dockerfile, tag)
        if not build_ok:
            return {"success": False, "stage": "build", "plan": asdict(plan),
                    "log": build_log, "attempts": []}

        result = _run_container(tag, cmd, RUN_TIMEOUT, network=False)
        attempts = [result]

        # 설계 결론 3: 네트워크 관련 에러로 보이면 네트워크를 열고 1회만 재시도.
        combined = result.stdout + result.stderr
        if not result.success and not result.timed_out and _NETWORK_ERROR_RE.search(combined):
            result2 = _run_container(tag, cmd, RUN_TIMEOUT, network=True)
            attempts.append(result2)
            result = result2

        return {
            "success": result.success, "stage": "run", "plan": asdict(plan),
            "attempts": [asdict(r) for r in attempts],
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


def _clone(url: str, dest_parent: Path) -> Path | None:
    if not url.startswith(_CLONABLE_HOSTS):
        return None
    name = re.sub(r"[^\w.-]", "_", url.rstrip("/").rsplit("/", 1)[-1]) or uuid.uuid4().hex[:8]
    dest = dest_parent / f"{name}-{uuid.uuid4().hex[:6]}"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=120, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return dest


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
        repo_dir = _clone(cand["url"], workdir)
        if repo_dir is None:
            attempts_log.append({"candidate": cand, "success": False, "stage": "clone"})
            server.save_repro_result(
                arxiv_id, cand["url"], cand["source"], cand["confidence"],
                False, None, "clone", i, False, 0.0, "",
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
        )

        if outcome["success"]:
            return {"arxiv_id": arxiv_id, "success": True, "attempt": i,
                    "local_path": local_path, "log": attempts_log}

    return {"arxiv_id": arxiv_id, "success": False, "attempt": len(attempts_log), "log": attempts_log}


if __name__ == "__main__":
    import json
    import sys

    for aid in sys.argv[1:]:
        print(json.dumps(reproduce(aid), ensure_ascii=False, indent=2, default=str))
