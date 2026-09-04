"""주간 동향 리뷰 — 네트워크 없이 돈다 (2026-09-02).

"논문들을 쭉 넣은 다음 마지막에 동향 보고가 필요하다"는 요구의 답이다.
**LLM 을 안 쓴다** — 전부 셈과 문자열 대조라 위조가 불가능하다(CLAUDE.md 7).
서술형 리뷰는 검증할 수 없는 산출물이라 넣지 않았다.
"""

import storage
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import trend_report

PROFILE = {
    "core_topics": ["defect detection", "robot manipulation", "quantization"],
    "core_weights": {"defect detection": 1.0, "robot manipulation": 0.6,
                     "quantization": 0.35},
    "target_domain": [], "exclude": [],
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    # 실제 스키마를 쓴다(2026-09-04) — 손으로 다시 쓰면 실제 스키마가
    # 바뀔 때 픽스처만 뒤처진다(§8-52). storage 가 유일한 소유자다.
    storage.init_storage(path)
    return path


def _add(db, aid, title, days_ago, source=None, engine="gemini", coverage=1.0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, published, source) "
                    "VALUES (?,?,?,?)", (aid, title, ts, source))
        con.execute("INSERT OR REPLACE INTO summaries "
                    "(arxiv_id, created_at, engine, coverage_ratio) VALUES (?,?,?,?)",
                    (aid, ts, engine, coverage))


def _build(db):
    return asyncio.run(trend_report.build(db, PROFILE, client=None))


def test_counts_this_week_and_compares_with_last_week(db):
    _add(db, "a", "A defect detection method", 1)
    _add(db, "b", "Another defect detection study", 2)
    _add(db, "c", "An old defect detection paper", 9)      # 지난주
    text = _build(db)
    assert "처리한 논문 2편 (지난주 1편)" in text
    assert "defect detection 2  (1→2, +1)" in text


def test_keyword_that_vanished_is_reported(db):
    """줄어든 것도 동향이다 — 늘어난 것만 보여주면 절반만 보는 것이다."""
    _add(db, "old", "quantization aware training int8", 9)
    _add(db, "new", "A defect detection method", 1)
    text = _build(db)
    assert "지난주엔 있었으나 이번주 없음" in text
    assert "quantization" in text


def test_source_mix_separates_journal_from_arxiv(db):
    """S2 를 붙인 뒤 이 비율이 바뀌는지가 관심사다(§ arXiv 커버리지 3%)."""
    _add(db, "a", "defect detection", 1, source=None)
    _add(db, "pdf-x", "surface inspection", 1, source="open-access: 10.1/x")
    text = _build(db)
    assert "arXiv 1편" in text
    assert "저널(오픈액세스) 1편" in text


def test_partial_coverage_is_surfaced(db):
    """Groq 폴백 날 요약이 원문 절반만 본 걸 주간 단위로도 드러낸다(§8-25)."""
    _add(db, "a", "defect detection", 1, engine="groq", coverage=0.40)
    text = _build(db)
    assert "원문을 다 못 본 요약 1편" in text
    assert "40%" in text


def test_title_only_matching_avoids_body_noise(db):
    """제목만 본다 — 주간 추이는 '무엇에 대한 논문이 나왔나'이지
    '어떤 낱말이 본문 어딘가에 있었나'가 아니다."""
    _add(db, "a", "A study of robot manipulation", 1)
    counts = trend_report.keyword_counts(trend_report._rows_between(
        db, datetime.now(timezone.utc) - timedelta(days=7),
        datetime.now(timezone.utc)), PROFILE)
    assert counts["robot manipulation"] == 1
    assert "defect detection" not in counts


def test_empty_week_does_not_crash(db):
    text = _build(db)
    assert "처리한 논문 0편" in text


# ---------------------------------------------------------------- 공통 인용


class _Rows(list):
    pass


