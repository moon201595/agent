"""api_usage.py — 계수기 자체의 회귀 (§8-15).

계측이 틀리면 "몇 번 호출했나"를 또 역산하게 된다. 특히 **재시도를 호출로
세는가**가 핵심이다 — 무료 티어 한도를 소모하는 것은 성공한 응답이 아니라
보낸 요청이기 때문이다.
"""

import threading

import api_usage


def setup_function():
    api_usage.reset()


def test_counts_each_attempt_including_retries():
    api_usage.record("gemini", "503")
    api_usage.record("gemini", "503")
    api_usage.record("gemini", "ok")
    assert api_usage.total() == 3
    assert api_usage.snapshot() == {"gemini": {"503": 2, "ok": 1}}


def test_summary_shows_failures_but_not_ok_noise():
    api_usage.record("gemini", "ok")
    api_usage.record("gemini", "429")
    api_usage.record("arxiv", "ok")
    s = api_usage.format_summary()
    assert "API 호출 3회" in s
    assert "gemini 2(429:1)" in s
    assert "arxiv 1" in s


def test_summary_is_empty_when_nothing_happened():
    assert api_usage.format_summary() == ""


def test_scope_measures_only_its_own_block():
    api_usage.record("arxiv", "ok")            # 스코프 밖
    with api_usage.Scope() as scope:
        api_usage.record("gemini", "ok")
        api_usage.record("gemini", "503")
    api_usage.record("arxiv", "ok")            # 스코프 밖
    assert scope.total() == 2
    assert scope.snapshot() == {"gemini": {"503": 1, "ok": 1}}


def test_scope_does_not_disturb_the_global_count():
    """논문 한 편을 재는 스코프가 실행 전체 집계를 망가뜨리면 안 된다."""
    outer = api_usage.Scope()
    outer.__enter__()
    with api_usage.Scope() as inner:
        api_usage.record("gemini", "ok")
    api_usage.record("groq", "ok")
    outer.__exit__(None, None, None)
    assert inner.total() == 1
    assert outer.total() == 2
    assert api_usage.total() == 2


def test_scope_snapshot_after_exception_still_reports():
    """실패한 논문도 호출은 이미 썼다 — 예외가 나도 그 비용이 보여야 한다."""
    scope = api_usage.Scope()
    try:
        with scope:
            api_usage.record("gemini", "429")
            raise RuntimeError("실패")
    except RuntimeError:
        pass
    assert scope.total() == 1


def test_thread_safe_under_concurrent_records():
    """code_finder 는 동기 코드고 스레드에서도 불린다."""
    def worker():
        for _ in range(200):
            api_usage.record("github", "ok")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert api_usage.total() == 1600
