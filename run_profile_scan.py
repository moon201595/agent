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
from datetime import datetime, timezone
from pathlib import Path

import httpx

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

    since = research_profile.next_since(db_path, profile_id)
    query = _arxiv_query_from_core_topics(profile["core_topics"])

    try:
        result = await find_new_papers.find_new_papers_since(
            client, query, since, page_size=page_size, max_pages=max_pages,
        )
    except Exception as e:  # noqa: BLE001 — 실패도 search_runs에 남기고 다시 올린다
        research_profile.record_run(
            db_path, profile_id, "arxiv", query, since, datetime.now(timezone.utc),
            "failed", 0, error_detail=str(e),
        )
        raise

    until = datetime.fromisoformat(result["until"])
    research_profile.record_run(
        db_path, profile_id, "arxiv", result["query"], since, until,
        result["status"], len(result["papers"]),
    )

    scored = profile_scoring.score_and_rank(
        result["papers"], profile, top_k=profile["max_items"],
    )
    return {
        "profile_id": profile_id, "since": since.isoformat(), "until": result["until"],
        "run_status": result["status"], "candidates_found": len(result["papers"]),
        **scored,
    }


async def scan_and_digest(
    db_path: Path, profile_id: str, client: httpx.AsyncClient,
    page_size: int = 50, max_pages: int = 10,
) -> tuple[dict, str]:
    """scan_profile() + digest 생성 + DB 저장까지 한 번에. review_app.py의
    "지금 스캔 실행" 버튼과 cron 둘 다 이 함수 하나만 부른다 — 다이제스트를
    "만드는 곳"과 "저장하는 곳"이 갈라져 있으면 한쪽 경로에서만 저장을
    까먹는 사고가 나기 쉽다(⑥→⑦ 트리거를 docker_runner.py 한 곳에 모은
    것과 같은 이유)."""
    result = await scan_profile(db_path, profile_id, client, page_size, max_pages)
    profile = research_profile.get_profile(db_path, profile_id)
    digest_text = digest.generate_digest(result, profile["name"] if profile else profile_id)
    research_profile.save_digest(db_path, profile_id, digest_text)
    return result, digest_text


async def scan_all_profiles(
    db_path: Path, client: httpx.AsyncClient, max_pages: int = 10,
) -> dict[str, dict]:
    """활성 프로필 전체를 순서대로 스캔한다 — cron이 부르는 진입점(설계
    문서 §2 "Scheduler"). 프로필 하나가 실패해도(예: 그 프로필만 core_topics
    없음, 혹은 그 시점 arXiv 장애) 나머지 프로필은 계속 처리한다 — 프로필
    간에 실패가 전파되면 안 된다는 게 이 함수의 핵심 설계 결정.

    returns {profile_id: {"status": "ok"|"error", ...}} — cron 로그에서
    무슨 일이 있었는지 한눈에 보이는 형태."""
    summary: dict[str, dict] = {}
    for profile_id in research_profile.list_profiles(db_path):
        try:
            result, _digest_text = await scan_and_digest(db_path, profile_id, client, max_pages=max_pages)
            summary[profile_id] = {
                "status": "ok", "run_status": result["run_status"],
                "candidates_found": result["candidates_found"],
                "scored_count": result["scored_count"],
            }
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
                         help="다이제스트를 이메일로 발송 — SMTP 미설정 상태라 지금은 항상 실패함(의도됨)")
    args = parser.parse_args()
    if not args.all and not args.profile_id:
        parser.error("profile_id를 주거나 --all을 지정할 것")

    db_path = Path(args.db) if args.db else server.DB_PATH

    if args.all:
        async def _run_all() -> dict:
            async with httpx.AsyncClient() as client:
                return await scan_all_profiles(db_path, client, max_pages=args.max_pages)

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
        import email_delivery
        profile = research_profile.get_profile(db_path, args.profile_id)
        recipients = research_profile.get_recipients(db_path, args.profile_id)
        subject = f"[HARNESS Daily] {profile['name'] if profile else args.profile_id}"
        email_delivery.send_digest_email(digest_text, subject, recipients)  # 지금은 항상 NotImplementedError


if __name__ == "__main__":
    main()
