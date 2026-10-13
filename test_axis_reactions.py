"""⑨·② 외부 정찰 연구축 반응 버튼과 메일 안내 문장(2026-10-12).

사용자 지적: "보여주기만 하는 게 아니라 사용자가 이해하거나 알아먹을 수 있게". 상자마다 무엇인지·숫자의 뜻·에이전트가 할 일을 정해진 문장으로
붙이고, 연구축에 "더 보고 싶음 · 관심 밖" 버튼을 달아 그 반응이 추적 목록과 주간 관리(`agent_maintenance.validate`)로 이어지게 했다.
"""
import inspect
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

import agent_maintenance as am
import digest
import external_scout as es
import feedback_links as fl
import saved_digest
import trend_report
from test_feedback_links import isolated_store  # noqa: F401 — 발송 테스트가 쓰는 격리 픽스처
from test_external_scout import AXIS, NOW, TERM, TXT, _adopt_axis, _axis_raw, _axis_official, _axis_verdict, _axis_world, _brief

SECRET = "s" * 40
URL = "https://script.google.com/macros/s/AKfycb" + "x" * 40 + "/exec"


@pytest.fixture
def configured(monkeypatch):
    import summarize_engine as engine
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", URL)
    monkeypatch.setitem(engine.ENV, "FEEDBACK_HMAC_SECRET", SECRET)


def _press(db, links, term, action, at, *, undo=False):
    token = parse_qs(urlparse(links[term][action]).query)["t"][0]
    if undo:
        _v, tid, _a, exp, _s = token.split(".")
        token = fl.make_token(tid, "undo", int(exp), SECRET)
    return fl.import_rows(db, [{"received_at": at.isoformat(), "token": token}], SECRET, now=at)


# ── 반응 저장·집계 ───────────────────────────────────────────────────────────────────────────

