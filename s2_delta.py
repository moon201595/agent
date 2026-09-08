"""s2_delta.py — Semantic Scholar 를 두 번째 델타 검색 소스로 (2026-09-02).

왜 필요한가(실측): 팀 표적 분야는 arXiv 가 아니라 저널에 실린다.
최근 5일 창을 S2 로 조회한 결과 —

    'surface inspection'   표본 100건 중 arXiv 있음 3건 · **없음 97건**
    'on-device inference'  표본  46건 중 arXiv 있음 2건 · **없음 44건**

즉 arXiv 단일 소스로는 그 분야 문헌의 3~4% 만 보고 있었다. 키워드를 아무리
다듬어도 못 보는 96% 는 그대로다. 그리고 그 97건이 **전부 openAccessPdf 와
DOI 를 갖고 있어서**(실측) 기존 fetch_pdf_from_url 경로로 본문까지 받을 수 있다.

`search_runs.source` 컬럼 주석에 "S2 는 day-level delta 불가(2026-08-24
리뷰)"라고 적혀 있었는데 **틀린 기록이다.** 그 리뷰는 `year` 파라미터만 보고
판단한 것으로 보인다 — S2 Graph API 는 `publicationDateOrYear` 로
`2026-08-28:2026-09-02` 형식의 날짜 범위를 지원한다(실측 확인).

**delta_search.collect_since 를 안 쓴다.** 그 함수는 "최신순 정렬 + 경계
감지"를 전제하는데, S2 `/paper/search` 는 관련도 순이라 경계 감지가 성립하지
않는다. 대신 `publicationDateOrYear` 가 **서버에서 하드 필터**로 걸리므로
(실측: 응답이 전부 창 안이었다) 페이지를 끝까지 받으면 그게 곧 답이다.

S2 검색은 arXiv 처럼 정확 필드 매칭이 아니라 관련도 매칭이라 결과가 헐겁다.
정밀도는 profile_scoring 의 단어 경계 정규식이 뒤에서 회수한다 — 이 모듈은
후보를 넓게 가져오는 역할만 한다.
"""

from __future__ import annotations

import http_client
import time
from datetime import datetime

import httpx

from dataclasses import dataclass


# 한 번에 받아올 건수(= S2 의 페이지 크기). S2 는 limit 상한이 100 이다.
PER_KEYWORD_LIMIT = 100

# 키워드당 최대 페이지 수(2026-09-07, §8-70). 그전에는 페이지 개념이 아예 없어
# **101번째가 있는지 확인하지도 않고 "done" 으로 기록**했다 — 못 본 것을
# 봤다고 말하는 것이라 그 자체로 규칙 8 위반이다.
#
# **2026-09-08 정정.** 처음 이 주석은 "done 은 래칫을 전진시키므로 그 창의
# 나머지 논문은 다시는 조회되지 않는다"고 썼는데 **둘 다 사실이 아니다.**
# (1) 일일 검색의 시작 시각은 `research_profile.next_since` 가 정하는데
#     `source` 기본값이 "arxiv" 라 **S2 의 status 는 커서에 한 번도 안 쓰인다.**
# (2) arXiv 쪽도 "영영"이 아니다 — `next_since` 가 `min(커서, 지금-5일)` 을
#     쓰므로(REINDEX_SAFETY_DAYS=5) 매일 도는 한 최근 5일은 늘 다시 훑는다.
#     실측(2026-09-08): 다음 검색 시작이 정확히 5일 전이었다.
# 근거를 확인하지 않고 주장을 적은 것이고, 이 저장소가 "주석이 코드보다 강한
# 주장을 한다"고 적어 둔 그 함정이다. status 를 정직하게 남기는 이유는 래칫이
# 아니라 **기록이 사실과 맞아야 하기 때문**이다. 남은 구멍(S2 가 놓친 구간이
# 어디에도 반영되지 않는다)은 §8 에 적었다.
#
# 그렇다고 끝까지 넘기지는 않는다. 페이지 하나가 호출 하나이고 S2 는 429 를
# 잘 낸다(§8-34). 3페이지 = 키워드당 최대 300편이면, 실측(09-03: 6키워드
# 266편)의 창 전체보다 크다 — 정상적인 날에는 첫 페이지에서 끝난다. 상한에
# 걸려 못 본 게 남으면 그건 **truncated 로 표시해 status 를 partial 로 만든다**
# — 다음 실행이 같은 창을 다시 본다. 못 본 것을 봤다고 하지 않는 게 요점이다.
MAX_PAGES_PER_KEYWORD = 3

