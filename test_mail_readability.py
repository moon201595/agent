"""⑨ 처음 받는 사람이 읽을 수 있는 표시(2026-10-22 팀장: "무슨 말인지 하나도 모르겠다").

지키는 계약: 저장 본문 형식은 그대로이고 보여 줄 때만 쉬운 말로 바꾼다 · 연구 흐름 화면에는 근거 표지가 없고 (해석)은 남는다 ·
재현 코드는 한국어 설명으로, 모르는 코드는 지우지 않는다 · 안내문은 별점을 말하지 않고 버튼 문장은 버튼이 있을 때만 쓴다.
"""
import digest
import saved_digest

STORY = """■ 1. 권한 지키기
↳ 지난 관측 2026-10-21 · 실행 과정의 정당성 · 갈라짐 · 오늘 1편의 후속 관측 — P5는 칩에서 검증한다 [P5:A]. (해석)
공통 문제는 권한이다 [P1:A][P2:A]. 결과가 나왔다 [P1:R], 원문도 있다 [P2:S0012].
- Act on Your Behalf (요약 논문 1/5) · 원문 분석 [P1:R]
- Toward Agentic AI (요약 논문 5/5) · 원문 분석 [P5:A]
- Cache Engineering for Agents — 캐시 [P11:A]"""


def test_story_screen_has_no_evidence_marks_but_keeps_interpretation_and_names():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 근거 표지([P1:A]·[P2:S0012])가 화면에 남거나, 지우다 (해석)까지 지우거나,
    목록 줄에 표지 대신 제목이 한 번 더 붙거나, 본문의 "P5" 를 다른 논문 이름으로 바꾸거나, 이어짐 줄을 문장으로 못 풀면 실패한다."""
    out = digest.readable_narrative(STORY)
    assert "[P" not in out and "S0012" not in out
    assert "(해석)" in out and "공통 문제는 권한이다." in out and "원문도 있다." in out
    assert "- Cache Engineering for Agents — 캐시\n" in out + "\n"
    assert out.count("Act on Your Behalf") == 1
    assert "P5는 칩에서" in out                         # 본문의 P숫자는 바꾸지 않는다(모델 이름일 수 있다)
    assert "2026-10-21 메일의 '실행 과정의 정당성' 흐름과 비교 — 갈라짐(같은 문제에 다른 접근이 나옴)" in out


def test_internal_codes_become_korean_and_unknown_codes_are_kept():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: build_failed·no_install_target 같은 코드가 그대로 나가거나, 모르는 코드를 지워 버리거나,
    "읽을 포인트 — `X` 관심축" 이 남으면 실패한다."""
    assert digest.readable_label("[코드 재현 실패 — build · build_failed]") == "[코드 동작을 확인하지 못함 — 설치: 설치 단계에서 실패]"
    assert "설치할 대상 파일" in digest.readable_label("[코드 재현 실패 — no_target · no_install_target]")
    assert "weird_code" in digest.readable_label("[코드 재현 실패 — run · weird_code]")
    assert digest.readable_label("읽을 포인트 — `LLM agent` 관심축과 직접 연관.") == "고른 이유: 관심 키워드 'LLM agent'에 해당."
    assert digest.readable_heading("이번 실행에서 걸러진 것: 조건 불일치 662건").startswith("이번 검색에서 선정하지 않은 이유")


