"""Phase 0 스모크 테스트 — MCP 도구의 내부 함수를 직접 호출해 실동작을 확인한다.

검증 항목:
  1. arXiv 검색이 실제 결과를 반환하는가
  2. 논문(1706.03762, Attention Is All You Need) PDF 다운로드·파싱·저장이 되는가
  3. 수치 검증기가 원문에 있는 숫자(28.4)는 통과, 없는 숫자(99.87)는 잡아내는가
  4. save_summary 라운드트립과 list_stored_papers가 동작하는가
"""

import asyncio
import json

import server


async def main() -> None:
    failures: list[str] = []

    r1 = json.loads(await server.arxiv_search_papers(
        server.ArxivSearchInput(query="transformer attention", category="cs.CL", max_results=3)
    ))
    print(f"[1] arxiv_search: count={r1.get('count')} "
          f"first={r1['papers'][0]['arxiv_id'] if r1.get('papers') else None}")
    if r1.get("count", 0) < 1:
        failures.append("arXiv 검색 결과 0건")

    # ② 중복 제거·선별 — 네트워크 없이 도는 결정적 규칙.
    # arXiv 결과(인용수 없음)와 S2 형태 레코드(인용수 있음)를 섞어 넣어
    # 제목으로 합쳐지고 인용수가 실리는지 본다.
    mixed = list(r1.get("papers", [])) + [
        {"arxiv_id": None, "title": r1["papers"][0]["title"], "citation_count": 99999,
         "year": 2017},
    ]
    r1b = json.loads(await server.dedupe_and_rank_papers(
        server.SelectPapersInput(papers=mixed, top_k=2)
    ))
    print(f"[2] dedupe_and_rank: {r1b['input_count']}건 → 중복제거 {r1b['deduped_count']}건 "
          f"→ 선별 {r1b['selected_count']}건, 1위 인용수={r1b['papers'][0].get('citation_count')}")
    if r1b["deduped_count"] != len(r1.get("papers", [])):
        failures.append(f"제목이 같은 레코드가 합쳐지지 않았음: {r1b['deduped_count']}")
    if r1b["papers"][0].get("citation_count") != 99999:
        failures.append("인용수가 랭킹에 반영되지 않았음")

    # ③ 원문 파싱 — 논문 두 편으로 HTML 경로와 PDF 폴백을 모두 태운다.
    # 어느 논문이 어느 경로를 타는지는 단정하지 않는다: arXiv HTML 제공 여부는
    # 투고 시점으로 예측되지 않는다 (실측 2026-07: 2017년 1706.03762 는 HTML 이
    # 있고 2024년 2405.15793 은 없다). 그래서 '경로가 유효한가'와
    # '두 경로가 다 커버되는가'만 본다.
    observed: dict[str, str] = {}
    for label, paper_id in (("[3]", "1706.03762"), ("[4]", "2405.15793")):
        res = json.loads(await server.fetch_paper(server.FetchPaperInput(arxiv_id=paper_id)))
        method = res.get("extract_method")
        chars = res.get("text_chars")
        print(f"{label} fetch_paper({paper_id}): title='{str(res.get('title'))[:34]}...' "
              f"chars={chars} method={method}")
        if not chars or chars < 10000:
            failures.append(f"{paper_id} 본문 추출 실패 또는 과소: {chars}")
        if method not in ("html", "pdf"):
            failures.append(f"{paper_id} 추출 경로가 유효하지 않음: {method!r}")
        else:
            observed[paper_id] = method

    if "html" not in observed.values():
        failures.append(f"HTML 경로가 한 번도 실행되지 않았음: {observed}")
    if "pdf" not in observed.values():
        failures.append(f"PDF 폴백이 한 번도 실행되지 않았음: {observed}")

    summary = (
        "① Transformer는 어텐션만으로 시퀀스 변환을 수행한다. "
        "④ WMT14 EN-DE에서 BLEU 28.4를 달성했고, 가짜 수치 99.87도 넣어 본다."
    )
    r3 = json.loads(await server.verify_summary_numbers(
        server.VerifyInput(arxiv_id="1706.03762", summary_text=summary)
    ))
    print(f"[5] verify: total={r3['total_numbers']} matched={r3['matched']} "
          f"unmatched={[u['token'] for u in r3['unmatched']]}")
    unmatched_tokens = {u["token"] for u in r3["unmatched"]}
    if "99.87" not in unmatched_tokens:
        failures.append("가짜 수치 99.87을 잡아내지 못함")
    if "28.4" in unmatched_tokens:
        failures.append("실제 수치 28.4를 불일치로 오판")

    r4 = json.loads(await server.save_summary(
        server.SaveSummaryInput(arxiv_id="1706.03762", markdown=summary)
    ))
    print(f"[6] save_summary: path={r4.get('saved_path')} "
          f"pass_ratio={r4['verification']['pass_ratio']}")

    r5 = json.loads(await server.list_stored_papers(server.ListPapersInput()))
    methods = {p["arxiv_id"]: p.get("extract_method") for p in r5["papers"]}
    print(f"[7] list_stored_papers: count={r5['count']} 추출경로={methods}")
    if r5["count"] < 2:
        failures.append("저장 목록이 2건 미만")

    if failures:
        print("\nSMOKE FAILED:")
        for f in failures:
            print(" -", f)
        raise SystemExit(1)
    print("\nSMOKE OK — 7/7 통과")


if __name__ == "__main__":
    asyncio.run(main())