# S2 는 offset+limit 이 1000 을 못 넘는다. 넘기면 400 이 온다.
S2_OFFSET_CEILING = 1000

# ③ 검색에 쓸 벽시계 예산(초). Deep Layer 에는 예산이 있는데
# (DEEP_LAYER_BUDGET_SECONDS) **검색에는 없었다** — 그래서 S2 가 나쁜 날엔
# 검색만 45분을 먹었다(§8-34).
#
# 실측(2026-09-04): S2 가 사흘째 거의 모든 요청에 429 를 냈고, 키워드마다
# 재시도 사슬(30+60+120+240 = 450초)이 붙어 6키워드면 최악 45분이다.
# 실제로 그날 스캔이 16분 넘게 ③ 를 못 빠져나갔고, 09-03 00:44 실행은
# 종료 기록조차 없다.
#
# **이 교환이 애초에 나쁘다.** arXiv 델타는 30초에 182편을 준다. 같은 창에서
# S2 를 위해 45분을 더 쓰는 건, 얻는 것(수백 편 후보 중 최종 6편에 들지
# 안 들지 모를 몇 편)에 비해 값이 안 맞는다. 사용자 지적이 정확했다 —
# "거의 최근 논문 몇 개 뽑는 건데 뭐 이리 오래 걸려".
#
# 300초로 잡는다: S2 가 정상인 날 6키워드는 1분이면 끝나므로(실측 09-03:
# 266편을 1분) 전혀 안 걸리고, 나쁜 날에만 잘린다. 잘려도 arXiv 결과는
# 그대로라 다이제스트가 빈손이 되지 않는다.
SEARCH_BUDGET_SECONDS = 300.0

# S2 는 **표적 계층 키워드에만** 쓴다(기본값). 근거:
#
# - 커버리지 격차가 거기서 난다. 실측에서 'surface inspection'·'defect
#   detection' 같은 검사 도메인은 arXiv 커버리지가 3~4% 인 반면, physical AI·
#   vision-language-action 같은 동향어는 원래 arXiv 중심 분야다. S2 를 쓸
#   값어치가 계층마다 다르다.
# - 비용이 든다. 실측(2026-09-02): 키워드 3개에 166초 걸렸다(S2 429 재시도
#   포함). 27개 전부면 25분이라 Deep Layer 예산(40분)을 잠식한다.
#
# 필요하면 호출부가 keywords 를 직접 넘겨 이 기본값을 무시할 수 있다.
S2_MIN_KEYWORD_WEIGHT = 1.0
_FIELDS = "title,abstract,publicationDate,externalIds,openAccessPdf,venue,citationCount"


def _window(since: datetime, until: datetime) -> str:
    """S2 publicationDateOrYear 형식. 날짜 단위라 시각은 버린다 — 그만큼
    창이 넓어지지만, 좁히려다 경계의 논문을 놓치는 것보다 낫다."""
    return f"{since.date().isoformat()}:{until.date().isoformat()}"


def _to_paper(item: dict) -> dict | None:
    """S2 응답 한 건을 파이프라인이 쓰는 모양으로. 제목이 없으면 버린다 —
    스코어링도 다이제스트도 제목 없이는 아무것도 못 한다."""
    title = (item.get("title") or "").strip()
    if not title:
        return None
    external = item.get("externalIds") or {}
    date = item.get("publicationDate")
    oa = item.get("openAccessPdf") or {}
    return {
        # arXiv 에도 있는 논문이면 그 ID 를 쓴다 — selection.dedupe 가 이걸로
        # arXiv 결과와 같은 논문을 합친다(중복 요약 방지).
        "arxiv_id": external.get("ArXiv"),
        "doi": external.get("DOI"),
        "title": title,
        "abstract": item.get("abstract") or "",
        "published": f"{date}T00:00:00Z" if date else None,
        "venue": item.get("venue") or "",
        "citation_count": item.get("citationCount"),
        "open_access_pdf": oa.get("url") or "",
        "source": "s2",
    }


