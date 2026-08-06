"""summarize_engine.py — ④ 요약 생성 엔진 (Gemini 우선, Groq 대체).

batch_summarize.py 와 review_app.py 가 공유하는 로직. 여기서 분리한 이유는
"검색·파싱·저장은 server.py, 판단은 사람/LLM" 원칙을 지키면서 요약 호출부만
따로 두 곳에서 쓰기 위함이다 — 로직 중복을 피한다.

2026-07-30/31 실측 근거는 docs/PROGRESS.md §5 "④ 요약 엔진 선정" 참고:
로컬 소형 오픈소스 모델(0.5B~3B)은 프롬프팅만으로는 품질이 안 나왔고,
Groq 무료 API는 숫자를 거의 안 써서 검증만 통과하는 Goodhart 함정에 빠졌다.
Gemini 무료 API가 실사용 가능한 유일한 무료 후보였다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import httpx

import sentence_grounding

# httpx 기본 요청 로깅이 URL(쿼리 파라미터 포함)을 그대로 찍는다 — 키가 URL에
# 실리는 실수를 해도 로그로 새지 않도록 방어적으로 꺼둔다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TEMPLATE_PATH = ROOT / "prompts" / "summary_template.md"
SURVEY_TEMPLATE_PATH = ROOT / "prompts" / "summary_template_survey.md"

# 2026-08-06 실측: 실증 연구용 템플릿(방법 상세→실험 설정→결과)을 서베이
# 논문에 그대로 쓰면 "④ 결과" 절이 억지로 비거나 남의 수치를 자기 결과처럼
# 적는 위험이 있다 — 실측(Small VLM Survey 등)에서 통과율은 1.0인데 검증된
# 숫자가 9개뿐이었다(정확하지만 빈약함, 검증기가 못 잡는 완전성 문제).
# 판정은 LLM 판단이 아니라 제목·초록의 결정적 키워드 규칙이다 — 서버가
# 판단하지 않는다는 원칙을 요약 엔진 쪽에서도 지킨다. 오판정되면 사람이
# 직접 템플릿을 지정해 덮어쓸 수 있다(select_template 의 force 인자).
_SURVEY_KEYWORDS = re.compile(
    r"\b(a\s+survey|surveys?\b|systematic\s+review|literature\s+review|"
    r"a\s+review\s+of|an?\s+overview\s+of)\b", re.I,
)


def is_survey_paper(title: str, abstract: str = "") -> bool:
    """제목에 서베이/리뷰임을 명시하는 표현이 있으면 서베이로 분류한다.
    제목이 가장 강한 신호다("X: A Survey", "A Survey of X") — 초록은 거짓
    양성이 잦아(예: "본 논문은 관련 연구를 review한다"는 흔한 도입 문구)
    제목에 신호가 없을 때만 약하게 참고한다.
    """
    if title and _SURVEY_KEYWORDS.search(title):
        return True
    if abstract and re.match(r"^\s*(this|we present|in this)\b.{0,80}" + _SURVEY_KEYWORDS.pattern,
                              abstract, re.I):
        return True
    return False


def select_template(title: str, abstract: str = "", force: str | None = None) -> str:
    """force: None(자동 판정) | 'default' | 'survey' — 오판정 시 수동 지정용."""
    if force == "survey" or (force is None and is_survey_paper(title, abstract)):
        return SURVEY_TEMPLATE_PATH.read_text(encoding="utf-8")
    return TEMPLATE_PATH.read_text(encoding="utf-8")

# Gemini는 넉넉히, Groq 무료 TPM 한도(12,000/분)에 걸리지 않게 폴백 시 더 줄인다.
#
# 2026-08-06 실측: 60,000자에서 자르니 Feelbert(236,983자)가 3.1절 도중
# 잘려 결과 절 자체를 못 보고 통과율 0.71로 떨어졌다("일부만 보고 요약"은
# 검증이 무의미하다는 지적을 받아들여, 자르지 않고 전부 읽도록 바꿨다).
# Gemini flash 의 실제 컨텍스트 창은 수백만 자 단위라 60,000자는 애초에
# 보수적으로 잡은 값이었다 — CHUNK_SIZE 를 크게 올려 대부분의 논문(지금까지
# 실측된 최대 462,289자도 청크 2개면 끝)을 한두 번 호출로 끝낸다. 그래도
# 넘치면 MAX_CHUNKS 상한까지 이어붙인다 — 상한 없는 루프가 아니라 여기도
# 상한 있는 예외 처리(이 프로젝트 전체 원칙과 동일).
MAX_PAPER_CHARS = 300000
CHUNK_SIZE = MAX_PAPER_CHARS
MAX_CHUNKS = 4  # 300,000 × 4 = 최대 1,200,000자까지 커버
GEMINI_CHUNK_DELAY = 3.0  # 청크 사이 최소 간격(초) — 429 재시도와 별개로 두는 예방적 여유

# 2026-08-06: Gemini만 청킹하고 Groq는 그냥 15,000자에서 잘랐었는데,
# "둘 다 전문을 읽을 수 있어야 한다"는 지적을 받아들여 Groq도 청킹한다.
# 청크 크기는 12,000 TPM 한도 그대로 유지(GROQ_FALLBACK_CHARS)하되, 청크
# 사이 간격을 60초로 크게 둔다 — 한 번 호출이 토큰 예산 대부분을 쓰기
# 때문에(발췌 15,000자 + 템플릿 + 응답 2,000토큰), 분당 한도가 찰 시간을
# 그대로 기다려야 한다. 그만큼 느리다(최악의 경우 논문 1편에 30분 안팎) —
# Gemini 가 완전히 막혔을 때만 타는 경로라 감수할 만하다고 판단했다.
GROQ_FALLBACK_CHARS = 15000
GROQ_CHUNK_SIZE = GROQ_FALLBACK_CHARS
GROQ_MAX_CHUNKS = 32  # 15,000 × 32 = 480,000자 — 지금까지 실측된 최대 논문(462,289자) 커버
GROQ_CHUNK_DELAY = 60.0

# 2026-08-05 실측: 논문 34편을 연속 호출로 돌렸더니 22편째부터 Gemini·Groq
# 무료 티어 둘 다 429(분당 한도 초과)를 맞았다 — 호출 사이에 페이싱이 전혀
# 없었기 때문이다. 수동으로 25초 간격을 두고 재시도하니 12/12 전부 성공했다.
# 그 교훈을 배치 스크립트마다 따로 페이싱하게 두지 않고 여기 한 곳에 고정한다
# — ①~③(arXiv/S2)의 _throttled_*_get 과 같은 발상이다. 429 상한(초과)까지만
# 재시도한다 — 무한 재시도가 아니라 상한 있는 예외 처리다.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF = (20.0, 40.0)  # 재시도 1회차·2회차 대기(초)


def _is_rate_limited(e: Exception) -> bool:
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


ENV = load_env()


def build_prompt(chunk_text: str, template: str) -> str:
    """chunk_text 는 이미 sentence_grounding 이 문장 단위로 잘라 [S번호] 태그를
    붙이고 청크 크기에 맞게 묶어둔 상태다 — 여기서는 더 자르지 않는다
    (2026-08-06, ⑤를 "숫자 대조"에서 "근거 문장 대조"로 격상, docs/PROGRESS.md
    §8-9). 태그를 요약문의 "출처위치"에 그대로 인용하게 해서, 검증기가
    "그 숫자가 원문 어딘가에 있다"가 아니라 "그 숫자가 인용한 그 문장에
    있다"까지 확인할 수 있게 한다.
    """
    return f"""다음은 한 논문의 원문 일부다. 아래 템플릿의 '## 템플릿' 항목 구조만 채워서
