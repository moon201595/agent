"""S2 두 번째 소스 — 네트워크 없이 돈다 (2026-09-02).

왜 붙였나(실측): 팀 표적 분야는 arXiv 가 아니라 저널에 실린다.
최근 5일 창에서 'surface inspection' 표본 100건 중 **97건이 arXiv 에 없었고**,
그 97건이 전부 openAccessPdf 와 DOI 를 갖고 있었다. arXiv 단일 소스로는
그 분야 문헌의 3~4% 만 보고 있었다는 뜻이다.

실전 확인(키워드 3개, 5일): 232편 중 arXiv 밖 224편. 우리 스코어러를 통과한
8편의 1위가 `PhyHGNet: Physics guided micro defect detection`(Solar Energy)로
표적어를 2개 맞힌 논문이었다 — arXiv 만 보면 못 보는 것이다.
"""

import asyncio
from datetime import datetime, timezone

import s2_delta


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- 응답 매핑


def test_arxiv_id_is_carried_so_dedupe_can_merge_with_arxiv_results():
    """arXiv 에도 있는 논문은 그 ID 를 달고 와야 selection.dedupe 가 arXiv
    결과와 합친다 — 안 그러면 같은 논문을 두 번 요약한다."""
    p = s2_delta._to_paper({"title": "T", "externalIds": {"ArXiv": "2608.28070"},
                            "publicationDate": "2026-08-28"})
    assert p["arxiv_id"] == "2608.28070"


def test_journal_paper_has_no_arxiv_id_but_keeps_doi_and_pdf():
    p = s2_delta._to_paper({
        "title": "Metal Surface Defect Detection", "externalIds": {"DOI": "10.1109/x"},
        "publicationDate": "2026-09-01", "openAccessPdf": {"url": "http://x/y.pdf"},
        "venue": "IEEE Instrumentation & Measurement"})
    assert p["arxiv_id"] is None
    assert p["doi"] == "10.1109/x"
    assert p["open_access_pdf"] == "http://x/y.pdf"
    assert p["venue"] == "IEEE Instrumentation & Measurement"


def test_published_is_shaped_for_the_scorer():
    """profile_scoring.recency_score 가 '%Y-%m-%dT%H:%M:%SZ' 를 파싱한다 —
    형식이 어긋나면 최신성이 조용히 None 으로 떨어진다."""
    import profile_scoring
    p = s2_delta._to_paper({"title": "T", "publicationDate": "2026-08-28"})
    assert profile_scoring.recency_score(p["published"], 30) is not None


def test_paper_without_title_is_dropped():
    """제목 없이는 스코어링도 다이제스트도 아무것도 못 한다."""
    assert s2_delta._to_paper({"title": "", "publicationDate": "2026-08-28"}) is None


def test_missing_date_does_not_crash():
    p = s2_delta._to_paper({"title": "T"})
    assert p["published"] is None


def test_window_is_day_level():
    """S2 는 publicationDateOrYear 로 날짜 범위를 지원한다 — search_runs 주석의
    'S2 는 day-level delta 불가(2026-08-24 리뷰)' 는 틀린 기록이었다."""
    w = s2_delta._window(_dt("2026-08-28T13:00:00"), _dt("2026-09-02T04:00:00"))
    assert w == "2026-08-28:2026-09-02"


# ---------------------------------------------------------------- 키워드 선별


def test_only_target_tier_keywords_go_to_s2():
    """커버리지 격차가 표적 계층에서 난다. 동향어(physical AI 등)는 원래
    arXiv 중심 분야라 S2 를 쓸 값어치가 낮고, 키워드당 약 55초가 든다."""
    profile = {"core_topics": ["defect detection", "physical AI", "quantization"],
               "core_weights": {"defect detection": 1.0, "physical AI": 0.6,
                                "quantization": 0.35}}
    assert s2_delta.keywords_for_s2(profile) == ["defect detection"]


def test_profile_without_weights_uses_all_keywords():
    """가중치가 없는 구형 프로필은 전부 1.0 으로 본다 — 하위 호환."""
    profile = {"core_topics": ["a", "b"]}
    assert s2_delta.keywords_for_s2(profile) == ["a", "b"]


# ---------------------------------------------------------------- 수집·합침


class _FakeResp:
    def __init__(self, items, total=None):
        self._items = items
        # 실제 S2 응답은 total 을 준다 — 남은 게 있는지 판단하는 근거다.
        # 안 주면 None: "모른다"이고, 그때는 페이지가 덜 찼는지로만 본다.
        self._total = len(items) if total is None else total

    def json(self):
        return {"data": self._items, "total": self._total}


