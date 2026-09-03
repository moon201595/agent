"""_summarize_chunked() 제어 흐름 단위 테스트 — 네트워크 불필요, 모킹.

2026-08-06: 청크 슬라이싱을 char 기반에서 sentence_grounding 기반으로
바꾸면서(⑤ 근거 문장 그라운딩, docs/PROGRESS.md §8-9) 이 흐름을 지키는
회귀 테스트가 없었다는 걸 발견해 새로 만든다. _post_gemini 만 모킹하고
sentence_grounding 은 실제로 돌려서 통합까지 확인한다.
"""

import asyncio

import httpx
import pytest

import summarize_engine as engine


async def _no_sleep(_s):
    """재시도 대기를 건너뛴다 — 테스트가 실제로 몇 초씩 자면 안 된다."""
    return None


def _make_paper(num_sentences: int) -> str:
    return " ".join(f"Sentence number {i} reports a value of {i}.0 percent." for i in range(1, num_sentences + 1))


def test_short_paper_single_call_no_addendum(monkeypatch):
    calls = []

    async def fake_post_gemini(client, prompt):
        calls.append(prompt)
        return "### 기본정보\n- 제목 : 테스트"

    monkeypatch.setattr(engine, "_post_gemini", fake_post_gemini)
    paper = _make_paper(5)  # 짧아서 청크 1개로 끝나야 함

    async def main():
        return await engine._summarize_chunked(
            client=None, paper_text=paper, template="템플릿",
            call_single=engine.call_gemini, call_addendum=engine.call_gemini_addendum,
            chunk_size=100000, max_chunks=4, chunk_delay=0.0, label="Gemini",
        )

    result = asyncio.run(main())
    assert len(calls) == 1  # 보충 호출 없음
    assert result == "### 기본정보\n- 제목 : 테스트"
    assert "[S0001]" in calls[0]  # 태그가 프롬프트에 실제로 들어갔다


