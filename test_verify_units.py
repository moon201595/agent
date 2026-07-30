"""⑤ 수치 검증기 회귀 테스트 — 네트워크 없이 돈다.

여기 있는 케이스는 전부 "한 번 틀렸거나 틀릴 뻔한 것"이다.
검증기가 거짓 통과를 내면 이 도구의 존재 이유가 사라지므로 경계 규칙을 잠가둔다.
"""

from verify import _extract_numbers, verify_numbers


def tokens(text: str) -> list[str]:
    return [t for t, _, _ in _extract_numbers(text)]


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
