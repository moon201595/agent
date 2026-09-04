"""http_client.py — ①~③ 의 외부 HTTP 호출 규약 (2026-09-04).

**왜 갈라냈나.** `find_new_papers`·`retraction`·`code_finder`·`s2_delta`·
`summarize_engine`·`api_usage` 가 `server._with_retry`·`server._throttled_*_get`
같은 **비공개 이름**을 직접 부르고 있었다. `_` 접두는 "밖에서 쓰지 마라"는
신호인데 여섯 모듈이 그걸 넘고 있었으니, 실제로는 `server` 의 공개 API 가
정의된 적이 없었던 것이다.

여기 모으는 것은 하나다: **밖에 나가는 요청의 규약** — 페이싱, 재시도 상한,
429 백오프, S2 인증 헤더. 이 규칙이 한 곳에 있어야 "arXiv 에는 3초, S2 에는
적응형" 같은 판단이 흩어지지 않는다(§8-30 에서 스로틀 네 벌을 pacing.py 로
모은 것과 같은 이유이고, 이건 그 위층이다).

`storage.py` 와 마찬가지로 **MCP SDK 를 임포트하지 않는다.**

옮긴 것뿐이고 로직은 안 바꿨다.
"""

from __future__ import annotations

import asyncio
import random
import sys

import httpx

import api_usage
import pacing

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_HTML = "https://arxiv.org/html/{arxiv_id}"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
# (UNPAYWALL_EMAIL 은 외부 API 어댑터 값이라 server.py 에 남긴다 — HTTP 규약이 아니다)

# arXiv 공식 안내에 따른 예의상 호출 간격 (초)
ARXIV_MIN_INTERVAL = 3.0

# Semantic Scholar 공식 문서: "1 request per second, cumulative across all
# endpoints" — 키를 발급받아도 이 한도는 그대로 적용된다(더 완화되지 않음).
# 2026-08-01 키 등록 시점에 사용자가 직접 확인한 값. 반드시 지킬 것.
S2_MIN_INTERVAL = 1.0

# 1.0 초를 지켜도 429 가 온다 — 2026-09-03 실측에서 7키워드 연속 호출 중
# **14회 중 7회가 429** 였고 재시도 사슬이 8분을 먹었다. 같은 날 8초 간격으로
# 다시 재보니 처음 두 호출만 429 를 맞고 이후 다섯 호출은 전부 통과했다.
# 그래서 간격을 미리 8초로 못 박는 대신 **429 를 맞을 때마다 두 배씩 넓히고**
# 여기서 멈춘다. 한도 수치를 코드에 적지 않는 방식이다(규칙 3).
S2_MAX_INTERVAL = 16.0

# ①~③ 구간의 제한 재시도 상한. 최초 1회 + 재시도 MAX_RETRIES 회.
# 이것은 에이전틱 루프가 아니라 예외 처리다 — 무엇을 다시 부를지 LLM 이 정하지 않고
# 코드가 정해진 횟수만 다시 부른다. 상한을 올리기 전에 왜 올리는지부터 정할 것.
MAX_RETRIES = 2

# 다시 불러서 결과가 달라질 수 있는 것만. 4xx 는 다시 불러도 같은 답이 온다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# 429(한도 초과)는 나머지 재시도와 **따로 센다**. 성격이 다르기 때문이다:
# 500·타임아웃은 몇 초 뒤 풀리지만 429 는 상대가 "너무 자주 왔다"고 말하는
# 것이라 몇 분을 기다려야 한다.
#
# 2026-09-02 실측: 기본 예산(2회 × 1·2초 = 약 3초)으로는 arXiv 429 를 못
# 넘겨 **스캔 전체가 검색 단계에서 죽었다**. 그날 아침 실행에서 이미
# `arxiv 12(429:3)` 이 찍혔는데 재시도로 흡수돼서 문제로 안 보였을 뿐이다.
#
# 예산을 정할 때 실패 비용을 봐야 한다는 건 Gemini 503 에서 이미 배웠다
# (§5 "503 재시도 예산 재산정"). 여기서 실패하면 그날 다이제스트가 통째로
# 없다 — 몇 분 기다리는 쪽이 명백히 싸다. 상한은 둔다: 4회 지수 백오프로
# 최대 약 7분 30초이고, Deep Layer 예산(40분)을 위협하지 않는다.
#
# Retry-After 헤더가 오면 그 값을 우선한다 — 상대가 말해준 시간을 무시하고
# 우리 계산으로 다시 두드리는 건 차단을 길게 만든다(CLAUDE.md 2).
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = 30.0
RATE_LIMIT_BACKOFF_MAX = 240.0



