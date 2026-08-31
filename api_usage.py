"""api_usage.py — 외부 API 호출 계수기 (계측 전용, 판정에 관여하지 않는다).

왜 필요했나(§8-15): 2026-08-28 종단 실측에서 stdout 을 `tail` 로 잘라 받는
바람에 Gemini 시도 횟수(429·503 재시도 포함)를 못 셌고, Groq 청크 수 24는
원문 길이에서 **역산한 값**이지 로그로 확인한 값이 아니었다. 무료 티어
한도에 언제 닿는지는 "논문 한 편이 호출을 몇 번 쓰는가"로 결정되는데,
그 숫자를 아무도 안 세고 있었다.

역산 대신 실제 호출 지점에서 센다. 호출은 전부 단일 병목을 지난다:
  summarize_engine._post_gemini / _post_groq   (LLM)
  server._throttled_arxiv_get / _throttled_s2_get
  retraction._throttled_get                    (OpenAlex / Crossref)
  code_finder.github_search                    (gh CLI)

**재시도도 한 번의 호출로 센다** — 한도를 소모하는 것은 성공한 호출이
아니라 보낸 요청이기 때문이다. outcome 으로 성공/429/503/오류를 구분해서
"몇 번 보냈고 그중 몇 번이 헛수고였나"가 같이 보이게 한다.

이 모듈은 아무것도 판정하지 않고 아무것도 저장하지 않는다(CLAUDE.md 7 과
무관한 순수 계측). 스레드에서도 불리므로(code_finder 는 동기 코드다)
Counter 갱신만 잠근다.
"""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_counts: Counter = Counter()


def record(provider: str, outcome: str = "ok") -> None:
    with _lock:
        _counts[(provider, outcome)] += 1


def reset() -> None:
    with _lock:
        _counts.clear()


def snapshot() -> dict[str, dict[str, int]]:
    """{provider: {outcome: n}} — 호출부가 스스로 합계를 낼 수 있게 원자료로 준다."""
    out: dict[str, dict[str, int]] = {}
    with _lock:
        for (provider, outcome), n in _counts.items():
            out.setdefault(provider, {})[outcome] = n
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def total() -> int:
    with _lock:
        return sum(_counts.values())


def format_summary() -> str:
    """사람이 로그에서 바로 읽는 한 줄. 호출이 없으면 빈 문자열."""
    snap = snapshot()
    if not snap:
        return ""
    parts = []
    for provider, outcomes in snap.items():
        n = sum(outcomes.values())
        detail = " ".join(f"{k}:{v}" for k, v in outcomes.items() if k != "ok")
        parts.append(f"{provider} {n}" + (f"({detail})" if detail else ""))
    return f"API 호출 {total()}회 — " + " · ".join(parts)


class Scope:
    """with 블록 동안의 호출만 따로 센다 — 논문 한 편의 비용을 재는 단위.

    전역 계수기를 리셋하지 않고 진입 시점의 값을 빼는 방식이라, 바깥에서
    전체 실행분을 세는 것과 겹쳐 써도 서로를 망가뜨리지 않는다.
    """

    def __init__(self) -> None:
        self._before: Counter = Counter()
        self._after: Counter | None = None

    def __enter__(self) -> "Scope":
        with _lock:
            self._before = _counts.copy()
        self._after = None
        return self

    def __exit__(self, *exc) -> bool:
        # 블록이 끝나는 시점을 고정한다. 안 그러면 블록 뒤에 일어난 호출까지
        # 이 스코프의 비용으로 잡힌다 — 논문 한 편의 비용을 재는 게 목적이라
        # 그건 틀린 값이다. 예외로 빠져나갈 때도 __exit__ 는 불리므로,
        # 실패한 논문이 이미 쓴 호출은 그대로 남는다.
        with _lock:
            self._after = _counts.copy()
        return False

    def snapshot(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        with _lock:
            after = self._after if self._after is not None else _counts.copy()
        delta = after - self._before
        for (provider, outcome), n in delta.items():
            out.setdefault(provider, {})[outcome] = n
        return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}

    def total(self) -> int:
        return sum(sum(v.values()) for v in self.snapshot().values())

    def format_summary(self) -> str:
        snap = self.snapshot()
        if not snap:
            return "API 호출 0회"
        parts = []
        for provider, outcomes in snap.items():
            n = sum(outcomes.values())
            detail = " ".join(f"{k}:{v}" for k, v in outcomes.items() if k != "ok")
            parts.append(f"{provider} {n}" + (f"({detail})" if detail else ""))
        return f"API 호출 {self.total()}회 — " + " · ".join(parts)
