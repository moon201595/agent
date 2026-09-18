"""② 주간 루프 · ⑨ 동향 — 용어 후보의 어휘 위생(lexical hygiene). 결정적, 네트워크·LLM 미사용.

세 소비자(trend_report.emerging_terms · term_discovery._grams · rule_advisor)가 같은 판정을 쓴다.
2026-09-13 실측(§8-108): 7일 창 탈락 풀 550편에서 상위 3 중 둘이 상투구였다 —
`state-of-the-art performance`(하이픈 결합형이 한 토큰이라 `state`·`art` 낱말 목록에 안 걸림),
`previous studies`(`study` 만 있고 `studies` 는 없음). 원인은 "상투구 목록이 없다"가 아니라
**정확 토큰 일치라 표기·형태 변형을 못 잡는 것**이었다(외부 검토 2026-09-13).

원칙 넷.
1. **표시 문자열은 바꾸지 않는다.** 정규화(소문자·하이픈/슬래시 분리·단순 단수화)는 판정에만 쓴다.
   출력은 계속 `vision-language models`, `computational overhead` 같은 원문 표기다.
2. **낱말 목록(any-token)과 담화 구절(phrase)을 가른다.** `state` 를 낱말 목록에 두면
   `state estimation`·`state representation` 이 죽는다(고치기 전 코드가 실제로 그랬다).
   `state of the art` 는 구절로 막는다.
3. **우산어 판정은 all-token 이다** — 조합의 낱말이 *전부* 범용어일 때만 탈락. `world model` 은
   `model` 하나가 범용어라도 `world` 가 아니므로 산다. 낱말 목록과 합치지 않는다.
4. **왜 버렸는지를 센다** — 사유별 진단 집계는 로컬 로그·재생 평가용이고 LLM 에는 안 나간다.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
import re

# ── 낱말 목록 ────────────────────────────────────────────────────────────
# URL·저장소 조각. 어디에 오든 버린다.
HARD_NOISE = frozenset("https http www github com org net arxiv doi io gitlab huggingface".split())

# 조합 **어디에도** 오면 안 되는 말 — 주제어가 아니라 논문의 형식이다. 정규화 토큰(단수화 뒤)에
# 대조하므로 `studies`→`study`, `open-source`→`open`+`source` 가 걸린다.
# `state` 는 넣지 않는다(state estimation). `art` 는 남긴다 — 낱말 `art` 가 기술 용어인 경우가
# 이 분야에는 사실상 없고, "state of the art" 를 띄어 쓴 원문의 `art performance` 조각을 잡는다.
BOILERPLATE_WORDS = frozenset("""
available publicly release released open source repository
paper article study experiments experiment evaluation results result
propose proposed proposes present presents introduce introduces
show shows shown demonstrate demonstrates achieve achieves achieved
art sota baseline baselines outperform outperforms
compared comparison extensive comprehensive significantly substantially
first second third recent recently novel
approach approaches method methods framework frameworks technique techniques
address addresses addressing findings finding suggest suggests suggesting
limitation limitations challenge challenges challenging remains remain
validated validate validates scenario scenarios
""".split())

# 양 끝에만 못 오는 말 — 불용어와 연구 동사. 원래 토큰(하이픈 복합어는 한 단위) 기준으로 본다:
# `two distinct` 는 죽지만 `two-stage detector` 는 산다.
# 2026-09-13 추가분은 550편 재생에서 실제로 상위에 올라온 조각들이다(§8-109).
EDGE_STOP = frozenset("""
a an the of for and or with in on to from by via using use uses used based
toward towards we our this that these those is are be was were can it its as
at into over under between within without more most less than however such
also each other both same many while when where which who what have has had
do does did been being not only but if then there their them they you your
his her will would could should may might must per across new various several
different multiple code analysis here thus hence therefore
yet
train trains trained training evaluate evaluates evaluated apply applies applied
leverage leverages leveraged employ employs employed utilize utilizes utilized
develop develops developed design designs designed build builds built enable
enables enabled allow allows allowed require requires required obtain obtains
previous prior further future often consistently increasingly jointly typically
usually commonly widely largely highly through about how among all two three
four five out aim aims examine examines collect collected carry carried
rely relies explore explores conclude concludes improve improves optimize
optimizes fall falls despite against strong promising consistent superior
competitive remarkable impressive significant substantial notable performance
gain gains improvement improvements year years aged
""".split()) | BOILERPLATE_WORDS

# 담화 구절 — 낱말 하나하나는 멀쩡한데 이어지면 논문 서술 형식인 것. 정규화 토큰열의
# 연속 부분열로 대조한다. (`study`·`result` 처럼 이미 낱말 목록에 있는 것은 여기 안 넣는다.)
# 두 글자 이하 낱말(of·in·on)은 trend_report._WORD_RE 가 원문에서 이미 버리므로, 대조 전에
# 양쪽에서 같이 뺀다 — 안 그러면 띄어 쓴 "state of the art" 는 [state, the, art] 가 되어
# 하이픈형 [state, of, the, art] 과 같은 구절로 안 잡힌다.
BOILERPLATE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("state", "of", "the", "art"),
    ("previous", "work"), ("prior", "work"), ("related", "work"), ("future", "work"), ("this", "work"), ("our", "work"),
    ("further", "research"), ("future", "research"),
    ("systematic", "review"), ("percentage", "point"),
    # 2026-09-18 실측: 첫 7일 창 메일에서 `project page` 가 team_ai_advance "자주 나온 말" 4위로 올라왔다.
    # 논문이 본문 끝에 "project page: https://…" 를 적어 생긴 링크 딱지이지 연구 개념이 아니다.
    # 같은 실행에서 함께 올라온 `ground truth`·`inference time` 은 실제 기술 용어라 넣지 않는다(과잉 필터 금지).
    ("project", "page"), ("project", "website"),
    ("order", "of", "magnitude"), ("paving", "the", "way"), ("pave", "the", "way"),
)

# 우산어 — 조합의 낱말이 **전부** 이것뿐이면 후보가 아니다. term_discovery 에서 옮겨 왔다.
GENERIC_WORDS = frozenset("""
large language model models llm llms machine learning deep artificial intelligence
neural network networks multimodal generative pretrained pre trained transformer transformers mllm mllms
""".split())

# 세 소비자가 같은 경계에서 같은 후보를 보게 한다. 문장 경계를 먼저 자르는 이유는
# 제목·초록을 이어 붙인 경계에서 서로 무관한 낱말이 한 용어가 되는 것을 막기 위해서다
# (2026-09-14 외부 검토). 두 글자 약어는 기존 매처 계약대로 후보 낱말에서 제외한다.
SEGMENT_RE = re.compile(r"[.;:!?\n]+")
_SEGMENT_RE = SEGMENT_RE
_WORD_RE = re.compile(r"[a-z][a-z0-9\-]+")


# ── 정규화 ───────────────────────────────────────────────────────────────
def singular(w: str) -> str:
    """판정용 단순 단수화. 어미·예외를 보존해 의미 낱말을 훼손하지 않는다(2026-09-14 외부 검토)."""
    if w in {"series", "species", "lens", "news"}:
        return w
    if w.endswith(("sis", "ics", "us", "ss")):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 4 and w[-3] in "sxz":
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def norm_tokens(words: list[str]) -> list[str]:
    """판정용 토큰열: 소문자 · 하이픈/슬래시를 낱말 경계로 · 단수화. 표시에는 쓰지 않는다."""
    out: list[str] = []
    for w in words:
        for part in w.lower().replace("/", "-").split("-"):
            if part:
                out.append(singular(part))
    return out


def _phrase_key(tokens) -> tuple[str, ...]:
    return tuple(t for t in tokens if len(t) > 2)


_PHRASE_KEYS = tuple(_phrase_key(ph) for ph in BOILERPLATE_PHRASES)


def _has_phrase(norm: list[str]) -> bool:
    key = _phrase_key(norm)
    for ph in _PHRASE_KEYS:
        k = len(ph)
        if k and any(tuple(key[i:i + k]) == ph for i in range(len(key) - k + 1)):
            return True
    return False


# ── 판정 ─────────────────────────────────────────────────────────────────
def reject_reason(words: list[str]) -> str | None:
    """조합(원래 토큰열, 소문자·하이픈 유지)을 버릴 사유. None 이면 통과.
    순서가 뜻이다: 잡음 → 가장자리 → 구절 → 낱말. 진단 집계가 이 사유를 센다."""
    if any(w.startswith("-") or w.endswith("-") for w in words):
        return "hard_noise"                     # `low- and middle-income` 의 깨진 조각
    norm = norm_tokens(words)
    if any(t in HARD_NOISE for t in norm):
        return "hard_noise"
    if words[0] in EDGE_STOP or words[-1] in EDGE_STOP:
        return "edge_stop"
    if _has_phrase(norm):
        return "boilerplate_phrase"
    if any(t in BOILERPLATE_WORDS for t in norm):
        return "boilerplate_word"
    return None


def is_umbrella(term: str) -> bool:
    """all-token: 낱말이 전부 우산어일 때만."""
    toks = norm_tokens(term.split())
    return bool(toks) and all(t in GENERIC_WORDS for t in toks)


def _tokens(value: str | list[str] | tuple[str, ...]) -> list[str]:
    """포함 판정용 토큰열을 만든다 — 표시 문자열은 이 정규화 결과로 바꾸지 않는다."""
    if isinstance(value, str):
        return norm_tokens(value.split())
    return norm_tokens(list(value))


def token_sequence_contains(container: str | list[str] | tuple[str, ...],
                            needle: str | list[str] | tuple[str, ...]) -> bool:
    """낱말 단위 연속 포함을 판정한다. `AI`가 `training`에 들어가는 오판을 막는다."""
    outer, inner = _tokens(container), _tokens(needle)
    return bool(inner) and any(outer[i:i + len(inner)] == inner
                               for i in range(len(outer) - len(inner) + 1))


def overlaps_known(term: str, known: set[str]) -> bool:
    """후보와 아는 말의 양방향 토큰열 포함을 판정한다(2026-09-14 외부 검토)."""
    return any(token_sequence_contains(term, k) or token_sequence_contains(k, term) for k in known)


def ngrams(text: str, n: int | None = None, diag: Counter | None = None) -> Iterator[str]:
    """문장별 2·3-gram을 만들고 공용 위생 정책을 적용한다.

    세 소비자가 이 진입점을 함께 써야 문장 경계·담화 구절·우산어의 처리 결과가
    달라지지 않는다(2026-09-14 외부 검토). `n`을 주면 호환용 단일 길이, 생략하면
    후보 탐색용 두 길이를 낸다.
    """
    sizes = (n,) if n is not None else (2, 3)
    for segment in SEGMENT_RE.split(text or ""):
        words = [w for w in _WORD_RE.findall(segment.lower()) if len(w) > 2]
        for size in sizes:
            if size is None or size < 1:
                continue
            for i in range(len(words) - size + 1):
                gram = words[i:i + size]
                why = reject_reason(gram)
                if why:
                    if diag is not None:
                        diag[why] += 1
                    continue
                shown = " ".join(gram)
                if is_umbrella(shown):
                    if diag is not None:
                        diag["umbrella_generic"] += 1
                    continue
                yield shown


def candidate_ngrams(text: str, diag: Counter | None = None) -> set[str]:
    """한 문서의 공용 후보 집합을 돌려준다 — 문서 안 반복은 호출부가 편수로 한 번 센다."""
    return set(ngrams(text, diag=diag))


def new_diag() -> Counter:
    return Counter()
