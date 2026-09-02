"""run_profile_scan.py — Phase 1 오케스트레이터: 프로필 하나(또는 전체) →
오늘 새 논문 찾기 → 스코어링 → 다이제스트 저장.

의도적으로 아직 안 하는 것: 메일 발송(§6, `.env`에 SMTP 관련 키가 아예
없음을 확인함 — 2026-08-24, email_delivery.py는 준비돼 있지만 SMTP_HOST
등이 주석 처리된 채 대기 중), arXiv 외 다른 delta 소스(§3 리뷰에서 나온
대로 S2는 day-level delta에 못 씀).

2026-08-24: cron으로 무인 실행하기 시작하면서(scan_all_profiles, crontab
등록) 다이제스트를 review_app.py의 st.session_state(브라우저 세션 전용)가
아니라 research_profile.save_digest()로 DB에 남기도록 바꿨다 — cron이
새벽에 혼자 스캔을 돌려도 화면에서 볼 수 있어야 하기 때문("cron이 돌아도
결과가 어디에도 안 남는다" 문제).

기존 하네스 코어(server.py/review_app.py)는 안 건드린다 — find_new_papers/
profile_scoring/research_profile/digest 네 모듈을 여기서 엮기만 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import api_usage

import batch_summarize
import digest
import find_new_papers
import profile_scoring
import research_profile
import server


def _arxiv_query_from_core_topics(core_topics: list[str]) -> str:
    """프로필의 core_topics(OR 조건, 설계 문서 §1)를 arXiv 검색 쿼리로 조립.
    여러 단어 키워드는 따옴표로 묶어 구문 검색되게 한다 — 안 묶으면 arXiv가
    "digital"과 "twin"을 각각 독립된 단어로 봐서 무관한 논문까지 걸린다."""
    terms = [f'all:"{kw}"' if " " in kw else f"all:{kw}" for kw in core_topics]
    return " OR ".join(terms)


# Deep Layer(④⑤⑦)에 쓸 수 있는 벽시계 예산(초). 넘으면 남은 논문을 내일로
# 미룬다 — §8-14 의 처리다.
#
# 왜 "편수"가 아니라 "시간"인가: 실제 제약은 "새벽 배치가 아침 전에 끝나야
# 한다"이지 "몇 편을 처리한다"가 아니다. 그리고 편당 비용이 엔진에 따라
# 24배까지 벌어진다(§8-15 실측: Gemini 호출 1회·1분 내외 vs Groq 중앙값
# 24회·약 25분). 편수로 자르면 좋은 날엔 놀고 나쁜 날엔 여전히 밤을 샌다.
#
# 미뤄도 잃지 않는다는 게 전제다 — §8-26 수정으로 델타 커서가 최근 5일을
# 무조건 다시 보므로, 오늘 못 한 논문은 내일 후보에 그대로 다시 올라온다.
# 그 수정 전이었다면 이 방식은 논문을 영구히 버리는 것이었다.
#
# 2400초(40분)로 둔 이유: 05:00 시작 기준 아침까지 여유가 있고, Gemini 가
# 정상이면 편당 1분 내외라 40편도 소화한다(max_items 는 6이다). Groq 로
# 떨어진 날에만 실제로 걸리는 상한이다. 환경변수로 덮을 수 있다.
DEEP_LAYER_BUDGET_SECONDS = float(os.environ.get("DEEP_LAYER_BUDGET_SECONDS", 2400))


async def scan_profile(
    db_path: Path, profile_id: str, client: httpx.AsyncClient,
    page_size: int = 50, max_pages: int = 10,
) -> dict:
    """returns profile_scoring.score_and_rank()의 결과에 이번 실행 메타데이터
    (since/until/run_status/candidates_found)를 더한 것."""
    profile = research_profile.get_profile(db_path, profile_id)
    if profile is None:
        raise ValueError(f"프로필 '{profile_id}' 없음 — research_profile.create_profile로 먼저 만들 것")
    if not profile["core_topics"]:
        raise ValueError(f"프로필 '{profile_id}'에 core_topics가 없음 — 검색어를 만들 수 없음")

    # 키워드가 바뀌었으면 델타 커서를 이어받으면 안 된다(§8-21) — 지문을
    # 넘겨서 next_since 가 스스로 판단하게 한다.
    signature = research_profile.topic_signature(profile["core_topics"])
    since = research_profile.next_since(db_path, profile_id, signature=signature)
    query = _arxiv_query_from_core_topics(profile["core_topics"])

    try:
        result = await find_new_papers.find_new_papers_since(
            client, query, since, page_size=page_size, max_pages=max_pages,
        )
    except Exception as e:  # noqa: BLE001 — 실패도 search_runs에 남기고 다시 올린다
        research_profile.record_run(
            db_path, profile_id, "arxiv", query, since, datetime.now(timezone.utc),
            "failed", 0, error_detail=str(e), signature=signature,
        )
        raise

    until = datetime.fromisoformat(result["until"])
    research_profile.record_run(
        db_path, profile_id, "arxiv", result["query"], since, until,
        result["status"], len(result["papers"]), signature=signature,
    )

    # 이미 요약된 논문은 후보에서 뺀다(§8-26) — 창이 겹치므로 안 빼면 어제
    # 메일에 나간 논문이 오늘 또 나간다. 이 필터가 겹침의 비용을 0 으로 만든다.
    seen = _already_summarized([p.get("arxiv_id") for p in result["papers"]])
    fresh = [p for p in result["papers"] if p.get("arxiv_id") not in seen]

    scored = profile_scoring.score_and_rank(
        fresh, profile, top_k=profile["max_items"],
    )
    return {
        "profile_id": profile_id, "since": since.isoformat(), "until": result["until"],
        "run_status": result["status"], "candidates_found": len(fresh),
        "retrieved_count": len(result["papers"]), "already_seen_count": len(seen),
        **scored,
    }


def _already_summarized(arxiv_ids: list[str]) -> set[str]:
    """이미 ④⑤가 끝나 저장된 논문 id 들. 한 번의 질의로 받는다.

    왜 랭킹 **전에** 걸러야 하나(§8-26): 색인 지연 때문에 매 실행이 최근
    며칠을 다시 조회하게 됐는데(REINDEX_SAFETY_DAYS), 이미 요약한 논문을
    그대로 두면 어제 메일에 나간 논문이 오늘도 또 나간다. _summary_exists
    가 Deep Layer 재처리는 막지만, 그 논문은 result["papers"] 에 남아
    다이제스트에 실린다(deep_status="skipped: ...") — 중복 발송을 막으려면
    후보 목록에서 아예 빼야 한다.

    아직 요약 안 된 논문은 그대로 둔다 — 어제 7위였던 논문이 오늘 3위가
    되는 것은 정상이다. 상위권이 요약되어 빠지면서 뒤가 올라오는 구조라
    밀린 후보가 며칠에 걸쳐 소진된다.
    """
    ids = [a for a in arxiv_ids if a]
    if not ids:
        return set()
    found: set[str] = set()
    with server._db() as con:
        # SQLite 변수 상한(999)을 넘지 않게 나눠 묻는다.
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = con.execute(
                f"SELECT arxiv_id FROM summaries WHERE arxiv_id IN ({placeholders})", batch
            ).fetchall()
            found.update(r["arxiv_id"] for r in rows)
    return found


def _summary_exists(arxiv_id: str) -> bool:
    """이미 ④⑤가 끝나 저장된 논문인지 — Deep Layer 재처리(=중복 LLM API
    호출, 무료 한도를 그대로 태우는 낭비)를 막는 스킵 체크(M1, 2026-08-28).
    fetch_paper는 멱등이지만 _process_paper의 요약 단계는 무조건 재실행
    이라(재확인함) 호출 전에 여기서 걸러야 한다. summaries는 profiles
    db_path가 아니라 server.DB_PATH에 산다 — _process_paper가 server 경유로
    저장하는 곳이 거기라서(운영에선 둘이 같은 파일이지만 테스트에선 다름)."""
    with server._db() as con:
        row = con.execute(
            "SELECT 1 FROM summaries WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    return row is not None


async def scan_and_digest(
    db_path: Path, profile_id: str, client: httpx.AsyncClient,
    page_size: int = 50, max_pages: int = 10,
) -> tuple[dict, str]:
    """scan_profile() + Deep Layer(④⑤⑦) + digest 생성 + DB 저장까지 한 번에.
    review_app.py의 "지금 스캔 실행" 버튼과 cron 둘 다 이 함수 하나만
    부른다 — 다이제스트를 "만드는 곳"과 "저장하는 곳"이 갈라져 있으면
    한쪽 경로에서만 저장을 까먹는 사고가 나기 쉽다(⑥→⑦ 트리거를
    docker_runner.py 한 곳에 모은 것과 같은 이유).

    M1(2026-08-28): 스코어링 상위 논문 각각에 batch_summarize._process_paper
    를 **직렬로** 적용해 ④요약→⑤검증→⑦재현 트리거까지 잇는다. 직렬인
    이유: 무료 API 분당 한도(RPM/TPM)에 병렬은 자살행위고, S2 1req/s
    스로틀과 같은 철학이다. ⑦ 트리거는 _process_paper 내부가 소유하므로
    여기서 launch_background를 직접 부르지 않는다(CLAUDE.md 5). 한 편의
    실패가 나머지를 막지 않는다 — scan_all_profiles의 프로필 간 실패
    격리와 동일한 원칙. 결과는 논문 항목의 deep_status에 남는다:
    "ok" | "skipped: ..." | "failed: <사유 1줄>".
    """
    # §8-15: 실행 전체와 논문 한 편의 외부 API 호출 수를 실제로 센다.
    # 역산이 아니라 호출 지점에서 세는 값이다(api_usage 모듈 docstring 참고).
    run_scope = api_usage.Scope()
    run_scope.__enter__()

    result = await scan_profile(db_path, profile_id, client, page_size, max_pages)
    search_calls = run_scope.total()
    print(f"  [계측] ③ 검색 단계: {run_scope.format_summary()}")

    # Deep Layer — result["papers"]는 score_and_rank가 이미 top_k=max_items로
    # 잘라놓은 목록이라 별도 상한을 두지 않는다.
    deep_started = time.monotonic()
    deferred: list[dict] = []
    for paper in result["papers"]:
        arxiv_id = paper.get("arxiv_id")
        if not arxiv_id:
            paper["deep_status"] = "failed: arxiv_id 없음"
            continue
        if _summary_exists(arxiv_id):
            paper["deep_status"] = "skipped: 이미 요약 저장됨"
            continue
        # 예산은 **논문을 시작하기 전에** 본다. 처리 중간에 끊으면 요약을
        # 반쯤 만들고 버리게 되고, 그 호출은 이미 무료 한도를 쓴 뒤다.
        elapsed = time.monotonic() - deep_started
        if elapsed > DEEP_LAYER_BUDGET_SECONDS:
            paper["deep_status"] = "deferred: 시간 예산 초과"
            deferred.append(paper)
            print(f"  [예산] {arxiv_id} 이후를 내일로 미룸 "
                  f"({elapsed / 60:.0f}분 경과 > {DEEP_LAYER_BUDGET_SECONDS / 60:.0f}분)")
            continue
        paper_scope = api_usage.Scope()
        try:
            with paper_scope:
                outcome = await batch_summarize._process_paper(client, arxiv_id)
        except Exception as e:  # noqa: BLE001 — 한 편의 실패가 나머지를 막으면 안 됨
            paper["deep_status"] = f"failed: {str(e).splitlines()[0][:200]}"
            paper["api_calls"] = paper_scope.snapshot()
            print(f"  [계측] {arxiv_id} (실패): {paper_scope.format_summary()}")
            continue
        # fetch 실패는 예외가 아니라 status="fetch_failed" dict로 온다(재확인함)
        if outcome.get("status") == "done":
            paper["deep_status"] = "ok"
        else:
            paper["deep_status"] = f"failed: {str(outcome.get('detail'))[:200]}"
        paper["api_calls"] = paper_scope.snapshot()
        print(f"  [계측] {arxiv_id}: {paper_scope.format_summary()}")

    # S2 tldr(M6) — Deep 처리가 실패한 논문은 우리 요약이 없어 초록 발췌만
    # 남는데, S2 의 한 줄 요약이 그보다 읽기 낫다. 배치 1회라 호출 비용이
    # 사실상 없다. 네트워크는 여기서만 타고 digest.py 는 순수하게 유지한다
    # (다이제스트 생성이 메일 발송 직전에 네트워크를 기다리면 안 된다).
    failed = [p for p in result["papers"] if str(p.get("deep_status", "")).startswith("failed")]
    if failed:
        tldrs = await server.fetch_s2_tldrs(client, [p.get("arxiv_id") for p in failed])
        for paper in failed:
            text = tldrs.get(paper.get("arxiv_id"))
            if text:
                paper["s2_tldr"] = text

    # 미룬 논문은 다이제스트 목록에서 뺀다. 요약이 없어 보여줄 내용이 없고,
    # 내일 다시 후보로 올라와 그때 제대로 실린다 — 오늘 제목만 내보내면
    # 같은 논문이 이틀 연속 나가게 된다. 대신 건수는 정직하게 보고한다.
    if deferred:
        result["papers"] = [p for p in result["papers"] if p not in deferred]
        result["deferred_count"] = len(deferred)

    profile = research_profile.get_profile(db_path, profile_id)
    digest_text = digest.generate_digest(result, profile["name"] if profile else profile_id)
    research_profile.save_digest(db_path, profile_id, digest_text)

    run_scope.__exit__(None, None, None)
    result["api_calls"] = run_scope.snapshot()
    result["api_calls_total"] = run_scope.total()
    deep_calls = run_scope.total() - search_calls
    print(f"  [계측] 실행 합계: {run_scope.format_summary()}")
    print(f"  [계측]   └ ③ 검색 {search_calls}회 + Deep Layer {deep_calls}회 "
          f"(⑦ 재현은 별도 프로세스라 여기 안 잡힌다 — 재현 로그를 따로 볼 것)")
    return result, digest_text


def _deliver(db_path: Path, profile_id: str, result: dict, digest_text: str) -> str:
    """다이제스트를 그 프로필의 수신자에게 보낸다. returns 사람이 읽을 상태 한 줄.

    **논문이 0편이어도 보낸다**(M8, 2026-08-28). 매일 오는 메일 자체가
    "파이프라인이 살아 있다"는 증거라서다 — healthchecks.io 같은 외부
    dead-man's switch 를 안 붙인 지금, 이게 그 역할을 대신한다. 메일이 안 온
    날은 "새 논문이 없었다"가 아니라 "무언가 고장났다"로 읽어야 한다
    (docs/TRIAL_CHECKLIST.md 의 (a) 항목이 이 전제 위에 서 있다).

    발송 실패를 예외로 올리지 않는다 — 한 프로필의 SMTP 실패가 나머지
    프로필의 스캔·발송을 막으면 안 된다.
    """
    import email_delivery

    recipients = research_profile.get_recipients(db_path, profile_id)
    if not recipients:
        return "수신자 없음 — 발송 안 함"
    profile = research_profile.get_profile(db_path, profile_id)
    name = profile["name"] if profile else profile_id
    try:
        digest_html = digest.generate_digest_html(result, name)
        email_delivery.send_digest_email(
            digest_text, f"[HARNESS Daily] {name}", recipients, digest_html,
        )
    except Exception as e:  # noqa: BLE001
        return f"발송 실패: {str(e).splitlines()[0][:200]}"
    return f"발송 완료 → {len(recipients)}명"


async def scan_all_profiles(
    db_path: Path, client: httpx.AsyncClient, max_pages: int = 10,
    send: bool = False,
) -> dict[str, dict]:
    """활성 프로필 전체를 순서대로 스캔한다 — cron이 부르는 진입점(설계
    문서 §2 "Scheduler"). 프로필 하나가 실패해도(예: 그 프로필만 core_topics
    없음, 혹은 그 시점 arXiv 장애) 나머지 프로필은 계속 처리한다 — 프로필
    간에 실패가 전파되면 안 된다는 게 이 함수의 핵심 설계 결정.

    send=True 면 프로필마다 그 프로필의 수신자에게 다이제스트를 보낸다
    (M8). 발송 결과도 summary 에 남는다 — cron 로그만 보고 "메일이 나갔나"를
    알 수 있어야 한다.

    returns {profile_id: {"status": "ok"|"error", ...}} — cron 로그에서
    무슨 일이 있었는지 한눈에 보이는 형태."""
    summary: dict[str, dict] = {}
    for profile_id in research_profile.list_profiles(db_path):
        try:
            result, digest_text = await scan_and_digest(db_path, profile_id, client, max_pages=max_pages)
            entry = {
                "status": "ok", "run_status": result["run_status"],
                "candidates_found": result["candidates_found"],
                "scored_count": result["scored_count"],
            }
            if send:
                entry["delivery"] = _deliver(db_path, profile_id, result, digest_text)
            summary[profile_id] = entry
        except Exception as e:  # noqa: BLE001 — 한 프로필의 실패가 나머지를 막으면 안 됨
            summary[profile_id] = {"status": "error", "detail": str(e)}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="프로필(들)로 delta 검색 + 스코어링 + 다이제스트를 돌린다")
    parser.add_argument("profile_id", nargs="?", help="생략하고 --all을 주면 전체 프로필 순회")
    parser.add_argument("--all", action="store_true", help="등록된 프로필 전체를 순회(cron이 쓰는 모드)")
    parser.add_argument("--db", default=None, help="대상 SQLite DB 경로 (기본: server.DB_PATH)")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="다이제스트 대신 원본 결과를 JSON으로 출력")
    parser.add_argument("--send", action="store_true",
                         help="다이제스트를 프로필 수신자에게 이메일로 발송(--all 과도 함께 쓸 수 있다)")
    args = parser.parse_args()
    if not args.all and not args.profile_id:
        parser.error("profile_id를 주거나 --all을 지정할 것")

    db_path = Path(args.db) if args.db else server.DB_PATH

    if args.all:
        async def _run_all() -> dict:
            async with httpx.AsyncClient() as client:
                return await scan_all_profiles(
                    db_path, client, max_pages=args.max_pages, send=args.send,
                )

        summary = asyncio.run(_run_all())
        # cron 로그(crontab 리다이렉트)에 그대로 남는 출력 — 사람이 나중에
        # 로그 파일만 보고도 그날 무슨 일이 있었는지 알 수 있어야 한다.
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    async def _run() -> tuple[dict, str]:
        async with httpx.AsyncClient() as client:
            return await scan_and_digest(db_path, args.profile_id, client, max_pages=args.max_pages)

    result, digest_text = asyncio.run(_run())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(digest_text)

    if args.send:
        # --all 경로와 **같은 함수**를 쓴다(M8) — 발송 로직이 두 벌이면 한쪽만
        # 고치고 다른 쪽을 놓치는 사고가 난다(⑦ 트리거를 docker_runner.py 한
        # 곳에 모은 것과 같은 이유).
        print(_deliver(db_path, args.profile_id, result, digest_text))


if __name__ == "__main__":
    main()
