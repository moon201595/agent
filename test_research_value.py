"""⑨ 카드의 "우리 연구에 쓸 수 있는가"(2026-10-23 팀장 "너무 모델 개발 쪽이다", research_value).

지키는 계약(계획 docs/PLAN_research_value_2026-10-23.md v2): 활용 관점은 검색·순위 입력에 들어가지 않는다 · 세 항목은 항목별 근거로 따로 살고 죽는다 ·
코드 공개 사실은 모델이 아니라 code_ladder 기록을 관측 범위로 낮춘 문구이고 메일에 따로 싣지 않는다 · 단정("직접 활용 가능")은 막고 정확한 부정문은 둔다 ·
카드끼리 거의 같은 문장은 버린다 · 실패하면 세 항목 없이 나간다 · 평문 → 저장 메일 재렌더가 같은 상자를 그린다. 논문은 합성이다.
"""
import asyncio
import json

import digest
import narrative_engine
import research_profile
import research_value
import saved_digest

VIEW = "공개된 로봇 모델·도구·실험 방법을 활용하여 물체 파지·조립·핸들링 등 실제 로봇 작업을 구현·검증하고 제조 현장 적용 가능성을 탐색한다."
A = {"arxiv_id": "2610.00001", "title": "Recovering Grasp Failures with a Watchdog",
     "abstract": "Robots often fail to grasp objects in cluttered bins. We add a watchdog that detects slips and replans the grasp. "
                 "On 40 real trials the success rate rose from 55% to 80%.",
     "link": "https://arxiv.org/abs/2610.00001", "abstract_brief": "요지", "_score": {"core_hits": ["robot manipulation"]}}
B = {"arxiv_id": "2610.00002", "title": "Scaling Diffusion Policies",
     "abstract": "We train a diffusion policy on 10,000 hours of robot data. The policy transfers across embodiments.",
     "link": "https://arxiv.org/abs/2610.00002", "abstract_brief": "요지", "_score": {"core_hits": ["diffusion policy"]}}

IDEA_A = "잡기가 실패하는 순간을 따로 감시하는 장치를 두고, 실패를 알아채면 잡는 방법을 다시 정한다는 생각이다."
USE_A = {"basis": "논문은 미끄러짐을 감지해 잡기를 다시 계획하는 감시 장치를 붙였다.",
         "proposal": "공개된 로봇 모델 바깥에 같은 감시 단계를 붙여 부품 집기 실험에서 실패 뒤 재시도를 시험해 볼 수 있다.",
         "check": "감시에 쓸 촉각·힘 센서가 있는지와 적용 조건 확인 필요."}
SEM_A = "실패 감지를 모델 바깥에 둘 때 어떤 실패는 놓치는가?"


def _reply(papers):
    return json.dumps({"papers": papers}, ensure_ascii=False)


def _item(pid, idea="", idea_ev=(), use=None, use_ev=(), sem="", sem_ev=()):
    use = use or {"basis": "", "proposal": "", "check": ""}
    return {"id": pid, "idea": {"text": idea, "evidence": list(idea_ev)},
            "use": {**use, "evidence": list(use_ev)}, "seminar": {"question": sem, "evidence": list(sem_ev)}}


def _entries(tmp_path):
    return research_value.corpus(tmp_path / "p.db", [A, B])


def test_perspective_never_reaches_scoring_inputs(tmp_path):
    """P1. 이 테스트가 무엇을 망가뜨리면 실패하는가: 활용 관점을 get_profile(채점 입력)·revision·순위 키에 섞어 같은 후보의 선정이
    관점 유무로 달라질 수 있게 하거나, 관점 표가 없을 때(migrate 전) 오류를 내서 스캔을 멈추면 실패한다."""
    import profile_scoring
    db = tmp_path / "p.db"
    assert research_value.perspective(db, "p") is None                     # 표도 행도 없다 — 단계만 빠진다
    research_profile.init_db(db)
    research_profile.create_profile(db, "p", "로봇", core_topics=["robot manipulation", "diffusion policy"])
    before, rev = research_profile.get_profile(db, "p"), research_profile.current_revision(db, "p")
    keys = [profile_scoring.rank_key(p, profile_scoring.score_paper(p, before), before) for p in (A, B)]
    assert research_value.set_perspective(db, "p", VIEW) == 1 and research_value.set_perspective(db, "p", VIEW + " 수정") == 2
    after = research_profile.get_profile(db, "p")
    assert after == before and research_profile.current_revision(db, "p") == rev
    assert keys == [profile_scoring.rank_key(p, profile_scoring.score_paper(p, after), after) for p in (A, B)]
    assert research_value.perspective(db, "p") == {"version": 2, "text": VIEW + " 수정"}     # 옛 버전은 지우지 않고 비활성