def test_axis_reactions_count_last_press_per_recipient_and_stay_out_of_paper_feedback(tmp_path, configured):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 연구축 반응이 논문 반응(`valid_reactions` — 가중치 학습·주간 관리 R)에 섞이거나,
    한 사람의 마음 바꿈을 두 번 세거나, 취소(undo)를 반영하지 않거나, 동수를 어느 한쪽으로 판정하면 실패한다."""
    db = tmp_path / "f.db"
    t0 = NOW
    alice = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS, AXIS], now=t0)
    bob = fl.axis_links(db, "p", "i1", "bob@x.com", [AXIS], now=t0)
    assert set(alice[AXIS]) == {"more", "out"} and alice[AXIS]["more"] != bob[AXIS]["more"]
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT item_no, paper_key FROM feedback_tokens ORDER BY rowid").fetchall()
    assert rows == [("A1", "axis:" + AXIS), ("A1", "axis:" + AXIS)]           # 같은 용어를 두 번 줘도 토큰은 하나
    _press(db, alice, AXIS, "out", t0 + timedelta(hours=1))
    _press(db, alice, AXIS, "more", t0 + timedelta(hours=2))                      # 마음을 바꿨다 — 뒤의 것
    _press(db, bob, AXIS, "out", t0 + timedelta(hours=3))
    assert fl.axis_reactions(db, "p") == {AXIS: {"term": AXIS, "more": 1, "out": 1, "state": None}}
    _press(db, bob, AXIS, "out", t0 + timedelta(hours=4), undo=True)               # 취소하면 그 사람의 관심 밖이 빠진다
    assert fl.axis_reactions(db, "p") == {AXIS: {"term": AXIS, "more": 1, "out": 0, "state": fl.AXIS_WANTED}}
    assert fl.valid_reactions(db, "p") == []
    carol = fl.axis_links(db, "p", "i2", "carol@x.com", [AXIS], now=t0)
    dave = fl.axis_links(db, "p", "i2", "dave@x.com", [AXIS], now=t0)
    _press(db, carol, AXIS, "out", t0 + timedelta(hours=5))
    _press(db, dave, AXIS, "out", t0 + timedelta(hours=5, minutes=1))
    assert fl.axis_reactions(db, "p")[AXIS] == {"term": AXIS, "more": 1, "out": 2, "state": fl.AXIS_STOPPED}
    assert fl.axis_reactions(tmp_path / "none.db", "p") == {}                      # 표가 없으면 빈 값 — 추적이 멈추지 않는다


def test_both_axis_buttons_opened_together_are_a_scanner_and_case_variants_are_one_axis(tmp_path, configured):
    """2026-10-12 Codex 검토 두 건. 이 테스트가 무엇을 망가뜨리면 실패하는가: (1) 발송 2분 뒤 두 버튼을 1초 간격으로 연 지연 스캐너를 사람으로 받아
    마지막 "관심 밖" 이 추적을 멈추게 하는 것 — 연구축은 버튼이 둘이라 둘 다 연 것이 연타 기준이다, (2) 정찰이 다음 주에 대소문자·하이픈만 바꿔 적은
    같은 연구축을 다른 축으로 세어 관심 밖이 안 걸리는 것. 논문 버튼(셋)의 연타 기준은 그대로여야 한다."""
    db = tmp_path / "f.db"
    links = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS], now=NOW)
    fl.mark_delivered(db, "i1", "alice@x.com", when=NOW)
    _press(db, links, AXIS, "more", NOW + timedelta(seconds=180))
    assert _press(db, links, AXIS, "out", NOW + timedelta(seconds=181))[fl.STATUS_BURST] == 1
    assert fl.axis_reactions(db, "p") == {}
    _press(db, links, AXIS, "out", NOW + timedelta(hours=1))                      # 격리 창 밖의 단독 클릭은 사람이다
    variant = fl.axis_links(db, "p", "i2", "bob@x.com", ["Visual-Anomaly  Reasoning"], now=NOW)
    _press(db, variant, "Visual-Anomaly  Reasoning", "out", NOW + timedelta(hours=2))
    got = fl.axis_reactions(db, "p")
    assert list(got) == [fl.axis_key(AXIS)] and got[fl.axis_key(AXIS)]["out"] == 2 and got[fl.axis_key(AXIS)]["state"] == fl.AXIS_STOPPED
    papers = fl.issue_links(db, "p", "i3", "carol@x.com", [{"arxiv_id": "2609.00001", "title": "A"}], now=NOW)
    paper_links = next(iter(papers.values()))
    _press(db, {"x": paper_links}, "x", "more", NOW + timedelta(hours=3))
    assert _press(db, {"x": paper_links}, "x", "out", NOW + timedelta(hours=3, seconds=1))[fl.STATUS_VALID] == 1


def test_axis_links_need_configuration_and_ops_screen_hides_axis_rows(tmp_path, configured, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 설정 없이 버튼을 싣거나, 운영 화면의 논문 반응 표에 `axis:` 행이 논문처럼 나오면 실패한다."""
    import ops_dashboard
    db = tmp_path / "f.db"
    links = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS], now=NOW)
    _press(db, links, AXIS, "more", NOW + timedelta(hours=1))
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        assert ops_dashboard._reaction_rows(con, "p") == []
    monkeypatch.setattr(fl, "config", lambda: None)
    assert fl.axis_links(db, "p", "i2", "alice@x.com", [AXIS]) == {}


# ── 추적 목록·메일 요약 ──────────────────────────────────────────────────────────────────────

