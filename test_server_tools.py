"""server.py 의 도구·수집 경로 — 네트워크 없이 돈다 (2026-09-02).

왜 지금인가: §8-30(스로틀 3벌 통합)의 종결 조건이 "server 커버리지 60%"다.
`code_finder`(58%)·`server`(39%) 를 가로지르는 리팩토링을 회귀 그물 없이
하면 안 된다는 판단이었고, 그물을 먼저 짜는 게 이 파일이다.

부수 효과가 더 중요할 수 있다: 지금까지 server 의 결함(S2 키가 .env 에만
있어 한 번도 안 쓰인 것, 철회 sweep 이 큐를 안 비운 것)은 전부 **출력을
보다가** 우연히 찾았다. 테스트가 있었으면 더 일찍 잡혔을 것들이다.
"""

import json
import sqlite3

import asyncio

import httpx
import pytest

import server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(server, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(server, "TEXT_DIR", tmp_path / "text")
    monkeypatch.setattr(server, "SUMMARY_DIR", tmp_path / "summaries")
    monkeypatch.setattr(server, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(server, "REPRO_DIR", tmp_path / "repro")
    for d in ("pdfs", "text", "summaries", "images", "repro"):
        (tmp_path / d).mkdir(exist_ok=True)
    server._init_storage()
    return tmp_path


def _seed_paper(arxiv_id="2608.1", title="논문", text="숫자 42 가 본문에 있다."):
    path = server.TEXT_DIR / f"{arxiv_id}.txt"
    path.write_text(text, encoding="utf-8")
    with server._db() as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, text_path, fetched_at) "
                    "VALUES (?,?,?,?)", (arxiv_id, title, str(path), server._now()))
    return arxiv_id


# ---------------------------------------------------------------- 인증 헤더


def test_s2_key_is_read_from_env_file_not_just_os_environ(monkeypatch):
    """2026-09-02 실측: S2_API_KEY 가 .env 에만 있는데 os.environ 으로 읽어서
    **한 번도 안 쓰였다.** 그동안 전부 비인증 호출이었다(공유 풀이라 한도가
    훨씬 빡빡하다). GOOGLE_API_KEY 임베딩 게이트와 똑같은 실수였다."""
    monkeypatch.setattr(server.summarize_engine, "ENV", {"S2_API_KEY": "k"})
    assert server._s2_headers() == {"x-api-key": "k"}


def test_no_s2_key_sends_no_auth_header(monkeypatch):
    monkeypatch.setattr(server.summarize_engine, "ENV", {})
    assert server._s2_headers() == {}


# ---------------------------------------------------------------- S2 도구


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _stub_s2(monkeypatch, payload):
    seen = {}

    async def fake(client, params, headers, url=None):
        seen["params"] = params
        seen["url"] = url
        return _Resp(payload)

    monkeypatch.setattr(server, "_throttled_s2_get", fake)
    return seen


def test_s2_search_maps_external_ids_to_arxiv_id(monkeypatch):
    _stub_s2(monkeypatch, {"data": [
        {"title": "T", "year": 2026, "citationCount": 3,
         "externalIds": {"ArXiv": "2608.1"}, "abstract": "A"}]})
    out = json.loads(asyncio_run(server.s2_search_papers(
        server.S2SearchInput(query="defect detection"))))
    assert out["count"] == 1
    assert out["papers"][0]["arxiv_id"] == "2608.1"


def test_s2_search_year_filter_is_passed_through(monkeypatch):
    seen = _stub_s2(monkeypatch, {"data": []})
    asyncio_run(server.s2_search_papers(
        server.S2SearchInput(query="q", year_from=2024)))
    assert seen["params"]["year"] == "2024-"


def test_citation_graph_returns_edge_label(monkeypatch):
    _stub_s2(monkeypatch, {"data": [
        {"citedPaper": {"title": "Base", "year": 2020, "citationCount": 99,
                        "externalIds": {}, "abstract": ""}}]})
    out = json.loads(asyncio_run(server._s2_citation_graph("2608.1", 10, "references")))
    assert out["edge"] == "references"