def test_items_live_and_die_by_their_own_evidence(tmp_path):
    """P2. 이 테스트가 무엇을 망가뜨리면 실패하는가: 공용 근거로 근거 없는 활용·세미나를 살리거나, 한 항목이 틀렸다고 같은 카드의 다른 항목까지
    버리거나, 다른 카드의 근거 ID·근거에 없는 숫자(다른 카드·날짜)를 받거나, 활용의 세 칸 중 빈 칸을 받으면 실패한다."""
    entries = _entries(tmp_path)
    reply = _reply([
        _item("P1", IDEA_A, ["P1:A2"], USE_A, ["P2:A1"], SEM_A, []),                       # 활용은 남의 근거, 세미나는 근거 없음
        _item("P2", "여러 로봇 몸체에 같은 정책을 옮겨 쓰려면 다양한 몸체의 자료를 함께 모아 학습한다는 생각이다.", ["P2:A2"],
              {**USE_A, "proposal": "로봇 자료 10,000시간을 모아 같은 정책을 학습해 본다.", "check": ""}, ["P2:A1"]),
    ])
    kept, dropped = research_value.accept(reply, entries)
    assert set(kept["2610.00001"]) == {"idea"} and set(kept["2610.00002"]) == {"idea"}
    reasons = {(d["arxiv_id"], d["item"]): d["reason"] for d in dropped}
    assert reasons[("2610.00001", "use")] == "그 카드의 근거 ID 가 아님" and reasons[("2610.00001", "seminar")] == "근거 없음"
    assert reasons[("2610.00002", "use")] == "근거·제안·조건 중 빈 칸"
    numbers = _reply([_item("P1", IDEA_A + " 성공률이 99% 로 올랐다.", ["P1:A3"]),       # 55·80·40 은 있지만 99 는 없다
                      _item("P2", "몸체를 바꿔도 2610 개 행동을 옮긴다는 생각이다.", ["P2:A1"])])   # arXiv 번호 숫자는 근거가 아니다
    kept, dropped = research_value.accept(numbers, entries)
    assert kept == {} and {d["reason"] for d in dropped} == {"근거에 없는 숫자"}


