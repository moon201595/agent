"""retraction.py 단위 테스트 — 네트워크 없이 돈다(classify는 순수 함수,
API 호출부는 httpx 트랜스포트를 가짜로 바꿔 검증한다)."""

import asyncio

import httpx

import retraction


# ---------------------------------------------------------------- classify (순수 함수)


def test_classify_not_retracted():
    assert retraction.classify(False, None) == retraction.NOT_RETRACTED


def test_classify_confirmed_retraction_needs_crossref_agreement():
    """(a-1) OpenAlex true + Crossref가 retraction으로 확인 → 철회 확정."""
    assert retraction.classify(True, ["correction", "retraction"]) == retraction.RETRACTED


def test_classify_openalex_true_but_only_correction_is_suspect():
    """(a-2) OpenAlex의 is_retracted는 과거 correction·expression of concern까지
    true로 오분류한 이력이 보고됐다 — Crossref가 정정만 말하면 "철회 확정"으로
    올리지 않는다. "철회됨"은 논문에 붙일 수 있는 가장 무거운 딱지다."""
    assert retraction.classify(True, ["correction"]) == retraction.SUSPECT
    assert retraction.classify(True, ["expression_of_concern"]) == retraction.SUSPECT
    assert retraction.classify(True, ["erratum"]) == retraction.SUSPECT


def test_classify_openalex_true_without_crossref_record_is_suspect():
    """실측(2026-08-28): arXiv DOI는 Crossref에 없다(404) — arXiv 논문은
    교차확인 자체가 불가능하므로 확정으로 올리지 않는다."""
    assert retraction.classify(True, None) == retraction.SUSPECT
    assert retraction.classify(True, []) == retraction.SUSPECT


def test_classify_unknown_stays_none():
    """(a-3) 조회 실패는 "정상"이 아니라 "모름"이다 — 절대 0으로 떨어뜨리지
    않는다(CLAUDE.md 8)."""
    assert retraction.classify(None, None) is None
    assert retraction.classify(None, ["retraction"]) is None


# ---------------------------------------------------------------- DOI 변환


def test_arxiv_doi_for_modern_id():
    assert retraction.arxiv_doi("2608.27184") == "10.48550/arXiv.2608.27184"


def test_arxiv_doi_for_legacy_id():
    assert retraction.arxiv_doi("cs.AI/0701001") == "10.48550/arXiv.cs.AI/0701001"


def test_arxiv_doi_none_for_synthetic_pdf_id():
    """PDF 업로드·오픈액세스로 들어온 합성 ID는 arXiv DOI가 없다 — 조회를
    아예 건너뛰어 크레딧을 낭비하지 않는다."""
    assert retraction.arxiv_doi("pdf-5bd2ec925e") is None


# ---------------------------------------------------------------- API 호출부 (가짜 트랜스포트)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_openalex_returns_none_on_404():
    """(a-4) 레코드가 없으면(최신 프리프린트 등) None — 실측에서 실제로
    전날 올라온 논문이 404였다."""
    async def main():
        async with _client(lambda req: httpx.Response(404, text="not found")) as c:
            return await retraction.openalex_is_retracted(c, "10.48550/arXiv.9999.99999", "k")

    assert asyncio.run(main()) is None


def test_openalex_reads_is_retracted_field():
    async def main():
        def handler(req):
            assert "api_key=k" in str(req.url)  # 키가 실제로 실려 나간다
            return httpx.Response(200, json={"id": "W1", "is_retracted": True})
        async with _client(handler) as c:
            return await retraction.openalex_is_retracted(c, "10.1/x", "k")

    assert asyncio.run(main()) is True


