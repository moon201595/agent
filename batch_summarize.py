"""batch_summarize.py — ④ 온디맨드 배치 요약 스크립트.

사람이 실행은 시키지만, 그 다음은 검색→선별→파싱→요약→검증→저장까지 무인으로 돈다.
채팅으로 한 단계씩 승인받지 않는다 (docs/PROGRESS.md §8-2 방향).

요약 엔진: Gemini(1순위) → Groq(실패·한도 초과 시 대체). 둘 다 무료 API — 상용 API
과금도, 로컬 GPU도 필요 없다. (2026-07-30/31 실측: 소형 오픈소스 모델 로컬 추론은
품질이 안 나왔고, Groq 단독은 수치를 거의 안 써서 검증만 통과하는 Goodhart 함정에
빠졌다. Gemini가 실사용 가능한 유일한 무료 후보였다.)

④⑤ 구간은 단일 패스 + 검증 1회만 한다 — 검증 실패해도 자동 재시도하지 않는다.
이건 이 하네스의 핵심 설계 원칙(검증기를 루프 판정자로 쓰지 않는다)을 그대로 지킨다.

사용법:
    python batch_summarize.py --ids 2505.13033 2405.15793
    python batch_summarize.py --title "TSPulse"
    python batch_summarize.py --keyword "LoRA fine-tuning summarization" --top-n 3

필요 파일: .env (GOOGLE_API_KEY, GROQ_API_KEY 중 최소 하나) — 저장소 루트, gitignore 대상.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

import server

# httpx의 기본 요청 로깅이 URL(쿼리 파라미터 포함)을 그대로 찍는다 — 키가 URL에
# 실리는 실수를 해도 로그로 새지 않도록 방어적으로 꺼둔다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TEMPLATE_PATH = ROOT / "prompts" / "summary_template.md"

# Gemini는 넉넉히, Groq 무료 TPM 한도(12,000/분)에 걸리지 않게 폴백 시 더 줄인다.
MAX_PAPER_CHARS = 60000
GROQ_FALLBACK_CHARS = 15000


def _load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


ENV = _load_env()


def _build_prompt(paper_text: str, template: str, max_chars: int) -> str:
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


async def _call_gemini(client: httpx.AsyncClient, paper_text: str, template: str) -> str:
    key = ENV.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY 없음")
    prompt = _build_prompt(paper_text, template, MAX_PAPER_CHARS)
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


async def _call_groq(client: httpx.AsyncClient, paper_text: str, template: str) -> str:
    key = ENV.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY 없음")
    prompt = _build_prompt(paper_text, template, GROQ_FALLBACK_CHARS)
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


async def _summarize(client: httpx.AsyncClient, paper_text: str, template: str) -> tuple[str, str]:
    try:
        return await _call_gemini(client, paper_text, template), "gemini"
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] Gemini 실패({e}) → Groq로 전환", file=sys.stderr)
    try:
        return await _call_groq(client, paper_text, template), "groq"
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Gemini·Groq 둘 다 실패: {e}") from e


async def _process_paper(client: httpx.AsyncClient, arxiv_id: str, template: str) -> dict:
    print(f"[{arxiv_id}] fetch_paper...")
    fetch_result = json.loads(await server.fetch_paper(server.FetchPaperInput(arxiv_id=arxiv_id)))
    if "error" in fetch_result:
        print(f"[{arxiv_id}] fetch 실패: {fetch_result}")
        return {"arxiv_id": arxiv_id, "status": "fetch_failed", "detail": fetch_result}

    text_result = json.loads(
        await server.get_paper_text(
            server.GetTextInput(arxiv_id=arxiv_id, offset=0, max_chars=MAX_PAPER_CHARS)
        )
    )
    paper_text = text_result["text"]

    print(f"[{arxiv_id}] 요약 생성 중...")
    summary, engine = await _summarize(client, paper_text, template)
    print(f"[{arxiv_id}] {engine} 로 생성됨 ({len(summary)}자)")

    save_result = json.loads(
        await server.save_summary(server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=summary))
    )
    verification = save_result.get("verification", {})
    print(
        f"[{arxiv_id}] 완료 — engine={engine} "
        f"pass_ratio={verification.get('pass_ratio')} "
        f"({verification.get('matched')}/{verification.get('total_numbers')})"
    )
    return {"arxiv_id": arxiv_id, "status": "done", "engine": engine, **verification}


async def _resolve_targets(args: argparse.Namespace) -> list[str]:
    if args.ids:
        return args.ids

    if args.title:
        result = json.loads(
            await server.arxiv_search_papers(
                server.ArxivSearchInput(query=args.title, max_results=1)
            )
        )
        papers = result.get("papers", [])
        if not papers:
            print(f"'{args.title}' 검색 결과 없음")
            return []
        return [papers[0]["arxiv_id"]]

    if args.keyword:
        arxiv_res = json.loads(
            await server.arxiv_search_papers(
                server.ArxivSearchInput(query=args.keyword, max_results=args.top_n * 3)
            )
        )
        s2_res = json.loads(
            await server.s2_search_papers(
                server.S2SearchInput(query=args.keyword, limit=args.top_n * 3)
            )
        )
        combined = arxiv_res.get("papers", []) + s2_res.get("papers", [])
        if not combined:
            print(
                f"검색 결과 없음 (arxiv={arxiv_res}, s2={s2_res}) — "
                "외부 API 한도 초과(429)일 수 있으니 잠시 후 다시 시도할 것"
            )
            return []
        ranked = json.loads(
            await server.dedupe_and_rank_papers(
                server.SelectPapersInput(papers=combined, top_k=args.top_n)
            )
        )
        return [p["arxiv_id"] for p in ranked.get("papers", []) if p.get("arxiv_id")]

    return []


async def main() -> None:
    parser = argparse.ArgumentParser(description="paper-harness ④ 온디맨드 배치 요약")
    parser.add_argument("--ids", nargs="+", help="arXiv ID 직접 지정 (복수 가능)")
    parser.add_argument("--title", help="논문 제목으로 검색해서 1편 처리")
    parser.add_argument("--keyword", help="키워드로 검색해서 상위 N편 처리")
    parser.add_argument("--top-n", type=int, default=3, help="--keyword 모드에서 선별할 편수")
    args = parser.parse_args()

    if not (args.ids or args.title or args.keyword):
        parser.error("--ids, --title, --keyword 중 하나는 지정해야 함")
    if not ENV.get("GOOGLE_API_KEY") and not ENV.get("GROQ_API_KEY"):
        parser.error(".env 에 GOOGLE_API_KEY 또는 GROQ_API_KEY 가 필요함")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    async with httpx.AsyncClient() as client:
        targets = await _resolve_targets(args)
        if not targets:
            print("처리할 논문이 없음")
            return
        print(f"대상 {len(targets)}편: {targets}")

        results = []
        for arxiv_id in targets:
            try:
                results.append(await _process_paper(client, arxiv_id, template))
            except Exception as e:  # noqa: BLE001
                print(f"[{arxiv_id}] 처리 실패: {e}", file=sys.stderr)
                results.append({"arxiv_id": arxiv_id, "status": "error", "detail": str(e)})

    print("\n=== 결과 요약 ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
