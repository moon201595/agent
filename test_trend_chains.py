"""⑨ 연구 동향 — 날마다의 흐름을 연결 기록으로 이어 여러 날 이어진 주제를 찾고, 모델 서술을 검사해 싣는다(2026-10-22).

지키는 계약: 연결(history.source)로만 잇는다 · 사흘 미만이거나 창 밖이면 동향이 아니다 · 며칠·몇 편·경과는 Python 이 센다 ·
모델 글은 주제 번호·한국어·숫자를 검사하고 틀린 주제만 글 없이(경과만) 싣는다 · 저장 평문에서 같은 HTML 로 다시 그린다. 자료는 합성이다.
"""
import asyncio
import json

import digest
import saved_digest
import trend_chains


def _row(nid, day, threads):
    story = {"version": "story-v3", "threads": threads}
    return {"narrative_id": nid, "reader_date": day, "body": "", "audit_json": json.dumps({"story": story})}


def _thread(name, titles, link=None):
    history = {"relation": "continuing", "note": "n", "source": {"narrative_id": link[0], "thread_index": link[1]}} if link else None
    return {"name": name, "body": f"{name} 설명 [P1:A]", "titles": titles, "history": history}


ROWS = [
    _row("a", "2026-10-08", [_thread("미래 변화 설계", ["Commit While Futures Agree"]), _thread("외딴 주제", ["Lonely"])]),
    _row("b", "2026-10-13", [_thread("미래 변화 감독", ["Evo-World", "Forward Dynamics"], ("a", 1))]),
    _row("c", "2026-10-16", [_thread("미래 정보 결합", ["ObjDream", "Evo-World"], ("b", 1)), _thread("이틀짜리", ["X"])]),
    _row("d", "2026-10-17", [_thread("이틀짜리 후속", ["Y"], ("c", 2))]),
    _row("old", "2026-09-01", [_thread("창 밖", ["Old"])]),
]


def test_chains_follow_links_count_days_and_papers_and_drop_short_or_old():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 연결이 없는 흐름을 이름만 보고 묶거나, 이틀짜리를 동향으로 부르거나, 창 밖 흐름을 넣거나,
    같은 논문을 두 번 세거나(Evo-World), 마지막 흐름 이름이 아닌 것을 제목으로 쓰면 실패한다."""
    found = trend_chains.chains(ROWS, today="2026-10-22")
    assert len(found) == 1
    chain = found[0]
    assert (chain["days"], chain["papers"], chain["first"], chain["last"]) == (3, 4, "2026-10-08", "2026-10-16")
    assert [s["name"] for s in chain["steps"]] == ["미래 변화 설계", "미래 변화 감독", "미래 정보 결합"] and chain["name"] == "미래 정보 결합"
    assert "[P1:A]" not in chain["steps"][0]["body"]
    assert trend_chains.chains(ROWS, today="2026-10-22", min_days=2)[-1]["name"] in {"이틀짜리 후속", "미래 정보 결합"}
    assert trend_chains.chains(ROWS, today="2026-11-20") == []           # 3주가 지나면 동향이 아니다


def test_model_text_is_checked_per_chain_and_failures_keep_only_the_timeline(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 없는 주제 번호·영어·자료에 없는 숫자를 받거나, 한 주제가 틀렸다고 다른 주제 글까지 버리거나,
    모델이 다 실패했을 때 동향 칸을 통째로 없애면 실패한다(경과는 Python 값이라 남는다)."""
    found = trend_chains.chains(ROWS, today="2026-10-22", min_days=2)
    material = trend_chains._material(found)
    good = {"chain": "C1", "title": "미래를 예측해 행동 만들기",
            "body": "처음에는 미래 변화를 설계에 썼다. 그다음에는 학습을 돕는 데 썼다.\n최근에는 결합 방식을 비교한다. 바뀌는 부분만 본다."}
    reply = json.dumps({"trends": [good, {"chain": "C2", "title": "x", "body": "This is English only."},
                                   {"chain": "C9", "title": "없는 주제", "body": "무시한다."}]}, ensure_ascii=False)
    accepted = trend_chains._accept(reply, found, material)
    assert accepted[0]["body"].split("\n") == ["처음에는 미래 변화를 설계에 썼다. 그다음에는 학습을 돕는 데 썼다.",
                                                "최근에는 결합 방식을 비교한다. 바뀌는 부분만 본다."] and accepted[1] == {}
    # 제목의 숫자도 보고, 날짜(2026-10-08 의 "2026")·다른 주제는 근거가 아니다(Codex 검토 2026-10-22 재현: 둘 다 통과했다).
    for bad in ({**good, "body": "논문 7편이 나왔다. 많이 나왔다."}, {**good, "title": "성능이 999% 향상된 로봇"},
                {**good, "body": "논문이 2026편 나와서 커졌다. 빠르게 성장한다."}):
        try:
            trend_chains._accept(json.dumps({"trends": [bad]}, ensure_ascii=False), found[:1], material)
            raise AssertionError(f"자료에 없는 숫자를 받았다: {bad}")
        except ValueError:
            pass

    async def nothing(*a, **k):
        return None
    import narrative_engine
    monkeypatch.setattr(narrative_engine, "generate", nothing)
    shown = asyncio.run(trend_chains.narrate(object(), found))
    assert [t["title"] for t in shown] == [c["name"] for c in found] and all(t["body"] == "" and t["steps"] for t in shown)