한국어로 정리하라. 템플릿 파일의 제목·작성규칙·작업순서 설명은 출력하지 말고,
'### 기본정보'부터 시작하는 항목만 그대로 채워 출력하라.
숫자·수치는 반드시 원문에서 확인한 것만 쓰고, 확인되지 않은 숫자는 쓰지 마라.

아래 원문은 문장마다 "[S번호]" 태그가 붙어 있다(예: [S0142]). 수치를 인용할 때는
템플릿이 요구하는 "출처위치" 뒤에 그 수치가 실제로 있는 문장의 태그를 **그대로**
덧붙여라(예: "본문 4.1절 [S0142]"). 어느 문장에서 나온 값인지 확신할 수 없으면
그 수치는 쓰지 마라. 태그는 원문에 이미 있는 것만 그대로 옮겨 적어야 한다 —
직접 지어내면 안 된다.

# 템플릿
{template}

# 논문 원문
{chunk_text}
"""


_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
)


async def _post_gemini(client: httpx.AsyncClient, prompt: str) -> str:
    key = ENV.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY 없음")
    # URL 쿼리 파라미터로 키를 보내면 로그·프록시 기록에 그대로 남는다 — 헤더로 보낸다.
    resp = await client.post(
        _GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        headers={"x-goog-api-key": key},
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


async def call_gemini(client: httpx.AsyncClient, chunk_text: str, template: str) -> str:
    return await _post_gemini(client, build_prompt(chunk_text, template))


_ADDENDUM_NO_CONTENT = "추가로 뽑을 새 내용 없음"


async def call_gemini_addendum(client: httpx.AsyncClient, chunk_text: str) -> str:
    """이어지는 청크에서 새 결과·한계점만 뽑는 보충 호출. 청크 1 이 이미
    만든 템플릿 구조를 마크다운 파싱으로 고쳐 끼워 넣는 위험한 수술을 하지
    않는다 — 그냥 뒤에 이어붙일 독립된 절을 새로 쓰게 한다.

    2026-08-06 실측: 원래 프롬프트는 "★등급"이라는 자리표시자만 던지고 그게
    구체적으로 뭔지(★★★/★★/★ 세 가지) 설명하지 않았다 — 본 템플릿(R3)의 전체
    맥락 없이 이 프롬프트 하나만 보는 모델 입장에선 "★등급"이 진짜 출력해야
    할 리터럴 문자열인지 자리표시자 설명인지 모호하다. Gemini는 이 모호함을
    잘 넘겼지만 Groq(llama-3.3-70b)는 종종 "★등급"을 그대로 베끼거나 이
    프로젝트에 없는 표기(★중, ★★★★)를 만들어냈다(§5 "서베이 템플릿·청킹
    실측 재검증" 참고). 세 가지 리터럴 옵션을 R3 처럼 직접 나열해 모호함을
    없앴다.
    """
    prompt = f"""다음은 같은 논문 원문의 뒷부분 발췌다. 앞부분은 이미 다른 절로 요약됐다.
이 발췌에**만** 있는 새로운 결과 수치나 한계점이 있으면 아래 두 항목만 채워라.
이미 다뤘을 방법론·서론 설명은 반복하지 마라. 수치는 반드시 이 발췌에서 확인한 것만 쓰고,
이 발췌는 문장마다 "[S번호]" 태그가 붙어 있다(예: [S0142]) — "값(조건/비교대상/지표) —
출처위치" 뒤에 그 수치가 실제로 있는 문장의 태그를 **그대로** 덧붙이고(직접 지어내지
말 것, 원문에 있는 태그만 옮겨 적을 것), 이어서 다음 세 가지 중 **정확히 하나만** 그대로
붙여라(다른 문자·설명은 절대 붙이지 마라): 본문 서술 문장에서 직접 확인했으면 ★★★,
표나 부록에서 확인했으면 ★★, 초록에만 등장하거나 원문 미확인이면 ★.
새로 뽑을 것이 없으면 두 항목 모두 "{_ADDENDUM_NO_CONTENT}"라고만 써라.