def _row(aid):
    # abstract 는 실제 _rows_between 이 뽑는 컬럼이다 — emerging_terms 가 쓴다.
    return {"arxiv_id": aid, "title": "T", "abstract": "", "published": None,
            "source": None, "engine": "gemini", "coverage_ratio": 1.0}


def test_shared_references_counts_papers_not_occurrences(monkeypatch):
    """한 논문이 같은 참고문헌을 두 번 인용해도 1로 센다 — '몇 편이 함께
    인용했나'가 신호이지 '총 몇 번 등장했나'가 아니다."""
    async def fake(aid, limit, edge):
        import json
        return json.dumps({"papers": [{"title": "Base Paper"}, {"title": "Base Paper"},
                                      {"title": "Other"}]})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert ("Base Paper", 2) in scan.shared
    assert scan.examined == 2 and scan.targets == 2


def test_shared_requires_more_than_one_paper(monkeypatch):
    """1편만 인용한 건 '공통'이 아니라 그냥 참고문헌 목록이다."""
    async def fake(aid, limit, edge):
        import json
        return json.dumps({"papers": [{"title": f"Ref for {aid}"}]})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert scan.shared == []


def test_synthetic_pdf_ids_are_skipped(monkeypatch):
    """S2 인용망은 arXiv ID 로 찾는다 — pdf-<해시> 는 조회 대상이 아니다."""
    calls = []

    async def fake(aid, limit, edge):
        calls.append(aid)
        import json
        return json.dumps({"papers": []})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(
        trend_report.shared_references(None, [_row("2608.1"), _row("pdf-abc")]))
    assert calls == ["2608.1"]
    assert scan.targets == 1


def test_budget_stops_early_and_reports_the_sample_size(monkeypatch):
    """실측(2026-09-02): 8편으로 돌렸더니 7분을 넘겼다. 부분 결과를 내되
    표본 크기를 숨기지 않는다 — 조용히 전체인 척하면 안 된다."""
    clock = {"t": 0.0}

    async def fake(aid, limit, edge):
        clock["t"] += 100.0
        import json
        return json.dumps({"papers": [{"title": "Base"}]})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    monkeypatch.setattr(trend_report.time, "monotonic", lambda: clock["t"])
    scan = asyncio.run(trend_report.shared_references(
        None, [_row(str(i)) for i in range(10)], budget_s=250.0))
    assert scan.examined < scan.targets
    assert scan.targets == 10


def test_lookup_failure_does_not_break_the_report(monkeypatch):
    """동향 보고가 못 나온다고 다이제스트를 막으면 안 된다."""
    async def fake(aid, limit, edge):
        raise RuntimeError("S2 죽음")

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(trend_report.shared_references(None, [_row("1")]))
    assert scan.shared == [] and scan.examined == 0 and scan.targets == 1


def test_report_states_the_sample_when_partial():
    rows = [_row(str(i)) for i in range(10)]
    text = trend_report.format_report(rows, [], PROFILE,
                                      shared=[("Base", 3)], examined=4, targets=10)
    assert "4/10편 조회" in text
    assert "시간 예산으로 일부만 봤다" in text


# ---------------------------------------------------------------- 미등록 용어 (2026-09-03)
#
# 닫힌 고리를 끊는 장치다 — core_topics 로 검색·채점·집계하면 새로 뜨는 것은
# 새 이름을 달고 오므로 구조적으로 안 보인다.

def _text_row(title, abstract=""):
    """emerging_terms 는 title·abstract 만 본다 — sqlite3.Row 대신 dict 로 충분."""
    return {"title": title, "abstract": abstract}


_PROFILE = {"core_topics": ["defect detection", "in-sensor computing"],
            "domain_hints": ["manufacturing"]}