def _stub_s2(monkeypatch, by_keyword):
    calls = []

    async def fake_get(client, params, headers, url=None, max_wait=None):
        calls.append(params["query"])
        if isinstance(by_keyword, Exception):
            raise by_keyword
        return _FakeResp(by_keyword.get(params["query"], []))

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    return calls


def _run(keywords, monkeypatch, by_keyword):
    calls = _stub_s2(monkeypatch, by_keyword)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, keywords, _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    return out, calls


def test_same_paper_from_two_keywords_is_counted_once(monkeypatch):
    """한 논문이 여러 키워드에 걸리는 건 흔하다 — 소스 안에서 먼저 합친다."""
    item = {"title": "Surface Defect Detection", "externalIds": {"DOI": "10.1/x"},
            "publicationDate": "2026-08-30"}
    out, _ = _run(["surface inspection", "defect detection"], monkeypatch,
                  {"surface inspection": [item], "defect detection": [item]})
    assert len(out["papers"]) == 1


def test_one_failing_keyword_does_not_kill_the_rest(monkeypatch):
    """한 키워드가 죽어도 나머지는 살아야 한다(프로필 간 실패 격리와 같은 원칙)."""
    good = {"title": "Good Paper", "publicationDate": "2026-08-30"}

    async def fake_get(client, params, headers, url=None, max_wait=None):
        if params["query"] == "bad":
            raise RuntimeError("S2 죽음")
        return _FakeResp([good])

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, ["bad", "good"], _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert len(out["papers"]) == 1
    assert out["keywords_failed"] == 1
    # **계약이 바뀌었다**(2026-09-07, §8-70). 예전에는 이 경우가 "done" 이었다.
    # 그런데 done 은 래칫을 전진시킨다(research_profile.next_window_start 가
    # status=='done' 일 때만 window_to 를 다음 커서로 쓴다) — 6개 중 5개가
    # 실패해도 done 이면 그 창의 나머지 논문은 **다시는 조회되지 않는다**.
    # 실패한 키워드가 하나라도 있으면 다 본 게 아니다.
    assert out["status"] == "partial"


def test_all_keywords_failing_is_reported_as_failed_not_empty(monkeypatch):
    """'S2 가 죽었다' 와 '정말 새 논문이 없다' 를 같게 보고하면 안 된다."""
    out, _ = _run(["a", "b"], monkeypatch, RuntimeError("죽음"))
    assert out["papers"] == []
    assert out["status"] == "failed"
    assert out["keywords_failed"] == 2


def test_empty_result_is_done_not_failed(monkeypatch):
    out, _ = _run(["a"], monkeypatch, {"a": []})
    assert out["status"] == "done"


def test_one_request_per_keyword(monkeypatch):
    """키워드당 55초가 드는 게 실측이라, 요청 수가 조용히 늘면 안 된다."""
    _out, calls = _run(["a", "b", "c"], monkeypatch, {})
    assert calls == ["a", "b", "c"]


# ---------------------------------------------------------------- 중복 질의 제외 (2026-09-03)

def test_redundant_keyword_dropped_only_when_substitute_present():
    """86% 겹치는 쌍만 뺀다. 대신할 키워드가 없으면 개념을 통째로 잃으므로 안 뺀다."""
    both = {"core_topics": ["in-sensor computing", "on-sensor computing", "autofocus"]}
    assert s2_delta.keywords_for_s2(both) == ["in-sensor computing", "autofocus"]

    alone = {"core_topics": ["on-sensor computing", "autofocus"]}
    assert s2_delta.keywords_for_s2(alone) == ["on-sensor computing", "autofocus"]


def test_low_overlap_keywords_are_kept():
    """실측 겹침이 낮은 것(5%·13%·61%)은 넓은 형제가 있어도 그대로 질의한다.

    문구가 포함관계라고 결과까지 포함관계는 아니다 — S2 는 관련도 검색이라
    'few-shot defect detection' 은 few-shot 학습 문헌을 데려온다(겹침 5%).
    """
    p = {"core_topics": ["defect detection", "micro defect detection",
                         "few-shot defect detection", "surface inspection"]}
    assert s2_delta.keywords_for_s2(p) == p["core_topics"]