# 간격 계산 규칙은 pacing.py 한 곳에만 있다(§8-30, 2026-09-02). 예전엔
# 같은 다섯 줄이 server 둘 + code_finder + retraction 으로 네 벌 있었다.
_arxiv_pacer = pacing.AsyncPacer(ARXIV_MIN_INTERVAL)

_s2_pacer = pacing.AsyncPacer(S2_MIN_INTERVAL, max_interval=S2_MAX_INTERVAL)


def rate_limit_wait(exc: httpx.HTTPStatusError, attempt: int) -> float:
    """429 를 만났을 때 기다릴 시간(초). Retry-After 가 있으면 그걸 쓴다."""
    header = exc.response.headers.get("Retry-After") if exc.response is not None else None
    if header:
        try:
            return max(1.0, min(float(header), RATE_LIMIT_BACKOFF_MAX))
        except ValueError:
            pass
    return min(RATE_LIMIT_BACKOFF * (2 ** attempt), RATE_LIMIT_BACKOFF_MAX)


def s2_headers() -> dict[str, str]:
    """S2 인증 헤더. 키는 `.env` 에도 있을 수 있으므로 os.environ 만
    보면 안 된다.

    2026-09-02 실측: `S2_API_KEY` 가 `.env` 에만 있는데 코드가
    `os.environ.get("S2_API_KEY")` 로 읽고 있어서 **한 번도 안 쓰였다.**
    §8-3 에는 "2026-08-01 키 등록 완료, 정상 동작 확인"으로 적혀 있지만,
    실제로는 그 뒤로 계속 비인증 호출이었다 — 비인증은 공유 풀이라 한도가
    훨씬 빡빡하고, 그동안의 S2 429 가 여기서 왔을 수 있다.

    같은 실수를 `GOOGLE_API_KEY` 임베딩 게이트에서도 했다(§8-27). 키를
    읽는 자리는 전부 summarize_engine.ENV 를 거쳐야 한다 — 그게 os.environ
    과 `.env` 를 합쳐 놓은 단일 출처다.
    """
    # 지연 임포트 — summarize_engine 이 이 모듈을 쓰므로 최상단에 두면 순환이다.
    # (.env 로딩을 그쪽이 소유하고 있어서 키는 거기서 읽어야 한다.)
    import summarize_engine

    key = summarize_engine.ENV.get("S2_API_KEY")
    return {"x-api-key": key} if key else {}


async def with_retry(attempt_fn, what: str, max_wait: float | None = None) -> httpx.Response:
    """①~③ 의 제한 재시도. 상한까지 시도하고 그래도 실패하면 마지막 예외를 올린다.

    max_wait 를 주면 **429 대기 총합**이 그 값을 넘을 때 더 안 기다리고 포기한다.
    2026-09-04 실측: S2 가 나쁜 날 키워드마다 30+60+120+240=450초를 다 쓰는데,
    ③ 검색 예산(300초)을 한 키워드가 통째로 넘겨버렸다. 예산은 키워드 사이에서만
    검사되므로 호출 안쪽에도 상한이 필요하다 — summarize_engine 의
    ADDENDUM_MAX_WAIT 와 같은 발상이다.

    루프가 아니라 예외 처리다. 재시도 여부를 LLM 이 판단하지 않고, 재시도할 대상도
    바꾸지 않는다. 상한을 넘으면 조용히 넘어가지 않고 예외를 올려 호출부가
    사용자에게 보고하게 한다.

    attempt_fn 은 매번 새로 await 할 수 있는 코루틴 팩토리다 (코루틴은 재사용 불가).
    """
    last: Exception | None = None
    attempt = 0          # 500·타임아웃 등
    rate_limited = 0     # 429 — 예산을 따로 센다
    waited_total = 0.0   # max_wait 판정용 누적 대기
    while True:
        try:
            return await attempt_fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in RETRYABLE_STATUS:
                raise  # 404·400 등 — 다시 불러도 같은 답
            last = e
            if e.response.status_code == 429:
                if rate_limited >= RATE_LIMIT_RETRIES:
                    raise
                wait = rate_limit_wait(e, rate_limited)
                if max_wait is not None and waited_total + wait > max_wait:
                    print(f"  [경고] {what} 429 — 대기 상한({max_wait:.0f}초)을 넘어 "
                          f"여기서 포기한다", file=sys.stderr)
                    raise
                waited_total += wait
                rate_limited += 1
                print(f"  [경고] {what} 429 — {wait:.0f}초 대기 후 재시도 "
                      f"({rate_limited}/{RATE_LIMIT_RETRIES})", file=sys.stderr)
                await asyncio.sleep(wait)
                continue
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        if attempt >= MAX_RETRIES:
            raise last if last else RuntimeError(f"{what} 재시도 실패")
        # 지수 백오프 + 지터. 동시에 실패한 호출들이 다시 겹치지 않게.
        await asyncio.sleep(2.0**attempt + random.uniform(0, 0.5))
        attempt += 1


