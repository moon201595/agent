"""요약문 수치 검증기.

요약문에서 숫자 토큰을 뽑아 원문 텍스트에 실제로 존재하는지 문자열 대조한다.
LLM을 쓰지 않는 결정적(deterministic) 검증이며, 하네스의 "미확인 수치 인용 금지"
규칙을 시스템 수준에서 강제하는 장치다.

한계(문서화된 설계 결정):
- 한 자리 정수(0~9)는 어떤 텍스트에도 존재해 검증 의미가 없어 제외한다.
- 단위 환산(예: 원문 0.5m ↔ 요약 50cm)은 탐지하지 못한다.
- PDF 텍스트 추출 품질에 따라 원문에 있는 숫자가 누락 판정될 수 있다.
  → 불일치는 "오류 확정"이 아니라 "사람이 원문을 확인할 것" 신호로 해석한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 1,234 / 28.4 / 3e-4 / 0.78 형태를 잡는다. % 기호는 숫자에서 분리해 취급.
# 경계 규칙: 앞뒤로 숫자·점·라틴 문자가 붙으면 식별자(v2, GPT4, 1706.03762)로 보고
# 시작점을 제한하되, 한글 조사("28.4를", "99.87도")는 경계로 허용해야 한다.
# 그래서 뒤쪽은 '숫자만 아니면' 경계로 인정한다 — \w 금지로 두면 한국어 문장에서
# 백트래킹으로 숫자가 잘려 나간다 (예: "99.87도" → "99").
_NUMBER_RE = re.compile(r"(?<![\d.a-zA-Z])(\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?)(?!\d)")


@dataclass
class NumberCheck:
    token: str          # 요약문에 등장한 원 토큰 (예: "28.4")
    normalized: str     # 콤마 제거 등 정규화된 값 (예: "28.4")
    found: bool
    context: str        # 요약문 내 주변 문맥 (검토 편의용)


@dataclass
class VerificationReport:
    total: int = 0
    matched: int = 0
    unmatched: list[NumberCheck] = field(default_factory=list)
    checks: list[NumberCheck] = field(default_factory=list)

    @property
    def pass_ratio(self) -> float:
        return 1.0 if self.total == 0 else self.matched / self.total

    def to_dict(self) -> dict:
        return {
            "total_numbers": self.total,
            "matched": self.matched,
            "pass_ratio": round(self.pass_ratio, 3),
            "unmatched": [
                {"token": c.token, "context": c.context} for c in self.unmatched
            ],
        }


def _normalize(token: str) -> str:
    return token.replace(",", "")


def _extract_numbers(text: str) -> list[tuple[str, str, str]]:
    """(원토큰, 정규화값, 문맥) 목록. 한 자리 정수는 제외."""
    out: list[tuple[str, str, str]] = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group(1)
        norm = _normalize(token)
        # 한 자리 정수는 검증 의미가 없어 제외 (소수점 있으면 포함: "0.5" 등)
        if "." not in norm and "e" not in norm.lower() and len(norm) < 2:
            continue
        start = max(0, m.start() - 30)
        end = min(len(text), m.end() + 30)
        context = text[start:end].replace("\n", " ").strip()
        out.append((token, norm, context))
    return out


def _number_in_text(norm: str, normalized_text: str) -> bool:
    """자릿수 경계를 지켜서 대조한다 ("28.4"가 "128.45" 내부에 매칭되지 않도록)."""
    pattern = r"(?<![\d.])" + re.escape(norm) + r"(?![\d])"
    return re.search(pattern, normalized_text) is not None


def verify_numbers(summary_text: str, source_text: str) -> VerificationReport:
    """요약문의 모든 숫자가 원문에 존재하는지 검사한다."""
    report = VerificationReport()
    normalized_source = source_text.replace(",", "")
    seen: set[str] = set()
    for token, norm, context in _extract_numbers(summary_text):
        if norm in seen:
            continue
        seen.add(norm)
        found = _number_in_text(norm, normalized_source)
        check = NumberCheck(token=token, normalized=norm, found=found, context=context)
        report.checks.append(check)
        report.total += 1
        if found:
            report.matched += 1
        else:
            report.unmatched.append(check)
    return report
