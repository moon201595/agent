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
    # widen() 이 올릴 수 있는 상한. 0 이면 이 페이서는 간격을 안 넓힌다.
    # **이건 상대 서비스의 한도 수치가 아니라 우리 백오프의 천장이다** —
    # 규칙 3 이 금지하는 건 전자다.
    max_interval: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_call: float = 0.0
    widened: int = 0

    def widen(self, factor: float = 2.0) -> float:
        """429 를 받았을 때 이후 호출 간격을 넓힌다.

        2026-09-03 실측: S2 를 1.0초 간격으로 7키워드 연속 호출하니 **14회 중
        7회가 429** 였고, 그 재시도 사슬(30·60·120·240초)이 **8분**을 먹었다.
        전체 36분의 22%가 순수 대기였다.

        간격을 미리 크게 잡는 방법도 있지만 그건 "S2 의 한도는 N 초"를 코드에
        적는 것이라 규칙 3 에 걸린다 — 한도는 예고 없이 바뀐다(2025-12 Gemini
        삭감). 대신 **실제 429 응답만 보고** 줄인다: 한 번 맞으면 두 배로
        넓히고, 그 실행 안에서는 유지한다. 매일 새 프로세스로 도니 다음
        실행에는 자연히 원래 간격에서 다시 시작한다 — 한도가 완화되면
        저절로 따라간다.

        되돌리지 않는 이유: 한 번 429 를 낸 상대에게 곧바로 원래 속도로
        돌아가면 같은 사슬을 또 탄다. 규칙 2 의 (c) 처리량 축소다.
        """
        if self.max_interval <= 0 or self.min_interval >= self.max_interval:
            return self.min_interval
        self.min_interval = min(self.min_interval * factor, self.max_interval)
        self.widened += 1
        return self.min_interval

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
