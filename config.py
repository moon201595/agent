"""공용 설정 로더 — `.env` 를 읽는 곳은 여기 하나다(2026-09-17, Codex 구조 검토: summarize_engine·server·hybrid_search 가 같은 파서를 셋 갖고 있었다).

규칙 5: 값은 어디에도 출력하지 않는다. 호출부는 `config.ENV.get("이름")` 으로 이름만 참조한다. 이미 설정된 OS 환경변수가 `.env` 보다 우선한다
(`setdefault`). `server.py` 는 MCP 서버라 `claude` 가 별도 프로세스로 띄워 터미널 환경을 못 물려받으므로 `os.environ` 에도 채운다(`apply_to_os`).
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """OS 환경 + `.env`(없는 키만). 파일이 없으면 OS 환경만."""
    env = dict(os.environ)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def apply_to_os(env: dict[str, str] | None = None) -> None:
    """`.env` 값을 os.environ 에 채운다(있는 것은 안 덮는다) — 자식 프로세스·표준 라이브러리가 읽는 값용."""
    for k, v in (env or ENV).items():
        os.environ.setdefault(k, v)


ENV: dict[str, str] = load_env()
