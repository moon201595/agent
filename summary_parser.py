"""summary_parser.py — 요약 마크다운을 구조화 JSON으로 바꾼다.

외부 "심층 조사와 발전 설계" 문서가 제안한 구조화 JSON 출력을 구현한다.

마크다운 자유 서술문(값의 조건/비교대상/지표 같은 세부 필드)을 정규식으로
억지로 쪼개려 하지 않는다 — 그건 LLM이 매번 조금씩 다르게 쓰는 자연어라
정규식이 쉽게 깨지고, 애매한 문장을 특정 필드로 분류하는 것 자체가 판단이라
"서버는 판단하지 않는다" 원칙과도 맞지 않는다. 대신 확실히 결정적인 것만
뽑는다:
1. "### 절 제목" 기준 구조(마크다운 자체가 이미 구조적이다) — 절마다 불릿 목록.
2. verify.py 가 이미 결정적으로 추출해 둔 숫자·[S번호] 인용·검증 결과
   (2026-08-06 문장 그라운딩 기능과 그대로 맞물린다 — 여기 나오는 각 주장에
   found/grounded/sentence_id 가 붙어 나온다).
"""

from __future__ import annotations

import re

import verify

_SECTION_RE = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*]|[①②③④⑤⑥⑦⑧⑨])\s*(.+)$")


def parse_sections(markdown: str) -> dict[str, list[str]]:
    """"### 제목" 기준으로 절을 나누고, 각 절 안의 불릿 줄(들여쓰기 무관하게
    "-"/"①"류로 시작하는 줄)을 평평한 리스트로 모은다. "※"로 시작하는
    템플릿 작성 안내문(예: "이 절에는 수치를 쓰지 않는다")은 실제 내용이
    아니라서 제외한다.
    """
    sections: dict[str, list[str]] = {}
    matches = list(_SECTION_RE.finditer(markdown))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end]
        bullets: list[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("※"):
                continue
            bm = _BULLET_RE.match(line)
            if bm:
                bullets.append(bm.group(1).strip())
        sections[heading] = bullets
    return sections


def parse_summary(markdown: str, source_text: str, meta: dict | None = None) -> dict:
    """summarize markdown + 원문 → 구조화 dict.

    meta: DB에서 가져온 논문 메타데이터(arxiv_id, title 등)를 그대로 병합한다
    (있으면). 이 함수 자체는 DB를 모른다 — server.py 가 메타데이터를 채워
    넣는다(다른 모듈들과 같은 경계 원칙, verify.py/selection.py 참고).
    """
    sections = parse_sections(markdown)
    report = verify.verify_numbers(markdown, source_text)
    result: dict = {
        "sections": sections,
        "verification": {
            "total": report.total,
            "matched": report.matched,
            "pass_ratio": round(report.pass_ratio, 3),
            "grounded": report.grounded,
            "claims": [
                {
                    "token": c.token,
                    "found": c.found,
                    "grounded": c.grounded,
                    "sentence_id": c.sentence_id,
                    "context": c.context,
                }
                for c in report.checks
            ],
        },
    }
    if meta:
        result["meta"] = meta
    return result
