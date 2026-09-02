"""임베딩 경로도 키 회전을 쓰는가 (§8-27, 2026-09-02).

요약과 **같은 키의 같은 무료 한도**를 나눠 쓴다. 검색을 많이 한 날 첫 키가
말라버리면 그 대가를 새벽 배치가 치른다 — 실제로 429 가 요약 실패의 주된
원인이었다(2026-09-01 계측: gemini 20 중 429 가 18).

실제 키 값은 이 파일 어디에도 안 들어간다.
"""

import asyncio

import httpx
import pytest

import hybrid_search
import server
import summarize_engine as engine


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch):
    monkeypatch.setattr(engine, "ENV", {
        "GOOGLE_API_KEY": "k1", "GOOGLE_API_KEY2": "k2", "GOOGLE_API_KEY3": "k3"})


class _FakeResp:
    def __init__(self, status, key):
        self.status_code = status
        self.request = httpx.Request("POST", "http://api.example/e")
        self._key = key

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(str(self.status_code), request=self.request,
                                        response=httpx.Response(self.status_code,
                                                                request=self.request))

    def json(self):
        return {"embedding": {"values": [1.0, 2.0]}}


class _FakeClient:
    def __init__(self, statuses):
        self.statuses = statuses     # 키 값 → 상태코드
        self.used = []

    async def post(self, url, json, headers, timeout):
        key = headers["x-goog-api-key"]
        self.used.append(key)
        return _FakeResp(self.statuses[key], key)


def test_embedding_rotates_to_next_key_on_429():
    client = _FakeClient({"k1": 429, "k2": 429, "k3": 200})
    vec = asyncio.run(hybrid_search.embed_text(client, "질의", "RETRIEVAL_QUERY"))
    assert vec == [1.0, 2.0]
    assert client.used == ["k1", "k2", "k3"]


def test_embedding_does_not_rotate_on_503():
    """503 은 모델 전체 혼잡이라 어느 키로 가도 같다 — 회전하면 요청만
    낭비하고 그게 다시 할당량을 깎는다(요약 경로와 같은 판단)."""
    client = _FakeClient({"k1": 503, "k2": 200, "k3": 200})
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(hybrid_search.embed_text(client, "질의", "RETRIEVAL_QUERY"))
    assert client.used == ["k1"]


def test_embedding_raises_429_when_all_keys_exhausted():
    client = _FakeClient({k: 429 for k in ("k1", "k2", "k3")})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        asyncio.run(hybrid_search.embed_text(client, "질의", "RETRIEVAL_QUERY"))
    assert exc.value.response.status_code == 429
    assert client.used == ["k1", "k2", "k3"]


def test_embedding_uses_the_same_key_list_as_summarize():
    """키 이름 목록은 한 곳(summarize_engine)에서만 정한다 — 두 곳에서
    각자 정하면 키를 늘릴 때 한쪽만 조용히 안 잡힌다."""
    client = _FakeClient({"k1": 200, "k2": 200, "k3": 200})
    asyncio.run(hybrid_search.embed_text(client, "q", "RETRIEVAL_QUERY"))
    assert client.used == [engine.ENV[engine.gemini_key_names()[0]]]


def test_no_keys_raises_clear_error(monkeypatch):
    monkeypatch.setattr(engine, "ENV", {})
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        asyncio.run(hybrid_search.embed_text(_FakeClient({}), "q", "RETRIEVAL_QUERY"))


def test_server_gate_sees_keys_from_env_file_not_just_os_environ(monkeypatch):
    """os.environ 만 보면 .env 에만 있는 키를 못 봐서 임베딩이 조용히
    꺼진다 — 사용자는 하이브리드 검색을 쓴다고 믿는데 BM25 단독이 된다."""
    import os
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not os.environ.get("GOOGLE_API_KEY")
    assert engine.gemini_key_names()          # .env 쪽에는 있다
    assert "summarize_engine.gemini_key_names()" in \
        (server.__file__ and open(server.__file__, encoding="utf-8").read())
