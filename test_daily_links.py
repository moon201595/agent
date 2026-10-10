"""⑨ 의미 연결(2026-10-10) — 이번 주 변화·정찰 추적과 오늘 논문의 실제 겹침, 연구축 근거·추적 표시, 지난 해석의 자기 강화 차단."""
import sqlite3
from datetime import datetime, timezone

import daily_links
import digest
import saved_digest
import trend_history as history
import trend_report as tr
from test_trend_history import CORPUS, PAST, plan

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def _world(tmp_path):
    db = tmp_path / "w.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE profile_keyword_events (event_id INTEGER PRIMARY KEY, profile_id TEXT, revision INTEGER, created_at TEXT, "
                    "keyword TEXT, kind TEXT, change TEXT, actor_origin TEXT)")
        con.execute("CREATE TABLE candidate_observations (scan_id TEXT, profile_id TEXT, paper_key TEXT, title TEXT, abstract TEXT)")
        con.execute("CREATE TABLE search_candidates (profile_id TEXT, paper_key TEXT, abstract TEXT)")
        events = [("2026-10-12T20:00:00+00:00", "agent harness", "core", "added", "agent"),
                  ("2026-10-12T20:00:00+00:00", "surgical", "exclude", "added", "user"),           # 제외어는 검색어가 아니다
                  ("2026-10-12T20:00:00+00:00", "world action model", "core", "added", "user"),
                  ("2026-10-13T01:00:00+00:00", "world action model", "core", "removed", "user"),  # 더했다 지운 것
                  ("2026-10-01T20:00:00+00:00", "old term", "core", "added", "agent")]               # 7일 밖
        for i, (at, kw, kind, change, origin) in enumerate(events):
            con.execute("INSERT INTO profile_keyword_events VALUES (?,?,?,?,?,?,?,?)", (i, "p", 1, at, kw, kind, change, origin))
        rows = [("k1", "Visual anomaly reasoning for factories", None), ("k2", "Other", "uses visual anomaly reasoning"), ("k3", "Unrelated", "")]
        for key, title, abstract in rows:
            con.execute("INSERT INTO candidate_observations VALUES ('s1','p',?,?,?)", (key, title, abstract))
        con.execute("INSERT INTO search_candidates VALUES ('p','k1','abstract from search')")
    return db


def test_only_real_overlaps_this_week_are_reported(tmp_path):
    """무엇을 망가뜨리면 실패하는가: 7일 밖·지워진·제외어 키워드를 "최근 7일 안에 더해진 검색어"로 세거나, 오늘 핵심 논문이 안 걸린 변화를 싣거나,
    추적 연구축을 오늘 후보(제목·초록 — 관측 abstract 가 없으면 검색 후보 abstract)에서 못 세거나, 겹침이 없는데 절을 만들면 실패한다."""
    db = _world(tmp_path)
    assert daily_links.added_keywords(db, "p", NOW) == [{"keyword": "agent harness", "kind": "core", "origin": "주간 관리"}]
    papers = [{"title": "Harness paper", "_score": {"core_hits": ["Agent Harness", "agentic AI"]}},
              {"title": "Visual anomaly reasoning X", "abstract": "", "_score": {"core_hits": ["defect detection"]}}]
    got = daily_links.collect(db, "p", "s1", papers, NOW, ["visual anomaly reasoning", "never seen term"])
    assert got == {"added": [{"keyword": "agent harness", "origin": "주간 관리", "cards": 1}],
                   "watched": [{"term": "visual anomaly reasoning", "candidates": 2, "cards": 1}]}
    assert daily_links.collect(db, "p", "s1", [{"title": "x", "_score": {"core_hits": ["defect detection"]}}], NOW, []) is None
    assert daily_links.collect(tmp_path / "missing.db", "p", "s1", papers, NOW, ["x"]) is None      # 실패는 절 없이


