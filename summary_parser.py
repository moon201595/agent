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

# 제목 줄. **깊이를 문서가 정한다** — 아래 parse_sections 참고.
_HEADING_RE = re.compile(r"^\s{0,3}(#{2,6})\s+(.+?)\s*$")
_HEADING_DEPTH_RE = re.compile(r"^\s{0,3}(#{2,6})\s+\S", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·–—]|[0-9]+[.)]|[①②③④⑤⑥⑦⑧⑨])\s*(.+)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# 절 이름 별칭. 프롬프트가 시킨 이름을 모델이 조금씩 바꿔 쓴다.
_SECTION_ALIASES = {
    "핵심 결과": "결과", "주요 결과": "결과", "실험 결과": "결과",
    "한계": "논문의 한계점", "한계점": "논문의 한계점",
    "개요": "연구 개요", "방법": "방법 상세", "실험 셋업": "실험 설정",
}


def _canonical(title: str) -> str:
    name = _BOLD_RE.sub(r"\1", title).strip().strip("*_`").strip()
    name = re.sub(r"^[0-9]+[.)]\s*", "", name).strip()
    return _SECTION_ALIASES.get(name, name)


def parse_sections(markdown: str) -> dict[str, list[str]]:
    """제목 기준으로 절을 나누고 각 절의 내용을 평평한 리스트로 모은다.
    "※"로 시작하는 템플릿 작성 안내문은 실제 내용이 아니라서 제외한다.

    **2026-09-08 정정 — digest 와 같은 규칙을 쓴다.** 그전에는 `^### ` 하나만
    보고 제목을 원문 그대로 담았다. 그래서 `### **결과**` 는 키가 `**결과**` 라
    조회에 안 걸리고 `## 결과` 는 절 자체를 못 봤다. 같은 날 digest 만 고쳐서
    **두 경로가 갈렸다** — 메일에는 보이는 절이 동향 입력(`trend_report.
    result_excerpts`)에서는 빠지는 상태였다.

    절의 깊이는 **문서에 나오는 가장 얕은 제목**으로 정한다. `###` 안의
    `####` 하위 제목까지 절로 올리면 부모 절이 비어 버린다(실측:
    `pdf-f9518e4d9d`).

    불릿이 하나도 없으면 **산문을 그대로 항목으로 쓴다.** 형식이 다르다고
    내용을 버리지 않는다 — 그 논문의 결과가 통째로 사라지던 실제 사례가 있다.
    """
    levels = [len(m.group(1)) for m in _HEADING_DEPTH_RE.finditer(markdown)]
    if not levels:
        return {}
    top = min(levels)

    sections: dict[str, list[str]] = {}
    current, body = None, []

    def flush():
        if current is None or current in sections:
            return
        bullets = [_BOLD_RE.sub(r"\1", m.group(1)).strip()
                   for m in (_BULLET_RE.match(l) for l in body) if m]
        if not bullets:
            bullets = [_BOLD_RE.sub(r"\1", l.strip()) for l in body
                       if l.strip() and not _HEADING_RE.match(l)]
        sections[current] = bullets

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("※"):
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == top:
            flush()
            current, body = _canonical(m.group(2)), []
        elif current is not None:
            body.append(line)
    flush()
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
