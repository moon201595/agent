"""Gemini 키 회전 — 네트워크 없이 돈다 (2026-09-01).

실측 배경: 복구 실행 계측이 `gemini 20(429:18, 503:2)` 이었다. Gemini 실패의
대부분이 "모델 혼잡"이 아니라 "이 키의 하루치가 끝났다"였고, 그 결과 6편
전부가 Groq 로 떨어져 호출 74회에 2시간 49분이 걸렸다(Gemini 였으면 편당 1회).
키는 세 개가 있었는데 코드가 첫 번째 것만 읽고 있었다.

실제 키 값은 이 파일 어디에도 안 들어간다 — 가짜 값으로만 돈다.
"""

import asyncio

import httpx
import pytest

import summarize_engine as engine


def _http_error(status):
    req = httpx.Request("POST", "http://api.example/v1")
    return httpx.HTTPStatusError(str(status), request=req,
                                 response=httpx.Response(status, request=req))


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch):
    """ENV 를 통째로 갈아끼운다 — 진짜 .env 값이 테스트에 새어들면 안 된다."""
    monkeypatch.setattr(engine, "ENV", {
        "GOOGLE_API_KEY": "k1", "GOOGLE_API_KEY2": "k2", "GOOGLE_API_KEY3": "k3",
    })
    monkeypatch.setattr(engine, "_gemini_key_cursor", 0)


# ---------------------------------------------------------------- 키 발견

def test_finds_all_numbered_keys_with_base_first():
    assert engine.gemini_key_names() == [
        "GOOGLE_API_KEY", "GOOGLE_API_KEY2", "GOOGLE_API_KEY3"]


def test_underscore_before_number_also_works(monkeypatch):
    """이름 규칙을 하나로 못박으면 나중에 키를 늘릴 때 조용히 안 잡힌다."""
    monkeypatch.setattr(engine, "ENV",
                        {"GOOGLE_API_KEY": "a", "GOOGLE_API_KEY_2": "b"})
    assert engine.gemini_key_names() == ["GOOGLE_API_KEY", "GOOGLE_API_KEY_2"]


def test_numeric_order_not_string_order(monkeypatch):
    """문자열 정렬이면 10 이 2 보다 앞에 온다."""
    monkeypatch.setattr(engine, "ENV", {f"GOOGLE_API_KEY{i}": "x" for i in (2, 10, 3)})
    assert engine.gemini_key_names() == [
        "GOOGLE_API_KEY2", "GOOGLE_API_KEY3", "GOOGLE_API_KEY10"]


def test_blank_keys_are_ignored(monkeypatch):
    """빈 값이 목록에 남으면 그 자리에서 한 번씩 헛호출한다."""
    monkeypatch.setattr(engine, "ENV",
                        {"GOOGLE_API_KEY": "a", "GOOGLE_API_KEY2": "  ", "GOOGLE_API_KEY3": ""})
    assert engine.gemini_key_names() == ["GOOGLE_API_KEY"]


def test_unrelated_names_are_not_picked_up(monkeypatch):
    monkeypatch.setattr(engine, "ENV",
                        {"GOOGLE_API_KEY": "a", "GOOGLE_API_KEY_BACKUP": "b",
                         "GROQ_API_KEY": "c"})
    assert engine.gemini_key_names() == ["GOOGLE_API_KEY"]


# ---------------------------------------------------------------- 회전 동작

def _run(monkeypatch, outcomes):
    """outcomes: 키 값 → 예외 또는 반환값. 사용된 키 순서를 같이 돌려준다."""
    used = []

    async def fake_once(client, prompt, key):
        used.append(key)
        r = outcomes[key]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(engine, "_post_gemini_once", fake_once)
    result = asyncio.run(engine._post_gemini(None, "prompt"))
    return result, used


def test_rotates_to_next_key_on_429(monkeypatch):
    """핵심 회귀: 한도가 끝난 키에서 멈추지 말고 다음 키로 간다."""
    result, used = _run(monkeypatch, {
        "k1": _http_error(429), "k2": _http_error(429), "k3": "요약"})
    assert result == "요약"
    assert used == ["k1", "k2", "k3"]


def test_does_not_rotate_on_503(monkeypatch):
    """503 은 모델 전체 혼잡이라 어느 키로 가도 같은 답이 온다. 회전하면
    요청만 키 개수만큼 낭비하고, 그 요청들이 다시 할당량을 깎아 429 를
    앞당긴다 — 2026-09-01 에 두 실패가 물려 돌아간 경로가 그것이다."""
    with pytest.raises(httpx.HTTPStatusError):
        _run(monkeypatch, {"k1": _http_error(503), "k2": "안 쓰임", "k3": "안 쓰임"})


def test_503_uses_exactly_one_key(monkeypatch):
    used = []

    async def fake_once(client, prompt, key):
        used.append(key)
        raise _http_error(503)

    monkeypatch.setattr(engine, "_post_gemini_once", fake_once)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(engine._post_gemini(None, "p"))
    assert used == ["k1"]


def test_all_keys_exhausted_raises_429_so_fallback_path_is_unchanged(monkeypatch):
    """전부 소진되면 429 를 그대로 올려야 기존 백오프·Groq 폴백이 지금까지와
    똑같이 동작한다. 이 층은 성공 확률을 올리는 것이지 실패 처리를 바꾸는
    게 아니다."""
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _run(monkeypatch, {k: _http_error(429) for k in ("k1", "k2", "k3")})
    assert exc.value.response.status_code == 429


def test_non_rate_limit_errors_propagate_immediately(monkeypatch):
    """400(잘못된 요청) 같은 건 다른 키로도 똑같이 실패한다 — 회전하면
    같은 오류를 세 번 만들 뿐이다."""
    used = []

    async def fake_once(client, prompt, key):
        used.append(key)
        raise _http_error(400)

    monkeypatch.setattr(engine, "_post_gemini_once", fake_once)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(engine._post_gemini(None, "p"))
    assert used == ["k1"]


def test_cursor_starts_from_the_last_working_key(monkeypatch):
    """죽은 키를 매번 먼저 찔러 한 번씩 버리면, 논문 6편이면 6번 버린다."""
    outcomes = {"k1": _http_error(429), "k2": "요약", "k3": "요약"}
    _run(monkeypatch, outcomes)                 # k1 죽고 k2 성공 → 커서가 k2
    _result, used = _run(monkeypatch, outcomes)
    assert used == ["k2"]                        # k1 을 다시 안 찌른다


def test_single_key_setup_behaves_exactly_as_before(monkeypatch):
    """키가 하나뿐인 환경(다른 사람의 클론 등)에서 동작이 안 바뀌어야 한다."""
    monkeypatch.setattr(engine, "ENV", {"GOOGLE_API_KEY": "only"})
    monkeypatch.setattr(engine, "_gemini_key_cursor", 0)
    with pytest.raises(httpx.HTTPStatusError):
        _run(monkeypatch, {"only": _http_error(429)})


def test_no_keys_raises_clear_error(monkeypatch):
    monkeypatch.setattr(engine, "ENV", {})
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        asyncio.run(engine._post_gemini(None, "p"))
