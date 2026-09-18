"""⑨ 동향 서술 — 실제 CLI·네트워크 없이 폴백과 격리 경계를 검증한다."""
import asyncio
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import narrative_engine as ne


@pytest.fixture
def engines(monkeypatch):
    """실제 실행 대신 경계의 가짜를 써서 운영 실행 함수까지 검사한다."""
    state = SimpleNamespace(cli="정상 서술", gemini="Gemini 서술", groq="Groq 서술",
                            calls=[], retries=[], processes=[], killed=[], communicates=[], which=[])

    def which(name, **kwargs):
        state.which.append(name)
        return "/fake/codex"

    class Process:
        pid = 987654
        returncode = 0

        def __init__(self, argv, **kwargs):
            state.calls.append("codex")
            state.processes.append((argv, kwargs))
            self.last = Path(argv[argv.index("-o") + 1])
            self.timed_out = False
            if state.cli == "spawn_error":
                raise OSError("private error detail")

        def communicate(self, text=None, timeout=None):
            state.communicates.append((text, timeout))
            if state.cli == "timeout" and not self.timed_out:
                self.timed_out = True
                raise subprocess.TimeoutExpired("codex", timeout)
            if state.cli == "exit":
                self.returncode = 1
            if state.cli != "missing_file":
                self.last.write_text(state.cli, encoding="utf-8")
            return "CLI 진행 로그", "private error detail"

    async def gemini(client, prompt):
        state.calls.append("gemini")
        state.api_args = (client, prompt)
        if isinstance(state.gemini, Exception):
            raise state.gemini
        return state.gemini

    async def groq(client, prompt):
        state.calls.append("groq")
        state.api_args = (client, prompt)
        if isinstance(state.groq, Exception):
            raise state.groq
        return state.groq

    async def retry(factory, label):
        state.retries.append(label)
        return await factory()

    async def to_thread(func, *args):
        # 이 실행 환경에서 스레드 종료가 멈춰 실행 경계만 가짜로 둔다. _codex·_run 은 실제 함수를 부른다.
        state.offload = (func, args)
        return func(*args)

    monkeypatch.setattr(ne.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(ne.shutil, "which", which)
    monkeypatch.setattr(ne.am.subprocess, "Popen", Process)
    monkeypatch.setattr(ne.am.os, "killpg", lambda pid, sig: state.killed.append((pid, sig)))
    monkeypatch.setattr(ne.se, "_post_gemini", gemini)
    monkeypatch.setattr(ne.se, "_post_groq", groq)
    monkeypatch.setattr(ne.se, "_call_with_rate_limit_retry", retry)
    return state


def test_codex_success_skips_apis(engines, capsys):
    """Codex 성공 뒤 API를 호출하거나 stdout 진행 로그를 글로 반환하면 실패한다."""
    engines.cli = "가" * 1234
    assert asyncio.run(ne.generate(None, "입력")) == (engines.cli, "codex")
    assert engines.calls == ["codex"]
    assert engines.retries == []
    assert capsys.readouterr().out == "  [동향] 서술 엔진 codex (1,234자)\n"


def test_codex_isolation_and_stdin(engines, monkeypatch):
    """격리 옵션·최소 환경·stdin·타임아웃 전달이나 임시 cwd 정리를 없애면 실패한다."""
    monkeypatch.setenv("NARRATIVE_TEST_PRIVATE", "synthetic-private-value")
    assert asyncio.run(ne.generate(None, "프롬프트", timeout=17))
    argv, opts = engines.processes[0]
    assert argv[:2] == ["/fake/codex", "exec"]
    for flag in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert {argv[i + 1] for i, arg in enumerate(argv) if arg == "--disable"} == {
        "shell_tool", "apps", "browser_use", "computer_use", "in_app_browser", "image_generation", "tool_suggest"}
    assert {argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"} == {
        "mcp_servers={}", 'web_search="disabled"'}
    assert "--output-schema" not in argv
    assert argv[-1] == "-" and "프롬프트" not in argv
    assert engines.communicates == [("프롬프트", 17)]
    assert engines.offload == (ne._codex, ("프롬프트", 17))
    assert opts["start_new_session"] is True and opts["text"] is True
    assert all(opts[key] == subprocess.PIPE for key in ("stdin", "stdout", "stderr"))
    assert set(opts["env"]) <= {"HOME", "PATH", "LANG", "LC_ALL", "USER", "CODEX_HOME", "XDG_CONFIG_HOME"}
    assert "PATH" in opts["env"]
    assert Path(argv[argv.index("-o") + 1]).parent == Path(opts["cwd"])
    assert Path(opts["cwd"]) != Path.cwd()
    assert not Path(opts["cwd"]).exists()


@pytest.mark.parametrize("failure", ["timeout", "exit", "", " \n ", "missing_file", "spawn_error"])
@pytest.mark.parametrize("gemini_fails", [False, True])
def test_codex_failure_falls_back(engines, failure, gemini_fails, capsys):
    """CLI 실패를 전파하거나 Gemini 실패 뒤 Groq를 생략하거나 종료 그룹을 바꾸면 실패한다."""
    engines.cli = failure
    if gemini_fails:
        engines.gemini = RuntimeError("private error detail")
    expected = ("Groq 서술", "groq") if gemini_fails else ("Gemini 서술", "gemini")
    assert asyncio.run(ne.generate(None, "입력", timeout=9)) == expected
    assert engines.calls == ["codex", "gemini"] + (["groq"] if gemini_fails else [])
    assert engines.killed == ([(987654, signal.SIGKILL)] if failure == "timeout" else [])
    if failure == "timeout":
        assert engines.communicates == [("입력", 9), (None, None)]
    log = capsys.readouterr().out
    assert "codex 실패 → 다음 엔진" in log
    assert "private error detail" not in log
    if failure == "timeout":
        assert "(TimeoutExpired)" in log


@pytest.mark.parametrize("failure", [RuntimeError("private error detail"), "", "  ", None])
def test_all_fail_returns_none(engines, failure):
    """모든 엔진의 예외·빈 결과를 None으로 흡수하지 못하면 실패한다."""
    engines.cli = "exit"
    engines.gemini = engines.groq = failure
    assert asyncio.run(ne.generate(None, "입력")) is None
    assert engines.calls == ["codex", "gemini", "groq"]


def test_missing_codex_skips_quietly(engines, monkeypatch, capsys):
    """CLI 미설치 때 프로세스를 띄우거나 예외·실패 로그를 내거나 폴백을 누락하면 실패한다."""
    monkeypatch.setattr(ne.shutil, "which", lambda *args, **kwargs: None)
    assert asyncio.run(ne.generate(None, "입력")) == ("Gemini 서술", "gemini")
    assert engines.calls == ["gemini"]
    assert "codex" not in capsys.readouterr().out


@pytest.mark.parametrize("engine", ["codex", "gemini", "groq"])
def test_secret_output_discarded(engines, engine, capsys):
    """어느 엔진이든 시크릿 모양 출력을 반환·로그에 노출하거나 폴백을 생략하면 실패한다."""
    secret = "api_key=synthetic-not-a-real-key"
    setattr(engines, "cli" if engine == "codex" else engine, secret)
    fallback = "gemini" if engine == "codex" else "codex"
    result = asyncio.run(ne.generate(None, "입력", order=(engine, fallback)))
    assert result == (("Gemini 서술", "gemini") if fallback == "gemini" else ("정상 서술", "codex"))
    assert engines.calls == [engine, fallback]
    log = capsys.readouterr().out
    assert secret not in log
    assert "secret_like_output" in log


@pytest.mark.parametrize("engine", ["codex", "gemini", "groq"])
def test_order_and_engine_identity(engines, engine):
    """지정 순서·실제 성공 엔진 이름·API 인자·기존 재시도 함수 연결을 바꾸면 실패한다."""
    client = object()
    result = asyncio.run(ne.generate(client, "입력", label="테스트 서술", order=(engine,)))
    assert result == ({"codex": "정상 서술", "gemini": "Gemini 서술", "groq": "Groq 서술"}[engine], engine)
    assert engines.calls == [engine]
    if engine != "codex":
        assert engines.api_args == (client, "입력")
        assert engines.retries == ["테스트 서술"]
        assert engines.which == []


def test_empty_order_disables_generation(engines):
    """빈 순서를 기본 순서로 바꿔 의도하지 않은 엔진을 호출하면 실패한다."""
    assert asyncio.run(ne.generate(None, "입력", order=())) is None
    assert engines.calls == []