def test_crossref_extracts_update_types():
    """실측 스키마: updated-by[].type. 정상 논문도 updated-by를 가질 수 있어
    (대조군이 erratum을 갖고 있었다) 존재가 아니라 type을 봐야 한다."""
    async def main():
        payload = {"message": {"updated-by": [
            {"type": "correction"}, {"type": "retraction"},
        ]}}
        async with _client(lambda req: httpx.Response(200, json=payload)) as c:
            return await retraction.crossref_update_types(c, "10.1/x")

    assert asyncio.run(main()) == ["correction", "retraction"]


def test_crossref_distinguishes_missing_record_from_no_updates():
    """None(조회 불가)과 []( 갱신 이력 없음)은 다른 사실이다."""
    async def missing():
        async with _client(lambda req: httpx.Response(404)) as c:
            return await retraction.crossref_update_types(c, "10.1/x")

    async def empty():
        async with _client(lambda req: httpx.Response(200, json={"message": {}})) as c:
            return await retraction.crossref_update_types(c, "10.1/x")

    assert asyncio.run(missing()) is None
    assert asyncio.run(empty()) == []


def test_crossref_omits_mailto_when_not_configured():
    """개인 이메일을 외부 서비스에 자동으로 흘리지 않는다 — mailto는 명시
    설정했을 때만 나간다."""
    seen = {}

    async def main():
        def handler(req):
            seen["url"] = str(req.url)
            seen["ua"] = req.headers.get("user-agent", "")
            return httpx.Response(200, json={"message": {}})
        async with _client(handler) as c:
            return await retraction.crossref_update_types(c, "10.1/x")

    asyncio.run(main())
    assert "mailto" not in seen["url"]
    assert "@" not in seen["ua"]


def test_check_skips_when_no_api_key():
    """키가 없으면 네트워크를 아예 안 탄다(크레딧·시간 낭비 방지)."""
    called = []

    async def main():
        def handler(req):
            called.append(1)
            return httpx.Response(200, json={})
        async with _client(handler) as c:
            return await retraction.check(c, "2608.27184", api_key=None)

    assert asyncio.run(main()) is None
    assert called == []


def test_check_does_not_call_crossref_when_not_retracted():
    """is_retracted=False면 교차확인이 불필요하다 — 호출을 아낀다."""
    hosts = []

    async def main():
        def handler(req):
            hosts.append(req.url.host)
            return httpx.Response(200, json={"id": "W1", "is_retracted": False})
        async with _client(handler) as c:
            return await retraction.check(c, "2608.27184", api_key="k")

    assert asyncio.run(main()) == retraction.NOT_RETRACTED
    assert hosts == ["api.openalex.org"]


def test_check_cross_checks_crossref_when_openalex_flags_retraction():
    hosts = []

    async def main():
        def handler(req):
            hosts.append(req.url.host)
            if req.url.host == "api.openalex.org":
                return httpx.Response(200, json={"is_retracted": True})
            return httpx.Response(200, json={"message": {"updated-by": [{"type": "retraction"}]}})
        async with _client(handler) as c:
            return await retraction.check(c, "2608.27184", api_key="k")

    assert asyncio.run(main()) == retraction.RETRACTED
    assert hosts == ["api.openalex.org", "api.crossref.org"]


def test_check_never_raises_on_network_error():
    """(b) 조회 실패가 예외로 전파되면 요약 저장을 막는다 — 절대 안 된다."""
    async def main():
        def handler(req):
            raise httpx.ConnectError("network down")
        async with _client(handler) as c:
            return await retraction.check(c, "2608.27184", api_key="k")

    assert asyncio.run(main()) is None


def test_check_survives_crossref_failure_and_falls_back_to_suspect():
    """OpenAlex는 철회라는데 Crossref가 죽어 있으면 — 정보를 버리지 않고
    요주의로 남긴다."""
    async def main():
        def handler(req):
            if req.url.host == "api.openalex.org":
                return httpx.Response(200, json={"is_retracted": True})
            raise httpx.ConnectError("crossref down")
        async with _client(handler) as c:
            return await retraction.check(c, "2608.27184", api_key="k")

    assert asyncio.run(main()) == retraction.SUSPECT
