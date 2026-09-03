"""pacing.py — 외부 API 호출 간 최소 간격을 지키는 공용 구현 (2026-09-02).

왜 모았나(§8-30): 같은 "호출 간 최소 간격" 로직이 세 벌로 나뉘어 있었다 —
`code_finder`(threading.Lock), `retraction`(asyncio.Lock + `globals()` 문자열
조회), `server`(asyncio.Lock ×2). 셋 다 같은 다섯 줄인데 락 종류와 상태
보관 방식만 달랐고, 그중 하나는 문자열 키로 전역을 읽고 써서 오타가 조용한
KeyError 가 되는 구조였다.

**동기와 비동기를 억지로 하나로 안 합친다.** `code_finder` 는 동기 코드이고
(⑦ 재현이 별도 프로세스에서 도는 경로다) 거기에 async 를 끌어들이면 호출부
전체를 바꿔야 한다. 클래스는 둘로 두되 **간격 계산 규칙은 한 곳에만 적는다**.

통합을 2026-09-02 코드 검토 시점에 바로 하지 않고 미뤘던 이유도 기록해 둔다:
그때 `code_finder`(58%)·`server`(39%) 커버리지로는 회귀를 잡을 그물이 없었다.
server 를 63% 로 올린 뒤에 착수했다 — 리팩토링은 그물이 먼저다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field


def _remaining(last_call: float, min_interval: float) -> float:
    """다음 호출까지 더 기다려야 하는 초. 규칙이 적힌 유일한 자리다."""
    return min_interval - (time.monotonic() - last_call)


@dataclass
class AsyncPacer:
    """asyncio 경로용. 락과 "마지막 호출 시각"을 한 덩어리로 묶는다 —
    따로 두면 둘을 짝지어 넘기다 어긋난다(retraction 이 `globals()` 문자열
    조회를 하게 된 경위가 그것이다)."""

    min_interval: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_call: float = 0.0

    async def wait(self) -> None:
        """락을 **잡은 채로** 돌려주지 않는다 — 호출부가 `async with
        pacer.lock:` 안에서 이걸 쓰는 게 아니라, `async with pacer.gate():`
        로 감싼다. 아래 gate() 참고."""
        remaining = _remaining(self.last_call, self.min_interval)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def gate(self):
        return _AsyncGate(self)


class _AsyncGate:
    """`async with pacer.gate():` 블록 동안 다른 호출이 못 끼어들게 하고,
    블록에 들어가기 전에 필요한 만큼 기다린다. 나갈 때 시각을 갱신한다 —
    **응답을 받은 시각** 기준이라 느린 응답이 다음 호출을 앞당기지 않는다.
    """

    def __init__(self, pacer: AsyncPacer) -> None:
        self._pacer = pacer

    async def __aenter__(self) -> AsyncPacer:
        await self._pacer.lock.acquire()
        try:
            await self._pacer.wait()
        except BaseException:
            self._pacer.lock.release()
            raise
        return self._pacer

    async def __aexit__(self, *exc) -> bool:
        self._pacer.last_call = time.monotonic()
        self._pacer.lock.release()
        return False


@dataclass
class SyncPacer:
    """동기 경로용(code_finder). 규칙은 AsyncPacer 와 같고 대기 방식만 다르다."""

    min_interval: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_call: float = 0.0

    def gate(self):
        return _SyncGate(self)


class _SyncGate:
    def __init__(self, pacer: SyncPacer) -> None:
        self._pacer = pacer

    def __enter__(self) -> SyncPacer:
        self._pacer.lock.acquire()
        try:
            remaining = _remaining(self._pacer.last_call, self._pacer.min_interval)
            if remaining > 0:
                time.sleep(remaining)
        except BaseException:
            self._pacer.lock.release()
            raise
        return self._pacer

    def __exit__(self, *exc) -> bool:
        self._pacer.last_call = time.monotonic()
        self._pacer.lock.release()
        return False