def test_scoring_keywords_are_not_reduced():
    """줄이는 건 나가는 질의뿐 — 채점은 로컬이라 공짜다."""
    p = {"core_topics": ["in-sensor computing", "on-sensor computing"]}
    assert len(s2_delta.keywords_for_s2(p)) == 1
    assert len(p["core_topics"]) == 2  # 프로필은 안 건드린다


# ---------------------------------------------------------------- ③ 검색 예산 (2026-09-04)
#
# "S2 가 어제도 1시간 반 넘게 검색하던데 너무 오래하는 거 아니야?"
# Deep Layer 에는 예산이 있는데 검색에는 없었다 — 키워드마다 재시도 사슬
# 450초가 붙어 6키워드면 최악 45분이다. arXiv 는 30초에 182편을 준다.

def test_budget_stops_searching_and_reports_partial(monkeypatch):
    clock = {"t": 0.0}
    asked = []

    async def slow_get(client, params, headers, url=None, max_wait=None):
        asked.append(params["query"])
        clock["t"] += 200.0            # 키워드마다 200초씩 먹는다
        return _FakeResp([{"title": f"T{len(asked)}", "publicationDate": "2026-09-01",
                           "externalIds": {}}])

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", slow_get)
    monkeypatch.setattr(s2_delta.time, "monotonic", lambda: clock["t"])

    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 4, tzinfo=timezone.utc)
    got = asyncio.run(s2_delta.find_new_papers_since(
        None, ["k1", "k2", "k3", "k4"], since, until, budget_s=300.0))

    assert len(asked) == 2                     # 200초 두 번이면 예산 초과
    assert got["status"] == "partial"          # 조용히 전체인 척하지 않는다
    assert got["keywords_searched"] == 2
    assert "2/4" in got["query"]
    assert len(got["papers"]) == 2             # 거기까지 모은 건 살린다


def test_budget_not_reached_stays_done(monkeypatch):
    """정상인 날(6키워드 1분)에는 전혀 안 걸려야 한다."""
    async def fast_get(client, params, headers, url=None, max_wait=None):
        return _FakeResp([{"title": "T", "publicationDate": "2026-09-01", "externalIds": {}}])

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fast_get)
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 4, tzinfo=timezone.utc)
    got = asyncio.run(s2_delta.find_new_papers_since(None, ["k1", "k2"], since, until))
    assert got["status"] == "done"
    assert got["keywords_searched"] == 2


def test_remaining_budget_is_passed_as_the_call_wait_cap(monkeypatch):
    """한 키워드가 예산을 통째로 넘기지 못하게 — 예산은 키워드 사이에서만
    검사되므로 호출 안쪽에도 상한이 필요하다."""
    seen = []
    clock = {"t": 0.0}

    async def capture(client, params, headers, url=None, max_wait=None):
        seen.append(max_wait)
        clock["t"] += 100.0
        return _FakeResp([])

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", capture)
    monkeypatch.setattr(s2_delta.time, "monotonic", lambda: clock["t"])
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 4, tzinfo=timezone.utc)
    asyncio.run(s2_delta.find_new_papers_since(
        None, ["k1", "k2"], since, until, budget_s=300.0))
    assert seen == [300.0, 200.0]              # 남은 예산이 줄어드는 게 그대로 전달된다


# ------------------------------------------- 페이지네이션 · 잘림 보고 (§8-70, 2026-09-07)
#
# 그전에는 키워드당 100건을 1회 조회하고 **101번째가 있는지 확인하지 않은 채**
# "done" 으로 기록했다. done 은 래칫을 전진시키므로 그 창의 나머지는 다시
# 조회되지 않는다 — 조용히 잃는 경로였다.


class _PagedResp:
    """페이지마다 다른 묶음을 주는 가짜 응답."""

    def __init__(self, items, total):
        self._items, self._total = items, total

    def json(self):
        # total=None 은 "S2 가 안 알려줬다" — 그때는 페이지가 덜 찼는지로만 본다.
        out = {"data": self._items}
        if self._total is not None:
            out["total"] = self._total
        return out


def _paged_stub(monkeypatch, pages, total):
    """pages: 페이지 번호 → 아이템 목록. 호출된 offset 을 기록한다."""
    seen_offsets = []

    async def fake_get(client, params, headers, url=None, max_wait=None):
        offset = params.get("offset", 0)
        seen_offsets.append(offset)
        page = offset // params["limit"]
        return _PagedResp(pages[page] if page < len(pages) else [], total)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    return seen_offsets


def _items(n, start=0):
    return [{"title": f"Paper {i}", "publicationDate": "2026-08-30",
             "externalIds": {"DOI": f"10.1/{i}"}} for i in range(start, start + n)]