@dataclass
class KeywordSearch:
    """키워드 하나 조회의 결과 전부.

    리스트만 돌려주면 "다 봤다"와 "상한에 걸려 여기까지"를 구분할 수 없다 —
    그 구분이 없어서 §8-70 의 거짓 done 이 생겼다. 값이 셋이 되면 이름을
    붙인다(trend_report.ReferenceScan 과 같은 이유).
    """
    papers: list[dict]
    total: int | None       # S2 가 알려준 창 안 전체 건수 (모르면 None)
    truncated: bool         # 더 있는데 못 가져왔다
    # **왜** 못 가져왔나. 2026-09-07 라이브 실행에서 잡았다 — 로그가
    # "상한까지 받고도 남았다"라고 말했는데 실제로는 429 재시도(30+60+120초)로
    # 시간 예산이 끝난 것이었다. 원인이 다르면 처방도 다르다(페이지 상한은
    # 우리가 정한 값이고, 예산 소진은 S2 가 나쁜 날이다). 안 가른 채로 두면
    # 로그를 읽고 엉뚱한 걸 고치게 된다.
    reason: str | None = None   # 'page_cap' | 'budget' | 'offset_ceiling' | 'page_failure' 


async def search_keyword_since(
    client: httpx.AsyncClient, keyword: str, since: datetime, until: datetime,
    limit: int = PER_KEYWORD_LIMIT, max_wait: float | None = None,
    max_pages: int = MAX_PAGES_PER_KEYWORD,
) -> KeywordSearch | None:
    """키워드 하나로 창 안의 논문을 받는다.

    **None(실패)과 빈 리스트(결과 없음)를 구분해서 돌려준다.** 둘을 같게
    다루면 "S2 가 죽었다"와 "정말 새 논문이 없다"를 구분할 수 없고, 그건
    이 프로젝트가 여러 번 지켜온 구분이다(검증 데이터 없음 vs 통과,
    재현 기록없음 vs 실패). 처음 짤 때 `if not found` 로 뭉갰다가
    테스트가 잡았다.

    실패해도 예외를 올리지 않는다 — 한 키워드가 죽어도 나머지는 살아야
    한다(scan_all_profiles 의 프로필 간 실패 격리와 같은 원칙)."""
    page_size = min(limit, 100)
    papers: list[dict] = []
    fetched = 0                # **원시** 건수. total 과 비교할 대상은 이쪽이다.
    total: int | None = None
    truncated = False
    # max_wait 는 이 키워드에 남은 전체 대기 예산이다. 페이지마다 그대로 넘기면
    # 3페이지가 각자 전체 예산을 쓸 수 있어 SEARCH_BUDGET_SECONDS 가 전체 검색
    # 시간을 못 막는다 — 호출 수를 늘리면서 생긴 문제라, 기한 하나를 잡고
    # 페이지마다 남은 만큼만 준다.
    deadline = None if max_wait is None else time.monotonic() + max_wait
    for page in range(max_pages):
        offset = page * page_size
        if offset + page_size > S2_OFFSET_CEILING:
            # S2 가 offset+limit 1000 을 넘기면 400 을 준다. 우리 상한이 아니다.
            return KeywordSearch(papers, total, True, "offset_ceiling")
        remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        if page and remaining is not None and remaining <= 0:
            # 예산이 끝났다. 남은 게 있는지 모르므로 "다 봤다"고 하지 않는다.
            return KeywordSearch(papers, total, True, "budget")
        params = {
            "query": keyword,
            "publicationDateOrYear": _window(since, until),
            "fields": _FIELDS,
            "limit": page_size,
        }
        if offset:
            params["offset"] = offset
        try:
            resp = await http_client.throttled_s2_get(client, params, http_client.s2_headers(),
                                                  max_wait=remaining if page else max_wait)
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            if papers:
                # 첫 페이지는 받았고 뒤에서 죽었다 — 받은 것은 살리되 "다 봤다"고
                # 하지 않는다. 전부 버리면 성한 결과까지 잃는다.
                print(f"  [경고] S2 검색 실패({keyword}, {page + 1}쪽): {type(e).__name__} "
                      f"— 앞 {len(papers)}편은 살린다")
                return KeywordSearch(papers, total, True, "page_failure")
            print(f"  [경고] S2 검색 실패({keyword}): {type(e).__name__}")
            return None
        items = payload.get("data") or []
        fetched += len(items)
        if total is None and isinstance(payload.get("total"), int):
            total = payload["total"]
        # 제목 없는 항목은 버린다(스코어링도 다이제스트도 못 한다). **버린 것까지
        # 세서** total 과 비교해야 한다 — 필터 후 건수로 비교하면 제목 없는 항목
        # 하나 때문에 다 훑은 창이 "잘렸다"가 되고, 래칫이 헛돌아 같은 창을
        # 계속 다시 본다.
        papers += [p for p in (_to_paper(i) for i in items) if p]
        if total is not None and offset + len(items) >= total:
            break                  # 전체 건수를 다 받았다 — 빈 페이지를 또 부르지 않는다
        if len(items) < page_size:
            break                  # 창을 다 훑었다
    else:
        # 페이지 상한을 다 쓰고도 끝을 못 봤다 = 더 남았을 수 있다.
        # (total 로 끝을 확인했으면 위에서 break 로 빠져나가 여기 안 온다.)
        return KeywordSearch(papers, total, True, "page_cap")
    return KeywordSearch(papers, total, truncated)