def test_stopped_axis_leaves_tracking_scout_requests_and_mail_but_is_announced(tmp_path, configured):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 관심 밖이 우세한 연구축을 계속 추적하거나(정찰에 다시 묻거나 일일 겹침에 쓰거나),
    메일 후보에 다시 싣거나, 멈췄다는 사실을 메일에서 감추면 실패한다. 더 보고 싶음은 추적·메일 표시에 남아야 한다."""
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    links = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS], now=NOW)
    _press(db, links, AXIS, "more", NOW + timedelta(hours=1))
    assert es.watchlist(db, "p", NOW)[0]["reaction"] == fl.AXIS_WANTED
    sc = es.mail_summary(db, "p", NOW)
    assert sc["axes"][0]["reaction"] == fl.AXIS_WANTED and sc["axes"][0]["first_week"] == "2026-W41" and "stopped" not in sc
    assert any(digest.AXIS_WANTED_NOTE in line for line in digest._external_scout_lines({"external_scout": sc}))

    _press(db, links, AXIS, "out", NOW + timedelta(hours=2))
    assert es.watchlist(db, "p", NOW)[0]["status"] == "user_out"
    assert es.watched_terms(db, "p", NOW) == []
    sc = es.mail_summary(db, "p", NOW)
    assert sc["axes"] == [] and sc["watch"] == [] and sc["stopped"] == [AXIS]
    assert digest.STOPPED_PREFIX + AXIS in digest._external_scout_html({"external_scout": sc})
    prompts = []
    later = NOW + timedelta(days=7)
    es.run_weekly(db, later, scout=lambda prompt, t: prompts.append(prompt) or _axis_raw({"p": []}, {}),
                  s2_batch=_axis_official, verify=_axis_verdict)
    assert prompts and AXIS not in prompts[0]


# ── 주간 관리 검증 ───────────────────────────────────────────────────────────────────────────

def _axis_brief(state, term=TERM, **kw):
    b = _brief(**kw)
    b.axis_reactions = {"A1": {"term": term, "more": int(state == fl.AXIS_WANTED), "out": int(state == fl.AXIS_STOPPED), "state": state}}
    b.texts["A1"] = f"메일 연구축 반응: {term}"
    return b


def _add(ev, term=TERM, weight=0.6):
    return {"op": "add_keyword", "term": term, "weight": weight, "evidence": ev, "reason": "메일 반응"}


def test_wanted_axis_reaction_replaces_the_two_missed_papers_rule_only_for_that_exact_term():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "더 보고 싶음" 이 우세한 연구축을 이름 그대로 더하지 못하거나, 그 반응으로 가중치 상한(0.7)을 넘기거나,
    다른 말(세부어)을 끼워 넣거나, 관심 밖 논문과 충돌하는 말을 받아들이면 실패한다."""
    wanted = _axis_brief(fl.AXIS_WANTED, external={"E1": "not_retrieved"}, texts={"E1": TXT})
    ok, bad = am.validate([_add(["A1"])], wanted)
    assert [a["basis"] for a in ok] == ["feedback"] and not bad
    ok, _bad = am.validate([_add(["E1", "A1"])], _axis_brief(fl.AXIS_WANTED, external={"E1": "not_retrieved"}, texts={"E1": TXT}))
    assert ok[0]["basis"] == "feedback+external"
    assert am.validate([_add(["A1"], weight=0.9)], wanted)[1][0]["reason"] == "external_weight_too_high"
    assert am.validate([_add(["A1"], term="anomaly segmentation")], wanted)[1][0]["reason"] == "no_liked_evidence"
    conflict = _axis_brief(fl.AXIS_WANTED, external={}, reactions={"R1": Counter(out=1)}, texts={"R1": TXT})
    assert am.validate([_add(["A1"])], conflict)[1][0]["reason"] == "conflicts_user_reaction"
    neutral = _axis_brief(None, external={}, texts={})
    assert am.validate([_add(["A1"])], neutral)[1][0]["reason"] == "no_liked_evidence"