def test_second_page_is_fetched_when_the_first_is_full(monkeypatch):
    """100건이 꽉 차서 돌아오면 101번째가 있을 수 있다 — 확인하지 않고
    '다 봤다'고 기록하던 것이 §8-70 의 지적이다."""
    offsets = _paged_stub(monkeypatch, [_items(100), _items(30, start=100)], total=130)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert offsets == [0, 100]
    assert len(got.papers) == 130
    assert got.truncated is False        # 창을 다 훑었다
    assert got.total == 130


def test_single_short_page_makes_no_second_call(monkeypatch):
    """정상적인 날에는 첫 페이지에서 끝난다 — 공짜 호출을 늘리지 않는다."""
    offsets = _paged_stub(monkeypatch, [_items(12)], total=12)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert offsets == [0]
    assert got.truncated is False


def test_page_cap_reports_truncation_instead_of_pretending_done(monkeypatch):
    """상한(3페이지)을 다 쓰고도 남으면 **못 본 것이 있다고 말한다.**
    비용 원칙상 끝까지 넘기지는 않는다 — 대신 숨기지 않는다."""
    offsets = _paged_stub(monkeypatch, [_items(100)] * 5, total=500)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert len(offsets) == s2_delta.MAX_PAGES_PER_KEYWORD
    assert got.truncated is True


def test_page_cap_is_recorded_but_does_not_make_the_run_partial(monkeypatch):
    """**계약이 바뀌었다**(2026-09-08, §8-78 ②). 페이지 상한은 **우리가 정한
    범위**다 — 관련도 순 앞쪽 300편이면 충분하다고 판단해 그렇게 정했고,
    실측에서 가장 많은 키워드도 218편으로 그 아래였다.

    그걸 partial 로 남기면 창이 안 전진해 **다음 날 같은 창을 다시 훑고 또
    상한에 걸린다** — 볼 생각도 없는 논문 때문에 호출만 매일 늘어난다.
    대신 `keywords_capped` 와 query 문자열로 남겨 기록에서 사라지지 않게 한다.
    `done` 은 "우리가 보기로 한 범위를 다 봤다"이지 "세계의 모든 논문을
    확인했다"가 아니다.
    """
    _paged_stub(monkeypatch, [_items(100)] * 5, total=500)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, ["k"], _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert out["status"] == "done"
    assert out["keywords_truncated"] == 1      # 잘린 사실은 그대로 남는다
    assert out["keywords_capped"] == 1
    assert out["keywords_blocked"] == 0
    assert "capped×1" in out["query"]          # search_runs 기록에서도 보인다


def test_budget_truncation_still_makes_the_run_partial(monkeypatch):
    """예산 소진은 **사고**다 — 우리가 고른 것이 아니므로 창을 붙잡아야 한다."""
    clock = [1000.0]
    monkeypatch.setattr(s2_delta.time, "monotonic", lambda: clock[0])

    async def slow_get(client, params, headers, url=None, max_wait=None):
        clock[0] += 40.0
        return _PagedResp(_items(100), 900)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", slow_get)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, ["k"], _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00"),
        budget_s=30.0))
    assert out["status"] == "partial"
    assert out["keywords_blocked"] == 1
    assert out["keywords_capped"] == 0


