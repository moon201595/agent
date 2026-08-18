"""요약문 수치 검증기.

요약문에서 숫자 토큰을 뽑아 원문 텍스트에 실제로 존재하는지 문자열 대조한다.
LLM을 쓰지 않는 결정적(deterministic) 검증이며, 하네스의 "미확인 수치 인용 금지"
규칙을 시스템 수준에서 강제하는 장치다.

2026-08-06(§8-9, docs/PROGRESS.md): "숫자가 원문 어딘가에 있다" 검증에서
"숫자가 요약이 인용한 그 문장에 있다" 검증으로 격상했다 — 원문 다른 절, 심지어
다른 논문 얘기여도 같은 숫자값이 우연히 있으면 통과하던 구멍을 막는다. 요약문에
"[S번호]" 태그(summarize_engine.py 가 sentence_grounding 으로 원문에 미리 붙여
LLM 에 전달한 문장 번호)가 있으면 그 문장(과 인접 1문장, 세그멘테이션 오차 흡수용)
안에서만 숫자를 찾는다 — grounded=True. 태그가 없는 요약(이 기능 도입 전에 생성된
것)은 기존 "원문 전체 대조" 방식으로 폴백한다 — grounded=False, 하위 호환.

한계(문서화된 설계 결정):
- 한 자리 정수(0~9)는 어떤 텍스트에도 존재해 검증 의미가 없어 제외한다.
- 단위 환산(예: 원문 0.5m ↔ 요약 50cm)은 탐지하지 못한다.
- PDF 텍스트 추출 품질에 따라 원문에 있는 숫자가 누락 판정될 수 있다.
  → 불일치는 "오류 확정"이 아니라 "사람이 원문을 확인할 것" 신호로 해석한다.
- 문장 그라운딩(grounded=True)도 sentence_grounding 의 세그멘테이션이 완벽하지
  않아(§6 참고 수준의 한계) 인접 1문장까지 봐주는 여유를 둔다 — 그래도 "원문
  전체"보다는 훨씬 좁으므로 검증 의미는 크게 강화된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sentence_grounding

# 1,234 / 28.4 / 3e-4 / 0.78 형태를 잡는다. % 기호는 숫자에서 분리해 취급.
# 경계 규칙: 앞뒤로 숫자·점·라틴 문자가 붙으면 식별자(v2, GPT4, 1706.03762)로 보고
# 시작점을 제한하되, 한글 조사("28.4를", "99.87도")는 경계로 허용해야 한다.
# 그래서 뒤쪽은 '숫자만 아니면' 경계로 인정한다 — \w 금지로 두면 한국어 문장에서
# 백트래킹으로 숫자가 잘려 나간다 (예: "99.87도" → "99").
_NUMBER_RE = re.compile(r"(?<![\d.a-zA-Z])(\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?)(?!\d)")

# 2026-08-06 실측: 프롬프트 v2 R2 규칙("값 ... — 출처위치")이 "본문 6.1절",
# "Table 3", "Figure 4(a)" 같은 출처 표기를 강제하는데, 이 표기 안의 숫자를
# 데이터 수치로 착각해 검증기가 오탐을 냈다(VegaEdge 등 실측 확인 — 불일치로
# 잡힌 게 전부 "6.1", "6.2" 같은 절 번호였다). "6.1절"은 소수처럼 생겨서
# 한 자리 정수 제외 규칙(len<2)을 안 타고 그대로 통과했었다.
# 숫자 바로 앞/뒤에 이런 출처 마커가 붙어 있으면 데이터가 아니라 위치
# 표기로 보고 검증 대상에서 아예 뺀다(불일치로도 안 세고 총계에도 안 넣음).
_LOCATION_MARKER = r"(절|장|Section|Sec\.|Table|표|Figure|Fig\.|그림|Appendix|부록|Eq\.|식|Chapter)"
_LOCATION_SUFFIX_RE = re.compile(r"^\s*" + _LOCATION_MARKER, re.I)
_LOCATION_PREFIX_RE = re.compile(_LOCATION_MARKER + r"\s*$", re.I)
_LOCATION_CONTEXT_CHARS = 12


@dataclass
class NumberCheck:
    token: str          # 요약문에 등장한 원 토큰 (예: "28.4")
    normalized: str     # 콤마 제거 등 정규화된 값 (예: "28.4")
    found: bool
    context: str        # 요약문 내 주변 문맥 (검토 편의용)
    grounded: bool = False       # True면 [S번호] 인용으로 문장 단위까지 확인함
    sentence_id: int | None = None  # 인용된 [S번호] (없으면 None — 구형 요약)
    cited_text: str | None = None   # grounded=True 일 때 실제로 조회한 원문 문장(들)


@dataclass
class VerificationReport:
    total: int = 0
    matched: int = 0
    grounded: int = 0  # 이번 검증에서 문장 단위까지 확인한 개수 (참고용)
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
            "grounded": self.grounded,
            "unmatched": [
                {
                    "token": c.token,
                    "context": c.context,
                    "grounded": c.grounded,
                    **({"cited_text": c.cited_text} if c.cited_text else {}),
                }
                for c in self.unmatched
            ],
        }


def _normalize(token: str) -> str:
    return token.replace(",", "")


def _is_location_reference(text: str, start: int, end: int) -> bool:
    """이 숫자가 '본문 6.1절'·'Table 3'·'Figure 4(a)' 같은 출처 위치
    표기의 일부인지 본다 — 데이터 값이 아니라 문서 좌표라 검증 대상이 아니다."""
    after = text[end:end + _LOCATION_CONTEXT_CHARS]
    before = text[max(0, start - _LOCATION_CONTEXT_CHARS):start]
    return bool(_LOCATION_SUFFIX_RE.match(after) or _LOCATION_PREFIX_RE.search(before))


_TAG_SEARCH_RE = re.compile(r"\[S(\d+)\]")
_TAG_SEARCH_MAX_CHARS = 200  # 숫자 뒤 이 범위(또는 줄 끝·다음 숫자) 안에서만 태그를 찾는다
_STAR_AFTER_TAG_RE = re.compile(r"^[\s,]*[★☆]")
_STAR_LOOKAHEAD_CHARS = 15  # 태그 바로 뒤 이 범위 안에 별점이 있어야 진짜 R2/R3 인용으로 본다


def _cited_sentence_id(text: str, num_end: int, next_num_start: int | None) -> int | None:
    """숫자 바로 뒤부터, 다음 숫자 전(또는 그 줄 끝, 또는 _TAG_SEARCH_MAX_CHARS)까지에서
    "[S번호]" 태그를 찾는다 — "값(...) — 출처위치 [S번호] ★등급" 형식을 전제로,
    숫자 뒤에 오는 첫 태그가 그 숫자의 출처라고 본다.

    2026-08-06 실측(Feelbert): 프롬프트가 "문장마다 태그가 있다"고 알려주자
    모델이 "결과" 절뿐 아니라 "연관 연구"의 참고문헌 인용(예: "Kajita &
    Espiau (2008) [S021]")에도 태그를 붙였다 — R2/R3가 요구한 형식이 아닌데
    가까이 있는 숫자(연도 2008)가 그 태그에 엮여 잘못 그라운딩됐다. R2/R3
    계약은 "[S번호] 바로 뒤에 ★등급"이 항상 붙는 것이므로, 태그 뒤에 별점이
    없으면 진짜 데이터 인용이 아니라고 보고 그라운딩하지 않는다(→ 구형
    폴백으로 넘어간다 — 아예 검증 안 하는 게 아니라 원문 전체 대조로 완화).
    """
    line_end = text.find("\n", num_end)
    if line_end == -1:
        line_end = len(text)
    search_end = min(line_end, num_end + _TAG_SEARCH_MAX_CHARS)
    if next_num_start is not None:
        search_end = min(search_end, next_num_start)
    m = _TAG_SEARCH_RE.search(text, num_end, search_end)
    if not m:
        return None
    after_tag = text[m.end():m.end() + _STAR_LOOKAHEAD_CHARS]
    if not _STAR_AFTER_TAG_RE.match(after_tag):
        return None
    return int(m.group(1))


def _sentence_span(text: str, start: int, end: int) -> str:
    """숫자가 속한 문장 전체를 반환한다(검토 화면용 문맥).

    예전엔 숫자 앞뒤 고정 30자였는데, 그 폭이 하필 단어나 "[S0241]" 같은
    인용 태그 중간에서 끊기는 경우가 실제로 있었다(2026-08-18, review_app.py
    화면 보고 지적). 먼저 줄 경계로 좁힌다 — "라벨 : 내용" 한 줄짜리 불릿은
    그 자체가 이미 문장 하나라 이걸로 충분하다. "결과" 절처럼 줄바꿈 없는
    프로즈 한 문단에 문장·숫자가 여러 개 같이 있으면(실측: LF-YOLO 요약의
    결과 절이 정확히 이 형태), ④가 원문에 [S번호]를 매길 때 쓰는 것과 같은
    문장 분리기(sentence_grounding.segment_sentences)로 한 번 더 좁혀 숫자가
    실제로 속한 문장만 남긴다."""
    line_start = text.rfind("\n", 0, start)
    line_start = 0 if line_start == -1 else line_start + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    line = text[line_start:line_end].strip()

    sentences = sentence_grounding.segment_sentences(line)
    if len(sentences) <= 1:
        return line

    local_start = start - line_start
    cursor = 0
    for s in sentences:
        idx = line.find(s, cursor)
        if idx == -1:
            idx = cursor
        s_end = idx + len(s)
        if idx <= local_start < s_end + 5:  # 문장 경계 인접 오차 흡수
            return s
        cursor = s_end
    return line  # 못 찾으면 줄 전체로 폴백 — 항상 뭔가는 보여준다


def _extract_numbers(text: str) -> list[tuple[str, str, str, int | None]]:
    """(원토큰, 정규화값, 문맥, 인용된 [S번호] 또는 None) 목록.
    한 자리 정수·출처 위치 표기는 제외."""
    candidates = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group(1)
        norm = _normalize(token)
        # 한 자리 정수는 검증 의미가 없어 제외 (소수점 있으면 포함: "0.5" 등)
        if "." not in norm and "e" not in norm.lower() and len(norm) < 2:
            continue
        if _is_location_reference(text, m.start(), m.end()):
            continue
        candidates.append(m)

    out: list[tuple[str, str, str, int | None]] = []
    for i, m in enumerate(candidates):
        token = m.group(1)
        norm = _normalize(token)
        context = _sentence_span(text, m.start(), m.end())
        next_start = candidates[i + 1].start() if i + 1 < len(candidates) else None
        sentence_id = _cited_sentence_id(text, m.end(), next_start)
        out.append((token, norm, context, sentence_id))
    return out


def _number_in_text(norm: str, normalized_text: str) -> bool:
    """자릿수 경계를 지켜서 대조한다 ("28.4"가 "128.45" 내부에 매칭되지 않도록)."""
    pattern = r"(?<![\d.])" + re.escape(norm) + r"(?![\d])"
    return re.search(pattern, normalized_text) is not None


# 그라운딩 실패 시 원문에서 얼마나 넓게 볼지 — 세그멘테이션 경계가 실제 문장
# 경계와 1개쯤 어긋나도(드문 약어 등) 견디되, "원문 전체"보다는 훨씬 좁게 유지한다.
_GROUNDING_WINDOW = 1


def _verify_grounded(norm: str, source_text: str, sentence_id: int) -> tuple[bool, str | None]:
    """인용된 [S번호] 문장(과 인접 1문장) 안에서만 숫자를 찾는다.
    returns (found, cited_text) — cited_text 는 실제로 조회한 원문(디버깅·검토용),
    번호가 범위 밖이면(지어낸 번호일 가능성) None 과 함께 실패로 처리한다."""
    window_text = sentence_grounding.sentence_lookup(source_text, sentence_id, window=_GROUNDING_WINDOW)
    if window_text is None:
        return False, None
    found = _number_in_text(norm, window_text.replace(",", ""))
    return found, window_text


def verify_numbers(summary_text: str, source_text: str) -> VerificationReport:
    """요약문의 모든 숫자가 원문에 존재하는지 검사한다.

    [S번호] 태그가 있으면(2026-08-06 도입) 그 문장 안에서만 확인한다(grounded=True) —
    "원문 어딘가에 있다"가 아니라 "인용한 그 문장에 있다"까지 본다. 태그가 없으면
    (이 기능 도입 전 요약) 기존처럼 원문 전체에서 찾는다(grounded=False) — 하위 호환.
    """
    report = VerificationReport()
    normalized_source = source_text.replace(",", "")
    seen: set[tuple[str, int | None]] = set()
    for token, norm, context, sentence_id in _extract_numbers(summary_text):
        key = (norm, sentence_id)
        if key in seen:
            continue
        seen.add(key)
        if sentence_id is not None:
            found, cited_text = _verify_grounded(norm, source_text, sentence_id)
            grounded = True
        else:
            found = _number_in_text(norm, normalized_source)
            cited_text, grounded = None, False
        check = NumberCheck(
            token=token, normalized=norm, found=found, context=context,
            grounded=grounded, sentence_id=sentence_id, cited_text=cited_text,
        )
        report.checks.append(check)
        report.total += 1
        if grounded:
            report.grounded += 1
        if found:
            report.matched += 1
        else:
            report.unmatched.append(check)
    return report
