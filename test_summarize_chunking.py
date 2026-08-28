"""_summarize_chunked() 제어 흐름 단위 테스트 — 네트워크 불필요, 모킹.

2026-08-06: 청크 슬라이싱을 char 기반에서 sentence_grounding 기반으로
바꾸면서(⑤ 근거 문장 그라운딩, docs/PROGRESS.md §8-9) 이 흐름을 지키는
회귀 테스트가 없었다는 걸 발견해 새로 만든다. _post_gemini 만 모킹하고
sentence_grounding 은 실제로 돌려서 통합까지 확인한다.
"""

import asyncio

import summarize_engine as engine


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
