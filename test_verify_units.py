"""⑤ 수치 검증기 회귀 테스트 — 네트워크 없이 돈다.

여기 있는 케이스는 전부 "한 번 틀렸거나 틀릴 뻔한 것"이다.
검증기가 거짓 통과를 내면 이 도구의 존재 이유가 사라지므로 경계 규칙을 잠가둔다.
"""

from verify import _extract_numbers, _number_in_text, verify_numbers


def tokens(text: str) -> list[str]:
    return [t for t, *_ in _extract_numbers(text)]


def found(summary: str, source: str) -> bool:
    report = verify_numbers(summary, source)
    assert report.checks, f"숫자를 하나도 못 뽑았다: {summary!r}"
    return report.checks[0].found


# ── 한국어 경계 ────────────────────────────────────────────────────
# 앞경계를 \w 로 막으면 한글(유니코드 \w)이 매칭을 막는다.
# 뒤경계를 \w 로 막으면 조사가 붙은 숫자가 잘려 나간다.


def test_korean_particle_does_not_truncate_number():
    """'99.87도' 가 '99' 로 잘리면 한국어 요약 검증이 통째로 무의미해진다."""
    assert tokens("가짜 수치 99.87도 넣어 본다") == ["99.87"]


def test_korean_prefix_does_not_block_match():
    assert tokens("정확도92.4% 달성") == ["92.4"]


def test_korean_prefix_does_not_corrupt_grouped_number():
    """'총12,345개' 가 '345' 로 잡히면 원문에 없는 숫자를 만들어낸다."""
    assert tokens("총12,345개") == ["12,345"]


# ── 자릿수 경계 (거짓 통과 방지) ───────────────────────────────────
# 단순 부분문자열 대조로 바꾸면 여기가 다 깨진다.


def test_number_does_not_match_inside_longer_number():
    assert found("28.4를 기록", "we report 128.45 BLEU") is False
    assert found("1.5 배", "value 21.55 shown") is False


def test_number_does_not_match_when_source_has_extra_trailing_digit():
    assert found("28.4를 기록", "we report 28.45 BLEU") is False


def test_exact_number_matches():
    assert found("28.4를 기록", "we report 28.4 BLEU") is True


# ── 표기 정규화 ────────────────────────────────────────────────────


def test_comma_grouping_matches_plain_digits():
    assert found("12,000개", "trained on 12000 examples") is True


def test_exponent_notation_is_extracted():
    assert tokens("학습률 3e-4 사용") == ["3e-4"]
    assert found("학습률 3e-4 사용", "learning rate 3e-4") is True


def test_percent_sign_is_not_part_of_token():
    """% 는 토큰에서 분리한다 — 원문이 '92.4 %' 로 띄어 써도 대조되게."""
    assert tokens("정확도 92.4% 달성") == ["92.4"]


# ── 설계상 유지해야 하는 성질 ──────────────────────────────────────


def test_single_digit_integers_are_excluded():
    """한 자리 정수는 어떤 원문에도 있어서 검증을 그냥 통과시킨다."""
    assert tokens("층이 3개, 헤드가 8개") == []


def test_decimal_under_two_digits_is_kept():
    """'0.5' 는 두 글자 미만 규칙에 걸리지 않아야 한다."""
    assert tokens("비율 0.5 적용") == ["0.5"]


def test_same_value_counted_once():
    report = verify_numbers("42.0 을 썼고 다시 42.0 을 썼다", "42.0")
    assert report.total == 1


def test_mismatch_is_reported_not_raised():
    """flag-and-pass — 불일치는 예외가 아니라 보고서다. 저장을 막지 않는다."""
    report = verify_numbers("정확도 99.87 달성", "실제로는 28.4 였다")
    assert report.total == 1
    assert report.matched == 0
    assert [c.token for c in report.unmatched] == ["99.87"]
    assert report.pass_ratio == 0.0


def test_summary_without_numbers_passes_vacuously():
    """숫자가 없으면 통과율 1.0. 이 값을 품질 지표로 오해하면 안 된다 —
    숫자를 안 쓰는 것이 최단 통과 경로가 되는 것이 Goodhart 문제의 핵심이다."""
    report = verify_numbers("숫자가 전혀 없는 요약", "원문")
    assert report.total == 0
    assert report.pass_ratio == 1.0