class _BatchClient:
    """fetch_s2_tldrs 는 _throttled_s2_get 이 아니라 client.post 를 직접 쓴다
    (/paper/batch 는 POST 라서). 스로틀 구현이 세 벌인 것과 같은 결의 흔적이다."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.calls += 1

        class R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return payload

        payload = self._payload
        return R


def test_s2_tldr_batch_skips_papers_without_one():
    """없는 논문은 키가 아예 안 들어간다 — 빈 문자열을 넣으면 다이제스트가
    "S2 한줄요약: " 를 빈 채로 찍는다."""
    client = _BatchClient([{"tldr": {"text": "한 줄"}}, None])
    out = asyncio_run(server.fetch_s2_tldrs(client, ["2608.1", "2608.2"]))
    assert out == {"2608.1": "한 줄"}


def test_s2_tldr_empty_input_makes_no_call():
    client = _BatchClient([])
    assert asyncio_run(server.fetch_s2_tldrs(client, [])) == {}
    assert client.calls == 0


def test_s2_tldr_failure_returns_empty_not_raise():
    """부가 정보라 실패해도 파이프라인은 계속돼야 한다."""
    class Boom:
        async def post(self, *a, **kw):
            raise RuntimeError("S2 죽음")

    assert asyncio_run(server.fetch_s2_tldrs(Boom(), ["2608.1"])) == {}


# ---------------------------------------------------------------- 요약 저장


def test_save_summary_records_engine_and_coverage():
    """§8-25: engine 을 안 넘기면 커버리지가 NULL 이 되고 "원문 몇 % 를
    봤나"를 영영 모른다."""
    aid = _seed_paper(text="본문 문장이 하나. 두 번째 문장. 세 번째 문장.")
    out = json.loads(asyncio_run(server.save_summary(server.SaveSummaryInput(
        arxiv_id=aid, markdown="### 결론\n① 한 줄 요약 : 요약", engine="gemini"))))
    assert "saved_path" in out
    with server._db() as con:
        row = con.execute("SELECT engine, coverage_ratio FROM summaries "
                          "WHERE arxiv_id=?", (aid,)).fetchone()
    assert row["engine"] == "gemini"
    assert row["coverage_ratio"] == 1.0


def test_save_summary_leaves_coverage_null_when_engine_unknown():
    """미실측을 측정값처럼 쓰지 않는다(CLAUDE.md 8)."""
    aid = _seed_paper()
    asyncio_run(server.save_summary(server.SaveSummaryInput(
        arxiv_id=aid, markdown="### 결론\n① 한 줄 요약 : 요약")))
    with server._db() as con:
        row = con.execute("SELECT coverage_ratio FROM summaries WHERE arxiv_id=?",
                          (aid,)).fetchone()
    assert row["coverage_ratio"] is None


def test_save_summary_refuses_unknown_paper():
    out = json.loads(asyncio_run(server.save_summary(server.SaveSummaryInput(
        arxiv_id="9999.9999", markdown="x"))))
    assert "error" in out


def test_save_summary_reports_verification_without_blocking():
    """불일치가 있어도 저장은 하되 보고서에 명시한다 — KPI 목표치처럼 원문
    밖 출처의 숫자는 정당하게 불일치할 수 있다."""
    aid = _seed_paper(text="본문에는 42 만 있다.")
    out = json.loads(asyncio_run(server.save_summary(server.SaveSummaryInput(
        arxiv_id=aid, markdown="정확도 99.9% 를 달성했다 [S0001].", engine="groq"))))
    assert out["verification"]["total_numbers"] >= 1
    assert "saved_path" in out          # 막지 않는다


# ---------------------------------------------------------------- 철회


def test_refresh_retraction_skips_already_known(monkeypatch):
    """철회는 되돌아가지 않는 상태다 — 매번 다시 조회하면 크레딧만 쓴다."""
    aid = _seed_paper()
    with server._db() as con:
        con.execute("UPDATE papers SET is_retracted=0 WHERE arxiv_id=?", (aid,))
    called = []
    monkeypatch.setattr(server.retraction, "check",
                        lambda *a, **kw: called.append(1))
    assert asyncio_run(server.refresh_retraction_status(aid)) == 0
    assert called == []


def test_refresh_retraction_never_raises(monkeypatch):
    """철회 조회가 요약 저장을 막으면 안 된다."""
    aid = _seed_paper()

    async def boom(*a, **kw):
        raise RuntimeError("OpenAlex 죽음")

    monkeypatch.setattr(server.retraction, "check", boom)
    assert asyncio_run(server.refresh_retraction_status(aid)) is None