async def find_new_papers_since(
    client: httpx.AsyncClient, keywords: list[str], since: datetime, until: datetime,
    limit: int = PER_KEYWORD_LIMIT, budget_s: float = SEARCH_BUDGET_SECONDS,
) -> dict:
    """핵심 키워드 전부로 창을 훑어 합친 결과. **시간 예산 안에서만.**

    arXiv 쪽 find_new_papers_since 와 **모양을 맞춘다** — 호출부가 두 소스를
    같은 방식으로 다룰 수 있어야 한다(status/papers/query).

    같은 논문이 여러 키워드에서 나오는 건 흔하다. 여기서 1차로 합치고,
    arXiv 결과와의 병합은 호출부가 selection.dedupe 로 한다 — 소스 간 병합
    로직을 두 곳에 두지 않는다.

    예산에 걸리면 **거기까지 모은 것으로** 끝낸다(status="partial").
    조용히 전체인 척하지 않는다 — 몇 개 키워드까지 봤는지 같이 돌려준다.
    """
    seen: set[str] = set()
    papers: list[dict] = []
    failed = 0
    truncated = 0
    started = time.monotonic()
    searched = 0
    for keyword in keywords:
        if time.monotonic() - started > budget_s:
            print(f"  [S2] 시간 예산 {budget_s:.0f}초 초과 — 키워드 "
                  f"{searched}/{len(keywords)}개까지 보고 멈춘다", flush=True)
            break
        searched += 1
        # 남은 예산을 그대로 이 호출의 대기 상한으로 준다 — 한 키워드가
        # 예산을 통째로 넘기지 못하게(§8-34).
        remaining = budget_s - (time.monotonic() - started)
        found = await search_keyword_since(client, keyword, since, until, limit,
                                           max_wait=max(remaining, 0.0))
        if found is None:          # 실패 — 결과 0건과 구분한다
            failed += 1
            continue
        if found.truncated:
            truncated += 1
            why = {
                "page_cap": f"페이지 상한({MAX_PAGES_PER_KEYWORD}쪽)까지 받고도 남았다",
                "budget": "시간 예산이 끝나 남은 쪽을 못 받았다",
                "offset_ceiling": "S2 offset 상한(1000)에 걸렸다",
                "page_failure": "뒤 페이지가 실패해 앞쪽만 살렸다",
            }.get(found.reason, "다 받지 못했다")
            print(f"  [S2] '{keyword}' — {why}"
                  f"{f' (전체 {found.total}편 중 {len(found.papers)}편)' if found.total else ''}"
                  f" — 이 실행은 partial 로 기록한다", flush=True)
        for paper in found.papers:
            key = (paper.get("arxiv_id") or paper.get("doi")
                   or paper["title"].lower())
            if key in seen:
                continue
            seen.add(key)
            papers.append(paper)

    # 키워드가 **전부** 실패했으면 "결과 없음"과 구분해야 한다 — 전자는
    # S2 가 죽은 것이고 후자는 정말 새 논문이 없는 것이다.
    #
    # 2026-09-07 §8-70 고침: `failed` 가 partial 조건에 없어서 **6개 중 5개가
    # 실패해도 searched == 6 이면 done** 이었다. 잘린 키워드(truncated)도 마찬가지다.
    #
    # **2026-09-08 정정.** 여기 원래 "done 은 래칫을 전진시키므로 그 창의 나머지
    # 논문은 다시 조회되지 않는다"고 적혀 있었는데 사실이 아니다 — 위
    # MAX_PAGES_PER_KEYWORD 주석 참고. S2 의 status 는 커서 계산에 안 쓰이고,
    # 5일 안전 창이 최근 구간을 어차피 다시 훑는다. **그래도 정직하게 남긴다** —
    # 기록이 사실과 달라지면 나중에 그 기록으로 아무것도 설명할 수 없기 때문이다.
    if keywords and failed == len(keywords):
        status = "failed"
    elif searched < len(keywords) or failed or truncated:
        status = "partial"          # 예산·실패·상한 중 하나로 다 못 봤다
    else:
        status = "done"
    return {
        "papers": papers,
        "status": status,
        "query": f"S2 keywords×{searched}/{len(keywords)} {_window(since, until)}",
        "keywords_failed": failed,
        "keywords_searched": searched,
        "keywords_truncated": truncated,
    }