def test_stopped_axis_blocks_the_term_and_its_variants_on_every_path():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "관심 밖" 이 우세한 연구축을 좋아요 논문·외부 근거로 더하거나, 그 말을 품은 더 긴 말·그 안의 말로
    비켜 가거나, 반응을 근거로 인용하지 않았다는 이유로 통과시키면 실패한다(규칙 1)."""
    liked = {"R1": Counter(more=1)}
    for term in (TERM, TERM + " model", "anomaly segmentation"):
        text = f"Paper on {term} for factories."
        b = _axis_brief(fl.AXIS_STOPPED, external={"E1": "not_retrieved", "E2": "no_core_hit"}, reactions=liked,
                        texts={"R1": text, "E1": text, "E2": text})
        b.axis_reactions["A1"]["term"] = TERM
        for ev in (["R1"], ["E1", "E2"]):
            assert am.validate([_add(ev, term=term)], b)[1][0]["reason"] == "conflicts_axis_reaction", (term, ev)
    other = _axis_brief(fl.AXIS_STOPPED, external={}, reactions=liked, texts={"R1": "Paper on thermal imaging inspection."})
    assert am.validate([_add(["R1"], term="thermal imaging inspection")], other)[0]


def test_stopped_axis_also_blocks_search_seed_and_weight_increase_but_not_decrease():
    """2026-10-12 Codex 검토. 이 테스트가 무엇을 망가뜨리면 실패하는가: 이미 핵심어인 축에 관심 밖이 우세한데 검색 시드로 넓히거나 가중치를 올리면
    실패한다. 내리는 것은 막지 않는다(사용자 키워드는 지우지 않고 하향만 된다)."""
    b = _axis_brief(fl.AXIS_STOPPED, term="defect detection", external={}, reactions={"R1": Counter(more=1)},
                    texts={"R1": "Paper on defect detection."})
    seed = {"op": "add_seed", "term": "defect detection", "weight": None, "evidence": ["R1"], "reason": "x"}
    up = {"op": "set_weight", "term": "defect detection", "weight": 1.5, "evidence": ["R1"], "reason": "x"}
    down = {"op": "set_weight", "term": "defect detection", "weight": 0.5, "evidence": ["R1"], "reason": "x"}
    assert am.validate([seed], b)[1][0]["reason"] == "conflicts_axis_reaction"
    assert am.validate([up], b)[1][0]["reason"] == "conflicts_axis_reaction"
    assert am.validate([down], b)[0]


def test_stop_reaction_wins_over_profile_adoption_in_tracking(tmp_path, configured):
    """2026-10-12 Codex 검토. 이 테스트가 무엇을 망가뜨리면 실패하는가: 이미 핵심어가 된 축에 관심 밖을 눌렀는데 추적 상태가 "검색어로 채택" 으로 남아
    메일의 "추적을 멈춘 연구축" 안내에서 빠지면 실패한다."""
    import research_profile
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    research_profile.create_profile(db, "p", "비전", core_topics=[AXIS])
    links = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS], now=NOW)
    _press(db, links, AXIS, "out", NOW + timedelta(hours=1))
    assert es.watchlist(db, "p", NOW)[0]["status"] == "user_out"
    assert es.mail_summary(db, "p", NOW)["stopped"] == [AXIS]


def test_brief_carries_only_decided_axis_reactions(tmp_path, configured):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 연구축 반응이 브리프의 A 근거로 안 들어가거나(모델이 못 본다), 동수(신호 아님)를 넣거나,
    반응만 있는 주에 주간 관리가 "신호 없음"으로 건너뛰면 실패한다."""
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    links = fl.axis_links(db, "p", "i1", "alice@x.com", [AXIS, "event camera"], now=NOW)
    _press(db, links, AXIS, "more", NOW + timedelta(hours=1))
    bob = fl.axis_links(db, "p", "i1", "bob@x.com", ["event camera"], now=NOW)
    _press(db, links, "event camera", "more", NOW + timedelta(hours=1))
    _press(db, bob, "event camera", "out", NOW + timedelta(hours=1))
    brief = am.build_brief(db, "p", NOW + timedelta(hours=2))
    assert brief.data["external"]["axis_reactions"] == [{"id": "A1", "term": AXIS, "more": 1, "out": 0, "state": fl.AXIS_WANTED}]
    assert AXIS in brief.texts["A1"] and brief.axis_reactions["A1"]["state"] == fl.AXIS_WANTED
    brief.data["external"]["items"] = []
    brief.data["reactions"] = []
    assert brief.has_signal()
    assert am.reader_reason("A1 반응을 따른다") == "반응을 따른다"


# ── 메일 문장·버튼·형식 수정본 ────────────────────────────────────────────────────────────────

def _scout(links=False):
    button = lambda term: {"_feedback_links": {"more": f"https://example.com/{term}/more", "out": f"https://example.com/{term}/out"}} if links else {}
    return {"verified": 5, "capture": {"evaluable": 4, "retrieved": 2, "core_hit": 1, "delivered": 0},
            "missed": [{"title": "Paper A", "venue": "CVPR", "stage": "검색 소스가 못 가져옴", "link": "https://arxiv.org/abs/2610.00001"},
                       {"title": "Paper B", "venue": "", "stage": "관련인데 자리에서 밀림", "link": ""}],
            "axes": [{"term": AXIS, "summary": "결함 원인을 설명하는 이상 탐지", "papers": 2, "recent": 7, "previous": 1,
                      "recent_total": 7504, "previous_total": 2149, "missed": {"ranked_out": 1}, "first_week": "2026-W41",
                      "reaction": "wanted", "evidence": [{"title": "Paper C", "link": "https://arxiv.org/abs/2610.00003", "stage": "관련인데 자리에서 밀림"}],
                      **button("a")}],
            "watch": [{"term": "event camera", "first_week": "2026-W40", "papers_total": 3, "new_this_week": 0, "status": "watching", **button("w")}],
            "stopped": ["latent reasoning"]}


