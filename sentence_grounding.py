"""sentence_grounding.py — ④⑤ 공유: 문장 단위 근거 인용(Grounding).

⑤ 검증기를 "숫자가 원문 어딘가에 있다"에서 "숫자가 요약이 인용한 그 문장에
있다"로 격상하기 위한 공유 모듈이다(2026-08-06, docs/PROGRESS.md §8-9).

기존 검증(verify.py)은 숫자가 원문 전체 어딘가에 있으면 통과였다 — 다른 절,
심지어 다른 논문 얘기여도 같은 숫자값이 우연히 있으면 통과했다. 이걸 막으려면
"어느 문장에서 나온 값인지"까지 확인해야 한다.

동작 방식:
1. ④ summarize_engine.py 가 원문을 문장 단위로 잘라 번호를 매긴다
   ([S0001] ~). LLM 은 수치를 인용할 때 그 문장 번호를 "출처위치" 뒤에
   그대로 적는다(예: "— 본문 4.1절 [S0142] ★★★").
2. ⑤ verify.py 가 **같은 함수로 같은 원문을** 다시 나눠 문장 목록을
   재구성하고, 인용된 번호의 문장(과 인접 1문장) 안에 그 숫자가 실제로
   있는지 확인한다.

두 쪽이 반드시 같은 함수(segment_sentences)로 같은 원문을 나눠야 번호가
어긋나지 않는다 — 이 함수는 결정적이라 같은 입력엔 항상 같은 출력을 낸다.
[S번호] 태그가 없는 요약(이 기능 도입 전에 생성된 것)은 verify.py 쪽에서
기존의 "원문 전체 대조" 방식으로 그대로 폴백한다 — 하위 호환.
"""

from __future__ import annotations

import re

# 논문 본문에 흔한 약어 — 이 단어 뒤의 "."은 문장 경계가 아니다.
# 특히 "Fig. 3", "Eq. 12", "Table 4" 처럼 숫자가 바로 뒤따르는 경우가 많아
# 이걸 문장 경계로 오인하면 인용 번호가 실제 데이터 문장이 아니라 약어
# 조각을 가리키게 된다.
_ABBREVIATIONS = {
    "fig", "figs", "eq", "eqs", "et al", "al", "e.g", "i.e", "vs", "cf",
    "no", "nos", "approx", "sec", "secs", "ref", "refs", "dr", "mr", "mrs",
    "ms", "prof", "jr", "sr", "etc", "vol", "pp", "p", "resp", "cap", "app",
    "def", "thm", "lem", "cor", "sect", "eqn", "ch", "chap",
}

# 구두점(.!?) 뒤에 공백 또는 문자열 끝이 와야 경계 후보다 — 소수점(92.4%)은
# 뒤에 공백이 없으므로 애초에 이 정규식에 걸리지 않는다.
_BOUNDARY_RE = re.compile(r"([.!?]+)(\s+|$)")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+)\.?$")

# 문장 하나가 비정상적으로 길어지면(추출 품질 문제로 마침표가 아예 안 잡히는
# 경우 등) 인용 정밀도가 떨어진다 — 공백 기준으로 강제로 끊는다.
_MAX_SENTENCE_CHARS = 600
_MIN_SENTENCE_CHARS = 8  # 이보다 짧은 조각은 약어 오탐일 가능성이 높아 다음 문장에 합친다


def _hard_split(sentence: str) -> list[str]:
    if len(sentence) <= _MAX_SENTENCE_CHARS:
        return [sentence]
    parts = []
    remainder = sentence
    while len(remainder) > _MAX_SENTENCE_CHARS:
        cut = remainder.rfind(" ", 0, _MAX_SENTENCE_CHARS)
        if cut <= 0:
            cut = _MAX_SENTENCE_CHARS
        parts.append(remainder[:cut].strip())
        remainder = remainder[cut:].strip()
    if remainder:
        parts.append(remainder)
    return parts