def test_daily_links_box_sits_under_the_narrative_and_never_claims_cause(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 연결 상자가 핵심 논문 아래로 밀리거나, "덕분에 찾았다" 같은 인과를 주장하거나, 평문·HTML·형식 수정본이 갈라지면 실패한다."""
    from test_saved_digest_parity import realistic_result
    scan = realistic_result(monkeypatch)
    scan["daily_links"] = {"added": [{"keyword": "agent harness", "origin": "주간 관리", "cards": 2}],
                           "watched": [{"term": "visual anomaly reasoning", "candidates": 3, "cards": 1}]}
    plain = digest.generate_digest(scan, "팀")
    html = digest.generate_digest_html(scan, "팀")
    texts = digest._daily_links_texts(scan)
    assert len(texts) == 2 and all("덕분에 새로 찾은 논문인지는" in t or "아직 핵심 키워드는 아니다" in t for t in texts)
    box = digest.daily_links_html(texts)
    assert box in html and html.index(digest.DAILY_LINKS_TITLE) < html.index('class="ph-card"')
    assert plain.index(digest.DAILY_LINKS_TITLE) < plain.index("■ 오늘의 핵심 논문")
    assert box in saved_digest.render_html(plain, "팀", scan["papers"])
    scan.pop("daily_links")
    assert digest.DAILY_LINKS_TITLE not in digest.generate_digest_html(scan, "팀")


def test_axis_evidence_and_watchlist_round_trip_in_every_mail_form():
    """무엇을 망가뜨리면 실패하는가: 연구축 대표 근거 논문(제목·링크·놓친 단계)이나 추적 목록(처음 발견·누적·이번 주·상태)이 평문·HTML 중 하나에서 빠지거나,
    형식 수정본이 그 줄을 못 읽어 외부 정찰 절 전체가 줄 렌더러로 떨어지면 실패한다."""
    sc = {"verified": 1, "capture": {"evaluable": 1, "retrieved": 0, "core_hit": 0, "delivered": 0}, "missed": [],
          "axes": [{"term": "visual anomaly reasoning", "summary": "설명형 이상 탐지", "papers": 2, "recent": 1, "previous": 0,
                    "recent_total": 40, "previous_total": 30, "missed": {"not_retrieved": 2},
                    "evidence": [{"title": "Visual Anomaly Reasoning (VAR)", "link": "https://arxiv.org/abs/2609.22220", "stage": "검색 소스가 못 가져옴"},
                                 {"title": "Second paper", "link": "", "stage": "관측 시작 전에 나온 논문"}]}],
          "watch": [{"term": "causal defect reasoning", "first_week": "2026-W41", "papers_total": 3, "new_this_week": 1, "status": "watching"},
                    {"term": "agent harness", "first_week": "2026-W41", "papers_total": 2, "new_this_week": 0, "status": "in_profile"}]}
    lines = digest._external_scout_lines({"external_scout": sc})
    html = digest._external_scout_html({"external_scout": sc})
    for text in ("근거: Visual Anomaly Reasoning (VAR) (검색 소스가 못 가져옴)", digest.WATCH_HEADING,
                 "처음 발견 2026-W41 · 누적 근거 3편 · 이번 주 새 근거 1편 · 추적 중", "검색어로 채택 — 이제 일일 검색이 본다"):
        assert text in html and any(text in line for line in lines), text
    assert saved_digest._scout_from_lines(lines) == {**sc, "missed": []}
    assert saved_digest._scout_html(lines) == html


def test_history_context_separates_past_papers_from_past_interpretation_and_drops_inflation():
    """2026-10-10 외부 검토: 지난 서술이 다음 날 맥락으로 들어가 근거 없이 "관측 → 확장 → 자리 잡음"으로 강해질 수 있다.
    무엇을 망가뜨리면 실패하는가: 지난 해석을 근거처럼 "관측 요지"로 넘기거나, 그날 논문 제목을 빼거나, 연결 문장의 확산·성장·자리 잡음 표현을 통과시키면 실패한다."""
    ctx = history.context(history.catalog(PAST))
    assert "그날의 해석(근거 아님)" in ctx and "관측 요지" not in ctx and "그날 논문: Prior paper" in ctx
    for word in ("확산되고 있다", "성장하는 흐름", "자리 잡고 있다", "주류가 됐다"):
        story = tr.repair_story(plan({"ref": "H1", "relation": "continuing", "note": f"도구 선택 연구가 {word} [P1:A]."}), CORPUS,
                                history=history.catalog(PAST))
        assert story["threads"][0].get("history") is None and "history_unverified" in story["repairs"], word
    ok = tr.repair_story(plan({"ref": "H1", "relation": "expanding", "note": "선택 문제에서 경로 탐색으로 범위를 넓힌다 [P2:A]."}), CORPUS,
                         history=history.catalog(PAST))
    assert ok["threads"][0]["history"]["relation"] == "expanding"


def test_empty_observation_window_reads_as_no_record_not_zero():
    """2026-10-10 실측: 후보 기록이 9/15 부터라 직전 4주 분모가 0 이었는데 "0/0편"으로 보였다. 무엇을 망가뜨리면 실패하는가: 분모 0 을 "0편"으로 쓰거나 역파싱이 그 줄을 못 읽으면 실패한다."""
    sc = {"verified": 0, "capture": {"evaluable": 0, "retrieved": 0, "core_hit": 0, "delivered": 0}, "missed": [],
          "axes": [{"term": "latent reasoning", "summary": "", "papers": 3, "recent": 2, "previous": 0, "recent_total": 3994,
                    "previous_total": 0, "missed": {}, "evidence": []}], "watch": []}
    lines = digest._external_scout_lines({"external_scout": sc})
    assert any("최근 4주 2/3994편 · 직전 4주 관측 없음" in line for line in lines)
    assert saved_digest._scout_from_lines(lines) == {**sc, "missed": []}


def test_axis_filter_keeps_method_names_ending_in_content_words():
    """2026-10-10 운영 DB 사본 실측: 로봇 프로필의 "Test-Time Training" 이 가장자리 금지어("training")에 걸려 버려졌다. 무엇을 망가뜨리면 실패하는가:
    정식 방법 이름을 다시 막거나, 기능어로 끝나는 조각("training of the")을 통과시키면 실패한다."""
    import external_scout as es
    profiles = [{"id": "p", "name": "로봇", "core": ["robot learning"], "exclude": []}]
    papers = [{"title": f"Test-Time Training for Robots {i}", "arxiv_id": f"2610.0470{i}"} for i in range(2)]
    raw = '{"axes": {"p": [{"term": "Test-Time Training", "summary_ko": "추론 중 학습", "papers": %s}]}}' % __import__("json").dumps(papers)
    assert [a["term"] for a in es.sanitize_axes(raw, profiles)["p"]] == ["Test-Time Training"]
    bad = raw.replace('"Test-Time Training"', '"training of the"')
    assert es.sanitize_axes(bad, profiles)["p"] == []


def test_axis_verification_gets_its_own_longer_timeout(tmp_path):
    """무엇을 망가뜨리면 실패하는가: 최대 48항목 연구축 검증에 300초 상한을 그대로 써 실측 속도(약 8초/항목)로 시간 초과가 나게 두면 실패한다."""
    import external_scout as es
    assert es.AXIS_VERIFY_TIMEOUT_S >= es.MAX_VERIFY_AXES * 8 + 60 > es.VERIFY_TIMEOUT_S


def test_review_fixes_core_seed_kinds_and_wording(tmp_path):
    """Codex 독립 검토(2026-10-10) P2·P3. 무엇을 망가뜨리면 실패하는가: 같은 문자열의 시드 삭제가 핵심어 추가를 지우는 것, 시드 추가를 카드 글에서 못 세는 것,
    7일 창을 "이번 주"라 부르는 것, 핵심어가 아닐 뿐인 축을 "검색어가 아니다"라고 단정하는 것."""
    db = _world(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO profile_keyword_events VALUES (90,'p',2,'2026-10-13T02:00:00+00:00','agent harness','s2_seed','removed','user')")
        con.execute("INSERT INTO profile_keyword_events VALUES (91,'p',2,'2026-10-13T02:00:00+00:00','tool routing','s2_seed','added','agent')")
    papers = [{"title": "Agentic tool routing", "abstract": "", "_score": {"core_hits": ["agent harness"]}}]
    got = daily_links.collect(db, "p", "s1", papers, NOW, [])
    assert got["added"] == [{"keyword": "agent harness", "origin": "주간 관리", "cards": 1},
                            {"keyword": "tool routing", "origin": "주간 관리", "cards": 1}]
    texts = digest._daily_links_texts({"daily_links": {**got, "watched": [{"term": "x y", "candidates": 1, "cards": 0}]}})
    assert texts[0].startswith("최근 7일 안에 더해진 검색어") and "이번 주" not in " ".join(texts)
    assert texts[-1].endswith("아직 핵심 키워드는 아니다.")


def test_review_fixes_axis_filter_inflation_regex_and_strict_parser():
    """Codex 독립 검토 P2·P3. 무엇을 망가뜨리면 실패하는가: 가장자리 예외가 상투 구절·기능어 조각을 통과시키는 것, 기술 용어("확산 모델"·"가속 추론")가 든
    정상 연결을 지우는 것, "정착하고"·"퍼지고" 같은 강화 표현을 놓치는 것, 근거 줄 3개·근거 편수 줄 2개를 받아 형식 수정본에서 내용을 조용히 잃는 것."""
    import external_scout as es
    for bad in ("our method", "previous work", "novel framework", "robot learning without", "robot learning has", "training of the",
                "systematic review", "robust state of the art detection"):   # 마지막 둘은 가장자리 검사가 아니라 상투 구절 검사만 잡는다
        assert es._axis_term_rejected(bad.split()), bad
    for good in ("Test-Time Training", "latent reasoning", "diffusion policy"):
        assert not es._axis_term_rejected(good.split()), good
    for note, kept in (("확산 모델의 선택 문제를 경로 탐색으로 확장한다 [P2:A].", True), ("가속 추론의 선택 문제를 다룬다 [P2:A].", True),
                       ("도구 선택 연구가 점차 정착하고 있다 [P1:A].", False), ("도구 선택 연구가 널리 퍼지고 있다 [P1:A].", False)):
        story = tr.repair_story(plan({"ref": "H1", "relation": "continuing", "note": note}), CORPUS, history=history.catalog(PAST))
        assert (story["threads"][0].get("history") is not None) is kept, note
    import pytest
    sc = {"verified": 0, "capture": {"evaluable": 0, "retrieved": 0, "core_hit": 0, "delivered": 0}, "missed": [], "watch": [],
          "axes": [{"term": "x y", "summary": "", "papers": 2, "recent": None, "previous": None, "recent_total": None, "previous_total": None,
                    "missed": {}, "evidence": [{"title": f"P{i}", "link": "", "stage": "s"} for i in range(2)]}]}
    lines = digest._external_scout_lines({"external_scout": sc})
    with pytest.raises(ValueError):
        saved_digest._scout_from_lines([*lines, "    근거: P2 (s)"])
    with pytest.raises(ValueError):
        saved_digest._scout_from_lines([*lines, "    근거 논문 99편"])


def test_tutorial_papers_are_not_axis_evidence():
    """2026-10-10 운영 DB 사본 실측: 연구축 근거에 "A Tutorial on …" 이 들어갔다. 무엇을 망가뜨리면 실패하는가: 비연구 글을 근거 편수에 넣는 것."""
    import json
    import external_scout as es
    profiles = [{"id": "p", "name": "로봇", "core": ["robot learning"], "exclude": []}]
    papers = [{"title": "Semantic Communication for Robots", "arxiv_id": "2610.02161"},
              {"title": "Embodied Semantic Communication: A Tutorial on Representation", "arxiv_id": "2609.35936"}]
    raw = json.dumps({"axes": {"p": [{"term": "semantic communication", "summary_ko": "의미 통신", "papers": papers}]}})
    assert es.sanitize_axes(raw, profiles)["p"] == []          # 연구 논문 1편만 남아 2편 조건을 못 채운다


def test_single_followup_paper_is_kept_only_with_a_verified_past_link():
    """2026-10-10 외부 검토: 자기 강화 차단 뒤에도 "지난 흐름 + 오늘 1편"의 연속·확장·분기를 설명할 수 있어야 한다. 예전 규칙(흐름은 오늘 논문 2편 이상)은
    이걸 통째로 버렸다. 무엇을 망가뜨리면 실패하는가: 검증된 지난 연결이 있는 1편 후속을 버리는 것, 지난 연결 없이(또는 연결이 검증 안 됐는데) 1편으로 흐름을
    만드는 것, 1편 후속을 여러 편의 공통 흐름처럼 표시하는 것, 오늘 논문 없이 지난 흐름만으로 흐름을 만드는 것."""
    def one(connection, papers=("P2",), body="경로 탐색으로 선택 문제를 다룬다 [P2:A]."):
        p = plan(connection)
        p["threads"][0].update(papers=list(papers), body=body)
        return tr.repair_story(p, CORPUS, history=history.catalog(PAST))

    follow = one({"ref": "H1", "relation": "expanding", "note": "선택 문제에서 경로 탐색으로 범위를 넓힌다 [P2:A]."})
    assert follow["threads"][0]["papers"] == [2] and follow["threads"][0]["follow_up"] is True
    assert "오늘 1편의 후속 관측" in tr.render_story(follow)
    assert one(None) is None                                                                    # 지난 연결 없는 1편
    assert one({"ref": "H9", "relation": "expanding", "note": "없는 지난 흐름 [P2:A]."}) is None      # 없는 H
    assert one({"ref": "H1", "relation": "continuing", "note": "도구 선택 연구가 확산되고 있다 [P2:A]."}) is None   # 강화 표현
    assert one({"ref": "H1", "relation": "continuing", "note": "지난 흐름을 잇는다."}, papers=(), body="지난 흐름 얘기") is None
    two = tr.repair_story(plan({"ref": "H1", "relation": "continuing", "note": "선택을 잇는다 [P1:A]."}), CORPUS, history=history.catalog(PAST))
    assert two["threads"][0].get("follow_up") is None and "오늘 1편" not in tr.render_story(two)


def test_daily_link_wording_says_term_appeared_not_that_related_research_was_confirmed():
    """외부 검토: "오늘 검색 후보 n편에 등장"은 관련 연구를 확인했다는 뜻이 아니라 용어가 후보 글에 나왔다는 뜻이다. 무엇을 망가뜨리면 실패하는가: 그 구분을 문장에서 지우는 것."""
    text = digest._daily_links_texts({"daily_links": {"added": [], "watched": [{"term": "fast weights", "candidates": 3, "cards": 1}]}})[0]
    assert "이 용어가 나왔다" in text and "관련 연구인지는 확인하지 않았다" in text


def test_followup_never_steals_a_paper_from_a_multi_paper_thread_and_its_body_cannot_inflate():
    """Codex 검토(2026-10-10, 한도로 끊기기 전 남긴 두 의문)를 재현한다. 무엇을 망가뜨리면 실패하는가: 앞에 나온 1편 후속이 뒤 2편 흐름의 논문을 먼저 가져가
    공통 흐름을 버리게 하는 것, 1편 후속의 본문에 "확산되고 있다" 같은 강화 표현을 남기는 것, 후속을 모델이 낸 순서 자리에서 빼는 것."""
    link = {"ref": "H1", "relation": "expanding", "note": "선택 문제에서 경로 탐색으로 범위를 넓힌다 [P2:A]."}
    p = {"headline": "검색 문제 [P1:A]", "relation": "", "implications": [], "side_signals": [], "threads": [
        {"name": "후속", "papers": ["P2"], "body": "경로 탐색으로 선택 문제를 다룬다 [P2:A].", "history": link},
        {"name": "공통", "papers": ["P1", "P2"], "body": "선택과 탐색 [P1:A][P2:A]", "history": None}]}
    story = tr.repair_story(p, CORPUS, history=history.catalog(PAST))
    assert [t["name"] for t in story["threads"]] == ["공통"] and story["threads"][0]["papers"] == [1, 2]
    assert "follow_up_dropped" in story["repairs"]
    p["threads"][1]["papers"], p["threads"][1]["body"] = ["P1", "P3"], "선택과 외부 [P1:A][P3:A]"
    story = tr.repair_story(p, CORPUS, history=history.catalog(PAST))
    assert [t["name"] for t in story["threads"]] == ["후속", "공통"] and story["threads"][0]["follow_up"] is True
    p["threads"][0]["body"] = "이 접근이 널리 확산되고 있다 [P2:A]."
    assert [t["name"] for t in tr.repair_story(p, CORPUS, history=history.catalog(PAST))["threads"]] == ["공통"]
