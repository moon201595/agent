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


def _add(db, aid, title, days_ago, source=None, engine="gemini", coverage=1.0,
         coverage_kind="measured"):
    """coverage_kind 기본값이 'measured' 인 이유(2026-09-07, §8-70): 주간 리뷰의
    "원문을 다 못 본 요약 N편"은 실측만 센다. 'planned' 는 그 엔진 설정의
    상한이라 실제로 못 봤다는 근거가 아니다."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, published, source) "
                    "VALUES (?,?,?,?)", (aid, title, ts, source))
        con.execute("INSERT OR REPLACE INTO summaries "
                    "(arxiv_id, created_at, engine, coverage_ratio, coverage_kind) "
                    "VALUES (?,?,?,?,?)",
                    (aid, ts, engine, coverage, coverage_kind))


def _build(db):
    return asyncio.run(trend_report.build(db, PROFILE, client=None))


def test_counts_this_week_and_compares_with_last_week(db):
    _add(db, "a", "A defect detection method", 1)
    _add(db, "b", "Another defect detection study", 2)
    _add(db, "c", "An old defect detection paper", 9)      # 지난주
    text = _build(db)
    assert "처리한 논문 2편 (지난주 1편)" in text
    assert "| defect detection | 2 | 1 | +1 |" in text   # 표 행: 이름 | 이번 주 | 지난주 | 증감


def test_weekly_review_shows_frequent_terms_as_a_table_without_old_labels(db):
    """2026-09-14 사용자 요청. 이 테스트가 잡는 것: "등록 안 된 말"·"(키워드로 넣을지는 사람이 판단)"
    문구가 되살아나는 것, 자주 나오는 용어가 표가 아니라 "용어 — N편 (지난주 없었음)" 나열로
    돌아가는 것, 비교 구간이 라벨 줄(KST)이 아니게 되는 것."""
    for i in range(3):
        _add(db, f"s{i}", f"Spiking sensor fusion for defect detection {i}", 1)
    text = _build(db)
    assert "▶ 자주 나오는 용어" in text
    assert "| spiking sensor fusion | 3 | 0 | +3 |" in text   # 긴 조합이 짧은 것을 품는다(_subsumed)
    assert "등록 안 된 말" not in text and "사람이 판단" not in text
    assert "   비교 구간 : " in text and "(KST)" in text
    # 표시값이 실제 KST 인지 — 끝 시각이 지금(KST)과 2분 안이어야 한다(UTC 그대로 찍으면 9시간 어긋난다)
    import re
    from zoneinfo import ZoneInfo
    m = re.search(r"비교 구간 : \d\d-\d\d \d\d:\d\d ~ (\d\d)-(\d\d) (\d\d):(\d\d) \(KST\)", text)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    shown = now.replace(month=int(m.group(1)), day=int(m.group(2)), hour=int(m.group(3)), minute=int(m.group(4)))
    assert abs((now - shown).total_seconds()) < 120


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


def test_planned_coverage_is_not_counted_as_a_short_read(db):
    """**§8-70 정정**(2026-09-07). 'planned' 는 그 엔진 설정의 **상한**이지
    "이만큼밖에 못 봤다"는 실측이 아니다. 이걸 같이 세면 "원문을 다 못 본
    요약 N편"이라는 셈 자체가 실측이 아니게 된다(규칙 8).
    """
    _add(db, "a", "defect detection", 1, engine="groq", coverage=0.40,
         coverage_kind="planned")
    text = _build(db)
    assert "원문을 다 못 본 요약" not in text


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
    """이 테스트가 잡는 것: 실제 복합어에서 짧은 n-gram이 긴 n-gram과 함께 보고되는 것."""
    rows = [_text_row("x", "spiking sensor fusion is useful") for _ in range(5)]
    got = {t for t, _n, _w in trend_report.emerging_terms(rows, _PROFILE)}
    assert "spiking sensor fusion" in got
    assert "spiking sensor" not in got


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
    corpus, used, _enriched = trend_report._narrative_corpus(rows)
    assert used == 4
    assert "Uploaded conference paper" in corpus


def test_prompt_carries_topics_but_never_our_measurements():
    """핵심 회귀 — 개정된 규칙 4 의 경계선.

    '무엇에 관심 있나'(core_topics)는 나가도 되지만 '무엇을 하고 있나'
    (집계·편수·별점·가중치·미등록 용어)는 안 된다.
    """
    corpus, _used, _enriched = trend_report._narrative_corpus(_PUB)
    profile = {"core_topics": ["defect detection", "in-sensor computing"],
               "core_weights": {"defect detection": 1.0, "in-sensor computing": 0.6}}
    # movement 는 2026-09-18 에 붙인 최근 창 맥락 자리다 — 여기서는 비워 두고,
    # 값이 들어갔을 때 수치가 새지 않는지는 아래에서 따로 본다.
    prompt = trend_report._NARRATIVE_PROMPT.format(
        papers=corpus, topics=trend_report.narrative_topics(profile), movement="", history="", weekly="", anchors="")

    assert "defect detection" in prompt          # 관심 분야는 나간다
    # 우리 쪽 측정값은 하나도 안 나간다. ("편수"라는 낱말 자체는 프롬프트에
    # 있지만 그건 "숫자를 쓰지 말라"는 지시어지 데이터가 아니다 — 값이 새는지를
    # 본다.)
    with_ctx = trend_report._NARRATIVE_PROMPT.format(
        papers=corpus, topics=trend_report.narrative_topics(profile),
        movement=trend_report._movement_context(
            {"comparable": True, "days": 7, "terms": [("agentic rl", 9, 3)]}), history="", weekly="", anchors="")
    assert "agentic rl" in with_ctx              # 늘어난 "말"은 맥락으로 나간다
    assert " 9" not in with_ctx.split("agentic rl")[1][:60]   # 그 말의 편수는 안 나간다

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
    corpus, _used, _enriched = trend_report._narrative_corpus(_PUB)
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


def test_report_labels_the_narrative_as_llm_written():
    """셈과 서술이 한 화면에서 섞이면 안 된다 — 어디부터 해석인지 보여야 한다."""
    out = trend_report.format_report([], [], {"core_topics": []},
                                     story=("결함 검출이 온센서와 만난다.", []))
    assert "제목·초록만 보고 쓴 것" in out
    assert "결함 검출이 온센서와 만난다." in out


def test_report_warns_about_invented_numbers():
    out = trend_report.format_report([], [], {"core_topics": []},
                                     story=("17편이 늘었다.", ["17"]))
    assert "원문에 없는 숫자" in out and "17" in out


def test_weekly_narrative_gets_the_same_citation_warning_as_daily():
    """외부 검토(2026-09-14): 근거 ID 감사가 일일 서술에만 연결돼 있었다.
    이 테스트가 잡는 것: 감사 결과가 나빠도 주간 리뷰에 경고가 안 붙는 것, 멀쩡한데 붙는 것."""
    bad = {"lines": 2, "cited_lines": 1, "unknown": ["[P99:A]"], "title_only": []}
    good = {"lines": 1, "cited_lines": 1, "unknown": [], "title_only": []}
    warn = "일부 주장에 내용 근거 ID가 없거나 유효하지 않다"
    assert warn in trend_report.format_report([], [], {"core_topics": []}, story=("주장 [P99:A]\n무인용 주장", []), audit=bad)
    assert warn not in trend_report.format_report([], [], {"core_topics": []}, story=("주장 [P1:A]", []), audit=good)


def test_weekly_build_normalises_interpretation_marks_and_audits(db, monkeypatch):
    """주간 서술도 "~므로 해석이다" 를 문장으로 고치고(일일과 같은 함수) 근거 ID 를 감사한다.
    이 테스트가 잡는 것: build 가 LLM 서술을 후처리 없이 그대로 싣는 것."""
    _add(db, "a", "A defect detection method", 1)

    async def fake_narrative(client, rows, profile=None, summaries=None):
        return ("근거가 없으므로 해석이다 [P99:A]", [], 0)

    monkeypatch.setattr(trend_report, "narrative", fake_narrative)
    text = asyncio.run(trend_report.build(db, PROFILE, client=object(), with_references=False))
    assert "근거가 없다 (해석)" in text and "없으므로 해석이다" not in text
    assert "일부 주장에 내용 근거 ID가 없거나 유효하지 않다" in text


def test_list_markers_are_not_treated_as_claims():
    """실측 오탐 — 첫 라이브 호출에서 목차 번호 1·3 에 경고가 붙었다.
    매번 뜨는 경고는 아무도 안 읽으므로 진짜 조작을 놓치게 만든다."""
    corpus, _used, _enriched = trend_report._narrative_corpus(_PUB)
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
    corpus, used, _enriched = trend_report._narrative_corpus(rows)
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
    """S2 인용망은 arXiv ID 로 찾는다 — ID 없는 토대는 시드가 못 된다."""
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


# ------------------------------------------- 서술이 원문 요약까지 본다 (2026-09-08)
#
# 초록은 저자가 쓴 홍보문이고 ④ 요약의 결과 절은 우리가 원문 전체를 읽고 뽑은
# 것이다. "그래서 무엇이 나왔나"는 거기 있다. 다만 **요약이 있는 논문만 깊어지면
# 그 논문들이 서술을 독식**하므로(§8-44 에서 데인 패턴) 붙이는 범위를 좁게 잡고,
# 실제로 몇 편에 붙었는지를 돌려줘 라벨이 그 수를 말하게 한다(규칙 8).


def _summary_file(tmp_path, aid, results):
    body = "### 기본정보\n- 제목 : T\n\n### 결과\n" + "".join(f"- {r}\n" for r in results)
    f = tmp_path / f"{aid}.md"
    f.write_text(body, encoding="utf-8")
    return f


def _store_summary(db, aid, path, *, total=10, matched=10, cov=1.0, kind="measured"):
    """서술에 넣을 자격을 갖춘 요약 한 편. 인자를 낮추면 자격을 잃는다."""
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO summaries "
            "(arxiv_id, path, numbers_total, numbers_matched, coverage_ratio, coverage_kind) "
            "VALUES (?,?,?,?,?,?)", (aid, str(path), total, matched, cov, kind))


def test_result_excerpts_pulls_only_the_result_section(db, tmp_path):
    f = _summary_file(tmp_path, "a", ["정확도 91.2%로 올랐다", "지연은 5ms 였다"])
    _store_summary(db, "a", f)
    out = trend_report.result_excerpts(db, ["a"])
    assert "정확도 91.2%" in out["a"] and "지연은 5ms" in out["a"]
    assert "기본정보" not in out["a"]      # 다른 절은 안 끌고 온다


def test_missing_summary_has_no_key_not_an_empty_string(db, tmp_path):
    """"요약이 없다"와 "결과가 없다"를 빈 문자열로 뭉개면 라벨이 틀어진다."""
    assert trend_report.result_excerpts(db, ["없는논문"]) == {}
    assert trend_report.result_excerpts(db, []) == {}


def test_excerpt_is_capped(db, tmp_path):
    f = _summary_file(tmp_path, "a", ["가" * 3000])
    _store_summary(db, "a", f)
    out = trend_report.result_excerpts(db, ["a"], limit_chars=120)
    assert len(out["a"]) == 120


def test_corpus_marks_which_papers_carry_a_summary():
    rows = [{"title": "Paper A", "abstract": "초록 A", "arxiv_id": "a"},
            {"title": "Paper B", "abstract": "초록 B", "arxiv_id": "b"},
            {"title": "Paper C", "abstract": "초록 C", "arxiv_id": "c"}]
    corpus, used, enriched = trend_report._narrative_corpus(
        rows, {"a": "정확도 91.2%로 올랐다"})
    assert used == 3
    assert enriched == 1                       # 라벨이 이 수를 말한다
    assert "[원문 요약 · 결과] 정확도 91.2%" in corpus
    assert corpus.count("[원문 요약 · 결과]") == 1
    assert "초록 B" in corpus                   # 요약 없는 논문도 그대로 들어간다


def test_corpus_without_summaries_is_unchanged():
    rows = [{"title": "Paper A", "abstract": "초록 A", "arxiv_id": "a"}] * 3
    plain, _u, enriched = trend_report._narrative_corpus(rows)
    assert enriched == 0
    assert "[원문 요약" not in plain


def test_verified_summary_numbers_count_as_grounded():
    """**⑤ 검증을 통과한** 요약의 숫자를 서술이 인용하면 근거가 있는 것이다.
    통과하지 못한 요약은 애초에 corpus 에 못 들어온다(아래 테스트 참고) —
    그 구분이 없으면 ⑨ 의 "원문에 없는 숫자" 경고가 조용히 꺼진다."""
    rows = [{"title": "A", "abstract": "초록", "arxiv_id": "a"},
            {"title": "B", "abstract": "초록", "arxiv_id": "b"},
            {"title": "C", "abstract": "초록", "arxiv_id": "c"}]
    corpus, _u, _e = trend_report._narrative_corpus(rows, {"a": "정확도 91.2% 를 기록했다"})
    assert trend_report.ungrounded_numbers("정확도가 91.2% 로 올랐다.", corpus) == []


# ------------------------------------------- 외부 검토(Codex)가 잡은 네 구멍 (2026-09-08)
#
# 요약을 서술에 넣으면서 내가 만든 것들이다. 넷 다 코드로 대조해 사실을 확인했다.


def test_unverified_summary_never_reaches_the_narrative(db, tmp_path):
    """**제일 아픈 것.** save_summary 는 수치가 안 맞아도 저장한다(불일치도
    기록으로 남겨야 하니까). 그런 요약을 corpus 에 넣으면 ⑨ 가 "원문에 없는
    숫자"를 대조하는 바로 그 corpus 에 원문에 없던 숫자가 들어가 경고가 꺼진다.

    실측(2026-09-08): 저장된 요약 132편 중 57편(43%)에 불일치가 있다.
    """
    f = _summary_file(tmp_path, "a", ["정확도 91.2%로 올랐다"])
    _store_summary(db, "a", f, total=10, matched=7)      # ⑤ 가 3건을 못 맞혔다
    assert trend_report.result_excerpts(db, ["a"]) == {}


def test_partially_read_summary_is_not_called_full_text(db, tmp_path):
    """프롬프트가 이 발췌를 "원문 전체를 읽고 뽑은 결과"라고 소개한다.
    청크가 중간에 실패한 부분 요약을 그렇게 부르면 재지 않은 것을 쟀다고
    하는 것이다(규칙 8)."""
    f = _summary_file(tmp_path, "a", ["결과가 좋았다"])
    _store_summary(db, "a", f, cov=0.42)                  # 원문의 42%만 봤다
    assert trend_report.result_excerpts(db, ["a"]) == {}


def test_unknown_coverage_summary_is_excluded(db, tmp_path):
    """커버리지를 모르는 구형 요약(coverage_kind NULL)도 마찬가지다 —
    모르는 것을 "다 봤다"로 취급하지 않는다."""
    f = _summary_file(tmp_path, "a", ["결과가 좋았다"])
    _store_summary(db, "a", f, cov=None, kind=None)
    assert trend_report.result_excerpts(db, ["a"]) == {}
    _store_summary(db, "a", f, cov=1.0, kind="planned")   # 계획 상한도 실측이 아니다
    assert trend_report.result_excerpts(db, ["a"]) == {}


def test_broken_summary_file_does_not_kill_the_whole_narrative(db, tmp_path):
    """`except OSError` 는 UnicodeDecodeError 를 안 잡는다. 호출부가 발췌와
    서술 생성을 같은 try 로 감싸므로, 여기서 예외가 나가면 초록만으로 쓸 수
    있었던 글까지 통째로 사라진다."""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"### \xff\xfe\xfa\n- \xff\xff")
    good = _summary_file(tmp_path, "b", ["결과가 좋았다"])
    _store_summary(db, "a", bad)
    _store_summary(db, "b", good)

    out = trend_report.result_excerpts(db, ["a", "b"])    # 예외가 나가면 안 된다
    assert "b" in out and "결과가 좋았다" in out["b"]


def test_same_paper_in_both_lists_is_counted_once():
    """같은 논문이 내용 자리와 각주에 겹쳐 들어오면 요약이 두 번 붙고 편수가
    부풀려진다 — "각주에는 안 붙인다"도 라벨의 편수도 거짓이 된다."""
    dup = {"title": "Paper A", "abstract": "초록 A", "arxiv_id": "a"}
    rows = [dup, dict(dup), {"title": "B", "abstract": "초록 B", "arxiv_id": "b"},
            {"title": "C", "abstract": "초록 C", "arxiv_id": "c"}]
    corpus, used, enriched = trend_report._narrative_corpus(rows, {"a": "결과가 좋았다"})
    assert corpus.count("[원문 요약 · 결과]") == 1
    assert enriched == 1
    assert used == 4          # 목록 자체는 손대지 않는다 — 요약만 한 번 붙인다


def test_prompt_demands_the_shape_digest_renders():
    """**프롬프트·조립·렌더링이 같은 형식을 말해야 한다.** story-v2(2026-10-01)부터 모델은 JSON 을 내고 소제목은
    `render_story` 가 쓴다. 망가뜨리면 실패하는 것: 프롬프트가 스키마 키를 안 시키는 것 · 조립한 소제목을 digest 가
    소제목으로 못 알아보는 것(흐름 소제목 `■ 1. 이름` 포함) · 프롬프트가 다시 "칸 채우기" 절 이름을 시키는 것."""
    import digest

    prompt = trend_report._NARRATIVE_PROMPT
    for key in trend_report.STORY_SCHEMA["required"]:
        assert f'"{key}"' in prompt, f"프롬프트가 '{key}' 를 시키지 않는다"
    assert "갈래" not in prompt and "넘어올 것" not in prompt
    corpus = "- [P1:T] Alpha paper\n  [P1:A] a\n- [P2:T] Beta paper\n  [P2:A] b\n- [P3:T] Gamma paper\n  [P3:A] c"
    plan = {"headline": "질문과 답 [P1:A]", "threads": [{"name": "흐름", "papers": ["P1", "P2"], "body": "공통 [P1:A][P2:A]"}],
            "relation": "", "implications": ["볼 것 [P1:A]"], "side_signals": [{"paper": "P3", "note": "주변"}]}
    text = trend_report.render_story(trend_report.repair_story(plan, corpus))
    heads = [ln for ln in text.splitlines() if ln.startswith("■")]
    assert heads == ["■ 오늘의 요점", "■ 1. 흐름", "■ 우리 연구에서 볼 것", "■ 주변 신호"]
    assert all(digest._is_narrative_heading(h) for h in heads)
    assert not digest._is_narrative_heading("1. 본문 목록 줄")       # ■ 없는 번호 줄은 소제목이 아니다


def test_citation_audit_treats_branch_list_lines_as_evidence_of_the_lead_sentence():
    """2026-09-12: 갈래를 "설명 문장 + '- 제목 [P#]' 줄"로 바꾸자 설명 문장이 근거 없는 줄로, 목록의
    T 가 제목만 근거로 잡혀 ⚠ 가 떴다. 이 테스트가 잡는 것: 목록 줄을 앞 문장에 안 붙이는 것,
    목록 줄의 T 를 title_only 로 세는 것, 그러면서 설명 문장의 T 는 놓치는 것."""
    corpus = "- [P1:T] A\n  [P1:A] a\n- [P2:T] B\n  [P2:A] b"
    text = "■ 갈래\n첫째, 이런 흐름이다.\n- A [P1:A]\n- B [P2:T]\n둘째, 저런 흐름이다 [P2:T]."
    a = trend_report.citation_audit(text, corpus)
    assert a["lines"] == 2 and a["cited_lines"] == 2, "목록 줄은 앞 문장에 딸린다"
    assert a["title_only"] == ["[P2:T]"] and a["unknown"] == []
    assert "[P2:T]" in a["cited"]
    # 목록 줄이 아닌 곳의 T 만 title_only — 목록에만 T 가 있으면 비어야 한다
    b = trend_report.citation_audit("첫째, 흐름이다.\n- B [P2:T]", corpus)
    assert b["title_only"] == [] and b["cited_lines"] == 1


# ── story-v2: 모델은 구조를 내고 Python 이 손질·조립한다 (2026-10-01, 사용자 지적 "지금 내용이 처참해")

_STORY_CORPUS = ("- [P1:T] FLASH: generate once synthesize many\n  [P1:A] a1\n  [P1:R] [원문 요약 · 결과] r1\n"
                 "- [P2:T] Visual anomaly synthesis for model selection\n  [P2:A] a2\n"
                 "- [P3:T] Wood defect detection with YOLO\n  [P3:A] a3\n"
                 "- [P4:T] Prototype aligned few-shot defect network\n  [P4:A] a4\n"
                 "- [P5:T] Title only paper\n"
                 "- [P6:T] Few-shot welding defect detection\n  [P6:A] a6\n"
                 "- [P7:T] Goose down YOLO\n  [P7:A] a7\n")


def _plan(**over):
    plan = {"headline": "결함 데이터 부족에 두 방향으로 답한다 [P1:A][P4:A]",
            "threads": [{"name": "합성 결함의 역할 확대", "papers": ["P1", "P2"], "body": "둘 다 합성 결함을 쓴다 [P1:R][P2:A]."},
                        {"name": "few-shot 검사", "papers": ["P4", "P6"], "body": "적은 샘플로 학습한다 [P4:A][P6:A]."}],
            "relation": "같은 문제에 대한 다른 대응이다 [P1:A][P4:A]",
            "implications": ["합성 결함의 현실성을 확인한다 [P2:A]", "근거 없는 제안"],
            "side_signals": [{"paper": "P7", "note": "경량 미세질감 검출"}, {"paper": "P5", "note": "제목만"}]}
    plan.update(over)
    return plan


def test_story_repair_keeps_threads_to_their_own_evidence():
    """망가뜨리면 실패하는 것: 흐름 body 에 다른 논문 근거를 남기는 것(거위털이 흐름에 끼는 10-01 실물) ·
    body 가 부르지 않은 논문을 목록에 남기는 것 · 한 논문을 두 흐름에 넣는 것 · 2편 미만을 흐름이라 부르는 것."""
    plan = _plan(threads=[
        {"name": "합성 결함", "papers": ["P1", "P2", "P3"], "body": "합성 결함이다 [P1:A][P2:A][P7:A]."},  # P3 는 body 에 없음, P7 은 남의 근거
        {"name": "중복", "papers": ["P2", "P6"], "body": "겹친다 [P2:A][P6:A]."},                          # P2 는 이미 앞 흐름
        {"name": "few-shot", "papers": ["P4", "P6"], "body": "적은 샘플 [P4:A][P6:A]."}])
    story = trend_report.repair_story(plan, _STORY_CORPUS)
    assert [t["papers"] for t in story["threads"]] == [[1, 2], [4, 6]]
    assert "[P7:A]" not in story["threads"][0]["body"]
    assert "thread_too_thin" in story["repairs"]


def test_story_repair_drops_unknown_and_title_only_tags():
    """망가뜨리면 실패하는 것: 자료에 없는 ID(P9)·제목 T 를 근거로 남기는 것 · 근거를 다 잃은 headline 을 싣는 것 ·
    근거 없는 implication 을 해석 표시 없이 싣는 것 · 초록 없는 논문(P5)을 주변 신호로 싣는 것."""
    story = trend_report.repair_story(_plan(headline="머리 [P9:A][P1:T]"), _STORY_CORPUS)
    assert story["headline"] == "" and "headline_uncited" in story["repairs"]
    assert story["implications"] == ["합성 결함의 현실성을 확인한다 [P2:A]", "근거 없는 제안 (해석)"]
    assert [s["paper"] for s in story["side_signals"]] == [7]
    assert story["relation"].endswith("[P1:A][P4:A]")


def test_story_needs_a_card_paper_in_every_thread():
    """anchors(메일 카드 논문 수)가 있으면 흐름마다 카드 논문이 있어야 한다 — 카드와 무관한 흐름이 본문을 차지하지 않게.
    망가뜨리면 실패하는 것: 조건을 빼는 것 · 흐름이 다 빠졌는데 빈 글을 내는 것."""
    plan = _plan(threads=[{"name": "카드 밖", "papers": ["P6", "P7"], "body": "둘 다 밖이다 [P6:A][P7:A]."},
                          {"name": "카드 안", "papers": ["P1", "P6"], "body": "하나는 카드다 [P1:A][P6:A]."}])
    story = trend_report.repair_story(plan, _STORY_CORPUS, anchors={1, 2, 3, 4})
    assert [t["name"] for t in story["threads"]] == ["카드 안"]
    only_outside = _plan(threads=[{"name": "카드 밖", "papers": ["P6", "P7"], "body": "밖 [P6:A][P7:A]."}])
    assert trend_report.repair_story(only_outside, _STORY_CORPUS, anchors={1, 2, 3, 4}) is None
    assert trend_report.repair_story(only_outside, _STORY_CORPUS) is not None     # 주간 글처럼 카드가 없으면 조건 없음


def test_story_render_lists_titles_from_the_corpus():
    """목록 줄의 제목은 자료에서 Python 이 붙인다 — 모델이 쓴 제목이 아니다. 근거는 요약 결과(R)가 있으면 R.
    망가뜨리면 실패하는 것: 흐름 순서·번호 · 제목 대신 ID 만 싣는 것(09-17 "- [P1:T] [P1:T]" 사고) · 흐름이 하나인데 관계 문장을 싣는 것."""
    text = trend_report.render_story(trend_report.repair_story(_plan(), _STORY_CORPUS))
    lines = text.splitlines()
    assert lines[:3] == ["■ 오늘의 요점", "결함 데이터 부족에 두 방향으로 답한다 [P1:A][P4:A]",
                         "같은 문제에 대한 다른 대응이다 [P1:A][P4:A]"]
    assert "■ 1. 합성 결함의 역할 확대" in lines and "■ 2. few-shot 검사" in lines
    assert "- FLASH: generate once synthesize many [P1:R]" in lines
    assert "- Goose down YOLO — 경량 미세질감 검출 [P7:A]" in lines
    one = trend_report.repair_story(_plan(threads=_plan()["threads"][:1]), _STORY_CORPUS)
    assert one["relation"] == ""


def test_parse_story_rejects_broken_shapes():
    """망가뜨리면 실패하는 것: 코드펜스·앞뒤 잡글이 붙은 정상 JSON 을 버리는 것 · 키가 빠진 JSON 을 받아들이는 것."""
    import json
    good = json.dumps(_plan(), ensure_ascii=False)
    assert trend_report.parse_story(f"```json\n{good}\n```")["threads"]
    for bad in ("■ 오늘 눈에 띄는 것\n글", json.dumps({"headline": "x"}), "{not json}"):
        with pytest.raises(ValueError):
            trend_report.parse_story(bad)


def test_narrative_falls_through_engines_until_a_valid_story(monkeypatch):
    """narrative() 가 구조 검증을 실제로 거친다. 망가뜨리면 실패하는 것: 옛 산문을 그대로 싣는 것 ·
    깨진 구조에서 다음 엔진으로 안 가는 것 · 마지막까지 깨졌는데 글을 내는 것 · 메일 카드 수(anchors)를 프롬프트에 안 넣는 것."""
    import json
    import narrative_engine as ne
    import summarize_engine as se
    rows = [{"arxiv_id": f"p{i}", "title": f"Paper number {i}", "abstract": f"abstract {i}", "published": "2026-09-15"}
            for i in (1, 2, 3)]
    story = {"headline": "한 줄 [P1:A]", "threads": [{"name": "흐름", "papers": ["P1", "P2"], "body": "공통 [P1:A][P2:A]"}],
             "relation": "", "implications": [], "side_signals": []}
    prompts = []

    async def gemini(fn, label):
        prompts.append(label)
        return "■ 오늘 눈에 띄는 것\n옛 산문 [P1:A]"

    async def groq(fn, label):
        return json.dumps(story, ensure_ascii=False)
    calls = iter([gemini, groq])
    monkeypatch.setattr(ne, "_codex", lambda *a, **k: None)
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", lambda fn, label: next(calls)(fn, label))
    seen = {}
    real = ne.generate

    async def spy(client, prompt, **kw):
        seen["prompt"], seen["kw"] = prompt, kw
        return await real(client, prompt, **kw)
    monkeypatch.setattr(ne, "generate", spy)
    text, ungrounded, _enriched, engine = asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}, anchors=2))
    assert engine == "groq" and "■ 1. 흐름" in text and "옛 산문" not in text
    assert "- Paper number 1 · 초록 기반 [P1:A]" in text                  # 상태가 원문 분석이 아니면 초록 기반으로 적는다
    assert "P1, P2 는 이 메일 아래에 상세 카드로" in seen["prompt"]
    assert seen["kw"]["schema"] is trend_report.STORY_SCHEMA         # Codex 에 출력 모양을 넘긴다

    async def always_prose(fn, label):
        return "산문만 [P1:A]"
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", always_prose)
    assert asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]})) is None


def test_clipping_never_strips_evidence_that_was_counted():
    """Codex 검토 2026-10-01: 근거를 센 **뒤에** 글자 수로 잘라 흐름·headline 이 근거 없이 남았다.
    망가뜨리면 실패하는 것: 자르기를 근거 판정 뒤로 되돌리는 것 · 문장 단위가 아니라 글자로 잘라 문장 끝 근거를 떨구는 것 ·
    잘려서 근거를 잃은 implication 에 "(해석)" 을 안 붙이는 것."""
    long_body = "가" * 510 + " [P1:A][P2:A]"
    story = trend_report.repair_story(_plan(threads=[{"name": "길다", "papers": ["P1", "P2"], "body": long_body},
                                                     {"name": "few-shot", "papers": ["P4", "P6"], "body": "적은 샘플 [P4:A][P6:A]."}],
                                            headline="나" * 500 + " [P4:A]",          # 상한(450)보다 길어야 잘린다 — 2026-10-06 요점 상한 300→450
                                            implications=["다" * 400 + " [P4:A]"]), _STORY_CORPUS)
    assert [t["papers"] for t in story["threads"]] == [[4, 6]]       # 근거가 잘린 흐름은 흐름이 아니다
    assert story["headline"] == "" and "headline_uncited" in story["repairs"]
    assert story["implications"][0].endswith("(해석)") and "[P4:A]" not in story["implications"][0]
    sentences = "첫 문장이다 [P1:A]. " + "둘째 " * 200 + "문장 [P2:A]."
    kept = trend_report.repair_story(_plan(threads=[{"name": "문장", "papers": ["P1", "P2"], "body": sentences},
                                                    {"name": "few-shot", "papers": ["P4", "P6"], "body": "적은 샘플 [P4:A][P6:A]."}]),
                                     _STORY_CORPUS)
    assert kept["threads"][0]["name"] == "few-shot"                 # 둘째 문장이 잘려 P2 근거가 없으면 흐름이 아니다
    for field in (kept["headline"], kept["relation"], *kept["implications"], *(t["body"] for t in kept["threads"])):
        assert len(field) <= max(trend_report._STORY_LIMITS.values()) + len(" (해석)")


def test_bundled_and_malformed_tags_never_reach_the_mail():
    """Codex 검토 2026-10-01: 이름·메모의 "[P99:A, P1:T]" 와 본문의 "[P1:A, P99:Z]" 가 정리를 우회했다.
    망가뜨리면 실패하는 것: 이름·메모에 근거 ID 모양을 남기는 것 · 묶음 속 정상 근거를 버리는 것 · 모양이 틀린 근거를 남기는 것 ·
    모델 글 줄머리의 ■ 를 남겨 렌더러가 흐름 소제목으로 읽게 하는 것."""
    plan = _plan(threads=[{"name": "■ 이름 [P99:A, P1:T]", "papers": ["P1", "P2"], "body": "■ 9. 공통 [P1:A, P99:Z] 그리고 [P2:A; P9:A]."}],
                 side_signals=[{"paper": "P7", "note": "주변 [P99:A, P1:T] [P7:A]"}])
    story = trend_report.repair_story(plan, _STORY_CORPUS)
    thread = story["threads"][0]
    assert thread["name"] == "이름" and thread["papers"] == [1, 2]
    assert "P99" not in thread["body"] and "P9:" not in thread["body"] and not thread["body"].startswith("■")
    assert "[P1:A]" in thread["body"] and "[P2:A]" in thread["body"]
    assert story["side_signals"] == [{"paper": 7, "note": "주변"}]
    import digest
    lines = trend_report.render_story(story).splitlines()
    body_line = lines[lines.index("■ 1. 이름") + 1]
    assert body_line == thread["body"] and not digest._is_narrative_heading(body_line)


def test_numbers_the_model_wrote_are_flagged_even_at_line_start():
    """Codex 검토 2026-10-01: 목차 예외에 ■ 를 넣자 모델이 쓴 "■ 987." 의 987 이 경고에서 빠졌다. 흐름 번호는 우리 목차라 빼되
    모델 글에는 예외를 주지 않는다. 망가뜨리면 실패하는 것: 숫자 대조를 렌더링한 글로 되돌리는 것 · model_text 에서 줄머리 표시를 빼는 것."""
    import json
    import narrative_engine as ne
    import summarize_engine as se
    rows = [{"arxiv_id": f"p{i}", "title": f"Paper number {i}", "abstract": "no digits here", "published": ""} for i in (1, 2, 3)]
    story = {"headline": "한 줄 [P1:A]", "threads": [{"name": "흐름", "papers": ["P1", "P2"], "body": "■ 987. 성능이 올랐다 [P1:A][P2:A]"}],
             "relation": "", "implications": [], "side_signals": []}

    async def gemini(fn, label):
        return json.dumps(story, ensure_ascii=False)
    monkeypatch_targets = (ne, "_codex", lambda *a, **k: None)
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr(*monkeypatch_targets)
        mp.setattr(se, "_call_with_rate_limit_retry", gemini)
        text, ungrounded, _e, _engine = asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}))
    finally:
        mp.undo()
    assert "■ 1. 흐름" in text and ungrounded == ["987"]


def test_card_rule_follows_corpus_numbers_when_a_row_has_no_title(monkeypatch):
    """Codex 검토 2026-10-01: 제목 없는 카드가 자료에서 빠지면 번호가 밀려 각주 논문이 카드로 판정됐다.
    rows = [제목 없는 카드, 카드, 각주 A, 각주 B], anchors=2 → 카드는 P1 하나. 망가뜨리면 실패하는 것: 카드를 "앞 n 편"으로 세는 것."""
    import json
    import narrative_engine as ne
    import summarize_engine as se
    rows = [{"arxiv_id": "x0", "title": "  ", "abstract": "a"},
            {"arxiv_id": "x1", "title": "Card paper", "abstract": "a1"},
            {"arxiv_id": "x2", "title": "Footnote A", "abstract": "a2"},
            {"arxiv_id": "x3", "title": "Footnote B", "abstract": "a3"}]
    plans = {"outside": {"headline": "h [P2:A]", "threads": [{"name": "각주끼리", "papers": ["P2", "P3"], "body": "b [P2:A][P3:A]"}],
                         "relation": "", "implications": [], "side_signals": []},
             "inside": {"headline": "h [P1:A]", "threads": [{"name": "카드 포함", "papers": ["P1", "P2"], "body": "b [P1:A][P2:A]"}],
                        "relation": "", "implications": [], "side_signals": []}}
    monkeypatch.setattr(ne, "_codex", lambda *a, **k: None)
    for key, expect in (("outside", None), ("inside", "■ 1. 카드 포함")):
        async def answer(fn, label, key=key):
            return json.dumps(plans[key], ensure_ascii=False)
        monkeypatch.setattr(se, "_call_with_rate_limit_retry", answer)
        got = asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}, anchors=2))
        assert (got is None) if expect is None else (expect in got[0])
    assert trend_report._included_rows(rows) == [1, 2, 3]


def test_story_caps_and_label_rules():
    """망가뜨리면 실패하는 것: 이름 없는 흐름을 받는 것 · 흐름 상한(3) · implication 상한(3) · 주변 신호 상한(5)·중복·흐름 논문 재사용 ·
    한 흐름 안 같은 ID 중복 · 근거 없는 관계 문장에 "(해석)" 을 안 붙이는 것."""
    corpus = "".join(f"- [P{i}:T] Paper title number {i}\n  [P{i}:A] a{i}\n" for i in range(1, 16))
    threads = [{"name": "", "papers": ["P1", "P2"], "body": "x [P1:A][P2:A]"}] + [
        {"name": f"흐름{i}", "papers": [f"P{2 * i + 1}", f"P{2 * i + 1}", f"P{2 * i + 2}"],
         "body": f"y [P{2 * i + 1}:A][P{2 * i + 2}:A]"} for i in range(1, 5)]
    plan = {"headline": "h [P3:A]", "threads": threads, "relation": "관계만 말한다",
            "implications": [f"볼 것 {i} [P3:A]" for i in range(5)],
            "side_signals": [{"paper": "P3", "note": "흐름 논문"}, {"paper": "P11", "note": "a"}, {"paper": "P11", "note": "중복"}]
                            + [{"paper": f"P{i}", "note": f"n{i}"} for i in range(12, 16)] + [{"paper": "P1", "note": "여섯째"}]}
    story = trend_report.repair_story(plan, corpus)
    assert [t["name"] for t in story["threads"]] == ["흐름1", "흐름2", "흐름3"] and "thread_cap" in story["repairs"]
    assert story["threads"][0]["papers"] == [3, 4]
    assert len(story["implications"]) == 3
    assert [s["paper"] for s in story["side_signals"]] == [11, 12, 13, 14, 15]
    assert story["relation"] == "관계만 말한다 (해석)"


def test_full_text_gets_the_floor_but_not_the_gate(monkeypatch):
    """2026-10-01 사용자 결정 + 판단: 순위는 관련도, 서사의 **발언권**은 깊이. 원문 확보는 흐름의 **자격이 아니다** — 저널 위주 분야의
    실제 흐름이 수집 사정으로 밀리면 안 된다. rows = [원문 카드 P1, 초록 카드 P2, 원문 카드 P3, 각주 P4].
    망가뜨리면 실패하는 것: 초록 카드 흐름을 버리는 것(자격으로 쓰기) · 각주만의 흐름을 받는 것 · 원문 흐름을 앞에 안 두는 것 ·
    목록에 깊이를 안 적는 것 · 프롬프트가 깊이를 안 알려 주는 것."""
    import json
    import narrative_engine as ne
    import summarize_engine as se
    rows = [{"arxiv_id": "a1", "title": "Full text card one", "abstract": "x", "deep_status": "ok"},
            {"arxiv_id": "a2", "title": "Abstract only card", "abstract": "x", "deep_status": "abstract_only"},
            {"arxiv_id": "a3", "title": "Full text card two", "abstract": "x", "deep_status": "skipped: 이미 요약 저장됨"},
            {"arxiv_id": "a4", "title": "Footnote paper one", "abstract": "x"},
            {"arxiv_id": "a5", "title": "Footnote paper two", "abstract": "x"},
            {"arxiv_id": "a6", "title": "Footnote paper three", "abstract": "x"}]
    # 각주끼리 흐름은 첫 흐름과 논문이 겹치지 않아야 한다 — 겹치면 중복 제거로 한 편만 남아 "카드 조건"이 아니라 "2편 미만"으로
    # 빠지고, 카드 조건을 지워도 이 테스트가 통과했다(Codex 최종 검토 2026-10-01 변이 실측).
    plan = {"headline": "h [P1:A]", "threads": [
                {"name": "초록 카드 흐름", "papers": ["P2", "P4"], "body": "b [P2:A][P4:A]"},
                {"name": "각주끼리", "papers": ["P5", "P6"], "body": "b [P5:A][P6:A]"},
                {"name": "원문 흐름", "papers": ["P1", "P3"], "body": "b [P1:A][P3:A]"}],
            "relation": "", "implications": [], "side_signals": []}
    prompts = []
    monkeypatch.setattr(ne, "_codex", lambda *a, **k: None)

    async def answer(fn, label):
        return json.dumps(plan, ensure_ascii=False)
    real = ne.generate

    async def spy(client, prompt, **kw):
        prompts.append(prompt)
        return await real(client, prompt, **kw)
    monkeypatch.setattr(ne, "generate", spy)
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", answer)
    text = asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}, anchors=3))[0]
    heads = [ln for ln in text.splitlines() if ln.startswith("■ ") and ln[2].isdigit()]
    assert heads == ["■ 1. 원문 흐름", "■ 2. 초록 카드 흐름"]                     # 각주끼리는 빠지고 원문 흐름이 앞
    assert "- Full text card one · 원문 분석 [P1:A]" in text
    assert "- Abstract only card · 초록 기반 [P2:A]" in text and "- Footnote paper one · 초록 기반 [P4:A]" in text
    assert "그중 P1, P3 는 본문을 읽고 요약한 논문이다" in prompts[0] and "P2 는 초록만 본 논문이다" in prompts[0]
    weekly = trend_report.render_story(trend_report.repair_story(plan, "".join(
        f"- [P{i}:T] {r['title']}\n  [P{i}:A] x\n" for i, r in enumerate(rows, 1))))
    assert "원문 분석" not in weekly and "초록 기반" not in weekly                 # 깊이 정보가 없는 글엔 표시 없음


def test_abstract_only_results_are_marked_and_open_tags_are_dropped():
    """Codex 최종 검토 2026-10-01 두 건. (1) "결과 주장은 원문 근거로만"이 프롬프트에만 있어 초록만 본 두 논문에 "정확도 95%를
    달성했다 [P1:A][P2:A]" 가 그대로 나갔다. (2) 본문 끝의 닫히지 않은 "[P99:A" 가 정리를 우회했다.
    망가뜨리면 실패하는 것: 표시를 빼는 것 · 원문 근거(R·S)가 있는 절이나 결과가 아닌 숫자(모델 버전·데이터셋 번호)에도 붙이는 것 ·
    headline·relation·implications 에 적용을 빼는 것 · 열린 조각을 남기는 것."""
    corpus = "".join(f"- [P{i}:T] t{i}\n  [P{i}:A] 정확도 95% mAP 0.82\n" for i in (1, 2, 3, 4)) + "  [P3:R] r\n  [P4:S7] s\n"
    plan = {"headline": "두 방법이 정확도 95%를 보고했다 [P1:A][P2:A].",
            "threads": [{"name": "초록 흐름", "papers": ["P1", "P2"],
                         "body": "두 방법 모두 정확도 95%를 달성했다 [P1:A][P2:A]. 추가 근거 [P99:A"},
                        {"name": "원문 흐름", "papers": ["P3", "P4"],
                         "body": "mAP 0.82를 얻었다 [P3:R], GPT-4.1 과 MVTec AD 2 를 쓴다 [P4:A][P3:A]. 0.82 를 재현했다 [P4:S7]."}],
            "relation": "둘 다 0.82 근처다 [P1:A][P3:A].", "implications": ["정확도 95% 조건을 본다 [P2:A]."],
            "side_signals": []}
    story = trend_report.repair_story(plan, corpus)
    one, two = story["threads"]
    assert one["body"] == "두 방법 모두 정확도 95%를 달성했다 [P1:A][P2:A] (초록 기준). 추가 근거"
    assert "(초록 기준)" not in two["body"]
    assert story["headline"].endswith("[P1:A][P2:A] (초록 기준).")
    assert "(초록 기준)" in story["relation"] and "(초록 기준)" in story["implications"][0]
    assert "P99" not in trend_report.render_story(story)


def test_cards_that_never_reach_the_corpus_keep_the_rule_on(monkeypatch):
    """Codex 2차 검토 2026-10-01: 카드가 전부 제목 없는 행이라 자료에서 빠지면 카드 조건이 꺼져 각주끼리의 흐름이 통과했다.
    망가뜨리면 실패하는 것: 빈 카드 집합을 None(조건 없음)으로 바꾸는 것."""
    import json
    import narrative_engine as ne
    import summarize_engine as se
    rows = [{"arxiv_id": "c1", "title": " ", "abstract": "a"}, {"arxiv_id": "c2", "title": "", "abstract": "a"},
            {"arxiv_id": "f1", "title": "Footnote one", "abstract": "a"}, {"arxiv_id": "f2", "title": "Footnote two", "abstract": "a"},
            {"arxiv_id": "f3", "title": "Footnote three", "abstract": "a"}]
    plan = {"headline": "h [P1:A]", "threads": [{"name": "각주끼리", "papers": ["P1", "P2"], "body": "b [P1:A][P2:A]"}],
            "relation": "", "implications": [], "side_signals": []}
    monkeypatch.setattr(ne, "_codex", lambda *a, **k: None)

    async def answer(fn, label):
        return json.dumps(plan, ensure_ascii=False)
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", answer)
    assert asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}, anchors=2)) is None
    assert asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]})) is not None     # 카드 없는 글(주간)은 조건 없음


def test_nested_tags_leave_no_residue():
    """Codex 2차 검토 2026-10-01: 중첩 괄호 "[근거 [P1:A], P2:A]" 는 안쪽만 지워지고 바깥이 남았다.
    망가뜨리면 실패하는 것: 남은 괄호를 한 번만 지우는 것 · 비어 버린 괄호를 남기는 것."""
    plan = _plan(threads=[{"name": "이름 [근거 [P1:A], P2:A] [[P2:A]]", "papers": ["P1", "P2"],
                           "body": "공통이다 [근거 [P9:A], P2:A] [[P9:Z]] [메모 [P9:Z] P8:Q] [P1:A][P2:A]."},
                          {"name": "둘째 ] [P4:A", "papers": ["P4", "P6"], "body": "적은 샘플 [P4:A][P6:A]."}],
                 side_signals=[{"paper": "P7", "note": "메모 [[P7:A]] [x [P1:T]]"}])
    story = trend_report.repair_story(plan, _STORY_CORPUS)
    thread = story["threads"][0]
    assert thread["name"] == "이름"
    assert thread["body"] == "공통이다 [P1:A][P2:A]."
    assert story["threads"][1]["name"] == "둘째"
    assert story["side_signals"][0]["note"] == "메모"
    third = trend_report.repair_story(_plan(threads=[{"name": "결함 [참고] 합성", "papers": ["P1", "P2"], "body": "b [P1:A][P2:A]"}]),
                                      _STORY_CORPUS)
    assert third["threads"][0]["name"] == "결함 합성"       # 닫힌 괄호는 그 묶음만 — 뒤 낱말까지 버리지 않는다


def test_cards_never_come_back_as_side_signals():
    """2026-10-01 사용자 결정: 무엇이 핵심(카드)인지는 Python 의 결정적 순위가 정한다 — 모델이 카드를 주변 신호로 내리면
    "왜 어제는 핵심이 오늘은 주변인가"를 설명할 수 없고 같은 논문이 두 번 실린다. 망가뜨리면 실패하는 것: 카드 제외를 빼는 것 ·
    카드가 없는 글(주간)에서도 제외를 거는 것."""
    plan = _plan(side_signals=[{"paper": "P3", "note": "카드인 목재 결함"}, {"paper": "P7", "note": "카드 밖"}])
    story = trend_report.repair_story(plan, _STORY_CORPUS, anchors={1, 2, 3, 4, 5})
    assert [s["paper"] for s in story["side_signals"]] == [7] and "side_signal_is_card" in story["repairs"]
    assert [s["paper"] for s in trend_report.repair_story(plan, _STORY_CORPUS)["side_signals"]] == [3, 7]


def test_parse_rendered_story_roundtrip():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 렌더러의 흐름·깊이·해석·주변 신호를 파서가 잃거나 근거 태그를 남기면 실패한다."""
    story = trend_report.repair_story(_plan(), _STORY_CORPUS, deep={1})
    result = trend_report.parse_rendered_story(trend_report.render_story(story))
    assert result["format"] == "v2"
    assert result["headline"] == "결함 데이터 부족에 두 방향으로 답한다"
    assert result["relation"] == "같은 문제에 대한 다른 대응이다"
    assert result["threads"] == [
        {"name": "합성 결함의 역할 확대", "body": "둘 다 합성 결함을 쓴다.", "items": [
            {"title": "FLASH: generate once synthesize many", "depth": "원문 분석"},
            {"title": "Visual anomaly synthesis for model selection", "depth": "초록 기반"}]},
        {"name": "few-shot 검사", "body": "적은 샘플로 학습한다.", "items": [
            {"title": "Prototype aligned few-shot defect network", "depth": "초록 기반"},
            {"title": "Few-shot welding defect detection", "depth": "초록 기반"}]}]
    assert result["implications"] == ["합성 결함의 현실성을 확인한다", "근거 없는 제안 (해석)"]
    assert result["side_signals"] == [{"title": "Goose down YOLO", "note": "경량 미세질감 검출"}]


def test_parse_rendered_story_legacy():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 옛 문단·서수·복합 근거·논문 목록의 경계를 잘못 읽으면 실패한다."""
    text = """■ 오늘 눈에 띄는 것

첫 문단 [P1:A, P2:S7] (초록 기준)

둘째 문단 [P2:R]

■ 갈래

첫째, 하나의 흐름 [P1:A].
줄바꿈도 같은 문단이다.

- Alpha [P1:A]
- Beta · 부분 분석 [P2:S7]

둘째, 다른 흐름 [P3:A]

- Gamma [P3:A]

■ 우리 분야와 만나는 지점

실험 제안 [P1:A] (해석)

적용 조건 [P2:S7]

■ 아직 밖에 있지만 넘어올 것

밖의 신호 [P9:A]

추가 신호 [P10:A]
"""
    got = trend_report.parse_rendered_story(text)
    assert got == {"format": "v1", "headline": "첫 문단 (초록 기준)", "relation": "둘째 문단",
                   "threads": [{"name": "", "body": "하나의 흐름. 줄바꿈도 같은 문단이다.", "items": [
                       {"title": "Alpha", "depth": ""}, {"title": "Beta", "depth": "부분 분석"}]},
                               {"name": "", "body": "다른 흐름", "items": [{"title": "Gamma", "depth": ""}]}],
                   "implications": ["실험 제안 (해석)", "적용 조건"],
                   "side_signals": [{"title": "", "note": "밖의 신호"}, {"title": "", "note": "추가 신호"}]}


def test_parse_rendered_story_unknown_and_empty():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 알 수 없는 글을 숨기거나 빈 글에 내용을 만들면 실패한다."""
    for text, expected in [("", ""), (" 낯선 글 [P2:S7]\n (해석) [P1:A, P3:R] ", "낯선 글 (해석)")]:
        assert trend_report.parse_rendered_story(text) == {
            "format": "unknown", "headline": expected, "relation": "", "threads": [], "implications": [], "side_signals": []}


def test_parse_rendered_story_without_headline_preserves_relation():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 머리 없는 렌더링의 흐름 뒤 관계 문장을 본문에 섞으면 실패한다."""
    story = trend_report.repair_story(_plan(headline=""), _STORY_CORPUS)
    got = trend_report.parse_rendered_story(trend_report.render_story(story))
    assert got["format"] == "v2" and got["headline"] == ""
    assert got["relation"] == "같은 문제에 대한 다른 대응이다"
    assert got["threads"][-1]["body"] == "적은 샘플로 학습한다."


def test_today_point_heading_and_legacy_heading_both_parse():
    """2026-10-06 "오늘의 한 줄" → "오늘의 요점". 새 글이 옛 소제목으로 나가거나, 되읽기가 옛 소제목(10/6 이전 저장 글)이나 새 소제목 중
    하나를 놓치면 실패한다. 요점이 두세 문장이어도 한 줄로 조립돼 relation 과 섞이지 않아야 한다."""
    corpus = "- [P1:T] Alpha paper\n  [P1:A] a\n- [P2:T] Beta paper\n  [P2:A] b"
    plan = {"headline": "공통 문제는 결함 데이터 부족이다 [P1:A]. 오늘은 합성 쪽 답이 붙었다 [P2:A]. 지난 관측보다 범위가 넓다 [P1:A].",
            "threads": [{"name": "흐름", "papers": ["P1", "P2"], "body": "공통 [P1:A][P2:A]"}],
            "relation": "", "implications": [], "side_signals": []}
    text = trend_report.render_story(trend_report.repair_story(plan, corpus))
    assert text.splitlines()[0] == "■ 오늘의 요점" and "오늘의 한 줄" not in text
    got = trend_report.parse_rendered_story(text)
    assert got["format"] == "v2" and got["headline"].startswith("공통 문제는") and got["headline"].endswith("범위가 넓다.")
    assert got["relation"] == ""
    old = trend_report.parse_rendered_story("■ 오늘의 한 줄\n옛 요지 [P1:A]\n\n■ 1. 흐름\n본문 [P1:A][P2:A]")
    assert old["format"] == "v2" and old["headline"] == "옛 요지"


def test_digest_boxes_both_lead_headings():
    """digest 가 새 소제목을 결론 상자로 못 알아보면 요점이 일반 문단으로 떨어져 실패한다(옛 소제목도 계속 상자)."""
    import digest
    assert {"오늘의 요점", "오늘의 한 줄"} <= digest._LEAD_HEADINGS <= digest._NARRATIVE_HEADINGS