def segment_sentences(text: str) -> list[str]:
    """원문을 문장 단위로 나눈다. 결정적 함수 — 같은 입력엔 항상 같은 출력.

    완벽한 언어학적 문장 경계가 목적이 아니다 — "인용 하나가 가리키는 범위가
    좁고 안정적"이면 충분하다. PDF 추출 잡음(줄바꿈이 문장 중간에 낀 것 등)에
    대비해 먼저 공백을 전부 정규화한다.
    """
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    raw_sentences: list[str] = []
    start = 0
    for m in _BOUNDARY_RE.finditer(normalized):
        end = m.end(1)  # 구두점까지 포함한 위치
        candidate = normalized[start:end]
        word_match = _WORD_BEFORE_RE.search(candidate[:-len(m.group(1))])
        prev_word = word_match.group(1).lower() if word_match else ""
        is_initial = len(prev_word) == 1  # "J. Smith" 의 "J." 같은 이니셜
        if prev_word in _ABBREVIATIONS or is_initial:
            continue  # 문장 경계 아님 — 다음 후보까지 이어붙인다
        sentence = candidate.strip()
        if sentence:
            raw_sentences.append(sentence)
        start = m.end()
    tail = normalized[start:].strip()
    if tail:
        raw_sentences.append(tail)

    # 너무 짧은 조각(약어 규칙이 못 거른 것)은 다음 문장에 합친다.
    merged: list[str] = []
    for s in raw_sentences:
        if merged and len(merged[-1]) < _MIN_SENTENCE_CHARS:
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)

    # 너무 긴 조각은 강제로 끊는다.
    out: list[str] = []
    for s in merged:
        out.extend(_hard_split(s))
    return out


def tag_sentences(sentences: list[str], start_id: int = 1) -> list[str]:
    """문장 리스트를 "[S0001] ..." 형태로 번호를 매긴다. 1-based, 4자리 0패딩
    (99,999문장까지 정렬 안 깨짐 — 지금까지 실측된 최대 논문도 수천 문장 수준)."""
    return [f"[S{i:04d}] {s}" for i, s in enumerate(sentences, start=start_id)]


_TAG_RE = re.compile(r"\[S(\d+)\]")


def parse_tag(token: str) -> int | None:
    """"[S0142]" → 142. 형식이 아니면 None."""
    m = _TAG_RE.fullmatch(token.strip())
    return int(m.group(1)) if m else None


def pack_into_chunks(tagged_sentences: list[str], chunk_size: int) -> list[str]:
    """태그 붙은 문장들을 chunk_size자 근처까지 그리디하게 묶는다. 문장 하나가
    chunk_size 보다 길면(드묾) 그 한 문장만으로 청크를 이룬다 — 문장을 또
    쪼개 청크 경계가 문장 중간에 걸리는 일은 없다."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for s in tagged_sentences:
        s_len = len(s) + 1
        if buf and buf_len + s_len > chunk_size:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(s)
        buf_len += s_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def build_tagged_chunks(paper_text: str, chunk_size: int, max_chunks: int) -> tuple[list[str], list[str]]:
    """returns (chunks, sentences) — chunks 는 LLM 에 보낼 태그 붙은 청크들
    (max_chunks 상한 적용됨), sentences 는 태그 없는 원본 문장 리스트 전체
    (⑤ 검증기가 인용 번호를 원본 문장으로 되찾을 때 이걸 그대로 다시
    계산해서 쓴다 — sentence_lookup 참고)."""
    sentences = segment_sentences(paper_text)
    tagged = tag_sentences(sentences)
    chunks = pack_into_chunks(tagged, chunk_size)
    return chunks[:max_chunks], sentences


def sentence_lookup(source_text: str, sentence_id: int, window: int = 1) -> str | None:
    """source_text 를 다시 세그멘테이션해서 sentence_id(1-based) 번 문장과
    그 앞뒤 window개 문장을 이어붙여 반환한다. 범위 밖이면 None.

    앞뒤 문장까지 포함하는 이유: segment_sentences 의 경계 판정이 완벽하지
    않아(예: 흔치 않은 약어) 실제 LLM이 본 경계와 1문장 정도 어긋날 수 있다.
    그 오차를 흡수하면서도 "원문 아무 데나"보다는 훨씬 좁은 범위로 유지한다.
    """
    sentences = segment_sentences(source_text)
    idx = sentence_id - 1
    if idx < 0 or idx >= len(sentences):
        return None
    lo = max(0, idx - window)
    hi = min(len(sentences), idx + window + 1)
    return " ".join(sentences[lo:hi])