def test_page_failure_still_makes_the_run_partial(monkeypatch):
    """뒤 페이지 실패도 사고다."""
    async def flaky(client, params, headers, url=None, max_wait=None):
        if params.get("offset"):
            raise RuntimeError("S2 죽음")
        return _PagedResp(_items(100), 900)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", flaky)
    out = asyncio.run(s2_delta.find_new_papers_since(
        None, ["k"], _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert out["status"] == "partial"
    assert out["keywords_blocked"] == 1


def test_failure_after_a_good_page_keeps_what_we_got(monkeypatch):
    """뒤 페이지가 죽었다고 앞 페이지까지 버리면 성한 결과를 잃는다.
    받은 것은 살리되 '다 봤다'고는 하지 않는다."""
    async def fake_get(client, params, headers, url=None, max_wait=None):
        if params.get("offset"):
            raise RuntimeError("S2 죽음")
        return _PagedResp(_items(100), 400)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert len(got.papers) == 100
    assert got.truncated is True


def test_first_page_failure_is_still_none_not_empty(monkeypatch):
    """첫 페이지부터 죽으면 '실패'다 — '결과 0건'과 구분해야 한다.
    이 구분은 이 모듈이 처음부터 지켜온 것이다."""
    async def fake_get(client, params, headers, url=None, max_wait=None):
        raise RuntimeError("S2 죽음")

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert got is None


# ------------------------------------------- 페이지네이션이 만든 결함 세 건 (2026-09-07)
#
# 위 페이지네이션을 넣으면서 생긴 것들이다. 외부 검토(Codex)가 짚었고 코드로
# 대조해 셋 다 사실임을 확인했다. 기존 테스트 25개가 전부 통과했다는 건
# **아무도 이 세 가지를 지키고 있지 않았다**는 뜻이다.


def test_time_budget_is_not_reused_by_every_page(monkeypatch):
    """**호출 수를 늘리면서 생긴 회귀.** max_wait 를 페이지마다 그대로 넘기면
    3페이지가 각자 전체 예산을 쓸 수 있어 SEARCH_BUDGET_SECONDS 가 전체
    검색 시간을 못 막는다 — S2 가 나쁜 날 45분을 먹던 그 문제(§8-34)로 돌아간다.
    """
    waits = []
    clock = [1000.0]
    monkeypatch.setattr(s2_delta.time, "monotonic", lambda: clock[0])

    async def fake_get(client, params, headers, url=None, max_wait=None):
        waits.append(max_wait)
        clock[0] += 12.0          # 페이지 하나가 12초를 먹었다
        return _PagedResp(_items(100), 500)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00"), max_wait=30.0))

    # 30초 예산으로 12초짜리 페이지를 돌면 3페이지째는 6초만 남는다.
    # 예전처럼 매 페이지에 30 을 그대로 주면 총 90초를 쓸 수 있었다.
    assert waits == [30.0, 18.0, 6.0]
    assert got.truncated is True      # 상한까지 받고도 남았다


def test_exact_page_multiple_is_not_called_truncated(monkeypatch):
    """total 이 정확히 3페이지면 **다 본 것**이다. 이걸 잘렸다고 하면 래칫이
    영원히 안 넘어가 같은 창을 매일 다시 조회한다(비용 원칙에도 어긋난다)."""
    offsets = _paged_stub(monkeypatch, [_items(100)] * 3, total=300)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert len(offsets) == 3
    assert len(got.papers) == 300
    assert got.truncated is False


def test_known_total_avoids_an_extra_empty_page(monkeypatch):
    """total=200 이면 2페이지로 끝난다 — 빈 3페이지를 확인하려고 무료 호출을
    한 번 더 쓰지 않는다."""
    offsets = _paged_stub(monkeypatch, [_items(100), _items(100, start=100), []], total=200)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert offsets == [0, 100]
    assert got.truncated is False


def test_title_less_items_do_not_fake_truncation(monkeypatch):
    """total 은 S2 **원시** 건수이고 papers 는 제목 없는 항목을 뺀 목록이다.
    둘을 비교하면 제목 없는 항목 하나 때문에 다 훑은 창이 '잘렸다'가 된다."""
    items = _items(11) + [{"title": "", "publicationDate": "2026-08-30"}]

    async def fake_get(client, params, headers, url=None, max_wait=None):
        return _PagedResp(items, 12)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert len(got.papers) == 11        # 제목 없는 것은 버리되
    assert got.truncated is False       # 그게 잘림은 아니다


def test_unknown_total_with_full_pages_stays_conservative(monkeypatch):
    """total 을 모르는데 3페이지가 다 찼으면 남았는지 알 수 없다 —
    그때는 '다 봤다'고 하지 않는 쪽이 맞다."""
    async def fake_get(client, params, headers, url=None, max_wait=None):
        return _PagedResp(_items(100), None)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", fake_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert got.total is None
    assert got.truncated is True


def test_truncation_says_which_cause(monkeypatch):
    """**라이브 실행이 잡은 결함**(2026-09-07 23:29). 로그가 "상한까지 받고도
    남았다"라고 말했는데 실제로는 429 재시도(30+60+120초)로 시간 예산이 끝난
    것이었다. 원인이 다르면 처방이 다르다 — 페이지 상한은 우리가 정한 값이고
    예산 소진은 S2 가 나쁜 날이다. 안 가르면 로그를 읽고 엉뚱한 걸 고친다.
    """
    # (a) 페이지 상한
    _paged_stub(monkeypatch, [_items(100)] * 5, total=900)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert (got.truncated, got.reason) == (True, "page_cap")

    # (b) 시간 예산 소진 — 첫 페이지를 받는 동안 예산이 끝난다
    clock = [1000.0]
    monkeypatch.setattr(s2_delta.time, "monotonic", lambda: clock[0])

    async def slow_get(client, params, headers, url=None, max_wait=None):
        clock[0] += 40.0
        return _PagedResp(_items(100), 900)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", slow_get)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00"), max_wait=30.0))
    assert (got.truncated, got.reason) == (True, "budget")
    assert len(got.papers) == 100          # 받은 것은 살린다

    # (c) 뒤 페이지 실패
    async def flaky(client, params, headers, url=None, max_wait=None):
        if params.get("offset"):
            raise RuntimeError("S2 죽음")
        return _PagedResp(_items(100), 900)

    monkeypatch.setattr(s2_delta.http_client, "throttled_s2_get", flaky)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert (got.truncated, got.reason) == (True, "page_failure")