def test_refresh_retraction_on_missing_paper_returns_none():
    assert asyncio_run(server.refresh_retraction_status("9999.9999")) is None


# ---------------------------------------------------------------- 순수 헬퍼


def test_extract_images_resolves_relative_urls():
    html = '<figure><img src="x/fig1.png"><figcaption>그림 1</figcaption></figure>'
    out = server._extract_images_from_html(html, "https://arxiv.org/html/2608.1v1/")
    assert out[0]["url"].startswith("https://arxiv.org/html/2608.1v1/")
    assert "그림 1" in out[0]["label"]


def test_extract_images_falls_back_to_alt_when_no_caption():
    html = '<figure><img src="a.png" alt="대체 텍스트"></figure>'
    out = server._extract_images_from_html(html, "https://x/")
    assert out[0]["label"] == "대체 텍스트"


def test_bare_image_outside_figure_is_dropped():
    """<figure> 밖 이미지는 저자 소속기관 로고·배너일 가능성이 높다. 논문
    Figure 는 LaTeXML HTML 에서 거의 항상 <figure><figcaption> 구조다 —
    이 기준 하나로 로고를 걸러낸다."""
    assert server._extract_images_from_html('<img src="logo.png">', "https://x/") == []


def test_extract_images_on_empty_html():
    assert server._extract_images_from_html("", "https://x/") == []


def test_clean_arxiv_id_strips_urls_and_versions():
    for raw, want in [("https://arxiv.org/abs/1706.03762v5", "1706.03762"),
                      ("https://arxiv.org/html/2505.19433v1", "2505.19433"),
                      ("https://arxiv.org/pdf/2608.1.pdf", "2608.1"),
                      ("  2608.1  ", "2608.1")]:
        assert server._clean_arxiv_id(raw) == want


# ---------------------------------------------------------------- 헬퍼


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro) if hasattr(coro, "__await__") else coro


# ---------------------------------------------------------------- fetch_paper (③)
#
# server.py 에서 가장 큰 미커버 덩어리(47줄)였다. httpx.AsyncClient 를 함수
# 안에서 직접 만들기 때문에 **전송 계층(MockTransport)** 을 갈아끼운다 —
# 클라이언트를 인자로 안 받는 함수는 이 방법 아니면 목이 안 먹는다.
# (retraction sweep·S2 델타에서 테스트가 실제 API 를 쳤던 것과 같은 뿌리다:
#  함수가 클라이언트를 스스로 만들면 주입 지점이 사라진다.)

_ARXIV_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.1v1</id>
    <title>Test Paper Title</title>
    <summary>초록이다.</summary>
    <published>2026-08-28T00:00:00Z</published>
    <author><name>A. Author</name></author>
  </entry>
</feed>"""

_HTML_BODY = ("<html><body><article><p>" + ("본문 문장이다. " * 40)
              + "</p></article></body></html>")


def _mock_transport(monkeypatch, *, html_status=200):
    """arXiv API·HTML·PDF 응답을 흉내낸다."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=_ARXIV_FEED)
        if "/html/" in url:
            return httpx.Response(html_status, text=_HTML_BODY)
        if "/pdf/" in url:
            return httpx.Response(200, content=b"%PDF-1.4 fake")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr(server.httpx, "AsyncClient", patched)


def test_fetch_paper_prefers_html_over_pdf(monkeypatch):
    """HTML 이 있으면 그쪽이 낫다 — pypdf 는 2단 조판과 표를 자주 뭉개고
    그게 ⑤ 수치 검증의 거짓 불일치로 직결된다."""
    _mock_transport(monkeypatch)
    out = json.loads(asyncio_run(server.fetch_paper(
        server.FetchPaperInput(arxiv_id="2608.1"))))
    assert out["extract_method"] == "html"
    assert out["title"] == "Test Paper Title"
    assert out["text_chars"] > 0


def test_fetch_paper_falls_back_to_pdf_when_html_missing(monkeypatch):
    """HTML 제공 여부는 논문마다 다르고 투고 시점으로 예측할 수 없다 —
    날짜로 분기하지 않고 404 면 폴백한다."""
    _mock_transport(monkeypatch, html_status=404)
    monkeypatch.setattr(server, "_text_from_pdf", lambda p: "PDF 에서 뽑은 본문. " * 20)
    out = json.loads(asyncio_run(server.fetch_paper(
        server.FetchPaperInput(arxiv_id="2608.2"))))
    assert out["extract_method"] == "pdf"


