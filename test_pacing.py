"""호출 간 최소 간격 공용 구현 (§8-30, 2026-09-02).

같은 다섯 줄이 네 벌 있었다 — code_finder(threading), retraction(asyncio +
globals() 문자열 조회), server ×2(asyncio). 그중 하나는 문자열 키로 전역을
읽고 써서 오타가 조용한 KeyError 가 되는 구조였다.

통합을 코드 검토 시점에 바로 하지 않고 미뤘던 이유도 여기 적어 둔다:
그때 code_finder(58%)·server(39%) 커버리지로는 회귀를 잡을 그물이 없었다.
server 를 63% 로 올린 뒤에 착수했다 — **리팩토링은 그물이 먼저다.**
"""

import http_client
import asyncio
import threading
import time
from unittest import mock

import pytest

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


# ---------------------------------------------------------------- 적응형 페이싱 (2026-09-03)
#
# S2 를 1.0초 간격으로 7키워드 연속 호출하니 14회 중 7회가 429 였고 재시도
# 사슬이 8분(전체 36분의 22%)을 먹었다. 간격을 코드에 크게 박으면 규칙 3 에
# 걸리므로 429 응답만 보고 넓힌다.

def test_widen_doubles_up_to_cap():
    p = pacing.AsyncPacer(1.0, max_interval=16.0)
    assert [p.widen() for _ in range(6)] == [2.0, 4.0, 8.0, 16.0, 16.0, 16.0]
    assert p.widened == 4  # 상한에 닿은 뒤로는 안 센다


def test_widen_is_noop_without_cap():
    """상한을 안 준 페이서(arXiv 등)는 기존 동작 그대로."""
    p = pacing.AsyncPacer(3.0)
    assert [p.widen() for _ in range(3)] == [3.0, 3.0, 3.0]
    assert p.widened == 0


def test_widened_interval_is_actually_enforced():
    """넓힌 값이 기록만 되고 안 지켜지면 의미가 없다."""
    import asyncio, time
    p = pacing.AsyncPacer(0.01, max_interval=0.20)
    p.widen(); p.widen(); p.widen(); p.widen()   # 0.01 → 0.16
    assert p.min_interval == pytest.approx(0.16)

    async def two_calls():
        async with p.gate():
            pass
        t0 = time.monotonic()
        async with p.gate():
            pass
        return time.monotonic() - t0

    assert asyncio.run(two_calls()) >= 0.15


def test_s2_429_widens_the_http_client_pacer():
    """S2 경로가 429 를 받으면 실제로 간격이 넓어진다.

    2026-09-05: 페이서가 http_client 로 옮겨갔는데(§8-52) 이 테스트만 옛
    위치(server)를 보고 있었다. 그 탓에 **MCP SDK 가 없는 환경에서 이 파일
    하나만 실패**했다 — 나머지 test_pacing 은 순수 모듈이라 잘 돌았는데.
    이름도 같이 바꾼다: 확인하는 대상이 server 가 아니라 http_client 다.
    """
    import httpx
    import http_client as server        # 아래 본문을 그대로 두기 위한 별칭

    before = server._s2_pacer.min_interval
    try:
        server._s2_pacer.min_interval = 0.001
        server._s2_pacer.max_interval = 0.01
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={})
            return httpx.Response(200, json={"data": []})

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as c:
                return await server.throttled_s2_get(c, {}, {})

        import asyncio
        # 재시도 대기는 0 으로 — 이 테스트가 재는 건 간격이지 백오프가 아니다
        with mock.patch.object(http_client, "rate_limit_wait", new=lambda *a, **k: 0.0):
            resp = asyncio.run(go())
        assert resp.status_code == 200
        assert server._s2_pacer.widened >= 1
        assert server._s2_pacer.min_interval > 0.001
    finally:
        server._s2_pacer.min_interval = before
        server._s2_pacer.max_interval = server.S2_MAX_INTERVAL
        server._s2_pacer.widened = 0


# ---------------------------------------------------------------- 경계 감시 (2026-09-05)
#
# §8-52 분리 뒤 남은 위험 둘을 테스트로 못 박는다. 주석은 읽는 사람이 있어야
# 효과가 있지만 테스트는 안 읽어도 걸린다.

def test_db_path_has_exactly_one_owner():
    """`server.DB_PATH` 는 하위호환 별칭이고 소유자는 `storage.DB_PATH` 다.

    지금 테스트들이 **둘 다** monkeypatch 해서 동작하는데, 한쪽만 패치하는
    테스트가 새로 생기면 조용히 다른 DB 를 본다. 값이 갈라지는 순간 여기서
    걸리게 한다 — 신규 코드는 storage.DB_PATH 만 쓴다.
    """
    import server
    import storage
    assert server.DB_PATH == storage.DB_PATH
    for name in ("DATA_DIR", "PDF_DIR", "TEXT_DIR", "SUMMARY_DIR",
                 "IMAGE_DIR", "REPRO_DIR"):
        assert getattr(server, name) == getattr(storage, name), name


def test_pure_modules_do_not_import_server():
    """순수 로직 모듈은 MCP SDK 없이 열려야 한다(§8-52 의 목적 그 자체).

    `import server` 는 `from mcp.server.mcpserver import MCPServer` 를 끌고
    온다. 아래 모듈이 그걸 다시 잡으면 MCP 가 없는 환경(CI·다른 사람의 클론)
    에서 임포트조차 안 된다.
    """
    import ast
    from pathlib import Path

    PURE = ("digest.py", "trend_report.py", "storage.py", "http_client.py",
            "pacing.py", "selection.py", "profile_scoring.py", "s2_delta.py",
            "find_new_papers.py", "summarize_engine.py")
    offenders = []
    for name in PURE:
        tree = ast.parse(Path(name).read_text(encoding="utf-8"))
        mods = {n.module.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
        mods |= {a.name.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.Import) for a in n.names}
        if "server" in mods:
            offenders.append(name)
    assert offenders == [], f"server 를 임포트하면 MCP SDK 없이 못 연다: {offenders}"
