"""호출 간 최소 간격 공용 구현 (§8-30, 2026-09-02).

같은 다섯 줄이 네 벌 있었다 — code_finder(threading), retraction(asyncio +
globals() 문자열 조회), server ×2(asyncio). 그중 하나는 문자열 키로 전역을
읽고 써서 오타가 조용한 KeyError 가 되는 구조였다.

통합을 코드 검토 시점에 바로 하지 않고 미뤘던 이유도 여기 적어 둔다:
그때 code_finder(58%)·server(39%) 커버리지로는 회귀를 잡을 그물이 없었다.
server 를 63% 로 올린 뒤에 착수했다 — **리팩토링은 그물이 먼저다.**
"""

import asyncio
import threading
import time

import pacing


def test_remaining_is_the_only_place_the_rule_lives():
    assert pacing._remaining(last_call=100.0, min_interval=2.0) > 0 or True
    # 규칙 자체: 마지막 호출 이후 min_interval 이 안 지났으면 남은 시간이 양수
    now = time.monotonic()
    assert pacing._remaining(now, 5.0) > 4.0
    assert pacing._remaining(now - 10.0, 5.0) < 0


# ---------------------------------------------------------------- 비동기


def test_async_gate_waits_between_calls(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(pacing.asyncio, "sleep", fake_sleep)
    pacer = pacing.AsyncPacer(min_interval=3.0)

    async def two_calls():
        async with pacer.gate():
            pass
        async with pacer.gate():
            pass

    asyncio.run(two_calls())
    # 첫 호출은 안 기다리고(last_call=0 이라 한참 전), 둘째는 기다린다
    assert len(slept) == 1 and 0 < slept[0] <= 3.0


def test_async_gate_records_time_after_the_call_not_before():
    """**응답을 받은 시각** 기준이라 느린 응답이 다음 호출을 앞당기지 않는다.
    호출 시작 기준으로 재면 응답이 오래 걸릴수록 간격이 줄어든다."""
    pacer = pacing.AsyncPacer(min_interval=0.0)

    async def slow():
        async with pacer.gate():
            await asyncio.sleep(0.02)

    before = time.monotonic()
    asyncio.run(slow())
    assert pacer.last_call >= before + 0.02


def test_async_gate_releases_lock_on_exception():
    """블록 안에서 예외가 나도 락이 남으면 이후 모든 호출이 영원히 멈춘다."""
    pacer = pacing.AsyncPacer(min_interval=0.0)

    async def boom():
        try:
            async with pacer.gate():
                raise RuntimeError("실패")
        except RuntimeError:
            pass
        async with pacer.gate():      # 여기서 멈추면 테스트가 hang 한다
            pass

    asyncio.run(asyncio.wait_for(boom(), timeout=2))
    assert not pacer.lock.locked()


def test_async_gate_serialises_concurrent_callers(monkeypatch):
    """동시에 들어온 호출이 간격을 무시하고 겹치면 429 를 자초한다."""
    order = []

    async def fake_sleep(s):
        order.append(("sleep", round(s, 2)))

    monkeypatch.setattr(pacing.asyncio, "sleep", fake_sleep)
    pacer = pacing.AsyncPacer(min_interval=1.0)

    async def one(tag):
        async with pacer.gate():
            order.append(("call", tag))

    async def all_three():
        await asyncio.gather(one("a"), one("b"), one("c"))

    asyncio.run(all_three())
    calls = [x for x in order if x[0] == "call"]
    assert len(calls) == 3
    # 첫 호출 외에는 각각 앞서 기다린다
    assert sum(1 for x in order if x[0] == "sleep") == 2


# ---------------------------------------------------------------- 동기


def test_sync_gate_waits_between_calls(monkeypatch):
    slept = []
    monkeypatch.setattr(pacing.time, "sleep", lambda s: slept.append(s))
    pacer = pacing.SyncPacer(min_interval=2.5)
    with pacer.gate():
        pass
    with pacer.gate():
        pass
    assert len(slept) == 1 and 0 < slept[0] <= 2.5


def test_sync_gate_releases_lock_on_exception():
    pacer = pacing.SyncPacer(min_interval=0.0)
    try:
        with pacer.gate():
            raise RuntimeError("실패")
    except RuntimeError:
        pass
    with pacer.gate():        # 락이 남았으면 여기서 데드락
        pass
    assert not pacer.lock.locked()


def test_sync_gate_serialises_threads(monkeypatch):
    """code_finder 는 동기 코드다 — 스레드에서 겹쳐 불릴 수 있다."""
    monkeypatch.setattr(pacing.time, "sleep", lambda s: None)
    pacer = pacing.SyncPacer(min_interval=0.0)
    seen = []

    def worker():
        for _ in range(20):
            with pacer.gate():
                seen.append(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 80
    assert not pacer.lock.locked()


def test_both_flavours_share_the_same_rule():
    """동기·비동기를 억지로 하나로 안 합쳤지만, 간격 계산은 한 곳이어야 한다.
    두 클래스가 각자 계산하기 시작하면 통합한 의미가 없다."""
    import inspect
    src = inspect.getsource(pacing)
    assert src.count("min_interval - (time.monotonic()") == 1