def test_scout_section_explains_what_it_is_what_numbers_mean_and_what_happens_next():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 안내·숫자 문장·"에이전트가 할 일"·단계 설명이 평문이나 HTML 한쪽에서 빠지거나, 놓친 목록에 없는 단계까지
    설명하거나, 버튼 설명을 버튼 없이 싣거나, 4주 비교를 "a/b편" 처럼 증감으로 읽히는 꼴로 되돌리면 실패한다."""
    sc = _scout(links=True)
    html = digest._external_scout_html({"external_scout": sc})
    text = "\n".join(digest._external_scout_lines({"external_scout": sc}))
    sentence = ("이번 주 확인한 외부 연구 중 우리 검색 기간과 겹치는 4편: 우리 검색이 가져온 것 2편 · 그중 핵심어에 걸린 것 1편 · 메일에 실린 것 0편."
                " 나머지 1편은 우리 마지막 검색 뒤에 나왔거나 관측 시작 전 논문이라 비교하지 않았습니다.")
    for body in (html, text):
        for part in (digest.SCOUT_GUIDE, sentence, digest.MISSED_ACTION, digest.AXES_GUIDE, digest.AXES_ACTION, digest.WATCH_GUIDE,
                     digest.STAGE_HELP["검색 소스가 못 가져옴"], digest.STAGE_HELP["관련인데 자리에서 밀림"],
                     "최근 4주 7편(전체 7504편 중) · 직전 4주 1편(전체 2149편 중) — 검색 조건이 달라 증감으로 읽지 않습니다",
                     "근거 논문 2편 중 우리가 놓친 것 1편(순위에서 밀림 1)", f"추적: 2026-W41부터 · {digest.AXIS_WANTED_NOTE}",
                     digest.STOPPED_PREFIX + "latent reasoning"):
            assert digest._esc(part) in body or part in body, part
        assert digest.STAGE_HELP["가져왔으나 핵심어에 안 걸림"] not in body and "7/7504" not in body
    assert digest._esc(digest.AXIS_BUTTON_HELP) in html
    assert re.search(r'<a href="https://example.com/a/more"[^>]*>더 보고 싶음</a>', html)
    assert re.search(r'<a href="https://example.com/w/out"[^>]*>관심 밖</a>', html)
    assert "반응 : 더 보고 싶음 https://example.com/a/more · 관심 밖 https://example.com/a/out" in text
    assert digest.AXIS_BUTTON_HELP in text                                          # 평문에도 버튼 설명(Codex 검토 P3)
    assert digest.AXIS_BUTTON_HELP not in "\n".join(digest._external_scout_lines({"external_scout": _scout()}))
    assert digest._esc(digest.AXIS_BUTTON_HELP) not in digest._external_scout_html({"external_scout": _scout()})


def test_scout_section_round_trips_new_and_old_saved_text_with_original_buttons():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 형식 수정본이 새 문장(추적·반응·멈춤 포함)이나 10/12 이전 저장 글을 못 읽어 옛 줄 렌더러로 떨어지거나,
    원래 회차의 연구축 버튼을 다시 달지 못하거나, 비교하지 않은 편수가 맞지 않는 글을 받아들이면 실패한다."""
    plain = _scout()
    lines = digest._external_scout_lines({"external_scout": plain})
    assert saved_digest._scout_from_lines(lines) == plain
    with_links = _scout(links=True)
    axis_links = {item["term"]: item["_feedback_links"] for item in with_links["axes"] + with_links["watch"]}
    assert saved_digest._scout_html(lines, axis_links) == digest._external_scout_html({"external_scout": with_links})
    # 수신자별 평문(버튼·설명 줄 포함)도 같은 값으로 읽는다. 버튼 줄의 꼴이 아니면 받지 않는다(Codex 검토 P3: 접두사만 보고 내용을 버렸다).
    recipient_lines = digest._external_scout_lines({"external_scout": with_links})
    assert saved_digest._scout_from_lines(recipient_lines) == plain
    with pytest.raises(ValueError):
        saved_digest._scout_from_lines([*lines[:-1], "       반응 : 알 수 없는 기록", lines[-1]])
    bad = [line.replace("나머지 1편", "나머지 2편") for line in lines]
    with pytest.raises(ValueError):
        saved_digest._scout_from_lines(bad)
    old = ["▶ 외부 정찰 — 이번 주 검증된 외부 연구 2편",
           "   판정 가능 2편 중 검색 소스가 가져온 것 1 · 핵심어에 걸린 것 1 · 메일까지 간 것 0",
           "   - Paper A (CVPR) — 검색 소스가 못 가져옴", "     https://arxiv.org/abs/2610.00001",
           "   " + digest.AXES_HEADING, f"   * {AXIS}", "       정찰 설명: 설명", "       근거 논문 2편 · 미포착 1편(검색 못 가져옴 1)",
           "       우리 검색 관측: 최근 4주 2/40편 · 직전 4주 관측 없음",
           "       근거: Paper C (검색 소스가 못 가져옴) https://arxiv.org/abs/2610.00003",
           "   " + digest.WATCH_HEADING, "   * event camera", "       처음 발견 2026-W41 · 누적 근거 3편 · 이번 주 새 근거 1편 · 추적 중"]
    got = saved_digest._scout_from_lines(old)
    assert got["capture"] == {"evaluable": 2, "retrieved": 1, "core_hit": 1, "delivered": 0}
    assert got["axes"][0]["missed"] == {"not_retrieved": 1} and (got["axes"][0]["recent"], got["axes"][0]["previous_total"]) == (2, 0)
    assert got["watch"][0]["status"] == "watching" and got["missed"][0]["link"].endswith("00001")


