"""④⑨ 공용 언어 검사 — 원문 인용을 보존하면서 새로 섞인 한자·가나를 거른다."""
from __future__ import annotations

import re
import sys

_FOREIGN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\u31f0-\u31ff]+")


class NonKoreanOutput(ValueError):
    """한국어가 아닌 문자가 섞인 출력. ValueError 의 하위형이라 기존 "엔진 실패 → 다음 엔진" 경로(동향 서술의 accept 등)를 그대로 탄다.
    따로 둔 이유는 잡는 쪽이 **이 사유만** 가려 잡게 하려는 것이다 — `except ValueError` 로 받으면 다른 결함까지 언어 문제로 분류된다."""


def foreign_script(text: str, allowed_source: str = "") -> list[str]:
    """원문에 그대로 있는 용어는 번역 실패로 오인하지 않기 위해 제외한다."""
    return [chunk for chunk in _FOREIGN.findall(text) if chunk not in allowed_source]


def require_korean(text: str, allowed_source: str, label: str) -> None:
    """거부 사유는 짧게 남기고 기존 엔진 실패 경로에 맡긴다."""
    chunks = foreign_script(text, allowed_source)
    if chunks:
        log_violation(label, chunks)
        raise NonKoreanOutput("non_korean_output")


def log_violation(label: str, chunks: list[str]) -> None:
    """출력 전체가 로그에 유출되지 않도록 예시는 세 덩어리·각 다섯 자로 제한한다."""
    sample = ", ".join(chunk[:5] for chunk in chunks[:3])
    print(f"  [언어] {label} 출력에 한국어 아닌 문자 ({sample}) — 결과 버림", file=sys.stderr)
