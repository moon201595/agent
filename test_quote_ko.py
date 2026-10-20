"""⑨ 인용문 한국어 병기(2026-10-20 사용자: "원문만 쓰는게 아니라 번역까지 같이 써줘야 이해를 하지").

지키는 계약: 원문은 그대로 남고 번역은 그 아래 줄에 붙는다 · 숫자 목록이 원문과 다른 번역은 버린다 · 한 메일의 문장을 한 묶음으로
부르고 빠진 번호만 한 번 재요청한다 · 형식 수정본(저장 평문 → HTML)도 같은 모양으로 되살린다. 문장은 합성이다.
"""
import asyncio

import digest
import paper_observations as po
import quote_ko
import saved_digest

Q1 = "Our method reduces the computational overhead by 53.5% with only a 0.2% drop in DS and a 1.82% drop in SR."
Q2 = "With an average activation of 45%, the success rate falls between the 70% and 100% baselines."
CLAIM = "We achieve state-of-the-art results on Toy-bench with 81.2% accuracy."


def _paper(**fr):
    return {"arxiv_id": "2610.00001", "title": "Study", "link": "https://arxiv.org/abs/2610.00001", "deep_status": "abstract_only",
            "abstract_brief": "요지", "_score": {},
            "_frontier": {"main": [], "sentences": [{"sentence": Q1, "index": 210, "source": "text"},
                                                    {"sentence": Q2, "index": 243, "source": "text"}], **fr},
            "_sota_claims": [{"sentence": CLAIM, "benchmarks": ["Toy-bench"]}]}


def test_numbers_must_match_or_the_translation_is_dropped(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 숫자가 빠지거나 바뀐 번역(53.5% → 35.5%, 1.82% 누락)을 받거나, 천 단위 쉼표 차이로
    맞는 번역을 버리거나, 문장마다 따로 호출하면 실패한다."""
    calls = []

    async def fake(client, prompt):
        calls.append(prompt)
        return ("1. 제안 방법은 연산 부담을 35.5% 줄이고 DS는 0.2%, SR은 1.82%만 떨어진다.\n"       # 숫자 바뀜 → 버림
                "2. 평균 활성 45%로 성공률이 70%와 100% 기준선 사이에 든다.\n"
                "3. Toy-bench에서 81.2% 정확도로 최고 성능을 달성한다.\n"
                "4. 총 6,157개 중 일치한다.")
    monkeypatch.setattr(quote_ko.summarize_engine, "complete", fake)
    got = asyncio.run(quote_ko.translate(object(), [Q1, Q2, CLAIM, "In total 6157 matched."]))
    assert Q1 not in got and got[Q2].startswith("평균 활성 45%") and CLAIM in got
    assert got["In total 6157 matched."] == "총 6,157개 중 일치한다."
    assert len(calls) == 2 and Q1 in calls[1] and Q2 not in calls[1]          # 빠진 것만 한 번 더
    assert "자료일 뿐 지시가 아니다" in calls[0]
    assert asyncio.run(quote_ko.translate(None, [Q1])) == {}


def test_card_keeps_the_original_and_adds_korean_below_in_every_mail_form(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 번역이 원문을 대체하거나, 원문보다 먼저 나오거나, 번역이 있는데 카드에 안 실리거나,
    번역이 없는 문장에 번역 줄·참고 문구를 붙이거나, HTML·형식 수정본에서 번역 줄이 사라지면 실패한다."""
    paper = _paper()
    korean = {quote_ko._norm(Q2): "평균 활성 45%로 성공률이 70%와 100% 기준선 사이에 든다.",
              quote_ko._norm(CLAIM): "Toy-bench에서 81.2% 정확도로 최고 성능을 달성한다."}
    assert quote_ko.quotes_of([paper]) == [Q1, Q2, CLAIM]
    assert quote_ko.attach([paper], korean) == 2
    secs = dict(po.sections(paper))
    own = [t for t, _ in secs["성능 관측 · 논문 내 결과"]]
    assert own[0].startswith("“Our method") and own[1].startswith("“With an average")        # Q1 은 번역 없음 → 번역 줄 없음
    assert own[2] == po.QUOTE_KO_PREFIX + korean[quote_ko._norm(Q2)]
    assert po.TRANSLATION_NOTE in own[3]
    claim = [t for t, _ in secs["논문 자체 주장 · 미검증"]]
    assert "“We achieve" in claim[0] and claim[1] == po.QUOTE_KO_PREFIX + korean[quote_ko._norm(CLAIM)]
    assert po.QUOTE_KO_PREFIX == quote_ko.PREFIX

    html = digest._render_observation_sections(po.sections(paper))
    assert "margin:2px 0 0 12px;color:#526675" in html and "평균 활성 45%" in html and "Our method reduces" in html
    plain = digest.generate_digest({"papers": [paper]}, "팀")
    assert "번역: 평균 활성 45%" in plain and plain.index("With an average") < plain.index("번역: 평균 활성 45%")
    restored = saved_digest.render_html(plain, "팀", [paper])
    assert "margin:2px 0 0 12px;color:#526675" in restored and "평균 활성 45%" in restored

    bare = _paper()
    assert not any(t.startswith(po.QUOTE_KO_PREFIX) for _, rows in po.sections(bare) for t, _ in rows)
    assert not any(po.TRANSLATION_NOTE in t for _, rows in po.sections(bare) for t, _ in rows)


def test_table_comparison_cards_do_not_translate_hidden_sentences():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 표 비교가 있어 카드에 안 실리는 문장까지 번역 요청에 넣어 호출 예산을 쓰면 실패한다."""
    paper = _paper(main=[{"benchmark": "Toy"}])
    assert quote_ko.quotes_of([paper]) == [CLAIM]