def test_guide_never_mentions_stars_and_mentions_buttons_only_when_present():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 9/15 결정으로 뺀 별점을 안내가 다시 말하거나, 버튼 없는 메일에 버튼 사용법을 쓰면 실패한다."""
    plain, with_buttons = digest.reader_guide(5), digest.reader_guide(5, buttons=True)
    assert not any("★" in line for line in with_buttons)
    assert not any("더 보고 싶음" in line for line in plain) and any("더 보고 싶음" in line for line in with_buttons)
    assert "핵심 논문 5편" in " ".join(plain)


def test_stored_text_is_unchanged_and_only_the_display_is_readable():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 저장 평문(generate_digest)에 안내·새 문구를 넣어 저장 형식을 바꾸거나(왕복 파서가 깨진다),
    메일 머리가 없는 조각 글에 안내를 끼우거나, 저장 평문을 다시 그린 형식 수정본에 안내·쉬운 표시가 안 나오면 실패한다."""
    scan = {"papers": [{"arxiv_id": "2610.00001", "title": "Paper One", "link": "https://arxiv.org/abs/2610.00001",
                        "abstract_brief": "요지", "_score": {}}],
            "narrative": (STORY, []), "unmatched_count": 3}
    stored = digest.generate_digest(scan, "팀")
    assert "이 메일 읽는 법" not in stored and "조건 불일치 3건" in stored
    shown = saved_digest.plain_text(stored)
    assert "■ 이 메일 읽는 법" in shown and "조건 불일치" not in shown and "[P1:A]" not in shown
    fragment = "■ 지난 8일 검색 기준 변화\n   반영된 사용자 반응 15건\n"
    assert saved_digest.plain_text(fragment) == fragment


def test_thread_body_keeps_paragraphs_and_strips_list_ornaments_per_paragraph():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 흐름 body 의 줄바꿈을 다시 공백으로 합치거나, "- " 로 시작하는 문단을 그대로 둬
    논문 목록으로 읽히게 하거나, 근거 검사가 문단을 나눈 글에서 근거를 놓치면 실패한다."""
    import trend_report
    body = "문제가 있다 [P1:A].\n- 논문 하나는 이렇게 했다 [P1:A].\n\n■ 둘째는 저렇게 했다 [P2:A]."
    out, cited = trend_report._fit(body, {1, 2}, {"P1:A", "P2:A"}, trend_report._STORY_LIMITS["body"], paragraphs=True)
    assert out.split("\n") == ["문제가 있다 [P1:A].", "논문 하나는 이렇게 했다 [P1:A].", "둘째는 저렇게 했다 [P2:A]."]
    assert cited == {1, 2}
    one, _ = trend_report._fit(body, {1, 2}, {"P1:A", "P2:A"}, trend_report._STORY_LIMITS["headline"])
    assert "\n" not in one                                  # 요점·관계 문장은 계속 한 문단


def test_model_named_p5_keeps_its_own_title_and_link():
    """Codex 검토 2026-10-22 재현: "P5" 라는 모델 이름을 내부 번호로 보고 다른 논문 이름으로 바꿔, 제목 목록과 원문 링크까지 바뀌었다.
    이 테스트가 무엇을 망가뜨리면 실패하는가: 본문·목록의 P숫자 낱말을 다시 치환하면 실패한다."""
    story = "■ 1. 추천 방법\nP5는 추천 모델이다 [P1:A].\n- P5 Personalized Recommendation [P1:A]\n- Other Study [P5:A]"
    out = digest.readable_narrative(story)
    assert "P5는 추천 모델이다." in out and "- P5 Personalized Recommendation" in out and out.count("Other Study") == 1


def test_negative_number_at_paragraph_start_survives():
    """Codex 검토 2026-10-22 재현: 문단마다 목록 기호를 떼다 "-0.5%" 의 부호가 사라져 숫자 검사도 통과했다.
    이 테스트가 무엇을 망가뜨리면 실패하는가: 줄머리 장식 제거가 숫자 앞 "-" 를 다시 떼면 실패한다."""
    import trend_report
    out, _ = trend_report._fit("첫 설명 [P2:A].\n-0.5%의 오차를 보고했다 [P1:A].\n- 목록처럼 시작했다 [P1:A].",
                               {1, 2}, {"P1:A", "P2:A"}, trend_report._STORY_LIMITS["body"], paragraphs=True)
    assert out.split("\n")[1].startswith("-0.5%") and out.split("\n")[2].startswith("목록처럼")
