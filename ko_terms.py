"""⑨ 배달 — 메일 한국어의 기술 용어 표기를 한 곳에서 고정한다(용어집 · 프롬프트 규칙 · 발송 직전 교정). 결정적, 네트워크·LLM 미사용.

2026-11-18 사용자: "에이전트의 메모리를 '기억'으로 해석해서 쓰네 — 메모리는 메모리라고 써야 하는데, 그냥 메모리로 쓰라고 하면 또 이상한
곳에서 이상한 해석이 나올 텐데". 실측(10월 이후 연구 흐름 서술 146건 + 그날 메일 4통): memory 를 '기억' 246회·'메모리' 60회로 섞어 썼고
context(맥락 16·컨텍스트 7)·world model(세계 모델 8·월드 모델 0)·fine-tuning·prompt 도 날마다 표기가 갈렸다. 로봇 메일은 grasp 를
'잡기'(파지 0회), gripper 를 '집게'(그리퍼 0회)로 썼다. 한국어를 만드는 곳이 요약·쉽게 말하면·제목·인용문·연구 가치·연구 흐름·참고 자료·
외부 정찰·주간 관리로 흩어져 있어 프롬프트 한 곳을 고치면 다른 곳에서 다시 터진다 — 그래서 표기 목록을 **여기 하나**에 두고 세 겹으로 쓴다.

1. `PROMPT_RULE` — 한국어를 만드는 모든 프롬프트가 같은 규칙 줄을 싣는다(목록에 없는 용어도 "일상어로 풀어 옮기지 않는다" 원칙으로 막는다).
2. `fix()` — 모델이 어겨도 발송 함수(`email_delivery.send_digest_email`, 새 메일·저장 메일 재발송이 모두 지난다)와 서술 저장
   (`narrative_store.save`, 다음 날 모델에 다시 들어간다)에서 틀린 표기를 정한 표기로 바꾼다. 받침이 달라지면 조사도 맞춘다(기억을 → 메모리를).
   동사·합성어·식별자(기억하다·기억력·맥락의존적·집게나물·memory_기억)와 태그·URL 은 건드리지 않는다.
3. 새 함정은 사용자가 본 것만 한 줄씩 더한다(term_hygiene 와 같은 원칙 — 추측으로 늘리지 않는다).

일상어로도 쓰는 말(지시문·집게·미세 조정)은 앞뒤 40자 안에 기술 문맥 낱말이 있을 때만 바꾼다(Codex 검토 2026-11-18: "안전 지시문을 읽고
주방 집게로"·"그림 위치를 미세 조정했다" 를 바꿨다). '기반 모델'은 "트랜스포머 기반 모델"(X-based)과 글자로 못 가른다 — 프롬프트로만 고정한다.
'잡기'는 "기준을 잡기 어렵다"·"균형 잡기"처럼 grasp 가 아닌 뜻이 있어(실측·검토) 물체 잡기·동작 나열·잡기 위치처럼 확실한 모양에서만 바꾼다.
조작(manipulation)·시연(demonstration)·정책(policy)·궤적(trajectory)은 국내 로봇 분야의 표준 번역이라 고정하지 않는다.
알려진 한계: 조사가 태그 너머에 있으면(기억<strong>이</strong>) 조사를 못 맞춘다 — 지금 메일 생성은 문장 중간에 태그를 넣지 않는다(미실측 경로).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ML = r"모델|학습|가중치|파라미터|데이터|LoRA|LLM|VLA|VLM|사전|에이전트|정책|토큰|프롬프트|추론|파인튜닝|fine-?tun"
_ROBOT = r"로봇|그리퍼|파지|시연|조작|휴대용|팔|손|말단|UMI|원격|물체|작업"


@dataclass(frozen=True)
class Term:
    en: str                        # 프롬프트에 보일 영어 용어
    ko: str                        # 정한 한국어 표기
    wrong: tuple[str, ...] = ()    # 발송 직전에 바꿀 틀린 표기 — 비우면 프롬프트로만 고정한다
    pattern: str = ""              # 틀린 표기를 찾는 정규식(비우면 낱말 경계) — 확실한 모양에서만 바꿀 때 쓴다
    verb: bool = False             # '~하다' 꼴도 바꾼다(미세 조정하여 → 파인튜닝하여). 기억하다처럼 일상 동사가 되는 말은 False
    avoid: str = ""                # 자동 교정은 안 하지만 프롬프트에 '아님' 으로 적을 표기
    near: str = ""                 # 이 정규식이 앞뒤 40자 안에 있을 때만 바꾼다 — 일상어로도 쓰는 말


TERMS: tuple[Term, ...] = (
    Term("memory", "메모리", ("기억",)),
    Term("context", "컨텍스트", ("맥락",)),
    Term("world model", "월드 모델", ("세계 모델", "세계모델")),
    Term("foundation model", "파운데이션 모델", avoid="기반 모델"),
    Term("prompt", "프롬프트", ("지시문",), near=r"모델|LLM|에이전트|입력|도구|언어|생성|추론|토큰"),
    Term("fine-tuning", "파인튜닝", ("미세 조정", "미세조정"), verb=True, near=_ML),
    Term("grasp / grasping", "파지", ("잡기",),
         r"(?:(?<=물체 )|(?<=·))잡기|(?<![가-힣A-Za-z0-9_])(?<!균형 )잡기(?=·| 위치| 성공| 실패)"),
    Term("gripper", "그리퍼", ("집게",), near=_ROBOT),
)

PROMPT_RULE = (
    "- 기술 용어 표기: AI·로봇 분야 용어는 일상어로 풀어 옮기지 말고 국내 연구자가 실제로 쓰는 표기(대개 원어 음차)로 쓴다. "
    "다음은 반드시 이 표기로 쓴다 — "
    + ", ".join(f"{t.en} → {t.ko}" + (f"('{t.avoid or t.wrong[0]}' 아님)" if t.avoid or t.wrong else "") for t in TERMS)
    + ". 풀이가 필요하면 표기 뒤 괄호에 붙인다.\n"
)

# 조사·서술격 — (받침 있는 말 뒤, 받침 없는 말 뒤). '으로/로' 는 ㄹ 받침 뒤에서도 '로' 다.
_PAIRS: tuple[tuple[str, str], ...] = (
    ("이라는", "라는"), ("이라고", "라고"), ("이라서", "라서"), ("이에요", "예요"), ("이지만", "지만"), ("이므로", "므로"),
    ("이어서", "여서"), ("이어야", "여야"), ("으로서", "로서"), ("으로써", "로써"),
    ("으로", "로"), ("이나", "나"), ("이랑", "랑"), ("이라", "라"), ("이란", "란"), ("이다", "다"), ("이며", "며"),
    ("이고", "고"), ("이었", "였"), ("이야", "야"), ("을", "를"), ("은", "는"), ("과", "와"), ("이", "가"),
)
# 받침과 상관없는 조사·서술격(메모리입니다·메모리인 경우·메모리일 때·메모리임을) — 뒤에 다른 조사가 더 붙어도 된다(에서도·까지만).
_NEUTRAL: tuple[str, ...] = ("입니다", "에서", "에게", "까지", "부터", "보다", "처럼", "조차", "마저", "인가", "의", "에", "도", "만",
                            "뿐", "들", "인", "임", "일")
# 조사 뒤에 더 붙어도 되는 것 — 이것 말고 한글이 이어지면 합성어로 보고 안 바꾼다(집게나물·맥락의존적).
_TAILS: tuple[str, ...] = tuple(sorted({*_NEUTRAL, "다", "고", "지만", "으며", "며", "는데", "던", "을", "를", "는", "은", "가", "이",
                                        "요", "니다", "습니다", "까", "서", "도", "만", "로", "으로", "와", "과"}, key=len, reverse=True))


def _final(word: str) -> str:
    """끝 글자의 받침 — 'V'(없음)·'L'(ㄹ)·'C'(그 밖). 한글이 아니면 'V' 로 본다."""
    ch = word.rstrip()[-1:]
    if not ch or not ("가" <= ch <= "힣"):
        return "V"
    jong = (ord(ch) - 0xAC00) % 28
    return "V" if jong == 0 else "L" if jong == 8 else "C"


def _form(pair: tuple[str, str], kind: str) -> str:
    """조사 쌍에서 받침 종류(V·L·C) 말 뒤에 오는 꼴. ㄹ 받침 뒤의 '으로' 계열만 '로' 로 간다."""
    cons, vowel = pair
    if kind == "V":
        return vowel
    if kind == "L" and cons.startswith("으로"):
        return "로" + cons[2:]
    return cons


def _tail_ok(tail: str) -> bool:
    """조사 뒤 한글이 조사·어미 조각으로만 이뤄졌는가."""
    while tail:
        for t in _TAILS:
            if tail.startswith(t):
                tail = tail[len(t):]
                break
        else:
            return False
    return True


def _particle(rest: str, kind: str) -> tuple[str, str] | None:
    """`rest`(틀린 표기 바로 뒤 한글)의 앞머리 조사와 그 뒤. 받침에 맞는 조사·중립 조사가 아니거나 뒤가 조사·어미 조각이 아니면
    None — 동사·합성어라는 뜻이다. 주격 '이/가' 는 뒤에 한글이 더 붙지 않을 때만 조사로 본다('기억이란'·'기억이다' 는 긴 꼴이 먼저 잡는다)."""
    for p in sorted({_form(pair, kind) for pair in _PAIRS} | set(_NEUTRAL), key=len, reverse=True):
        if rest.startswith(p) and (p not in ("이", "가") or rest == p) and _tail_ok(rest[len(p):]):
            return p, rest[len(p):]
    return None


def _convert(p: str, src: str, dst: str) -> str:
    """조사 `p` 를 받침 `src` 말 뒤 꼴에서 받침 `dst` 말 뒤 꼴로 바꾼다."""
    if p not in _NEUTRAL:
        for pair in _PAIRS:
            if p == _form(pair, src):
                return _form(pair, dst)
    return p


def _fix_term(text: str, term: Term) -> str:
    for wrong in term.wrong:
        head = f"(?:{term.pattern})" if term.pattern else rf"(?<![가-힣A-Za-z0-9_]){re.escape(wrong)}"
        pat = re.compile(head + r"([가-힣]*)(?![A-Za-z0-9_])")
        src, dst = _final(wrong), _final(term.ko)
        near = re.compile(term.near, re.I) if term.near else None

        def repl(m: re.Match, src: str = src, dst: str = dst) -> str:
            if near and not near.search(text[max(0, m.start() - 40):m.end() + 40].replace(m.group(0), " ")):
                return m.group(0)
            rest = m.group(1)
            if not rest:
                return term.ko
            if term.verb and rest[0] in "하해한할했함된되됨":
                return term.ko + rest
            found = _particle(rest, src)
            if found is None:
                return m.group(0)
            p, tail = found
            return term.ko + _convert(p, src, dst) + tail

        text = pat.sub(repl, text)
    return text


# HTML 태그(따옴표 속 '>' 포함)와 URL(대소문자 스킴·www.·//) 은 건드리지 않는다 — 주소 속 한글을 바꾸면 링크가 깨진다.
_SKIP_RE = re.compile(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>|(?:[a-z][a-z0-9+.-]*:)?//[^\s<>"']+|\bwww\.[^\s<>"']+""", re.I)


def _fix_plain(text: str) -> str:
    for term in TERMS:
        if term.wrong:
            text = _fix_term(text, term)
    return text


def fix(text: str) -> str:
    """틀린 표기를 정한 표기로 바꾼다(태그·URL 밖의 글만). 비어 있거나 문자열이 아니면 그대로 돌려준다."""
    if not isinstance(text, str) or not text:
        return text
    out, pos = [], 0
    for m in _SKIP_RE.finditer(text):
        out.append(_fix_plain(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_fix_plain(text[pos:]))
    return "".join(out)


def fix_obj(obj):
    """dict·list 안의 문자열을 모두 교정한다(키는 그대로). 서술 저장의 구조 자료(audit.story)가 다음 날 모델에 다시 들어가서다."""
    if isinstance(obj, str):
        return fix(obj)
    if isinstance(obj, dict):
        return {k: fix_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_obj(v) for v in obj]
    return obj
