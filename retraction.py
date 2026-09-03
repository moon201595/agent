"""retraction.py — ⑧ 축적 시점의 철회(retraction) 여부 조회.

LLM을 전혀 안 쓴다. 외부 API 두 곳의 필드를 읽어 이진 판정만 한다 —
"기계가 위조 불가능하게 판정할 수 있는 것만 자동화한다"는 원칙에 정확히
맞는 작업이다(CLAUDE.md 7).

DB를 모른다(순수 조회 + 분류) — 저장은 server.py 가 한다. selection.py·
verify.py·hybrid_search.py 와 같은 경계 원칙이다.

## 왜 두 곳을 보나 (2026-08-28 실측)

OpenAlex 의 `is_retracted` 는 Retraction Watch 파생 데이터인데, 과거
correction·expression of concern 까지 true 로 오분류한 이력이 학술적으로
보고됐다. 그래서 true 를 "확정"이 아니라 "요주의 플래그"로 받고, Crossref
로 교차확인한다.

실측으로 확인한 Crossref 스키마: 철회된 논문에는 `updated-by` 배열이 있고
각 항목의 `type` 이 `retraction` / `correction` / `expression_of_concern` /
`erratum` 중 하나다. **정상 논문에도 `updated-by` 가 있을 수 있다**(대조군으로
쓴 Lancet 논문은 `erratum` 을 갖고 있었다) — 필드 존재 여부가 아니라 `type`
을 봐야 한다는 뜻이다.

## 실측으로 확인한 커버리지 한계

- arXiv DOI(`10.48550/arXiv.{id}`) 로 OpenAlex 싱글턴 조회: 저장소 논문
  6건 중 5건 적중. 실패한 1건은 **전날 올라온 프리프린트**로 아직 색인
  전이었다. 유명한 구논문(1706.03762)도 404 였는데, 정식 출판되면서
  출판사 DOI 로 색인돼 arXiv DOI 를 안 갖고 있기 때문이다.
- **arXiv DOI 는 Crossref 에 없다**(404 실측). arXiv 는 DataCite 등록이라
  그렇다. 즉 arXiv 논문은 교차확인 자체가 불가능하다 — 그 경우 OpenAlex
  플래그를 "확정"으로 올리지 않고 요주의(2)로 둔다.

## 비용

싱글턴 조회만 쓴다. 리스트·PDF content·vector search 엔드포인트는 호출하지
않는다 — 크레딧 단가가 10~1,000배라 무료 예산을 빠르게 태운다. 실측 헤더
기준 하루 한도는 10,000 크레딧($1)이고 싱글턴은 건당 1이므로, 하루 논문
16편이면 예산의 0.16% 다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time

import httpx
import api_usage
import pacing

# 판정 코드 — server.papers.is_retracted 컬럼에 그대로 들어간다.
NOT_RETRACTED = 0
RETRACTED = 1        # OpenAlex true + Crossref 가 retraction 으로 교차확인
SUSPECT = 2          # OpenAlex true 인데 교차확인이 안 됨(정정만 있거나 레코드 없음)
# 판정 불가는 None — "조회 못 했다"와 "정상이다"를 절대 같게 저장하지 않는다.

OPENALEX_API = "https://api.openalex.org/works/doi:"
CROSSREF_API = "https://api.crossref.org/works/"
# OpenAlex 는 공식 분당 한도를 문서로 못 박지 않았지만, S2(초당 1회)와 같은
# 보수적 간격을 쓴다 — 하루 16편 규모에선 이걸로 충분히 여유롭다.
OPENALEX_MIN_INTERVAL = 1.0
CROSSREF_MIN_INTERVAL = 1.0
_TIMEOUT = 20.0

# 간격 계산 규칙은 pacing.py 한 곳에만 있다(§8-30).
_openalex = pacing.AsyncPacer(OPENALEX_MIN_INTERVAL)
_crossref = pacing.AsyncPacer(CROSSREF_MIN_INTERVAL)

# arXiv ID 형태만 DOI 로 바꾼다. PDF 업로드·오픈액세스로 들어온 합성 ID
# (pdf-<hash>)는 arXiv DOI 가 없으므로 조회 자체를 건너뛴다.
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$|^[a-z-]+(\.[A-Z]{2})?/\d{7}$")


def arxiv_doi(arxiv_id: str) -> str | None:
    """arXiv ID → DataCite DOI. arXiv ID 형태가 아니면 None."""
    return f"10.48550/arXiv.{arxiv_id}" if _ARXIV_ID_RE.match(arxiv_id) else None


async def _throttled_get(
    client: httpx.AsyncClient, url: str, params: dict, headers: dict,
    pacer: pacing.AsyncPacer,
) -> httpx.Response:
    """server._throttled_s2_get 과 같은 발상 — 호출 간 최소 간격을 프로세스
    전역에서 강제한다. 재시도는 하지 않는다: 철회 조회는 실패해도 파이프라인을
    멈추면 안 되는 부가 정보라, 한 번 실패하면 그냥 None(미조회)으로 두고
    다음 실행에서 다시 본다(NULL 자체가 재시도 큐 역할을 한다)."""
    async with pacer.gate():
        resp = await client.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    provider = "openalex" if "openalex" in url else "crossref"
    api_usage.record(provider, "ok" if resp.status_code == 200 else str(resp.status_code))
    return resp


async def openalex_is_retracted(
    client: httpx.AsyncClient, doi: str, api_key: str,
) -> bool | None:
    """OpenAlex 싱글턴 1회. 레코드가 없거나(404) 오류면 None."""
    resp = await _throttled_get(
        client, OPENALEX_API + doi,
        {"api_key": api_key, "select": "id,is_retracted"}, {},
        _openalex,
    )
    if resp.status_code != 200:
        return None
    value = resp.json().get("is_retracted")
    return value if isinstance(value, bool) else None


async def crossref_update_types(
    client: httpx.AsyncClient, doi: str, mailto: str | None = None,
) -> list[str] | None:
    """Crossref `updated-by` 의 type 목록. 레코드가 없으면 None(빈 리스트와
    구분해야 한다 — 빈 리스트는 "갱신 이력이 없다", None 은 "조회 불가").

    mailto 는 Crossref 의 "polite pool" 예의 파라미터로 **선택**이다. 없으면
    아예 안 보낸다 — 개인 이메일 주소를 외부 서비스에 자동으로 흘리지 않기
    위해서다. 넣고 싶으면 환경변수 CROSSREF_MAILTO 로 명시적으로 준다.
    """
    params = {"mailto": mailto} if mailto else {}
    ua = f"paper-harness/1.0 (mailto:{mailto})" if mailto else "paper-harness/1.0"
    resp = await _throttled_get(
        client, CROSSREF_API + doi, params, {"User-Agent": ua},
        _crossref,
    )
    if resp.status_code != 200:
        return None
    message = resp.json().get("message", {})
    return [u.get("type", "") for u in message.get("updated-by", [])]


def classify(is_retracted: bool | None, update_types: list[str] | None) -> int | None:
    """두 조회 결과를 판정 코드로. 순수 함수라 네트워크 없이 테스트된다.

    OpenAlex 가 true 인데 Crossref 로 확정하지 못한 경우(정정만 있거나 레코드
    자체가 없거나)를 RETRACTED 로 올리지 않는 것이 이 함수의 핵심이다 —
    "철회됨"은 논문에 붙일 수 있는 가장 무거운 딱지라, 확신이 없으면
    SUSPECT 로 낮춰 사람이 보게 한다.
    """
    if is_retracted is None:
        return None
    if not is_retracted:
        return NOT_RETRACTED
    if update_types and "retraction" in update_types:
        return RETRACTED
    return SUSPECT


async def check(
    client: httpx.AsyncClient, arxiv_id: str, api_key: str | None,
    mailto: str | None = None,
) -> int | None:
    """전체 흐름. 어떤 실패에도 예외를 올리지 않는다 — 철회 조회가 요약
    저장을 막으면 안 된다(부가 정보이지 파이프라인 게이트가 아니다)."""
    if not api_key:
        return None
    doi = arxiv_doi(arxiv_id)
    if doi is None:
        return None
    try:
        is_retracted = await openalex_is_retracted(client, doi, api_key)
    except Exception:  # noqa: BLE001 — 네트워크·JSON 오류 전부 "미조회"로 떨어뜨린다
        return None
    if not is_retracted:
        # False 면 교차확인이 필요 없고, None 이면 확인할 근거 자체가 없다.
        return classify(is_retracted, None)
    try:
        update_types = await crossref_update_types(client, doi, mailto)
    except Exception:  # noqa: BLE001
        update_types = None
    return classify(is_retracted, update_types)