# 서로 거의 같은 결과를 돌려주는 키워드 쌍. **왼쪽만 질의하고 오른쪽은 뺀다.**
#
# 2026-09-03 실측(60일 창, 키워드당 상한 100편)으로 겹침을 직접 쟀다:
#
#   'on-sensor computing' 100편 중 86편(86%)이 'in-sensor computing' 에도 있음
#   'micro defect detection' 100편 중 61편(61%)이 'defect detection' 에도 있음
#   'surface inspection'    100편 중 13편(13%)만 'defect detection' 에도 있음
#   'few-shot defect detection' 100편 중 5편(5%)만 'defect detection' 에도 있음
#
# **처음 세운 가설("키워드 7개인데 개념은 4개")은 틀렸다.** 문구가 포함관계면
# 결과도 포함관계일 거라 봤는데, S2 검색은 부분문자열 매칭이 아니라 관련도
# 검색이라 그렇게 안 움직인다 — 'few-shot defect detection' 은 few-shot 학습
# 문헌을 데려와서 겹침이 5% 다. 합집합 443편 / 단순합 614편으로 **72%가 고유**다.
# 그래서 넓은 키워드로 좁은 것을 대체하지 않는다. 지우면 논문을 잃는다.
#
# 86% 인 한 쌍만 뺀다. 잃는 건 그 개념 합집합 114편 중 14편(12%)이고,
# 얻는 건 S2 호출 하나다 — 429 사슬 하나가 최대 450초였다.
#
# **채점에서는 안 뺀다.** 27개 core_topics 로 점수를 매기는 건 전부 로컬이라
# 공짜다. 줄이는 건 나가는 질의뿐이다.
S2_REDUNDANT_KEYWORDS = {
    "on-sensor computing": "in-sensor computing",
}


def keywords_for_s2(profile: dict, min_weight: float = S2_MIN_KEYWORD_WEIGHT) -> list[str]:
    """S2 로 **질의할** 키워드 — 표적 계층(가중치 >= 1.0) 중 중복 제외.

    채점 키워드와 다르다. 채점은 core_topics 전부를 쓰고 로컬이라 공짜지만,
    질의는 한 개마다 S2 호출 하나이고 그게 429 의 원인이다.

    가중치를 안 준 프로필(구형)은 전부 1.0 으로 보므로 자연히 전 키워드가
    대상이 된다 — 하위 호환.
    """
    weights = profile.get("core_weights") or {}
    picked = [kw for kw in profile.get("core_topics", [])
              if float(weights.get(kw, 1.0)) >= min_weight]
    # 대신할 키워드가 실제로 질의 목록에 있을 때만 뺀다 — 없으면 그 개념을
    # 통째로 잃는다.
    return [kw for kw in picked
            if S2_REDUNDANT_KEYWORDS.get(kw) not in picked]