def test_fetch_paper_is_idempotent(monkeypatch):
    """이미 저장된 논문이면 다시 안 받는다 — 멱등. 안 그러면 겹치는 창을
    다시 조회할 때마다 arXiv 를 또 두드린다(§8-26)."""
    _mock_transport(monkeypatch)
    first = json.loads(asyncio_run(server.fetch_paper(
        server.FetchPaperInput(arxiv_id="2608.3"))))
    calls = []
    monkeypatch.setattr(server, "_throttled_arxiv_get",
                        lambda *a, **kw: calls.append(1))
    second = json.loads(asyncio_run(server.fetch_paper(
        server.FetchPaperInput(arxiv_id="2608.3"))))
    assert second["text_path"] == first["text_path"]
    assert calls == []


# ---------------------------------------------------------------- Unpaywall


def _mock_unpaywall(monkeypatch, payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(server.httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=transport, **kw))


def test_unpaywall_returns_pdf_url_and_title(monkeypatch):
    """응답에 제목이 이미 들어 있어서 사람이 따로 안 쳐도 된다."""
    _mock_unpaywall(monkeypatch, {"best_oa_location": {"url_for_pdf": "http://x/y.pdf"},
                                  "title": "Journal Paper"})
    out = asyncio_run(server.resolve_unpaywall_pdf("10.1109/x"))
    assert out == {"url": "http://x/y.pdf", "title": "Journal Paper"}


def test_unpaywall_strips_doi_url_prefix(monkeypatch):
    """사람이 https://doi.org/... 를 통째로 붙여넣는 경우가 실제로 있다."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "u"},
                                         "title": "T"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(server.httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=transport, **kw))
    asyncio_run(server.resolve_unpaywall_pdf("https://doi.org/10.1109/x"))
    assert "10.1109/x" in seen["url"]
    assert "doi.org/10.1109" not in seen["url"].split("api.unpaywall.org")[-1].split("?")[0][1:]


def test_unpaywall_404_means_not_open_access(monkeypatch):
    """못 찾으면 None — 이 논문은 오픈액세스가 아니라는 뜻이고 수동 업로드로 간다."""
    _mock_unpaywall(monkeypatch, {}, status=404)
    assert asyncio_run(server.resolve_unpaywall_pdf("10.1/none")) is None


def test_unpaywall_without_pdf_link_is_none(monkeypatch):
    """오픈액세스 레코드는 있는데 PDF 링크가 없는 경우가 있다."""
    _mock_unpaywall(monkeypatch, {"best_oa_location": {}, "title": "T"})
    assert asyncio_run(server.resolve_unpaywall_pdf("10.1/x")) is None


# ---------------------------------------------------------------- 재시도 대기 상한 (2026-09-04)

def test_with_retry_gives_up_when_wait_budget_exceeded(monkeypatch):
    """429 대기 총합이 상한을 넘으면 더 안 기다린다. 상한이 없으면 한 호출이
    30+60+120+240=450초를 다 써서 ③ 검색 예산을 통째로 넘긴다(§8-34)."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    calls = {"n": 0}

    async def always_429():
        calls["n"] += 1
        req = httpx.Request("GET", "http://x")
        raise httpx.HTTPStatusError(
            "429", request=req, response=httpx.Response(429, request=req))

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(server._with_retry(always_429, "테스트", max_wait=50.0))

    # 30초는 자고, 다음 대기(60초)에서 30+60 > 50 이라 포기한다
    assert slept == [30.0]
    assert calls["n"] == 2


def test_with_retry_without_cap_keeps_old_behaviour(monkeypatch):
    """상한을 안 주면 종전대로 예산(RATE_LIMIT_RETRIES)까지 간다."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    async def always_429():
        req = httpx.Request("GET", "http://x")
        raise httpx.HTTPStatusError(
            "429", request=req, response=httpx.Response(429, request=req))

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(server._with_retry(always_429, "테스트"))
    assert len(slept) == server.RATE_LIMIT_RETRIES
