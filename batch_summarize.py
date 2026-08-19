"""batch_summarize.py — ④ 온디맨드 배치 요약 스크립트.

사람이 실행은 시키지만, 그 다음은 검색→선별→파싱→요약→검증→저장까지 무인으로 돈다.
채팅으로 한 단계씩 승인받지 않는다 (docs/PROGRESS.md §8-2 방향).

요약 엔진 호출부는 summarize_engine.py (review_app.py 와 공유).
④⑤ 구간은 단일 패스 + 검증 1회만 한다 — 검증 실패해도 자동 재시도하지 않는다.
이건 이 하네스의 핵심 설계 원칙(검증기를 루프 판정자로 쓰지 않는다)을 그대로 지킨다.

저장된 요약의 review_status 는 항상 'pending' 으로 시작한다 — ⑥ 사람 판단은
review_app.py 에서 한다.

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
import sys
from pathlib import Path

import httpx

import server
import summarize_engine as engine


def _write_progress(path: str | None, **fields) -> None:
    """review_app.py가 이 스크립트를 별도 프로세스로 띄우고("취소"가 실제로
    눌리게 하려면 Streamlit의 한 스크립트 실행 안에 갇혀 있으면 안 된다,
    2026-08-19) 진행률을 알아야 할 때만 쓰는 선택적 훅이다 — --progress-file
    없이 터미널에서 직접 돌리는 기존 사용법은 전혀 안 바뀐다. 매번 전체
    상태를 새로 써서(부분 갱신 아님) review_app.py가 읽는 도중에 절반만
    쓰인 파일을 보는 일이 없게 한다(JSON 파싱 중간에 파일이 안 바뀜)."""
    if not path:
        return
    Path(path).write_text(json.dumps(fields), encoding="utf-8")


async def _process_paper(client: httpx.AsyncClient, arxiv_id: str) -> dict:
    print(f"[{arxiv_id}] fetch_paper...")
    fetch_result = json.loads(await server.fetch_paper(server.FetchPaperInput(arxiv_id=arxiv_id)))
    if "error" in fetch_result:
        print(f"[{arxiv_id}] fetch 실패: {fetch_result}")
        return {"arxiv_id": arxiv_id, "status": "fetch_failed", "detail": fetch_result}

    # get_paper_text(MCP 도구)는 채팅 컨텍스트 절약용 80,000자 상한이 있다 —
    # 여기서는 원문 전체를 읽는다. 길면 summarize_engine 이 알아서 청크로 나눈다.
    paper_text = server.read_full_text(arxiv_id)

    # 서베이/리뷰 논문은 결정적 키워드 규칙으로 감지해 전용 템플릿을 쓴다
    # (판단은 LLM이 아니라 코드가 한다 — engine.select_template 참고).
    template = engine.select_template(fetch_result.get("title", ""))

    print(f"[{arxiv_id}] 요약 생성 중...")
    summary, used_engine = await engine.summarize(client, paper_text, template)
    print(f"[{arxiv_id}] {used_engine} 로 생성됨 ({len(summary)}자)")

    save_result = json.loads(
        await server.save_summary(server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=summary))
    )
    verification = save_result.get("verification", {})
    print(
        f"[{arxiv_id}] 완료 — engine={used_engine} "
        f"pass_ratio={verification.get('pass_ratio')} "
        f"({verification.get('matched')}/{verification.get('total_numbers')}) "
        "— review_status=pending, review_app.py 에서 검토할 것"
    )
    return {"arxiv_id": arxiv_id, "status": "done", "engine": used_engine, **verification}


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
    parser.add_argument(
        "--progress-file",
        help="review_app.py 전용 — 진행률을 이 경로에 JSON으로 계속 써준다(선택, "
             "터미널 직접 실행 시엔 안 줘도 됨)",
    )
    args = parser.parse_args()

    if not (args.ids or args.title or args.keyword):
        parser.error("--ids, --title, --keyword 중 하나는 지정해야 함")
    if not engine.ENV.get("GOOGLE_API_KEY") and not engine.ENV.get("GROQ_API_KEY"):
        parser.error(".env 에 GOOGLE_API_KEY 또는 GROQ_API_KEY 가 필요함")

    try:
        async with httpx.AsyncClient() as client:
            targets = await _resolve_targets(args)
            if not targets:
                print("처리할 논문이 없음")
                return
            print(f"대상 {len(targets)}편: {targets}")
            _write_progress(args.progress_file, total=len(targets), done=0, targets=targets)

            results = []
            for i, arxiv_id in enumerate(targets):
                try:
                    results.append(await _process_paper(client, arxiv_id))
                except Exception as e:  # noqa: BLE001
                    print(f"[{arxiv_id}] 처리 실패: {e}", file=sys.stderr)
                    results.append({"arxiv_id": arxiv_id, "status": "error", "detail": str(e)})
                _write_progress(args.progress_file, total=len(targets), done=i + 1, targets=targets)
    finally:
        # 정상 종료·예외 둘 다 여기로 온다 — review_app.py가 이 파일의
        # 존재 여부로 "아직 진행 중"을 판단하므로(_read_search_job 참고)
        # 끝났으면(취소가 아니라 스스로 끝났으면) 반드시 지운다. "취소"로
        # 죽었을 때(SIGTERM)는 이 finally 자체가 못 돌기 때문에, 그 경우의
        # 정리는 review_app.py의 _cancel_search_job이 직접 맡는다.
        if args.progress_file:
            Path(args.progress_file).unlink(missing_ok=True)

    print("\n=== 결과 요약 ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
