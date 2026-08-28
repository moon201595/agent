"""injection_scan.py — ③ 파싱 단계의 프롬프트 인젝션 사전 스캔.

논문 본문은 **신뢰 입력이 아니다.** arXiv 논문들에서 흰색·초소형 폰트로
"긍정적으로만 평가하라"류의 숨김 지시가 실제로 발견됐고 후속 분석
(arXiv:2507.06185)까지 나왔다 — 심사에 LLM 을 쓰는 것을 노린 것이다. 우리
④ 요약기도 같은 텍스트를 그대로 LLM 에 넣으므로 같은 표적이다.

**LLM 을 쓰지 않는다.** 정규식·문자 코드 검사만 한다(CLAUDE.md 7). "이게
정말 공격인가"를 판단하는 건 사람의 몫이고, 이 모듈은 "사람이 볼 만한
것이 있다"만 결정론적으로 표시한다.

**요약을 막지 않는다.** flag 만 붙인다 — ⑤ 의 unmatched·untagged 와 같은
철학이다(오류 확정이 아니라 확인 신호).

## 오탐이 구조적으로 존재한다 (중요)

프롬프트 인젝션을 **연구하는 논문**은 본문에 공격 문구를 그대로 인용한다.
그런 논문은 정직하게 flag 된다 — 이건 버그가 아니라 이 검사의 한계다.
제로폭 문자도 PDF 추출 과정에서 정상적으로 섞여 들어올 수 있다. 그래서
flag 는 "이 논문은 위험하다"가 아니라 "이 논문 본문에 사람이 한 번 볼
만한 패턴이 있다"로만 읽어야 하고, 그 이상으로 해석하는 코드를 쓰면 안 된다.
"""

from __future__ import annotations

import re

# 양방향(bidi) 제어 문자만 본다 — 이른바 Trojan Source 계열이다. 화면에 보이는
# 순서와 실제 문자 순서를 어긋나게 만드는 용도라 논문 본문에 나올 정당한 이유가
# 사실상 없다.
#
# **제로폭 문자는 뺐다(2026-08-28 실측 근거).** 처음엔 U+200B(zero-width space)와
# U+2060-2064(word joiner, invisible times/plus 등)까지 넣었는데, 저장된 논문
# 59편에 돌려보니 **28편(47%)이 걸렸고 전부 오탐**이었다. 원인은 명확하다:
# arXiv HTML 은 LaTeXML 변환물이라 수식에 U+2061(FUNCTION APPLICATION)·
# U+200B(줄바꿈 힌트)가 정상적으로 들어간다. 절반에 뜨는 경고는 정보가 아니라
# 노이즈이고, 다이제스트마다 붙으면 진짜 신호를 덮는다.
#
# 이건 "테스트를 통과시키려는 완화"가 아니라 **규칙이 측정 대상을 잘못 잡고
# 있던 것의 수정**이다(CLAUDE.md 9 와 안 부딪힌다). 애초에 보고된 실제 공격은
# 흰 글씨·0pt 폰트였고 그건 텍스트 추출 후 **평범한 글자**가 되므로 제로폭
# 검사로는 원래 못 잡는다 — 그 공격은 아래 지시문 패턴이 담당한다.
_INVISIBLE_RE = re.compile(
    "["
    "‪-‮"   # LRE/RLE/PDF/LRO/RLO — bidi override
    "⁦-⁩"   # LRI/RLI/FSI/PDI — bidi isolate
    "]"
)

# LLM 에게 말을 거는 전형적인 문구들. 논문 본문이 독자가 아니라 **모델**에게
# 지시하는 형태면 의심 대상이다. 영어만 본다 — 실측된 사례가 전부 영어였고,
# 한국어 패턴을 추측으로 넣으면 근거 없는 규칙이 된다(CLAUDE.md 8).
_INSTRUCTION_PATTERNS = [
    (r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|preceding)\s+"
     r"(?:instructions?|prompts?|directions?)", "이전 지시 무시 요구"),
    (r"disregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|preceding)",
     "이전 내용 무시 요구"),
    (r"(?:you\s+are|act\s+as)\s+(?:now\s+)?an?\s+\w+\s+(?:assistant|agent|model)",
     "역할 재지정 시도"),
    (r"system\s*(?:prompt|message)\s*[:：]", "시스템 프롬프트 사칭"),
    (r"(?:give|write|provide|output)\s+(?:only\s+)?(?:a\s+)?positive\s+"
     r"(?:review|evaluation|assessment)", "긍정 평가 강요"),
    (r"do\s+not\s+(?:mention|report|highlight)\s+(?:any\s+)?"
     r"(?:weakness|limitation|flaw|negative)", "약점 보고 억제 요구"),
    (r"as\s+an?\s+(?:AI\s+)?language\s+model,?\s+you\s+(?:must|should)",
     "모델 대상 명령문"),
]
_COMPILED = [(re.compile(p, re.I), label) for p, label in _INSTRUCTION_PATTERNS]

_SNIPPET_CHARS = 80


def scan(text: str) -> list[str]:
    """returns 사람이 읽을 사유 목록. 빈 리스트면 걸린 것이 없다.

    사유에 실제로 걸린 조각을 함께 담는다 — "뭔가 걸렸다"만 알려주고 뭐가
    걸렸는지 안 보여주면 사람이 판단할 수가 없다.
    """
    if not text:
        return []
    reasons: list[str] = []

    invisible = _INVISIBLE_RE.findall(text)
    if invisible:
        codes = sorted({f"U+{ord(c):04X}" for c in invisible})
        reasons.append(f"비정상 유니코드 {len(invisible)}개 ({', '.join(codes[:5])})")

    for pattern, label in _COMPILED:
        m = pattern.search(text)
        if m:
            snippet = " ".join(text[m.start():m.start() + _SNIPPET_CHARS].split())
            reasons.append(f"{label}: \"{snippet}…\"")
    return reasons