def test_action_sentences_match_the_rules_python_enforces():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: `agent_maintenance` 의 외부 근거 조건(놓친 논문 수·가중치 상한)이나 추적 기간(8주)을 바꾸고
    메일의 "에이전트가 할 일" 문장은 그대로 두어 메일이 실제와 다른 약속을 하면 실패한다."""
    assert f"놓친 논문 {am.EXTERNAL_MIN_MISSED}편 이상" in digest.MISSED_ACTION
    assert f"가중치 {am.EXTERNAL_WEIGHT_MAX} 이하" in digest.MISSED_ACTION and f"가중치 {am.EXTERNAL_WEIGHT_MAX} 이하" in digest.AXIS_BUTTON_HELP
    weeks = inspect.signature(es.watchlist).parameters["weeks"].default
    assert f"최근 {weeks}주" in digest.AXES_ACTION and f"최근 {weeks}주" in digest.WATCH_GUIDE
    assert f"근거 {es.AXIS_MIN_PAPERS}편 이상" in digest.AXES_ACTION


def test_weekly_delivery_gives_each_recipient_own_axis_buttons_and_survives_failure(tmp_path, monkeypatch, configured, isolated_store):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 주간 메일에 연구축 버튼을 안 싣거나, 두 수신자에게 같은 링크를 주거나, 연구축 토큰의 발송 시각을
    안 남겨 선열람 격리가 못 돌거나, 연구축 링크 생성 실패가 메일을 막으면 실패한다."""
    import email_delivery
    import mail_ledger
    import research_profile as rp
    import run_profile_scan as rps
    monkeypatch.setattr(mail_ledger, "_now", lambda: datetime(2026, 10, 5, 20, 10, tzinfo=timezone.utc))
    db = tmp_path / "p.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    rp.add_recipient(db, "p", "alice@x.com")
    rp.add_recipient(db, "p", "bob@x.com")
    sent = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda text, subject, to, html: sent.append(html))
    result = {"papers": [], "candidates_found": 0, "external_scout": _scout()}
    assert rps._deliver(db, "p", result, "").endswith("2명")
    hrefs = [m[1] for html in sent for m in [re.search(r'<a href="([^"]+)"[^>]*>더 보고 싶음</a>', html)] if m]
    assert len(hrefs) == 2 and hrefs[0] != hrefs[1]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM feedback_tokens WHERE paper_key LIKE 'axis:%' AND delivered_at IS NOT NULL").fetchone()[0] == 4
    assert "_feedback_links" not in result["external_scout"]["axes"][0]            # 수신자별 링크가 공용 결과에 새지 않는다

    def boom(*a, **k):
        raise RuntimeError("링크 생성 실패")
    monkeypatch.setattr(fl, "axis_links", boom)
    monkeypatch.setattr(mail_ledger, "_now", lambda: datetime(2026, 10, 6, 20, 10, tzinfo=timezone.utc))
    sent.clear()
    assert rps._deliver(db, "p", result, "").endswith("2명") and len(sent) == 2 and ">더 보고 싶음</a>" not in sent[0]


