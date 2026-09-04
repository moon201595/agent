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
    def __init__(self, items):
        self._items = items

    def json(self):
        return {"data": self._items}


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
    assert out["status"] == "done"


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