def test_emerging_terms_finds_words_not_in_core_topics():
    rows = [_text_row(f"paper {i}", "we evaluate on jetson orin nano for edge devices")
            for i in range(4)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert "jetson orin nano" in got
    assert "edge devices" in got


def test_registered_keywords_are_excluded():
    """이미 아는 말은 여기 뜨면 안 된다 — 주제별 편수 절이 이미 센다."""
    rows = [_text_row("defect detection on surfaces", "defect detection everywhere")
            for _ in range(5)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert not any("defect detection" in t for t in got)


def test_boilerplate_is_not_a_trend():
    """'code available at https://github.com' 은 주제어가 아니라 논문의 형식이다."""
    rows = [_text_row("a paper", "our code is publicly available at https://github.com/x/y "
                            "and we propose a novel approach that achieves state of the art")
            for _ in range(6)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert not any(w in t for t in got
                   for w in ("github", "https", "available", "propose", "novel", "sota"))


def test_model_and_learning_survive_as_compound_heads():
    """model·learning 을 상투어로 막았더니 vla models 와 reinforcement learning 이
    통째로 죽었다 — 꼬리 자리가 바로 우리가 찾는 자리다."""
    rows = [_text_row("x", "we train vla models with reinforcement learning") for _ in range(4)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert "vla models" in got
    assert "reinforcement learning" in got


def test_longer_term_subsumes_shorter():
    """'large language' 와 'large language models' 를 둘 다 보고하지 않는다."""
    rows = [_text_row("x", "large language models are everywhere") for _ in range(5)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert "large language models" in got
    assert "large language" not in got


def test_counts_papers_not_occurrences():
    """장황한 논문 하나가 동향을 만들면 안 된다."""
    rows = [_text_row("x", "edge devices " * 40)]           # 1편이 40번 반복
    assert trend_report.emerging_terms(rows, _PROFILE, min_papers=3) == []


def test_previous_week_count_is_reported():
    """새로 생긴 것과 원래 있던 것이 구분돼야 한다."""
    now = [_text_row("x", "we use edge devices here") for _ in range(4)]
    prev = [_text_row("x", "we use edge devices here") for _ in range(2)]
    got = dict((t, (n, w)) for t, n, w in trend_report.emerging_terms(now, _PROFILE, prev))
    assert got["edge devices"] == (4, 2)


# ---------------------------------------------------------------- 서술 (2026-09-03)
#
# 규칙 7 이 막는 건 판정이지 서술이 아니다. 여기서 지켜야 할 건 규칙 4
# (LLM 에는 공개 논문 텍스트만)와 규칙 8(수치를 만들어내지 않는다)이다.

_PUB = [{"title": "Defect detection on PV panels", "abstract": "We report 42.5% gain.",
         "source": "arxiv"},
        {"title": "In-sensor computing array", "abstract": "A memristor array.", "source": None},
        {"title": "Edge inference on Jetson", "abstract": "Runs at 30 fps.",
         "source": "open-access-doi"}]


def test_all_paper_sources_reach_the_prompt():
    """2026-09-03 규칙 4 개정. **이 주장은 뒤집혔다** — 사유를 남긴다.

    개정 전에는 출처를 arXiv·오픈액세스로 한정했다. 그런데 ④ 요약은 이미
    직접 올린 PDF 를 LLM 에 보내고 있어(실측 `pdf-5bd2ec925e`) 여기만 엄격한
    게 앞뒤가 안 맞았고, 무엇보다 목적을 막고 있었다.
    개정 규칙은 **논문 텍스트는 출처 무관 허용**이다.
    """
    rows = _PUB + [{"title": "Uploaded conference paper", "abstract": "본문",
                    "source": "manual-pdf: streamlit-upload"}]
    corpus, used = trend_report._narrative_corpus(rows)
    assert used == 4
    assert "Uploaded conference paper" in corpus


def test_prompt_carries_topics_but_never_our_measurements():
    """핵심 회귀 — 개정된 규칙 4 의 경계선.

    '무엇에 관심 있나'(core_topics)는 나가도 되지만 '무엇을 하고 있나'
    (집계·편수·별점·가중치·미등록 용어)는 안 된다.
    """
    corpus, _ = trend_report._narrative_corpus(_PUB)
    profile = {"core_topics": ["defect detection", "in-sensor computing"],
               "core_weights": {"defect detection": 1.0, "in-sensor computing": 0.6}}
    prompt = trend_report._NARRATIVE_PROMPT.format(
        papers=corpus, topics=trend_report.narrative_topics(profile))

    assert "defect detection" in prompt          # 관심 분야는 나간다
    # 우리 쪽 측정값은 하나도 안 나간다. ("편수"라는 낱말 자체는 프롬프트에
    # 있지만 그건 "숫자를 쓰지 말라"는 지시어지 데이터가 아니다 — 값이 새는지를
    # 본다.)
    for leak in ("core_weights", "0.6", "가중치", "★", "pass_ratio",
                 "coverage_ratio", "emerging", "재현 성공", "team_ai_advance"):
        assert leak not in prompt


def test_narrative_topics_sends_names_without_weights():
    """가중치는 우선순위라 '무엇을 하고 있나'에 가깝다 — 이름만 보낸다."""
    profile = {"core_topics": ["low", "high"],
               "core_weights": {"low": 0.3, "high": 1.0}}
    got = trend_report.narrative_topics(profile)
    assert got == "high, low"                    # 가중치 순, 값은 빠진다
    assert "0.3" not in got and "1.0" not in got


def test_ungrounded_numbers_flags_invented_ones():
    """규칙 8 의 두 번째 겹 — 프롬프트로 막고, 그래도 나오면 대조로 잡는다."""
    corpus, _ = trend_report._narrative_corpus(_PUB)
    assert trend_report.ungrounded_numbers("42.5% 올랐다", corpus) == []
    assert trend_report.ungrounded_numbers("17편에서 88% 늘었다", corpus) == ["17", "88"]


def test_narrative_skips_when_sample_too_small():
    """3편 미만이면 '흐름'이라 부를 게 없다 — 부르지도 않는다."""
    import asyncio
    assert asyncio.run(trend_report.narrative(None, _PUB[:2])) is None


def test_narrative_returns_none_when_both_engines_fail(monkeypatch):
    """서술이 실패해도 셈은 나가야 한다."""
    import asyncio
    import summarize_engine as se

    async def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(se, "_call_with_rate_limit_retry", boom)
    assert asyncio.run(trend_report.narrative(None, _PUB)) is None


def test_report_labels_the_narrative_as_unverified():
    """셈과 서술이 한 화면에서 섞이면 안 된다 — 어디부터 해석인지 보여야 한다."""
    out = trend_report.format_report([], [], {"core_topics": []},
                                     story=("결함 검출이 온센서와 만난다.", []))
    assert "검증되지 않았다" in out
    assert "결함 검출이 온센서와 만난다." in out


def test_report_warns_about_invented_numbers():
    out = trend_report.format_report([], [], {"core_topics": []},
                                     story=("17편이 늘었다.", ["17"]))
    assert "원문에 없는 숫자" in out and "17" in out


def test_list_markers_are_not_treated_as_claims():
    """실측 오탐 — 첫 라이브 호출에서 목차 번호 1·3 에 경고가 붙었다.
    매번 뜨는 경고는 아무도 안 읽으므로 진짜 조작을 놓치게 만든다."""
    corpus, _ = trend_report._narrative_corpus(_PUB)
    assert trend_report.ungrounded_numbers("1. 흐름\n2) 접점\n- 3. 새로움", corpus) == []
    # 줄머리를 뺐다고 본문 숫자까지 놓치면 안 된다
    assert trend_report.ungrounded_numbers("1. 성능이 17편 늘었다", corpus) == ["17"]


def test_topics_narrow_to_what_actually_matched_this_week():
    """키워드 하나하나는 일반 용어지만 27개를 한 줄로 늘어놓으면 조합이
    과제 구성이 된다 — 이번 주 걸린 것만 보낸다(2026-09-03)."""
    profile = {"core_topics": ["defect detection", "in-sensor computing",
                               "contactless vital sign", "robot manipulation"],
               "core_weights": {}}
    rows = [{"title": "Defect detection on PV panels", "abstract": ""}]
    got = trend_report.narrative_topics(profile, rows)
    assert got == "defect detection"
    assert "contactless vital sign" not in got     # 안 걸린 건 안 나간다


def test_topics_fall_back_to_full_list_when_nothing_matched():
    """하나도 안 걸리면 빈 문자열보다 전체가 낫다 — 서술이 '왜 봐야 하나'를
    아예 못 쓰게 되는 것보다는 낫고, 그 주는 어차피 표본이 얇다."""
    profile = {"core_topics": ["defect detection", "in-sensor computing"], "core_weights": {}}
    rows = [{"title": "Something unrelated entirely", "abstract": ""}]
    got = trend_report.narrative_topics(profile, rows)
    assert "defect detection" in got and "in-sensor computing" in got


def test_author_names_never_reach_the_prompt():
    """저자 빈도 집계는 로컬 셈으로 똑같이 나온다 — 얻는 게 같고 성격만
    나쁘면 안 보낸다(2026-09-03 결정). 나중 세션이 '공개 메타데이터니까
    괜찮다'로 넘어가지 않게 못 박는다."""
    rows = [{"title": "Defect detection", "abstract": "본문",
             "authors": "Hong Gildong, Kim Cheolsu", "source": "arxiv"}]
    corpus, used = trend_report._narrative_corpus(rows)
    assert used == 1
    assert "Hong Gildong" not in corpus and "Kim Cheolsu" not in corpus


# ---------------------------------------------------------------- §8-40 (2026-09-03)
#
# 인용·저자 신호를 하나도 안 쓰고 있었다. 셋 다 추가 API 호출이 0회다 —
# authors 는 이미 저장돼 있고, 참고문헌 집합과 citationCount 는 공통 인용
# 조회가 이미 받아오는데 세고 나서 버리고 있었다.

def _author_row(names, title="T"):
    import json as _json
    return {"title": title, "abstract": "", "authors": _json.dumps(names)}


def test_author_counts_needs_two_papers():
    """공저자가 많은 논문 한 편이 저자 전원을 1편씩 올린다 — 1편짜리를 세면
    그냥 저자 목록이지 '누가 밀고 있나'가 아니다."""
    rows = [_author_row(["Ai", "Wei"]), _author_row(["Ai", "Liu"])]
    assert trend_report.author_counts(rows) == [("Ai", 2)]


def test_author_counts_survives_broken_json():
    """리뷰가 부가 정보라 필드 하나 때문에 죽으면 안 된다."""
    rows = [{"title": "T", "abstract": "", "authors": "not json"},
            {"title": "T", "abstract": "", "authors": None},
            _author_row(["Ai"]), _author_row(["Ai"])]
    assert trend_report.author_counts(rows) == [("Ai", 2)]


def test_author_counted_once_per_paper():
    rows = [_author_row(["Ai", "Ai"]), _author_row(["Ai"])]
    assert trend_report.author_counts(rows) == [("Ai", 2)]


def test_lineage_groups_papers_sharing_references():
    """공통 인용이 '다들 무엇을 딛고 있나'라면 여기는 '누가 누구와 같은 데를
    딛고 있나'다."""
    by_paper = {
        "a": {"R1", "R2", "R3", "R4"},
        "b": {"R1", "R2", "R3", "R9"},      # a 와 크게 겹친다
        "c": {"Z1", "Z2", "Z3", "Z4"},      # 완전히 다른 토대
    }
    rows = [{"arxiv_id": k, "title": f"Paper {k}"} for k in "abc"]
    groups = trend_report.lineage_groups(by_paper, rows)
    assert len(groups) == 1
    assert sorted(groups[0]) == ["Paper a", "Paper b"]


def test_lineage_ignores_lone_papers():
    """혼자인 논문은 갈래가 아니다."""
    by_paper = {"a": {"R1"}, "b": {"Z1"}}
    rows = [{"arxiv_id": k, "title": k} for k in "ab"]
    assert trend_report.lineage_groups(by_paper, rows) == []


def test_lineage_needs_no_extra_api_calls(monkeypatch):
    """핵심 — 계보 묶기는 공통 인용이 이미 받아온 집합을 재사용한다."""
    calls = []

    async def fake(aid, limit, edge):
        calls.append(aid)
        import json as _json
        return _json.dumps({"papers": [{"title": "Base", "citationCount": 900},
                                       {"title": "Other", "citationCount": 3}]})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert len(calls) == 2                       # 논문당 한 번, 그게 전부
    trend_report.lineage_groups(scan.by_paper, [_row("1"), _row("2")])
    assert len(calls) == 2                       # 묶는 데는 한 번도 안 부른다
    assert scan.cites["Base"] == 900


def test_shared_references_ranked_by_citation_count_within_same_share(monkeypatch):
    """'분야의 토대라 다들 인용한다'와 '우연히 같은 무명 논문을 인용했다'가
    같은 줄에 섞여 있었다 — 둘 다 2편이 인용해도 순서가 갈려야 한다."""
    async def fake(aid, limit, edge):
        import json as _json
        return _json.dumps({"papers": [{"title": "Obscure", "citationCount": 1},
                                       {"title": "Foundational", "citationCount": 50000}]})

    monkeypatch.setattr(trend_report.http_client, "s2_citation_graph", fake)
    scan = asyncio.run(trend_report.shared_references(None, [_row("1"), _row("2")]))
    assert [t for t, _n in scan.shared] == ["Foundational", "Obscure"]

    text = trend_report.format_report([_row("1")], [], PROFILE, shared=scan.shared,
                                      examined=2, targets=2, cites=scan.cites)
    assert "총 인용 50,000" in text


def test_frontier_looks_forward_from_the_foundations_not_our_papers():
    """§8-40 넷째 항목. 순진한 형태('우리 논문을 누가 인용하나')는 안 된다 —
    delta 논문은 30일 이내라 인용이 0이다. 토대 논문에서 앞으로 본다."""
    seeds = {"Attention Is All You Need": "1706.03762"}
    asked = []
    this_year = datetime.now(timezone.utc).year

    async def fake(aid, limit, edge):
        asked.append((aid, edge))
        import json as _json
        return _json.dumps({"papers": [
            {"title": "Recent work building on it", "year": this_year},
            {"title": "Old work", "year": 2018},          # 최전선이 아니다
            {"title": "No year given"},
        ]})

    import trend_report as tr
    orig = tr.http_client.s2_citation_graph
    tr.http_client.s2_citation_graph = fake
    try:
        frontier, examined = asyncio.run(tr.frontier_papers(
            None, [("Attention Is All You Need", 3)], seeds))
    finally:
        tr.http_client.s2_citation_graph = orig

    assert asked == [("1706.03762", "citations")]     # 앞 방향으로 물었다
    assert examined == 1
    titles = [t for t, _n in frontier]
    assert "Recent work building on it" in titles
    assert "Old work" not in titles                   # 오래된 인용은 최전선이 아니다


def test_frontier_skips_references_without_an_arxiv_id():
    """S2 인용망은 arXiv ID 로 찾는다 — ID 없는 토대는 씨앗이 못 된다."""
    frontier, examined = asyncio.run(
        trend_report.frontier_papers(None, [("Some Book", 5)], {}))
    assert frontier == [] and examined == 0


def test_frontier_failure_does_not_break_the_report():
    async def boom(aid, limit, edge):
        raise RuntimeError("S2 죽음")

    import trend_report as tr
    orig = tr.http_client.s2_citation_graph
    tr.http_client.s2_citation_graph = boom
    try:
        frontier, examined = asyncio.run(
            tr.frontier_papers(None, [("T", 2)], {"T": "1706.03762"}))
    finally:
        tr.http_client.s2_citation_graph = orig
    assert frontier == [] and examined == 0