def test_single_sentence_paragraphs_join_the_one_before():
    """2026-10-22 사용자: "문장마다 들여쓰기를 하면 어떻게 해". 이 테스트가 무엇을 망가뜨리면 실패하는가: 단계마다 줄을 바꾼 글을 그대로 두어
    한 문장짜리 문단이 따로 들여 쓰이거나, 여러 문장 문단까지 합치거나, 첫 문단이 한 문장이라고 버리거나, 물음표 든 논문 이름에서 문장을 잘못 세면 실패한다."""
    body = "주제를 푼다.\n처음에는 A를 했다.\n그다음에는 B를 했다(Is C Enough?).\n최근에는 D를 한다. 지금은 E로 간다.\n이어서 F가 나왔다."
    assert trend_chains._paragraphs(body).split("\n") == [
        "주제를 푼다. 처음에는 A를 했다. 그다음에는 B를 했다(Is C Enough?).", "최근에는 D를 한다. 지금은 E로 간다. 이어서 F가 나왔다."]
    assert trend_chains._paragraphs("한 문장이다.") == "한 문장이다."
    assert "두세 문단" in trend_chains._prompt(trend_chains.chains(ROWS, today="2026-10-22"), "x")


def test_trends_section_renders_the_same_in_new_mail_and_saved_reformat():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 연구 동향이 오늘의 연구 흐름 뒤로 가거나, 평문·HTML·형식 수정본 중 하나에서 빠지거나,
    며칠·몇 편·경과(Python 값)가 사라지거나, 본문 문단이 합쳐지면 실패한다."""
    found = trend_chains.chains(ROWS, today="2026-10-22")
    trends = trend_chains.present(found, [{"title": "미래를 예측해 행동 만들기", "body": "첫 문단이다.\n둘째 문단이다."}])
    scan = {"papers": [{"arxiv_id": "2610.00001", "title": "Paper", "link": "https://arxiv.org/abs/2610.00001",
                        "abstract_brief": "요지", "_score": {}}],
            "narrative": ("■ 1. 흐름\n오늘 설명 [P1:A]\n- Paper [P1:A]", []), "research_trends": trends}
    plain = digest.generate_digest(scan, "팀")
    assert plain.index("■ " + digest.RESEARCH_TRENDS_HEADING) < plain.index("■ 오늘의 연구 흐름")
    assert "3일 · 논문 4편 · 10/08 ~ 10/16" in plain and "경과: 10/08 미래 변화 설계 → 10/13 미래 변화 감독 → 10/16 미래 정보 결합" in plain
    html = digest.generate_digest_html(scan, "팀")
    restored = saved_digest.render_html(plain, "팀", [{"title": "Paper", "link": "https://arxiv.org/abs/2610.00001"}])
    for mail in (html, restored):
        assert "미래를 예측해 행동 만들기" in mail and "3일 · 논문 4편" in mail and mail.count("text-indent:1em;margin:6px 0;\">첫 문단") == 1
        assert mail.index(">" + digest.RESEARCH_TRENDS_HEADING + "</p>") < mail.index(">오늘의 연구 흐름</p>")   # 절 제목끼리(읽는 법 안내 글자 말고)
        assert "경과 · 10/08 미래 변화 설계" in mail
