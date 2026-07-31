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

import logging
import os
import sys
from pathlib import Path

import httpx

# httpx 기본 요청 로깅이 URL(쿼리 파라미터 포함)을 그대로 찍는다 — 키가 URL에
# 실리는 실수를 해도 로그로 새지 않도록 방어적으로 꺼둔다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

# Gemini는 넉넉히, Groq 무료 TPM 한도(12,000/분)에 걸리지 않게 폴백 시 더 줄인다.
MAX_PAPER_CHARS = 60000
GROQ_FALLBACK_CHARS = 15000


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


def build_prompt(paper_text: str, template: str, max_chars: int) -> str:
    text = paper_text[:max_chars]
    return f"""다음은 한 논문의 원문 일부다. 아래 템플릿의 '## 템플릿' 항목 구조만 채워서
한국어로 정리하라. 템플릿 파일의 제목·작성규칙·작업순서 설명은 출력하지 말고,
'### 기본정보'부터 시작하는 항목만 그대로 채워 출력하라.
숫자·수치는 반드시 원문에서 확인한 것만 쓰고, 확인되지 않은 숫자는 쓰지 마라.

# 템플릿
{template}

# 논문 원문
{text}
"""


async def call_gemini(client: httpx.AsyncClient, paper_text: str, template: str) -> str:
    key = ENV.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY 없음")
    prompt = build_prompt(paper_text, template, MAX_PAPER_CHARS)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-flash-latest:generateContent"
    )
    # URL 쿼리 파라미터로 키를 보내면 로그·프록시 기록에 그대로 남는다 — 헤더로 보낸다.
    resp = await client.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        headers={"x-goog-api-key": key},
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


async def call_groq(client: httpx.AsyncClient, paper_text: str, template: str) -> str:
    key = ENV.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY 없음")
    prompt = build_prompt(paper_text, template, GROQ_FALLBACK_CHARS)
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


async def summarize(client: httpx.AsyncClient, paper_text: str, template: str) -> tuple[str, str]:
    """returns (summary_markdown, engine_name)"""
    try:
        return await call_gemini(client, paper_text, template), "gemini"
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] Gemini 실패({e}) → Groq로 전환", file=sys.stderr)
    try:
        return await call_groq(client, paper_text, template), "groq"
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Gemini·Groq 둘 다 실패: {e}") from e
