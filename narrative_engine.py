"""⑨ 동향 서술 엔진 — 구독 CLI 우선, 무료 API 폴백. 판정하지 않고 글만 만든다."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import agent_maintenance as am
import summarize_engine as se


def _codex(prompt: str, timeout: int, schema: dict | None = None) -> str | None:
    """주간 관리와 같은 격리를 써서 논문 속 지시가 도구 실행으로 이어지지 않게 한다.
    schema 가 있으면 `--output-schema` 로 출력 모양을 고정한다(story-v2, 2026-10-01)."""
    exe = shutil.which("codex", path=am._cli_env()["PATH"])
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="narrative-codex-") as cwd:
        last = Path(cwd) / "last.txt"
        argv = [exe, "exec", "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--ignore-rules", "-s", "read-only"]
        for feature in ("shell_tool", "apps", "browser_use", "computer_use", "in_app_browser",
                        "image_generation", "tool_suggest"):
            argv += ["--disable", feature]
        argv += ["-c", "mcp_servers={}", "-c", 'web_search="disabled"']
        if schema is not None:
            schema_path = Path(cwd) / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv += ["--output-schema", str(schema_path)]
        argv += ["-o", str(last), "-"]
        # _run 이 최소 환경·stdin·시간 상한·프로세스 그룹 종료를 함께 보장한다.
        rc, _out, _err = am._run(argv, prompt, timeout, cwd)
        if rc != 0:
            raise am.StepError("codex_exit")
        return last.read_text(encoding="utf-8")


async def generate(client, prompt: str, *, label: str = "동향 서술",
                   timeout: int = 180, order: tuple[str, ...] | None = None,
                   schema: dict | None = None, accept=None) -> tuple[str, str] | None:
    """(글, 엔진이름) 또는 None. 서술 실패가 아침 메일을 막지 않도록 다음 엔진을 시도한다.

    accept 가 있으면 출력을 그 함수에 통과시킨 결과를 돌려준다. ValueError 면 그 엔진의 출력을 버리고 다음 엔진으로 —
    구조가 깨진 글을 "그래도 글"이라며 싣지 않는다(story-v2, 2026-10-01). schema 는 Codex 에만 넘긴다."""
    for engine in ("codex", "gemini", "groq") if order is None else order:
        try:
            if engine == "codex":
                # 동기 CLI 대기가 다른 비동기 배달 작업까지 멈추지 않게 한다.
                text = await (asyncio.to_thread(_codex, prompt, timeout, schema) if schema is not None
                              else asyncio.to_thread(_codex, prompt, timeout))
                if text is None:  # CLI 미설치는 조용히 건너뛴다.
                    continue
            elif engine == "gemini":
                text = await se._call_with_rate_limit_retry(lambda: se._post_gemini(client, prompt), label)
            elif engine == "groq":
                text = await se._call_with_rate_limit_retry(lambda: se._post_groq(client, prompt), label)
            else:
                continue
            if am.secret_like(text):
                raise am.StepError("secret_like_output")
            text = (text or "").strip()
            if not text:
                raise am.StepError("empty_output")
            if accept is not None:
                try:
                    text = accept(text)
                except ValueError as error:
                    raise am.StepError("invalid_output") from error
        except Exception as exc:
            # 예외 메시지·CLI 출력에는 시크릿이 섞일 수 있어 분류만 기록한다.
            reason = exc.code if isinstance(exc, am.StepError) else type(exc).__name__
            if reason == "timeout":
                reason = "TimeoutExpired"
            print(f"  [동향] {engine} 실패 → 다음 엔진 ({reason})")
            continue
        print(f"  [동향] 서술 엔진 {engine} ({len(text):,}자)")
        return text, engine
    return None
