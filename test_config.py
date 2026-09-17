"""config.load_env — `.env` 파서가 하나뿐임을 지키는 테스트(2026-09-17).

무엇을 망가뜨리면 실패하는가: (1) `.env` 값이 OS 환경을 덮으면 (2) 주석·빈 줄·`=` 없는 줄을 키로 읽으면
(3) summarize_engine.ENV·hybrid_search.ENV 가 config.ENV 와 다른 객체가 되면 — 테스트의 `monkeypatch.setitem(engine.ENV, …)` 가
다른 모듈에 안 보이게 된다. 값은 출력하지 않는다(규칙 5) — 가짜 파일만 쓴다.
"""
from __future__ import annotations

import config
import hybrid_search
import summarize_engine


def test_load_env_reads_file_without_overriding_os(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# 주석\n\nFOO_FROM_FILE = a\nBAR=b=c\nNOEQUALS\nOS_WINS=file\n", encoding="utf-8")
    monkeypatch.setenv("OS_WINS", "os")
    env = config.load_env(env_file)
    assert env["FOO_FROM_FILE"] == "a"
    assert env["BAR"] == "b=c"            # 첫 `=` 에서만 가른다
    assert "NOEQUALS" not in env
    assert "# 주석" not in env
    assert env["OS_WINS"] == "os"


def test_load_env_missing_file_is_os_only(tmp_path):
    env = config.load_env(tmp_path / "absent.env")
    assert env.keys() == dict(__import__("os").environ).keys()


def test_engine_and_hybrid_share_the_config_dict():
    assert summarize_engine.ENV is config.ENV
    assert hybrid_search.ENV is config.ENV


def test_apply_to_os_does_not_override(monkeypatch):
    monkeypatch.setenv("PH_TEST_APPLY", "keep")
    config.apply_to_os({"PH_TEST_APPLY": "new", "PH_TEST_APPLY_2": "set"})
    import os
    assert os.environ["PH_TEST_APPLY"] == "keep"
    assert os.environ["PH_TEST_APPLY_2"] == "set"
    monkeypatch.delenv("PH_TEST_APPLY_2")
