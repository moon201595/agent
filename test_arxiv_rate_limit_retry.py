"""429 재시도 예산 — 네트워크 없이 돈다 (2026-09-02).

실측: 기본 예산(2회 × 1·2초 ≈ 3초)으로는 arXiv 429 를 못 넘겨 **스캔 전체가
검색 단계에서 죽었다**. 그날 아침 실행에서 이미 `arxiv 12(429:3)` 이 찍혔는데
재시도로 흡수돼 문제로 안 보였을 뿐이다.

예산을 정할 때 실패 비용을 봐야 한다는 건 Gemini 503 에서 이미 배운 것이고
(§5), 여기서 실패하면 그날 다이제스트가 통째로 없다.
"""

import asyncio

import httpx
import pytest

import server


def _resp(status, headers=None):
    req = httpx.Request("GET", "http://api.example/q")
    return httpx.Response(status, request=req, headers=headers or {})


def _err(status, headers=None):
    r = _resp(status, headers)
    return httpx.HTTPStatusError(str(status), request=r.request, response=r)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fake(_s):
        return None
    monkeypatch.setattr(server.asyncio, "sleep", fake)


def _run(fn):
    return asyncio.run(server._with_retry(fn, "테스트 API"))


def test_429_gets_its_own_budget_separate_from_other_errors():
    """500 은 몇 초 뒤 풀리지만 429 는 상대가 '너무 자주 왔다'고 말하는
    것이라 성격이 다르다. 한 카운터로 묶으면 한쪽이 다른 쪽 예산을 먹는다."""
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) <= 4:          # 기본 예산(2회)으로는 못 넘긴다
            raise _err(429)
        return _resp(200)

    assert _run(flaky).status_code == 200
    assert len(calls) == 5


def test_429_budget_is_bounded():
    """무한 재시도가 아니다 — 상한을 넘으면 올려서 호출부가 보고하게 한다."""
    async def always():
        raise _err(429)

    with pytest.raises(httpx.HTTPStatusError):
        _run(always)


def test_retry_after_header_is_honoured():
    """상대가 말해준 시간을 무시하고 우리 계산으로 다시 두드리면 차단이
    길어진다(CLAUDE.md 2)."""
    assert server._rate_limit_wait(_err(429, {"Retry-After": "45"}), 0) == 45.0


def test_retry_after_is_capped():
    """서버가 한 시간을 요구해도 새벽 배치를 그만큼 붙잡아둘 수는 없다."""
    wait = server._rate_limit_wait(_err(429, {"Retry-After": "3600"}), 0)
    assert wait == server.RATE_LIMIT_BACKOFF_MAX


def test_malformed_retry_after_falls_back_to_backoff():
    wait = server._rate_limit_wait(_err(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), 0)
    assert wait == server.RATE_LIMIT_BACKOFF


def test_backoff_grows_and_is_capped():
    waits = [server._rate_limit_wait(_err(429), i) for i in range(server.RATE_LIMIT_RETRIES)]
    assert waits == sorted(waits)
    assert all(w <= server.RATE_LIMIT_BACKOFF_MAX for w in waits)


def test_budget_is_generous_enough_to_outlast_a_rate_limit():
    """3초로는 못 넘겼다는 게 실측이다. 누가 다시 줄이면 이 테스트가 막는다."""
    total = sum(server._rate_limit_wait(_err(429), i) for i in range(server.RATE_LIMIT_RETRIES))
    assert total >= 300          # 최소 5분은 버틴다


def test_budget_does_not_threaten_the_deep_layer_budget():
    """검색에서 40분을 다 쓰면 요약할 시간이 없다(§8-14)."""
    import run_profile_scan as rps
    total = sum(server._rate_limit_wait(_err(429), i) for i in range(server.RATE_LIMIT_RETRIES))
    assert total < rps.DEEP_LAYER_BUDGET_SECONDS / 4


def test_non_retryable_status_still_raises_immediately():
    calls = []

    async def bad_request():
        calls.append(1)
        raise _err(400)

    with pytest.raises(httpx.HTTPStatusError):
        _run(bad_request)
    assert len(calls) == 1


def test_timeout_still_uses_the_short_budget():
    """타임아웃은 429 예산을 쓰면 안 된다 — 몇 분씩 기다릴 종류가 아니다."""
    calls = []

    async def always_timeout():
        calls.append(1)
        raise httpx.TimeoutException("timeout")

    with pytest.raises(httpx.TimeoutException):
        _run(always_timeout)
    assert len(calls) == server.MAX_RETRIES + 1