### 추가 결과 (원문 후반부)

### 추가 한계점 (원문 후반부)

# 논문 원문 (후반부 발췌)
{chunk_text}
"""
    return await _post_gemini(client, prompt)


async def _post_groq(client: httpx.AsyncClient, prompt: str) -> str:
    key = ENV.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY 없음")
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
        "temperature": 0.3,
    }
    resp = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=body,
        headers={
            "Authorization": f"Bearer {key}",
            # Cloudflare가 기본 UA를 봇으로 오인해 1010으로 차단한 사례가 있어 명시한다.
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) paper-harness/1.0",
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def call_groq(client: httpx.AsyncClient, chunk_text: str, template: str) -> str:
    return await _post_groq(client, build_prompt(chunk_text, template))


async def call_groq_addendum(client: httpx.AsyncClient, chunk_text: str) -> str:
    """call_gemini_addendum 과 같은 발상, Groq 용. 발췌 자체도 GROQ_FALLBACK_CHARS
    이내로 자른다 — 청크 크기 자체가 이미 그 값이라 여기선 그대로 쓴다."""
    prompt = f"""다음은 같은 논문 원문의 뒷부분 발췌다. 앞부분은 이미 다른 절로 요약됐다.
이 발췌에**만** 있는 새로운 결과 수치나 한계점이 있으면 아래 두 항목만 채워라.
이미 다뤘을 방법론·서론 설명은 반복하지 마라. 수치는 반드시 이 발췌에서 확인한 것만 쓰고,
이 발췌는 문장마다 "[S번호]" 태그가 붙어 있다(예: [S0142]) — "값(조건/비교대상/지표) —
출처위치" 뒤에 그 수치가 실제로 있는 문장의 태그를 **그대로** 덧붙이고(직접 지어내지
말 것, 원문에 있는 태그만 옮겨 적을 것), 이어서 다음 세 가지 중 **정확히 하나만** 그대로
붙여라(다른 문자·설명은 절대 붙이지 마라): 본문 서술 문장에서 직접 확인했으면 ★★★,
표나 부록에서 확인했으면 ★★, 초록에만 등장하거나 원문 미확인이면 ★.
새로 뽑을 것이 없으면 두 항목 모두 "{_ADDENDUM_NO_CONTENT}"라고만 써라.

### 추가 결과 (원문 후반부)

### 추가 한계점 (원문 후반부)