async def throttled_arxiv_get(client: httpx.AsyncClient, params: dict) -> httpx.Response:
    """arXiv API 호출 간 최소 간격을 서버 전역에서 강제하고, 상한까지 재시도한다.

    간격 강제가 재시도마다 다시 적용된다 — 재시도가 arXiv 권장 간격을 무시하면
    한도에 걸려 상황이 나빠진다.
    """

    async def once() -> httpx.Response:
        async with _arxiv_pacer.gate():
            # 2026-08-31 실측: arXiv 응답 시간이 크게 흔들린다 — 같은 시각에
            # 키워드 3개짜리 짧은 쿼리가 45초 타임아웃이 나고 21개짜리 긴
            # 쿼리는 15초에 왔다. 쿼리 복잡도가 아니라 서버 쪽 변동이다.
            # 정상 응답이 43초 걸린 사례를 실제로 측정해서, 30초로는 멀쩡한
            # 응답을 실패로 버리게 된다 — 60초로 올린다(with_retry 가 상한
            # 2회까지 재시도하므로 최악 대기는 여전히 유한하다).
            resp = await client.get(ARXIV_API, params=params, timeout=60)
        api_usage.record("arxiv", "ok" if resp.status_code == 200 else str(resp.status_code))
        resp.raise_for_status()
        return resp

    return await with_retry(once, "arXiv API")


async def throttled_s2_get(
    client: httpx.AsyncClient, params: dict, headers: dict, url: str = S2_API,
    max_wait: float | None = None,
) -> httpx.Response:
    """Semantic Scholar 호출 간 최소 간격(S2_MIN_INTERVAL)을 서버 전역에서 강제한다.
    "초당 1회, 전체 엔드포인트 합산" 이 키 등록 여부와 무관하게 적용되는 공식 한도라
    throttled_arxiv_get 과 같은 패턴으로 막는다 — 재시도마다 다시 적용해야
    재시도가 한도를 또 넘기지 않는다.

    url 을 파라미터로 받는다(기본값은 검색 엔드포인트) — "전체 엔드포인트 합산"이라
    references/citations 처럼 다른 엔드포인트를 불러도 이 락을 그대로 같이 써야
    간격이 실제로 지켜진다. 엔드포인트마다 별도 락을 두면 한도를 우회하게 된다.
    """

    async def once() -> httpx.Response:
        async with _s2_pacer.gate():
            resp = await client.get(url, params=params, headers=headers, timeout=30)
        api_usage.record("s2", "ok" if resp.status_code == 200 else str(resp.status_code))
        if resp.status_code == 429:
            # 재시도 대기(rate_limit_wait)는 이번 호출만 늦춘다. 이건 **다음
            # 호출부터**를 늦춘다 — 그래야 사슬을 처음부터 안 탄다.
            widened = _s2_pacer.widen()
            print(f"  [페이싱] Semantic Scholar 호출 간격을 {widened:.0f}초로 넓힘"
                  f" (429 를 받아 처리량을 줄인다)", flush=True)
        resp.raise_for_status()
        return resp

    return await with_retry(once, "Semantic Scholar", max_wait=max_wait)