def test_reformat_snapshot_ignores_axis_tokens_when_checking_paper_tokens(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 회차의 연구축 토큰을 논문 토큰으로 세어 형식 수정본이 "토큰과 명세가 다름" 으로 멈추면 실패한다."""
    import mail_ledger
    import mail_reformat
    import research_profile as profile
    from test_mail_layout import SAVED
    db = tmp_path / "original.db"
    cfg = ("https://script.google.com/macros/s/" + "a" * 24 + "/exec", "test-secret-" * 5)
    monkeypatch.setattr(fl, "config", lambda: cfg)
    monkeypatch.setattr(fl, "page_url", lambda: "")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(mail_reformat, "datetime", Clock)
    profile.create_profile(db, "p", "팀", ["agent"])
    profile.save_digest(db, "p", SAVED)
    paper = {"arxiv_id": "2609.00001", "title": "One & Paper"}
    when = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
    mail_ledger.record_issue(db, "original-issue", "p", "브리핑 2026-10-02", [paper], 1, 1, when=when)
    fl.issue_links(db, profile_id="p", issue_id="original-issue", recipient="user@example.com", papers=[paper], now=when)
    fl.axis_links(db, "p", "original-issue", "user@example.com", [AXIS], now=when)
    fl.mark_delivered(db, "original-issue", "user@example.com", when=when)
    got = mail_reformat.load_snapshot(db, "p", "2026-10-02", "user@example.com")
    assert got["papers"] == 1


# ── 서술 옆 칸 ─────────────────────────────────────────────────────────────────────────────────

def test_implications_cite_readable_thread_numbers_and_side_boxes_have_guides():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "우리 연구에서 볼 것" 에 메일 어디에도 없는 P 번호를 그대로 두거나, 흐름 번호를 잘못 옮기거나,
    "(해석)" 을 근거 앞으로 보내거나, 지난 글로 되읽을 때 오늘의 흐름 번호를 남기거나, 옆 칸 안내가 HTML 에서 빠지면 실패한다."""
    thread_of = {2: 1, 9: 2, 4: 1}
    assert trend_report._cite_threads("자료 정렬 오류를 비교한다. [P2:A][P9:A] (해석)", thread_of) == "자료 정렬 오류를 비교한다. (근거: 1·2번 흐름) (해석)"
    assert trend_report._cite_threads("결함 검출을 비교한다. [P4:A]", thread_of) == "결함 검출을 비교한다. (근거: 1번 흐름)"
    assert trend_report._cite_threads("근거 없는 문장 (해석)", thread_of) == "근거 없는 문장 (해석)"
    assert trend_report._cite_threads("흐름 밖 [P7:A]", thread_of) == "흐름 밖"
    story = {"headline": "", "relation": "", "titles": {2: "Two", 9: "Nine"}, "tags": {"P2:A", "P9:A"},
             "threads": [{"name": "가", "body": "본문 [P2:A]", "papers": [2]}, {"name": "나", "body": "본문 [P9:A]", "papers": [9]}],
             "implications": ["조건을 확인한다. [P9:A] (해석)"], "side_signals": []}
    text = trend_report.render_story(story)
    assert "- 조건을 확인한다. (근거: 2번 흐름) (해석)" in text
    assert not any("번 흐름" in x for x in trend_report.parse_rendered_story(text)["implications"])
    assert any("조건을 확인한다." in x and "(해석)" in x for x in trend_report.parse_rendered_story(text)["implications"])
    html = digest._narrative_boxes_html(text + "\n■ 주변 신호\n- Side — 메모 [P5:A]")
    for guide in digest.NARRATIVE_GUIDES.values():
        assert digest._esc(guide) in html