# 논문 원문 (후반부 발췌)
{chunk_text}
"""
    return await _post_groq(client, prompt)


async def _call_with_rate_limit_retry(coro_fn, label: str) -> str:
    """429 만 상한(RATE_LIMIT_RETRIES)까지 재시도한다. 429 가 아닌 실패(키 없음,
    5xx 등)는 즉시 올려서 호출부가 바로 다른 엔진으로 넘어가게 한다 —
    429 만 "잠깐 기다리면 풀릴 수 있는" 실패이기 때문이다.

    coro_fn: 인자 없이 바로 await 할 수 있는 커루틴을 만드는 팩토리(같은
    커루틴 객체는 재사용할 수 없어서 함수로 받는다). 이렇게 하면 청크 1개짜리
    호출이든 보충 청크 호출이든 시그니처 상관없이 그대로 감쌀 수 있다.
    """
    last: Exception | None = None
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return await coro_fn()
        except Exception as e:  # noqa: BLE001
            if not _is_rate_limited(e) or attempt >= RATE_LIMIT_RETRIES:
                raise
            wait = RATE_LIMIT_BACKOFF[attempt]
            print(f"  [경고] {label} 429 — {wait:.0f}초 대기 후 재시도 "
                  f"({attempt + 1}/{RATE_LIMIT_RETRIES})", file=sys.stderr)
            last = e
            await asyncio.sleep(wait)
    raise last  # pragma: no cover — 루프가 항상 return 또는 raise 로 빠짐


async def _summarize_chunked(
    client: httpx.AsyncClient, paper_text: str, template: str,
    call_single, call_addendum, chunk_size: int, max_chunks: int,
    chunk_delay: float, label: str,
) -> str:
    """첫 청크로 전체 템플릿을 채운 뒤, 남은 청크들로 "추가 결과·추가
    한계점"만 보충한다 — 긴 논문이라고 뒷부분을 조용히 안 보고 넘어가지
    않는다. 청크 처리 중 하나가 실패하면 그 뒤는 포기하고 지금까지 만든
    결과로 마무리한다(무한정 재시도하지 않음). Gemini·Groq 둘 다 이 함수를
    쓴다 — 청크 크기·상한·간격만 엔진별로 다르게 준다(summarize() 참고).

    2026-08-06: 청크 경계를 원문 char 위치로 그냥 자르던 방식에서
    sentence_grounding.build_tagged_chunks 로 바꿨다 — 문장 단위로만 잘라
    문장이 청크 경계에서 반토막 나지 않고, 문장마다 전체 논문 기준의
    전역 [S번호]가 붙어 어느 청크에서 처리하든 인용 번호가 서로 안 겹친다
    (⑤ 검증기가 그 번호로 원문 문장을 다시 찾아 대조한다 — §8-9 참고).
    """
    chunks, _sentences = sentence_grounding.build_tagged_chunks(paper_text, chunk_size, max_chunks)
    if not chunks:
        chunks = [""]
    summary = await _call_with_rate_limit_retry(
        lambda: call_single(client, chunks[0], template), label,
    )
    for chunk_num, chunk_text in enumerate(chunks[1:], start=2):
        if chunk_delay:
            await asyncio.sleep(chunk_delay)
        try:
            addendum = await _call_with_rate_limit_retry(
                lambda: call_addendum(client, chunk_text), f"{label}(청크{chunk_num})",
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] {label} 청크 {chunk_num} 처리 실패({e}) — 이 이후 구간은 건너뜀",
                  file=sys.stderr)
            break
        if _ADDENDUM_NO_CONTENT not in addendum:
            summary += f"\n\n{addendum}"
    return summary


async def summarize(client: httpx.AsyncClient, paper_text: str, template: str) -> tuple[str, str]:
    """returns (summary_markdown, engine_name)

    Gemini·Groq 둘 다 청크로 전문을 읽는다(2026-08-06, "둘 다 되게 하자"
    요청 반영) — 청크 크기·상한·간격만 다르다. Groq는 12,000 TPM 한도가
    빠듯해서 청크 사이 간격을 60초로 크게 둔다 — 그만큼 느리다(최악의
    경우 논문 1편에 30분 안팎). Gemini가 완전히 막혔을 때만 타는 경로라
    감수할 만하다고 판단했다.
    """
    try:
        summary = await _summarize_chunked(
            client, paper_text, template, call_gemini, call_gemini_addendum,
            CHUNK_SIZE, MAX_CHUNKS, GEMINI_CHUNK_DELAY, "Gemini",
        )
        return summary, "gemini"
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] Gemini 실패({e}) → Groq로 전환", file=sys.stderr)
    try:
        summary = await _summarize_chunked(
            client, paper_text, template, call_groq, call_groq_addendum,
            GROQ_CHUNK_SIZE, GROQ_MAX_CHUNKS, GROQ_CHUNK_DELAY, "Groq",
        )
        return summary, "groq"
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Gemini·Groq 둘 다 실패: {e}") from e