# ── 출처 위치 표기 오탐 방지 (2026-08-06, VegaEdge 실측) ───────────
# 프롬프트 v2 R2 규칙이 "본문 6.1절"류 출처 표기를 강제하는데, 이 안의
# 숫자를 데이터로 착각해 검증기가 불일치로 잘못 신고했다 — "6.1"이
# 소수처럼 생겨서 한 자리 정수 제외 규칙을 안 타고 통과했었다.


def test_section_reference_is_not_a_data_number():
    """'본문 6.1절' 의 6.1은 위치 표기지 데이터가 아니다."""
    assert tokens("AUC-ROC 0.99 ★★★ — 본문 6.1절 및 Figure 3a") == ["0.99"]


def test_table_reference_is_not_a_data_number():
    assert tokens("정확도 92.4% (본문 4.2절 Table 12)") == ["92.4"]


def test_multi_digit_table_reference_excluded_but_nearby_data_kept():
    """'Table 12' 의 12는 한 자리 정수 제외 규칙(len<2)을 안 타서 이 수정
    전에는 그대로 데이터로 오인됐다. 같은 문장의 진짜 데이터(92.4)는 그대로 남아야 한다."""
    assert tokens("Table 12의 정확도는 92.4였다") == ["92.4"]


# ── 문장 단위 근거 그라운딩 (2026-08-06, §8-9) ──────────────────────
# "숫자가 원문 어딘가에 있다"에서 "숫자가 인용한 [S번호] 문장에 있다"로
# 검증을 격상했다. 핵심 테스트는 "값이 원문 다른 곳엔 있지만 인용한
# 문장에는 없는" 경우 — 예전 방식이면 통과했을 걸 이제는 잡아내야 한다.


def test_grounded_citation_matches_correct_sentence():
    source = "The model achieves 92.4% accuracy on ImageNet in this run. Training took 8 hours on 4 GPUs for this experiment."
    summary = "정확도 92.4% (ImageNet) — 본문 3절 [S0001] ★★★"
    report = verify_numbers(summary, source)
    assert report.total == 1
    assert report.matched == 1
    assert report.grounded == 1
    assert report.checks[0].sentence_id == 1
    assert report.checks[0].found is True


def test_grounded_citation_to_wrong_sentence_fails_even_if_value_exists_elsewhere():
    """이 기능의 핵심 케이스다 — 값이 원문 어딘가엔 있지만 인용한 문장에는
    없으면 실패해야 한다. 예전(비-그라운딩) 방식이면 원문 전체에서 찾아 통과했을
    것 — 아래에서 그것도 함께 확인한다(대조군)."""
    source = (
        "The baseline model performs poorly on this benchmark overall today. "
        "This sentence is unrelated and contains no numeric result at all whatsoever. "
        "A separate experiment on CIFAR reported 92.4 percent accuracy in the appendix section."
    )
    summary = "정확도 92.4% (베이스라인) — 본문 3절 [S0001] ★★★"  # 실제 값은 3번 문장에 있는데 1번을 인용
    report = verify_numbers(summary, source)
    assert report.total == 1
    assert report.matched == 0
    assert report.grounded == 1
    assert report.checks[0].found is False
    assert report.checks[0].cited_text is not None  # 문장은 찾았지만 그 안에 값이 없었다
    # 대조군: 예전 방식(원문 전체 대조)이면 이건 통과했을 것 — 이게 이번에 막은 구멍이다.
    assert _number_in_text("92.4", source.replace(",", ""))


def test_grounded_citation_out_of_range_sentence_id_fails():
    source = "Only one sentence exists here with the number 42.5 in it for this test."
    summary = "값 42.5 (조건) — 본문 [S0099] ★★★"  # 존재하지 않는 문장 번호(지어낸 태그 시나리오)
    report = verify_numbers(summary, source)
    assert report.checks[0].found is False
    assert report.checks[0].cited_text is None  # 범위 밖이라 조회 자체가 안 됨
    assert report.grounded == 1


def test_ungrounded_legacy_summary_falls_back_to_whole_document_search():
    """[S번호] 태그가 없는 구형 요약(이 기능 도입 전 생성분)은 기존 방식
    (원문 전체 대조) 그대로 동작해야 한다 — 하위 호환, 기존 39편 재검증 불필요."""
    source = "Sentence one has nothing interesting in it at all. Sentence two reports 55.3 percent somewhere else entirely."
    summary = "값 55.3% (조건) — 본문 4절 ★★★"  # 태그 없음
    report = verify_numbers(summary, source)
    assert report.checks[0].found is True
    assert report.checks[0].grounded is False
    assert report.checks[0].sentence_id is None