def test_complete_window_has_no_reason(monkeypatch):
    """다 봤으면 잘림도 사유도 없다."""
    _paged_stub(monkeypatch, [_items(12)], total=12)
    got = asyncio.run(s2_delta.search_keyword_since(
        None, "k", _dt("2026-08-28T00:00:00"), _dt("2026-09-02T00:00:00")))
    assert got.truncated is False and got.reason is None


# ── S2 씨앗과 채점 가중치의 분리 (2026-09-09, §8-79)
#
# 이 세 테스트가 지키는 것: **가중치를 올려도 S2 호출이 안 늘어나야 한다.**
# 그전에는 `keywords_for_s2` 가 가중치 1.0 이상을 질의어로 골랐고, 그래서
# "이 논문이 얼마나 우리 얘기인가"를 올리는 순간 무료 API 호출이 같이 늘었다.

def test_씨앗이_있으면_가중치를_올려도_질의어가_안_늘어난다():
    """이 테스트가 잡는 것: 명시적 씨앗 경로에 가중치 필터를 다시 연결하는 것.

    비씨앗 키워드를 전부 1.0 으로 올려도 질의 목록이 그대로여야 한다 —
    분리의 존재 이유가 바로 이것이다.
    """
    profile = {
        "core_topics": ["defect detection", "world model", "physical AI"],
        "core_weights": {"defect detection": 0.6, "world model": 0.6, "physical AI": 0.6},
        "s2_seeds": ["world model"],
    }
    assert s2_delta.keywords_for_s2(profile) == ["world model"]

    profile["core_weights"] = {k: 1.0 for k in profile["core_topics"]}
    assert s2_delta.keywords_for_s2(profile) == ["world model"], (
        "가중치를 올렸더니 질의어가 늘었다 — 씨앗과 중요도가 다시 붙었다")


def test_씨앗이_비면_종전대로_가중치로_고른다():
    """이 테스트가 잡는 것: 하위 호환 경로 삭제.

    씨앗을 안 쓰는 구형 프로필은 예전과 똑같이 동작해야 한다. 씨앗이 비었다고
    S2 를 꺼 버리면 기존 프로필의 검색이 조용히 멈춘다.
    """
    profile = {
        "core_topics": ["a", "b", "c"],
        "core_weights": {"a": 1.0, "b": 1.0, "c": 0.6},
    }
    assert s2_delta.keywords_for_s2(profile) == ["a", "b"]
    assert s2_delta.keywords_for_s2(dict(profile, s2_seeds=[])) == ["a", "b"]
    # 가중치조차 없는 더 오래된 프로필은 전부 1.0 으로 본다.
    assert s2_delta.keywords_for_s2({"core_topics": ["a", "b"]}) == ["a", "b"]


def test_씨앗에도_중복_제거_규칙이_그대로_걸린다():
    """이 테스트가 잡는 것: 씨앗 경로에서 S2_REDUNDANT_KEYWORDS 를 빠뜨리는 것.

    분리하면서 중복 규칙을 새 경로에 안 옮기면 429 를 부르던 호출이 되살아난다.
    """
    profile = {"core_topics": [], "s2_seeds": ["in-sensor computing", "on-sensor computing"]}
    assert s2_delta.keywords_for_s2(profile) == ["in-sensor computing"]
    # 대신할 씨앗이 없으면 개념을 통째로 잃지 않게 그대로 둔다.
    assert s2_delta.keywords_for_s2({"core_topics": [], "s2_seeds": ["on-sensor computing"]}) \
        == ["on-sensor computing"]
