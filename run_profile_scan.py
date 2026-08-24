"""run_profile_scan.py — Phase 1 오케스트레이터 최소형: 프로필 하나 →
오늘 새 논문 찾기 → 스코어링된 목록.

의도적으로 아직 안 하는 것: Digest 문서 생성, 메일 발송(§6, `.env`에
SMTP 관련 키가 아예 없음을 확인함 — 2026-08-24), 자동 스케줄 실행(cron은
사람이 따로 건다), arXiv 외 다른 delta 소스(§3 리뷰에서 나온 대로 S2는
day-level delta에 못 씀). 이 스크립트는 "한 번 실행하면 실제로 무슨 일이
일어나는지"를 그대로 보여주는 최소 단위 — search_runs에 결과를 남기는
것까지가 책임 범위고, 그 뒤(사람에게 보여주기·전달하기)는 다음 단계다.

기존 하네스 코어(server.py/review_app.py)는 안 건드린다 — find_new_papers/
profile_scoring/research_profile 세 모듈을 여기서 엮기만 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

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


def main() -> None:
    import digest
    import research_profile

    parser = argparse.ArgumentParser(description="프로필 하나로 delta 검색 + 스코어링 + 다이제스트를 한 번 돌린다")
    parser.add_argument("profile_id")
    parser.add_argument("--db", default=None, help="대상 SQLite DB 경로 (기본: server.DB_PATH)")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="다이제스트 대신 원본 결과를 JSON으로 출력")
    parser.add_argument("--send", action="store_true",
                         help="다이제스트를 이메일로 발송 — SMTP 미설정 상태라 지금은 항상 실패함(의도됨)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else server.DB_PATH

    async def _run() -> dict:
        async with httpx.AsyncClient() as client:
            return await scan_profile(db_path, args.profile_id, client, max_pages=args.max_pages)

    result = asyncio.run(_run())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    profile = research_profile.get_profile(db_path, args.profile_id)
    digest_text = digest.generate_digest(result, profile["name"] if profile else args.profile_id)
    print(digest_text)

    if args.send:
        import email_delivery
        recipients = research_profile.get_recipients(db_path, args.profile_id)
        subject = f"[HARNESS Daily] {profile['name'] if profile else args.profile_id}"
        email_delivery.send_digest_email(digest_text, subject, recipients)  # 지금은 항상 NotImplementedError


if __name__ == "__main__":
    main()
