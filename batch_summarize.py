"""batch_summarize.py — ④ 온디맨드 배치 요약 스크립트.

사람이 실행은 시키지만, 그 다음은 검색→선별→파싱→요약→검증→저장→재현까지 무인으로 돈다.
채팅으로 한 단계씩 승인받지 않는다 (docs/PROGRESS.md §8-2 방향).

요약 엔진 호출부는 summarize_engine.py (review_app.py 와 공유).
④⑤ 구간은 단일 패스 + 검증 1회만 한다 — 검증 실패해도 자동 재시도하지 않는다.
이건 이 하네스의 핵심 설계 원칙(검증기를 루프 판정자로 쓰지 않는다)을 그대로 지킨다.

2026-08-24: ⑥ 사람 승인 게이트를 하네스 전체에서 없앴다 — "코드 재현까지
다 끝난 상태로 자동 이메일을 보내야 하는데 승인 버튼을 언제 누르냐"는
지적을 받아들인 것. 그래서 ④⑤가 끝나면(요약 저장) 곧바로 ⑦(코드 재현)이
자동으로 시작된다(docker_runner.launch_background) — 사람이 따로 승인할
필요가 없다. review_status 컬럼 자체는 DB에 남아있지만(값은 항상
'pending') 더 이상 어떤 흐름도 이 값으로 무언가를 막지 않는다.

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

import docker_runner
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


async def _process_paper(client: httpx.AsyncClient, arxiv_id: str, on_progress=None,
                          paper: dict | None = None) -> dict:
    """paper 를 주면 arXiv 밖 논문(저널 오픈액세스)도 처리한다(2026-09-02).

    왜 여기서 분기하나: ⑦ 재현 트리거를 소유한 지점이 이 함수라(CLAUDE.md 5)
    새 경로를 옆에 만들면 전이 지점이 둘이 된다. 본문을 어디서 받든 그 뒤는
    같은 한 줄기로 흐르게 둔다.

    arXiv 밖 논문은 fetch_pdf_from_url → ingest_local_pdf 경로를 탄다. 그
    함수들은 이미 있고 docstring 에 "S2 검색 결과의 open_access_pdf 필드용"
    이라고 적혀 있다 — 쓰이기를 기다리고 있던 배선이다. 합성 ID(pdf-<해시>)가
    나오면 그 뒤 저장·검증·재현은 arXiv 논문과 완전히 같다.
    """
    if not arxiv_id:
        pdf_url = (paper or {}).get("open_access_pdf")
        title = (paper or {}).get("title") or ""
        if not pdf_url:
            return {"arxiv_id": "", "status": "fetch_failed",
                    "detail": "arXiv ID 도 오픈액세스 PDF 링크도 없음"}
        doi = (paper or {}).get("doi") or ""
        try:
            fetched = await server.fetch_pdf_from_url(
                pdf_url, title, source_note=f"open-access: {doi or pdf_url}")
        except Exception as e:  # noqa: BLE001 — 링크가 초록·로그인 페이지인 경우가 흔하다
            # **S2 의 openAccessPdf 는 못 믿는다.** 2026-09-04 실측: 그 링크를 가진
            # 5편 중 1편은 403, 4편은 PDF 가 아니라 초록·로그인 HTML 이 왔다.
            # 링크가 있다는 것과 받을 수 있다는 건 다르다.
            #
            # 그래서 DOI 로 Unpaywall 에 한 번 더 묻는다. `resolve_unpaywall_pdf`
            # 는 이미 있는데 이 경로에서만 배선이 빠져 있었다.
            #
            # **회수율을 처음에 20% 라고 썼는데 틀렸다(같은 날 정정).** Unpaywall 이
            # URL 을 *돌려준다*는 것만 확인하고 그 URL 이 실제로 PDF 를 주는지는
            # 안 봤다 — S2 필드에서 지적한 바로 그 실수를 한 층 위에서 반복한
            # 것이다. 끝까지 받아보니 nature.com 이 그 직링크에도 HTML 을 준다
            # (봇 차단). `oa_locations` 도 그 하나뿐이라 다른 경로가 없다.
            # **실측 회수율은 5편 중 0편.**
            #
            # 그래도 남겨 둔다: 실패한 경로에서 무료 호출 하나를 더 쓸 뿐이고,
            # 저장소(PMC·기관 리포지터리)에 사본이 있는 논문에는 실제로 듣는다.
            # 다만 "이걸 넣었으니 해결됐다"고 세지 않는다.
            fetched = None
            if doi:
                try:
                    alt = await server.resolve_unpaywall_pdf(doi)
                    if alt and alt.get("url") and alt["url"] != pdf_url:
                        fetched = await server.fetch_pdf_from_url(
                            alt["url"], title or alt.get("title") or "",
                            source_note=f"open-access(unpaywall): {doi}")
                        print(f"  [본문] Unpaywall 로 복구 — {title[:40]}")
                except Exception as alt_err:  # noqa: BLE001 — 원래 실패로 되돌린다
                    # 왜 실패했는지는 남긴다. 조용히 삼키니 "폴백이 왜 안 걸렸나"를
                    # 로그만으로는 알 수 없었다(2026-09-04 실측 중 실제로 겪음).
                    print(f"  [본문] Unpaywall 폴백도 실패({type(alt_err).__name__}) — "
                          f"초록으로 간다")
                    fetched = None
            if fetched is None:
                # Unpaywall 로도 안 되면 이 논문은 앞으로도 초록밖에 못 본다.
                # **그러면 초록이라도 제대로 정리한다**(2026-09-04) — 잘린 초록
                # 한 토막에 오류 문자열을 붙여 내보내는 건 요약이 아니다.
                abstract = ((paper or {}).get("abstract") or "").strip()
                if not abstract and doi:
                    # S2 가 초록을 안 주는 논문이 많다 — 09-04 메일 상위 6편 중
                    # 3편이 "(초록 없음)" 이었다. OpenAlex 에는 있었다(실측 5/7).
                    abstract = await server.resolve_openalex_abstract(doi)
                    if abstract:
                        print(f"  [초록] OpenAlex 에서 보강 ({len(abstract):,}자) — {title[:40]}")
                brief = await engine.summarize_abstract(client, title, abstract)
                return {"arxiv_id": "", "status": "abstract_only" if brief else "fetch_failed",
                        "brief": brief,
                        "detail": "본문 비공개(오픈액세스 아님) — 초록만 확인"
                                  if brief else
                                  f"오픈액세스 PDF 수집 실패: {type(e).__name__} {str(e)[:120]}"}
        arxiv_id = fetched["arxiv_id"]
        print(f"[{arxiv_id}] 오픈액세스 PDF 수집됨 ({fetched.get('text_chars', 0):,}자) — {title[:40]}")
        fetch_result = fetched
    else:
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
    summary, used_engine = await engine.summarize(client, paper_text, template, on_progress=on_progress)
    print(f"[{arxiv_id}] {used_engine} 로 생성됨 ({len(summary)}자)")

    save_result = json.loads(
        await server.save_summary(server.SaveSummaryInput(
            arxiv_id=arxiv_id, markdown=summary, engine=used_engine))
    )
    verification = save_result.get("verification", {})

    # ⑧ 철회 여부 조회(M5, 2026-08-28) — OpenAlex 싱글턴 1회 + 필요 시
    # Crossref 교차확인. 실패해도 None 으로 떨어지고 파이프라인은 계속된다.
    retracted = await server.refresh_retraction_status(arxiv_id)

    # ⑥ 승인 게이트 없이 곧바로 ⑦로 넘어간다(2026-08-24, 모듈 docstring
    # 참고) — Docker clone+install+run은 무거운 작업이라 여기서 기다리지
    # 않고 별도 프로세스로 띄우기만 하고 바로 다음 논문으로 넘어간다.
    repro_msg = docker_runner.launch_background(arxiv_id)

    print(
        f"[{arxiv_id}] 완료 — engine={used_engine} "
        f"pass_ratio={verification.get('pass_ratio')} "
        f"({verification.get('matched')}/{verification.get('total_numbers')}) "
        f"— ⑦ {repro_msg}"
    )
    return {"arxiv_id": arxiv_id, "status": "done", "engine": used_engine,
            "repro": repro_msg, "is_retracted": retracted, **verification}


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
    if not engine.gemini_key_names() and not engine.ENV.get("GROQ_API_KEY"):
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
                # 청크 하나 시도할 때마다 progress 파일에 "지금 몇 번째
                # 청크"까지 얹어준다 — Groq 폴백은 청크 간격이 60초라 큰
                # 논문은 한 편에 최대 31분까지 걸리는데, 이 콜백 없이는
                # 그 시간 내내 progress 파일이 done 카운트 그대로라
                # review_app.py 화면이 몇십 분씩 안 바뀌어 "멈춘 것처럼
                # 보인다"는 지적을 그대로 반복했다(2026-08-19, "거의 10분째
                # 이 상태야").
                def _on_chunk_progress(engine_label, chunk_num, total_chunks):
                    _write_progress(
                        args.progress_file, total=len(targets), done=i,
                        targets=targets, results=results,
                        stage=f"{arxiv_id} 처리 중 — {engine_label} · 청크 {chunk_num}/{total_chunks}",
                    )
                try:
                    results.append(await _process_paper(client, arxiv_id, on_progress=_on_chunk_progress))
                except Exception as e:  # noqa: BLE001
                    print(f"[{arxiv_id}] 처리 실패: {e}", file=sys.stderr)
                    results.append({"arxiv_id": arxiv_id, "status": "error", "detail": str(e)})
                # results도 매번 같이 써준다 — 예전엔 done 카운트만 넘겨서
                # review_app.py가 "몇 번째 시도까지 끝났나"만 알고 "그 시도가
                # 성공했는지 실패했는지, 실패라면 왜인지"는 로그 파일에만
                # 남고 화면엔 전혀 안 보였다("2개 처리 중인데 1개만 올라오고
                # 왜 실패했는지 모르겠다" 지적, 2026-08-19). 여기선 stage를
                # 안 넘겨서(이 논문은 끝났으니) 화면에서 자연히 사라진다 —
                # _write_progress가 매번 전체를 새로 쓰지 부분 갱신이 아니라서
                # 명시적으로 지울 필요 없이 그냥 안 넣으면 된다.
                _write_progress(
                    args.progress_file, total=len(targets), done=i + 1,
                    targets=targets, results=results,
                )
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