def test_assertions_are_blocked_but_accurate_negations_and_cited_numbers_pass(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "바로 적용할 수 있다" 같은 단정을 받거나, "직접 활용 가능하다고 볼 근거는 없다" 같은 정확한 경고를
    지우거나, 그 항목이 인용한 발췌에 있는 숫자(55%→80%)를 거절하면 실패한다."""
    entries = _entries(tmp_path)
    bad = {**USE_A, "proposal": "이 감시 장치는 공장 로봇에 바로 적용할 수 있다."}
    good = {**USE_A, "check": "코드가 공개돼 있어도 직접 활용 가능하다고 볼 근거는 없다. 적용 조건 확인 필요."}
    kept, dropped = research_value.accept(_reply([_item("P1", use=bad, use_ev=["P1:A2"])]), entries)
    assert kept == {} and dropped[0]["reason"] == "단정 표현"
    idea = IDEA_A + " 실물 시험에서 성공률이 55%에서 80%로 올랐다고 보고했다."
    kept, _ = research_value.accept(_reply([_item("P1", idea, ["P1:A3"], good, ["P1:A2"])]), entries)
    assert set(kept["2610.00001"]) == {"idea", "use"}


def test_near_identical_items_across_cards_are_dropped_as_boilerplate(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 어느 논문에나 붙는 같은 문장을 카드마다 싣거나, 겹치지 않는 다른 항목까지 버리면 실패한다."""
    entries = _entries(tmp_path)
    same = "현장 작업에서 이 방법을 시험해 보려면 장비와 데이터 조건을 먼저 따져 봐야 한다는 질문이다."
    reply = _reply([_item("P1", IDEA_A, ["P1:A2"], sem=same, sem_ev=["P1:A1"]),
                    _item("P2", sem=same.replace("질문이다", "질문이다."), sem_ev=["P2:A1"])])
    kept, dropped = research_value.accept(reply, entries)
    assert kept == {"2610.00001": {"idea": {"texts": [IDEA_A], "evidence": ["P1:A2"]}}}
    assert sorted(d["arxiv_id"] for d in dropped if d["reason"] == "다른 카드와 거의 같은 문장") == ["2610.00001", "2610.00002"]


def test_unrelated_negation_cannot_rescue_a_direct_use_claim(tmp_path):
    """뒤 문장·다른 칸의 부정이 직접 적용 단정을 살리면 실패한다. 해당 단정 자체를 부정하는 경고는 남긴다."""
    entries = _entries(tmp_path)
    for proposal, check in (
        ("이 방법은 바로 적용할 수 있다. 추가 센서는 필요하지 않다.", USE_A["check"]),
        ("이 방법은 바로 적용할 수 있다.", "추가 센서는 필요하지 않다."),
    ):
        kept, dropped = research_value.accept(_reply([_item(
            "P1", use={**USE_A, "proposal": proposal, "check": check}, use_ev=["P1:A2"])]), entries)
        assert not kept and dropped[0]["reason"] == "단정 표현"
    assert not research_value._assertive("직접 활용 가능하다고 판단할 근거는 없다.")
    assert not research_value._assertive("바로 적용할 수 있다고 단정할 수 없다.")


def test_code_fact_states_only_what_the_ladder_observed(tmp_path):
    """P2 사실/해석. 이 테스트가 무엇을 망가뜨리면 실패하는가: official(본문 링크 — clone 실패·프로젝트 페이지여도 붙는다)을 "공식 코드 확인"·
    "실행 가능" 으로 올리거나, 미조회·제3자·없음을 "공개 구현 없음" 같은 사실로 바꾸거나, 카드의 기존 코드 칸과 겹치는 사실 줄을 세 항목 상자에
    다시 싣거나(10/23 미리보기), 모델 입력에서 사실을 빼면 실패한다."""
    import code_ladder
    db = tmp_path / "p.db"
    assert research_value.code_fact(db, "2610.00001") == ""                                 # 미조회
    code_ladder._record(db, "2610.00001", "official", {"url": "https://example.org/project"}, [])
    code_ladder._record(db, "2610.00002", "third_party", {"url": "https://github.com/x/y"}, [])
    fact = research_value.code_fact(db, "2610.00001")
    assert fact.startswith("논문 본문에 코드·프로젝트 링크가 있다") and "확인하지 않았다" in fact
    assert "공식 코드" not in fact and "실행 가능" not in fact
    assert research_value.code_fact(db, "2610.00002") == ""
    shown = research_value.display(db, A, {"idea": {"texts": [IDEA_A], "evidence": ["P1:A2"]}})
    assert list(shown) == ["idea"] and research_value.display(db, A, {}) == {}
    assert "(확인된 사실 — 근거 ID 아님) 논문 본문에 코드·프로젝트 링크가 있다" in research_value.prompt(VIEW, research_value.corpus(db, [A]))


def test_value_box_renders_the_same_in_new_and_saved_mail_and_guide_says_it_is_interpretation():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 평문·새 HTML·저장 메일 재렌더 중 하나에서 줄이 빠지거나 순서가 바뀌거나 관측 칸·상세 분석으로
    빨려 들어가거나, 항목이 없는 카드에 빈 상자가 붙거나, 읽는 법 안내가 해석이라는 말을 빼면 실패한다."""
    a, b = dict(A), dict(B)
    a["plain_ko"] = "로봇이 물건을 놓치면 알아채고 다시 잡게 하는 장치를 다룬다."
    a["research_value"] = {"idea": IDEA_A,
                           "use": f"논문 근거 — {USE_A['basis']} / 제안 — {USE_A['proposal']} / 확인할 조건 — {USE_A['check']}",
                           "seminar": SEM_A}
    scan = {"papers": [a, b]}
    plain = digest.generate_digest(scan, "팀")
    order = [plain.index(p) for p in (digest.PLAIN_KO_PREFIX, *digest.RESEARCH_VALUE_PREFIXES)]
    assert order == sorted(order) and plain.count(digest.RESEARCH_VALUE_PREFIXES[0]) == 1
    html = digest.generate_digest_html(scan, "팀")
    restored = saved_digest.render_html(plain, "팀", [{"title": a["title"], "link": a["link"]}, {"title": b["title"], "link": b["link"]}])
    box = digest.research_value_html(digest.research_value_lines(a))
    for mail in (html, restored):
        assert mail.count(box) == 1 and mail.count("border-left:3px solid #C99A2E") == 1      # B 에는 상자가 없다
        assert "활용 가능성의 ‘제안’은 논문 내용이 아닙니다" in mail
    shown = saved_digest.plain_text(plain)
    assert "활용 가능성의 ‘제안’은 논문 내용이 아닙니다" in shown
    assert "‘제안’은" not in digest.generate_digest_html({"papers": [dict(B)]}, "팀")             # 항목 없는 메일엔 안내도 없다


def test_engine_failure_means_no_items_and_codex_is_not_retried_in_the_same_run(tmp_path, monkeypatch):
    """규칙 6·시간. 이 테스트가 무엇을 망가뜨리면 실패하는가: 엔진이 다 실패했을 때 예외를 올리거나 빈 상자를 붙이거나, Codex 가 한도·시간 초과로
    실패한 뒤 같은 실행의 다음 호출(다음 프로필)에서 Codex 를 다시 기다리면 실패한다."""
    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(narrative_engine, "generate", nothing)
    view = {"version": 1, "text": VIEW}
    assert asyncio.run(research_value.generate(object(), tmp_path / "p.db", [A, B], view)) == ({}, [], None)
    monkeypatch.undo()
    monkeypatch.setattr(narrative_engine, "_codex_blocked", False)
    calls = []

    def fake_codex(prompt, timeout, schema=None):
        calls.append("codex")
        raise narrative_engine.am.StepError("codex_exit")

    async def fake_retry(fn, label):
        calls.append("gemini")
        return _reply([_item("P1", IDEA_A, ["P1:A2"])])
    monkeypatch.setattr(narrative_engine, "_codex", fake_codex)
    monkeypatch.setattr(narrative_engine.se, "_call_with_rate_limit_retry", fake_retry)
    for _ in range(2):
        kept, _dropped, engine = asyncio.run(research_value.generate(object(), tmp_path / "p.db", [A, B], view))
        assert engine == "gemini" and set(kept) == {"2610.00001"}
    assert calls == ["codex", "gemini", "gemini"]


def test_numbers_are_checked_only_against_the_cited_excerpts(tmp_path):
    """Codex 구현 검토 2026-10-23 재현. 이 테스트가 무엇을 망가뜨리면 실패하는가: 제목의 번호("Watchdog 99")로 근거 없는 "성공률 99%" 를 받거나,
    같은 카드의 **인용하지 않은** 발췌에만 있는 숫자(A3 의 80%)를 인용 발췌(A2)의 근거로 받으면 실패한다(대조 범위를 카드 전체로 넓히는 변이를 잡는다)."""
    titled = dict(A, title="Watchdog 99: Recovering Grasp Failures")
    entries = research_value.corpus(tmp_path / "p.db", [titled, B])
    for idea in (IDEA_A + " 성공률은 99%를 기록했다.", IDEA_A + " 성공률이 80%로 올랐다."):
        kept, dropped = research_value.accept(_reply([_item("P1", idea, ["P1:A2"])]), entries)
        assert kept == {} and dropped[0]["reason"] == "근거에 없는 숫자"
    kept, _ = research_value.accept(_reply([_item("P1", IDEA_A + " 성공률이 80%로 올랐다.", ["P1:A3"])]), entries)
    assert "idea" in kept["2610.00001"]


def test_more_assertion_and_negation_forms_and_boilerplate(tmp_path):
    """Codex 구현 검토 재현. 이 테스트가 무엇을 망가뜨리면 실패하는가: "바로 쓸 수 있다" 를 받거나, "직접 활용 가능하지 않으므로"·
    "바로 적용할 수 있지는 않다" 같은 정확한 경고를 단정으로 지우거나, 사용자가 든 일반론("제조 분야에 활용할 수 있을 것으로 기대된다")을 받으면 실패한다."""
    entries = _entries(tmp_path)
    assert research_value._assertive("이 감시 장치는 제조 현장에서 바로 쓸 수 있다.")
    assert not research_value._assertive("직접 활용 가능하지 않으므로 적용 조건을 확인해야 한다.")
    assert not research_value._assertive("바로 적용할 수 있지는 않다.")
    generic = {**USE_A, "proposal": "제조 분야에 활용할 수 있을 것으로 기대된다."}
    kept, dropped = research_value.accept(_reply([_item("P1", IDEA_A, ["P1:A2"], generic, ["P1:A2"])]), entries)
    assert set(kept["2610.00001"]) == {"idea"} and dropped == [{"arxiv_id": "2610.00001", "item": "use", "reason": "일반론"}]


def test_new_mail_plain_part_carries_the_same_reader_guide_as_html(tmp_path, monkeypatch):
    """Codex 구현 검토 재현. 이 테스트가 무엇을 망가뜨리면 실패하는가: 새 메일 발송(_deliver)의 평문 파트에 읽는 법 안내(세 항목은 해석·'제안'은
    논문 내용이 아님)가 HTML 파트에만 있고 평문에는 빠지면 실패한다 — 그전엔 표시 계층이 저장 메일 재발송에만 걸려 있었다."""
    import email_delivery
    import run_profile_scan as rps
    import server
    import storage
    store, repro = tmp_path / "store.db", tmp_path / "repro"       # digest 가 읽는 DB·재현 경로를 임시로(test_feedback_links.isolated_store 와 같다)
    repro.mkdir()
    storage.init_storage(store)
    for mod in (server, storage):
        monkeypatch.setattr(mod, "DB_PATH", store)
        monkeypatch.setattr(mod, "REPRO_DIR", repro)
    db = tmp_path / "l.db"
    research_profile.create_profile(db, "p", "P", ["robot manipulation"])
    research_profile.add_recipient(db, "p", "alice@x.com")
    paper = dict(A, research_value={"idea": IDEA_A, "seminar": SEM_A})
    sent = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda text, subject, to, html: sent.append((text, html)))
    rps._deliver(db, "p", {"papers": [paper], "candidates_found": 1}, "")
    text, html = sent[0]
    for part in (text, html):
        assert "활용 가능성의 ‘제안’은 논문 내용이 아닙니다" in part and "이 메일 읽는 법" in part


def test_conditional_and_negated_cautions_and_names_with_digits_are_not_over_blocked(tmp_path):
    """Codex 2차 검토 재현(과잉 차단). 이 테스트가 무엇을 망가뜨리면 실패하는가: 조건·부정으로 범위를 제한하는 구체적 경고를 일반론·단정으로 지우거나,
    제목에 있는 이름 속 숫자("Watchdog 99", "RT-2")를 수치 주장으로 보아 지우면 실패한다. 이름을 지워도 근거 없는 "99%" 는 여전히 거절해야 한다."""
    entries = _entries(tmp_path)
    for check in ("다양한 분야로 일반화할 근거는 없으므로 부품 파지에서 미끄러짐 감지 지연을 확인해야 한다.",
                  "부품 파지에 적용 가능성이 있다면 먼저 미끄러짐 감지 지연을 확인해야 한다.",
                  "바로 쓸 수 있다는 것은 아니므로 촉각 감지 조건을 확인해야 한다."):
        kept, dropped = research_value.accept(_reply([_item("P1", use={**USE_A, "check": check}, use_ev=["P1:A2"])]), entries)
        assert "use" in kept.get("2610.00001", {}), (check, dropped)
    assert research_value._assertive("이 장치는 바로 쓸 수 있다. 그렇다는 것은 아니다.")          # 뒤 문장의 부정은 앞 단정을 살리지 않는다
    for title, name in (("Watchdog 99: Recovering Grasp Failures", "Watchdog 99"), ("RT-2 Watchdog for Grasping", "RT-2")):
        named = research_value.corpus(tmp_path / "p.db", [dict(A, title=title)])
        idea = f"{name}는 미끄러짐을 감시하고 실패 뒤 잡기를 다시 계획한다는 생각이다."
        kept, dropped = research_value.accept(_reply([_item("P1", idea, ["P1:A2"])]), named)
        assert "idea" in kept.get("2610.00001", {}), (title, dropped)
        kept, _ = research_value.accept(_reply([_item("P1", idea + " 성공률은 99%다.", ["P1:A2"])]), named)
        assert kept == {}