def test_adjacent_sentence_window_tolerates_off_by_one_segmentation():
    """세그멘테이션이 완벽하지 않을 수 있어(§6) 인용 문장의 바로 앞뒤 1문장까지
    허용한다 — 정확히 그 문장이 아니라 바로 인접해 있어도 통과해야 한다."""
    source = "First sentence sets up context for the study. Second sentence reports 77.1 percent as the main result. Third sentence wraps everything up nicely."
    summary = "값 77.1% (조건) — 본문 [S0001] ★★★"  # 정확히는 2번 문장인데 1번을 인용(±1 오차)
    report = verify_numbers(summary, source)
    assert report.checks[0].found is True
    assert report.grounded == 1


def test_same_value_different_citations_counted_separately():
    """같은 숫자값이라도 서로 다른 문장을 인용하면 서로 다른 주장이므로 따로
    센다 — 값만으로 뭉뚱그려 dedup 하면 안 된다(예전 방식의 진짜 한계였다)."""
    source = (
        "First experiment reports 50.0 percent on task A in this section. "
        "Nothing interesting here at all in this particular sentence. "
        "Second experiment reports 50.0 percent on task B in this section."
    )
    summary = "50.0% (과제 A) — [S0001] ★★★ / 50.0% (과제 B) — [S0003] ★★★"
    report = verify_numbers(summary, source)
    assert report.total == 2  # 값은 같지만 인용이 달라 별개로 센다
    assert report.matched == 2


def test_tag_without_following_star_is_not_treated_as_grounding_citation():
    """실측(Feelbert, 2026-08-06): 모델이 '결과' 절뿐 아니라 '연관 연구'의
    참고문헌 인용에도 [S번호] 태그를 붙였다("Kajita (2008) [S021]") — 이건
    R2/R3가 요구한 "[S번호] 뒤에 ★등급" 형식이 아니므로 그라운딩하면 안 된다.
    별점이 안 붙은 태그는 무시하고 구형 폴백(원문 전체 대조)으로 넘어가야 한다."""
    source = "This work builds on Kajita's 2008 study of legged robots extensively. The main experiment here reports 91.0 percent accuracy on the test set."
    # "(2008)" 뒤에 태그만 있고 별점이 없다 — 데이터 인용이 아니라 참고문헌 위치 표기다.
    summary = "이 논문은 Kajita (2008) [S0001] 연구를 기반으로 한다."
    report = verify_numbers(summary, source)
    assert report.total == 1
    assert report.checks[0].token == "2008"
    assert report.checks[0].grounded is False  # 별점이 없어 그라운딩 안 됨 — 폴백
    assert report.checks[0].sentence_id is None
    # 폴백된 값(2008)이 원문 어딘가에 있으면 구형 방식대로 통과한다.
    assert report.checks[0].found is True


def test_tag_with_following_star_is_grounded_correctly():
    """대조군 — 별점이 바로 뒤에 붙은 진짜 R2/R3 형식 인용은 정상적으로 그라운딩된다."""
    source = "This work builds on Kajita's 2008 study of legged robots extensively. The main experiment here reports 91.0 percent accuracy on the test set."
    summary = "정확도 91.0% (테스트 조건) — 본문 [S0002] ★★★"
    report = verify_numbers(summary, source)
    assert report.checks[0].grounded is True
    assert report.checks[0].sentence_id == 2
    assert report.checks[0].found is True


def test_same_value_same_citation_deduplicated():
    """④ 결과가 ③ 결과의 값을 그대로 재사용하는 것(R2 설계 의도)은 같은 주장의
    반복이므로 여전히 한 번만 센다 — 같은 (값, 인용문장) 조합이면 dedup."""
    source = "The main result here is 33.3 percent on the benchmark in this study."
    summary = "값 33.3% (과제) — [S0001] ★★★ (뒤에서 재사용) 값 33.3% (과제) — [S0001] ★★★"
    report = verify_numbers(summary, source)
    assert report.total == 1
    assert report.matched == 1
