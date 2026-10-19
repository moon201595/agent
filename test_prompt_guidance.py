"""주간 관리 제안 프롬프트의 근거 사용 규칙(2026-10-16).

9/20~10/5 규칙 검증이 거절한 변경 3건은 모두 X(핵심어 미적중으로 이미 탈락한 논문)를 근거로 한 제외어였다. 그 용어가 든 논문 중
브리핑에 수록된 것은 기간 내 0편이라 넣었어도 결과가 안 바뀌었다 — 모델이 같은 제안을 반복하지 않게 프롬프트에 이유를 적었다.
"""
from pathlib import Path


def test_propose_prompt_says_x_is_not_exclusion_evidence():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 제안 프롬프트에서 "X 는 제외어 근거가 아니다" 문구를 지워 모델이 탈락 논문을 근거로 효과 없는
    제외어를 다시 제안하게 하면 실패한다(규칙 검증이 막지만 판정 호출을 낭비한다)."""
    text = (Path(__file__).resolve().parent / "prompts" / "agent_propose_v1.md").read_text(encoding="utf-8")
    assert "X(탈락 논문)는 제외어 근거가 아니다" in text and "add_keyword 후보를 찾는 자료" in text
