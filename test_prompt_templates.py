"""prompts/*.md 자체를 대상으로 한 회귀 테스트 — 네트워크 불필요.

2026-08-06 실측: 프롬프트 템플릿의 "나쁜 예 / 좋은 예" 절이 실제 벤치마크처럼
보이는 가짜 수치("VUS-PR +14%", "TSB-AD" 등)를 예시로 쓰고 있었는데, Groq가
이걸 실제 논문 결과인 것처럼 그대로 베껴 쓴 사례가 실전에서 나왔다(Agentic
Reasoning 논문 요약, §5 참고). 템플릿을 "형식만 보여주는 명백히 가짜인
자리표시자"로 고쳤다 — 이 테스트는 그 위험한 문자열들이 다시 기어들어오지
않게 잠근다. 프롬프트 파일은 사람이 자유롭게 고칠 수 있는 텍스트라 코드
리뷰만으로는 놓치기 쉬워서, 결정적으로 검사할 수 있는 부분은 테스트로 못박는다.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
_TEMPLATE_PATHS = [
    ROOT / "prompts" / "summary_template.md",
    ROOT / "prompts" / "summary_template_survey.md",
]

# 2026-08-06 실측에서 실제로 다른 논문 요약에 그대로 베껴져 나왔던 가짜
# 벤치마크명·수치·절 번호·인용 번호. 이 문자열들이 템플릿에 다시 나타나면
# "실제 데이터처럼 보이는 예시"가 재도입됐다는 신호다.
_RISKY_STRINGS = [
    "VUS-PR",
    "TSB-AD",
    "+14%",
    "+24%",
    "Figure 4~7",
    "[S0142]",
    "[S0087]",
    "[S0091]",
    "[S0103]",
]


def test_templates_do_not_contain_realistic_looking_fake_benchmarks():
    for path in _TEMPLATE_PATHS:
        text = path.read_text(encoding="utf-8")
        for risky in _RISKY_STRINGS:
            assert risky not in text, (
                f"{path.name} 에 실제 벤치마크처럼 보이는 가짜 예시 문자열 "
                f"'{risky}' 가 있다 — 모델이 이걸 실제 결과로 베낄 위험이 있다."
            )


def test_summary_template_has_explicit_example_warning():
    """"나쁜 예 / 좋은 예" 절 예시가 가짜라는 명시적 경고가 있어야 한다."""
    text = (ROOT / "prompts" / "summary_template.md").read_text(encoding="utf-8")
    assert "가짜" in text
    assert "베끼" in text  # "베끼지 마라" 류 경고


def test_survey_template_has_explicit_example_warning():
    text = (ROOT / "prompts" / "summary_template_survey.md").read_text(encoding="utf-8")
    assert "가짜" in text