def test_long_paper_triggers_addendum_calls(monkeypatch):
    calls = []

    async def fake_post_gemini(client, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "### 기본정보\n- 제목 : 테스트"
        return f"### 추가 결과 (원문 후반부)\n청크 {len(calls)} 결과 — [S0001] ★★★"

    monkeypatch.setattr(engine, "_post_gemini", fake_post_gemini)
    # 문장이 아주 많은 논문을 작은 chunk_size 로 강제 분할 — 여러 청크가 나오게 한다.
    # chunk_delay 는 아주 작게 둬서(0.01초) 실제 대기는 걸되 테스트가 느려지지 않게 한다.
    paper = _make_paper(200)

    async def main():
        return await engine._summarize_chunked(
            client=None, paper_text=paper, template="템플릿",
            call_single=engine.call_gemini, call_addendum=engine.call_gemini_addendum,
            chunk_size=500, max_chunks=4, chunk_delay=0.01, label="Gemini",
        )

    result = asyncio.run(main())
    assert len(calls) > 1  # 청크 1(본문) + 보충 청크 여러 번
    assert len(calls) <= 4  # max_chunks 상한 준수
    assert "청크 2 결과" in result
    # 각 보충 청크 프롬프트에도 [S번호] 태그가 들어있다 — 이어지는 문장에도 번호가 붙는다
    assert all("[S" in c for c in calls[1:])


def test_addendum_no_content_is_not_appended(monkeypatch):
    calls = []

    async def fake_post_gemini(client, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "### 기본정보\n- 제목 : 테스트"
        return engine._ADDENDUM_NO_CONTENT

    monkeypatch.setattr(engine, "_post_gemini", fake_post_gemini)
    paper = _make_paper(200)

    async def main():
        return await engine._summarize_chunked(
            client=None, paper_text=paper, template="템플릿",
            call_single=engine.call_gemini, call_addendum=engine.call_gemini_addendum,
            chunk_size=500, max_chunks=3, chunk_delay=0.0, label="Gemini",
        )

    result = asyncio.run(main())
    assert len(calls) > 1  # 보충 호출은 실제로 갔다
    assert result == "### 기본정보\n- 제목 : 테스트"  # 그런데 "새 내용 없음"이라 안 붙었다


def test_mid_chunk_failure_preserves_partial_result(monkeypatch):
    calls = []

    async def fake_post_gemini(client, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "### 기본정보\n- 제목 : 테스트"
        raise RuntimeError("일시적 네트워크 오류")

    monkeypatch.setattr(engine, "_post_gemini", fake_post_gemini)
    paper = _make_paper(200)

    async def main():
        return await engine._summarize_chunked(
            client=None, paper_text=paper, template="템플릿",
            call_single=engine.call_gemini, call_addendum=engine.call_gemini_addendum,
            chunk_size=500, max_chunks=4, chunk_delay=0.0, label="Gemini",
        )

    result = asyncio.run(main())
    assert result == "### 기본정보\n- 제목 : 테스트"  # 청크 1 결과는 보존됨
    assert len(calls) == 2  # 청크 1 성공 + 청크 2 시도(실패) 후 중단, 청크 3은 시도 안 함


def test_sentence_ids_are_globally_continuous_across_chunks(monkeypatch):
    """청크 1 이 문장 1~N을 쓰면, 청크 2는 N+1부터 시작해야 한다 —
    두 청크가 같은 번호를 다시 쓰면 ⑤ 검증기가 엉뚱한 문장을 찾게 된다."""
    prompts = []

    async def fake_post_gemini(client, prompt):
        prompts.append(prompt)
        return "결과"

    monkeypatch.setattr(engine, "_post_gemini", fake_post_gemini)
    paper = _make_paper(200)

    async def main():
        return await engine._summarize_chunked(
            client=None, paper_text=paper, template="템플릿",
            call_single=engine.call_gemini, call_addendum=engine.call_gemini_addendum,
            chunk_size=500, max_chunks=4, chunk_delay=0.0, label="Gemini",
        )

    asyncio.run(main())
    assert len(prompts) >= 2

    import re
    tag_re = re.compile(r"\[S(\d+)\]")

    def ids_in_paper_section(prompt: str) -> list[int]:
        # 프롬프트 지시문 자체에 예시로 "[S0142]"가 들어있어(build_prompt 설명) 그걸
        # 실제 문장 태그와 섞어 세면 안 된다 — "# 논문 원문" 마커 뒤쪽만 본다.
        body = prompt.split("# 논문 원문", 1)[-1]
        return [int(x) for x in tag_re.findall(body)]

    chunk1_ids = ids_in_paper_section(prompts[0])
    chunk2_ids = ids_in_paper_section(prompts[1])
    assert chunk1_ids and chunk2_ids
    assert max(chunk1_ids) < min(chunk2_ids)  # 겹치지 않고 이어짐
    assert min(chunk2_ids) == max(chunk1_ids) + 1  # 정확히 다음 번호부터 시작


# ---------------------------------------------------------------- M1: 429 재시도 대기시간


def _make_429(headers=None):
    request = __import__("httpx").Request("POST", "http://api.example/v1")
    response = __import__("httpx").Response(429, headers=headers or {}, request=request)
    return __import__("httpx").HTTPStatusError("429", request=request, response=response)


def test_retry_wait_respects_retry_after_header():
    """서버가 Retry-After: 7 을 주면 고정 백오프(20초) 대신 그 값을 쓴다 —
    지터(0~3초)만 더해진 범위여야 한다(M1, 2026-08-28)."""
    wait = engine._retry_wait_seconds(_make_429({"retry-after": "7"}), attempt=0)
    assert 7.0 <= wait <= 10.0


def test_retry_wait_falls_back_to_fixed_backoff_without_header():
    wait = engine._retry_wait_seconds(_make_429(), attempt=0)
    assert engine.RATE_LIMIT_BACKOFF[0] <= wait <= engine.RATE_LIMIT_BACKOFF[0] + 3.0


def test_retry_wait_ignores_unparseable_http_date_header():
    """Retry-After가 HTTP-date 형식이면(숫자 아님) 파싱을 시도하지 않고
    고정 백오프로 폴백한다 — 잘못 파싱해 0초 대기로 또 429를 맞는 것보다
    보수적으로 기다리는 쪽이 안전하다."""
    wait = engine._retry_wait_seconds(
        _make_429({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), attempt=1,
    )
    assert engine.RATE_LIMIT_BACKOFF[1] <= wait <= engine.RATE_LIMIT_BACKOFF[1] + 3.0


def test_retry_wait_adds_jitter_not_constant():
    """지터가 실제로 들어간다 — 같은 입력 20회에서 값이 전부 같으면 지터가
    없는 것이다(무작위라 이론상 전부 같을 확률은 사실상 0)."""
    waits = {engine._retry_wait_seconds(_make_429(), attempt=0) for _ in range(20)}
    assert len(waits) > 1


# ---------------------------------------------------------------- 503 일시적 혼잡 재시도


def _http_error(status):
    import httpx
    req = httpx.Request("POST", "http://api.example/v1")
    return httpx.HTTPStatusError(str(status), request=req,
                                  response=httpx.Response(status, request=req))


def test_503_is_retried_and_succeeds(monkeypatch):
    """실측(2026-08-28): 503을 받은 바로 그 키가 2초 뒤 200을 줬다. 재시도하면
    풀리는 신호인데 예전엔 즉시 Groq로 넘어가 3시간을 태웠다."""
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(503)
        return "성공"

    result = asyncio.run(engine._call_with_rate_limit_retry(flaky, "Gemini"))
    assert result == "성공"
    assert len(calls) == 3


def test_503_gives_up_after_limit(monkeypatch):
    """무한 재시도가 아니라 상한 있는 예외 처리 — 상한을 넘으면 올려서
    호출부가 Groq로 넘어가게 한다."""
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    calls = []

    async def always_503():
        calls.append(1)
        raise _http_error(503)

    with pytest.raises(Exception):
        asyncio.run(engine._call_with_rate_limit_retry(always_503, "Gemini"))
    assert len(calls) == engine.OVERLOAD_RETRIES + 1


def test_503_and_429_have_separate_budgets(monkeypatch):
    """둘을 한 카운터로 묶으면 하나가 다른 하나의 재시도 예산을 먹는다.
    503 세 번을 쓴 뒤에도 429 재시도가 온전히 남아 있어야 한다."""
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    seq = [503, 503, 503, 429, 429]
    calls = []

    async def mixed():
        calls.append(1)
        if len(calls) <= len(seq):
            raise _http_error(seq[len(calls) - 1])
        return "성공"

    result = asyncio.run(engine._call_with_rate_limit_retry(mixed, "Gemini"))
    assert result == "성공"
    assert len(calls) == 6  # 503 3회 + 429 2회 전부 재시도한 뒤 성공


def test_non_retryable_error_still_raises_immediately(monkeypatch):
    """400·401 같은 건 다시 불러도 같은 답이라 즉시 올린다(기존 동작 불변)."""
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    calls = []

    async def bad_request():
        calls.append(1)
        raise _http_error(400)

    with pytest.raises(Exception):
        asyncio.run(engine._call_with_rate_limit_retry(bad_request, "Gemini"))
    assert len(calls) == 1


def test_addendum_gives_up_on_long_wait_instead_of_stalling(monkeypatch):
    """실측 사고 회귀(2026-08-28): Groq 일일 한도가 소진돼 서버가 청크마다
    20분 대기를 요구했고, 부록을 붙이자고 논문 한 편에 3시간을 태웠다.
    청크 1이 이미 진짜 요약을 만들었으므로 보충 청크는 손절하는 게 맞다."""
    import httpx
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    req = httpx.Request("POST", "http://api.example/v1")
    resp = httpx.Response(429, headers={"retry-after": "1200"}, request=req)
    calls = []

    async def rate_limited():
        calls.append(1)
        raise httpx.HTTPStatusError("429", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(engine._call_with_rate_limit_retry(
            rate_limited, "Groq(청크9)", max_wait=engine.ADDENDUM_MAX_WAIT))
    assert len(calls) == 1  # 20분을 기다리지 않고 즉시 포기


def test_short_wait_is_still_honored_for_addendum(monkeypatch):
    """짧은 대기는 그대로 기다린다 — 손절은 "오늘 한도가 끝났다" 수준일 때만."""
    import httpx
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    req = httpx.Request("POST", "http://api.example/v1")
    resp = httpx.Response(429, headers={"retry-after": "5"}, request=req)
    calls = []

    async def once_then_ok():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.HTTPStatusError("429", request=req, response=resp)
        return "성공"

    out = asyncio.run(engine._call_with_rate_limit_retry(
        once_then_ok, "Groq(청크9)", max_wait=engine.ADDENDUM_MAX_WAIT))
    assert out == "성공"


def test_first_chunk_has_no_wait_cap(monkeypatch):
    """청크 1은 진짜 요약이라 기다릴 가치가 있다 — max_wait 없이 부른다."""
    import inspect
    src = inspect.getsource(engine._summarize_chunked)
    first_call = src.split("for chunk_num")[0]
    assert "max_wait" not in first_call


# ------------------------------------------- 503 재시도 예산 (2026-09-01)


def test_overload_backoff_grows_and_is_capped():
    """혼잡은 몇 초 만에 풀리기도 하고 몇 분 가기도 한다 — 앞은 짧게 자주,
    뒤로 갈수록 간격을 벌리되 상한을 둔다(새벽 배치가 안 끝나면 안 된다)."""
    waits = [engine._overload_wait_seconds(i) for i in range(engine.OVERLOAD_RETRIES)]
    assert waits == sorted(waits)                       # 단조 증가
    assert all(w <= engine.OVERLOAD_BACKOFF_MAX for w in waits)
    assert waits[0] == engine.OVERLOAD_BACKOFF


def test_overload_budget_is_far_cheaper_than_falling_back_to_groq():
    """예산을 정한 근거 자체를 못박는다. 실측(§8-15, §8-25): Gemini 성공은
    호출 1회에 원문 전체를 보고, Groq 폴백은 호출 중앙값 24회에 긴 논문은
    원문의 절반만 본다(청크당 60초 간격이라 한 편에 약 25분).

    즉 503 을 더 기다리는 비용이 폴백 비용보다 한참 싸야 이 설계가 성립한다.
    누군가 예산을 다시 9초로 줄이면 이 테스트가 막는다."""
    budget = sum(engine._overload_wait_seconds(i) for i in range(engine.OVERLOAD_RETRIES))
    groq_one_paper = 24 * engine.GROQ_CHUNK_DELAY       # 약 1440초
    assert budget >= 120                                # 혼잡이 지나갈 만큼은 기다린다
    assert budget < groq_one_paper / 5                  # 그래도 폴백보다 한참 싸다


def test_transient_overload_recovers_within_the_larger_budget(monkeypatch):
    """9초(옛 예산 3회×3초)로는 못 넘겼을 혼잡을 새 예산으로는 넘긴다 —
    2026-09-01 복구 실행이 정확히 이 지점에서 통째로 Groq 로 넘어갔다."""
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) <= 4:            # 옛 예산으로는 여기서 포기했다
            raise _http_error(503)
        return "요약"

    result = asyncio.run(engine._call_with_rate_limit_retry(flaky, "Gemini"))
    assert result == "요약"
    assert len(calls) == 5


# ---------------------------------------------------------------- Groq 적응형 청크 간격 (2026-09-03)
#
# GROQ_CHUNK_DELAY=60.0 은 재본 적 없는 추정치였고 논문 한 편의 절반 이상을
# 이 상수가 정하고 있었다. Groq 이 응답 헤더로 정확한 회복 시간을 알려준다.

def test_parse_reset_seconds_handles_groq_formats():
    p = engine.parse_reset_seconds
    assert p("35.812s") == pytest.approx(35.812)
    assert p("1m26.4s") == pytest.approx(86.4)
    assert p("2m") == pytest.approx(120.0)
    assert p("7.66s") == pytest.approx(7.66)
    assert p("") is None and p("oops") is None and p(None) is None


def test_chunk_delay_prefers_observed_over_the_guess(monkeypatch):
    monkeypatch.setattr(engine, "_groq_reset_seconds", None)
    assert engine.groq_chunk_delay() == engine.GROQ_CHUNK_DELAY   # 관측 전엔 폴백

    monkeypatch.setattr(engine, "_groq_reset_seconds", 35.8)
    assert engine.groq_chunk_delay() == pytest.approx(35.8)


def test_chunk_delay_honors_backpressure_above_the_old_guess(monkeypatch):
    """핵심 회귀 — 상한이 위험 구간을 만들면 안 된다.

    라이브 실측에서 연속 호출 중 reset 이 51.6초까지 올랐다. 상한을 60 에 두면
    서버가 70 초를 요구할 때 60 만 기다려 429 를 자초하고, 그 재시도가 아낀
    시간보다 크다.
    """
    monkeypatch.setattr(engine, "_groq_reset_seconds", 70.0)
    assert engine.groq_chunk_delay() == pytest.approx(70.0)


def test_chunk_delay_gives_up_on_daily_quota_style_waits(monkeypatch):
    """'오늘은 끝났다' 수준(분 단위)은 여기서 안 버틴다 — 기준을 두 개 두지
    않으려고 재시도 경로와 같은 ADDENDUM_MAX_WAIT 를 쓴다."""
    monkeypatch.setattr(engine, "_groq_reset_seconds", 900.0)
    assert engine.groq_chunk_delay() == engine.ADDENDUM_MAX_WAIT


def test_post_groq_records_the_reset_header(monkeypatch):
    """헤더를 읽어야 다음 청크 간격이 맞는다."""
    monkeypatch.setattr(engine, "ENV", {"GROQ_API_KEY": "fake"})
    monkeypatch.setattr(engine, "_groq_reset_seconds", None)

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "요약"}}]},
                              headers={"x-ratelimit-reset-tokens": "35.812s"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await engine._post_groq(c, "prompt")

    assert asyncio.run(go()) == "요약"
    assert engine._groq_reset_seconds == pytest.approx(35.812)


def test_chunked_accepts_callable_delay(monkeypatch):
    """Groq 경로는 매 청크마다 최신 관측값을 다시 읽어야 한다 —
    상수로 한 번 고정하면 프롬프트 길이가 달라져도 안 따라간다."""
    waited = []

    async def fake_sleep(s):
        waited.append(s)

    async def single(client, chunk, template):
        return "요약 [S1]"

    async def addendum(client, chunk):
        return "추가"

    monkeypatch.setattr(engine.asyncio, "sleep", fake_sleep)
    values = iter([10.0, 20.0])
    asyncio.run(engine._summarize_chunked(
        None, _make_paper(200), "T", single, addendum,
        chunk_size=500, max_chunks=3, chunk_delay=lambda: next(values),
        label="Groq"))
    assert waited == [10.0, 20.0]      # 청크마다 다시 물어봤다 — 고정값이 아니다


# ---------------------------------------------------------------- Gemini 모델 회전 (2026-09-03)
#
# 503 은 모델별 서빙 풀 혼잡이라 키를 바꿔도 같은 풀이다. 모델을 바꾸면 다른
# 풀이다 — 429↔키 회전과 대칭.

def test_503_advances_the_model_cursor(monkeypatch):
    monkeypatch.setattr(engine, "ENV", {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(engine, "_gemini_model_cursor", 0)
    first = engine.current_gemini_model()

    def handler(request):
        assert first in str(request.url)      # 첫 시도는 기본 모델로 간다
        return httpx.Response(503, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await engine._post_gemini_once(c, "p", "k")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert engine.current_gemini_model() != first   # 다음 시도는 다른 풀로


def test_429_does_not_touch_the_model_cursor(monkeypatch):
    """429 는 키 문제다 — 모델을 바꾸면 엉뚱한 축을 건드린다."""
    monkeypatch.setattr(engine, "ENV", {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(engine, "_gemini_model_cursor", 0)
    before = engine.current_gemini_model()

    async def go():
        async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))) as c:
            return await engine._post_gemini_once(c, "p", "k")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert engine.current_gemini_model() == before
