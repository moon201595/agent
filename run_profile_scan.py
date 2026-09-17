"""run_profile_scan.py — 일일 진입점(①~⑨ 조정). 프로필 하나(또는 전체) → 반응 가중치 갱신 → 검색(arXiv·S2) → 선별 →
본문·요약·검증(batch_summarize, ⑦ 재현은 백그라운드) → 동향·근거·코드 사다리·철회 → 다이제스트 저장 → **메일 발송·발송 대장** → 주간 후속.

2026-09-16 정정(Codex 구조 검토): 이 docstring 은 "메일은 아직 안 보낸다, 네 모듈만 엮는다"(2026-08-24)고 적혀 있었지만 실제 cron 은
`run_daily_scan.sh` 가 `--all --send` 로 부르고 여기서 발송까지 한다. 지금 이 파일이 조정하는 모듈은 find_new_papers·s2_delta·profile_scoring·
research_profile·batch_summarize·trend_report·evidence_state·code_ladder·retraction·digest·email_delivery·mail_ledger·feedback_weights 다 —
1,300줄이 넘고 책임이 섞여 있어 단계별 서비스 분할이 "나중 과제"로 기록돼 있다(PROGRESS §8-150).

2026-08-24: cron 으로 무인 실행하기 시작하면서 다이제스트를 st.session_state 가 아니라 research_profile.save_digest() 로 DB 에 남긴다 —
cron 이 새벽에 혼자 돌려도 화면에서 볼 수 있어야 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import api_usage

import batch_summarize
import digest
import find_new_papers
import profile_scoring
import s2_delta
import selection
import research_profile
import trend_report
import storage
import server


def _arxiv_query_from_core_topics(core_topics: list[str]) -> str:
    """프로필의 core_topics(OR 조건, 설계 문서 §1)를 arXiv 검색 쿼리로 조립.
    여러 단어 키워드는 따옴표로 묶어 구문 검색되게 한다 — 안 묶으면 arXiv가
    "digital"과 "twin"을 각각 독립된 단어로 봐서 무관한 논문까지 걸린다."""
    # 키워드 안의 따옴표는 arXiv 질의 문법을 깬다(Codex 검토 2026-09-16) — 빼고 보낸다(매처는 원문 그대로 채점한다).
    clean = [kw.replace('"', "").strip() for kw in core_topics]
    terms = [f'all:"{kw}"' if " " in kw else f"all:{kw}" for kw in clean if kw]
    return " OR ".join(terms)


# arXiv 질의 하나에 넣는 키워드 상한. 2026-09-16 실측: 키워드 47개를 OR 로 묶은 질의 하나는 응답에 36초가 걸리고 503·500·
# 타임아웃으로 죽는데, 같은 키워드를 24·23개로 갈라 두 번 던지면 각각 10초·15초에 200 이었다. arXiv 는 OR 항 수에 비례해 느려지고
# 30여 초에서 서버가 포기한다. 그날 우리팀 프로필의 arXiv 검색이 통째로 실패해 S2 만으로 갔다. 환경변수로 덮을 수 있다.
ARXIV_TERMS_PER_QUERY = int(os.environ.get("ARXIV_TERMS_PER_QUERY", 20))


def _arxiv_queries_from_core_topics(core_topics: list[str], per_query: int = ARXIV_TERMS_PER_QUERY) -> list[str]:
    """core_topics 를 per_query 개 이하씩 **고르게** 갈라 질의 목록으로. 20개 이하면 종전처럼 하나다.
    고르게 가르는 이유: 47개를 20·20·7 로 자르면 앞 둘은 여전히 무겁다 — 16·16·15 가 낫다."""
    topics = [t for t in core_topics if t and t.strip()]
    if not topics:
        return []
    n_chunks = max(1, -(-len(topics) // max(1, per_query)))
    size = -(-len(topics) // n_chunks)
    return [_arxiv_query_from_core_topics(topics[i:i + size]) for i in range(0, len(topics), size)]


async def _search_arxiv_chunked(client: httpx.AsyncClient, queries: list[str], since: datetime,
                                page_size: int, max_pages: int) -> dict:
    """질의 여러 개를 차례로 던져 하나의 결과로 합친다. 같은 논문이 두 질의에 걸리면 한 번만 남긴다.
    status: 전부 done 이면 done, 하나라도 partial 이거나 실패했으면 partial(받은 것은 살리고 커서는 window_from 에 남아 내일 다시 본다),
    전부 실패하면 예외 — 호출부의 기존 'arXiv 실패 → S2 만으로' 경로를 그대로 탄다."""
    papers: list[dict] = []
    statuses: list[str] = []
    untils: list[str] = []
    errors: list[str] = []
    for q in queries:
        try:
            r = await find_new_papers.find_new_papers_since(client, q, since, page_size=page_size, max_pages=max_pages)
        except Exception as e:  # noqa: BLE001 — 질의 하나가 죽어도 나머지는 던진다
            errors.append(f"{type(e).__name__}: {str(e).splitlines()[0][:120]}")
            continue
        papers += r["papers"]
        statuses.append(r["status"])
        untils.append(r["until"])
    if not statuses:
        raise RuntimeError(f"arXiv 질의 {len(queries)}개 전부 실패 — " + " / ".join(errors))
    seen: set[str] = set()
    uniq: list[dict] = []
    for p in papers:
        key = p.get("arxiv_id") or (p.get("title") or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    status = "done" if not errors and all(s == "done" for s in statuses) else "partial"
    return {"papers": uniq, "status": status, "until": min(untils), "query": " ‖ ".join(queries),
            "chunks": len(queries), "chunk_errors": errors}


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

# 주간 동향 리뷰를 붙이는 요일(0=월). 매일 붙이면 어제와 거의 같은 표가
# 반복돼 읽히지 않고, 인용망 조회 비용도 매일 낼 이유가 없다.
WEEKLY_REVIEW_WEEKDAY = 0
# 읽는 사람의 시간대. §8-71 은 "읽는 사람의 요일"이 맞았지만 구현이 `astimezone()`
# (= 이 컴퓨터의 시간대)여서 UTC 컨테이너에서는 같은 순간이 일요일이 됐다
# (§8-93 ①, 2026-09-11 외부 검증). 컴퓨터가 어디 있든 답이 같아야 한다.
from time_policy import KST as READER_TZ   # 2026-09-17: 시각 정책 통합

# 상위 목록에서 **본문을 받을 수 있는 논문에 최소한 보장할 자리 수**(2026-09-05).
#
# 실측이 두 번 겹쳐 나온 결론이다.
#
# (1) **저널 본문을 받을 무료 경로가 없다.** 어제 걸린 저널 14편으로 쟀다:
#     arXiv preprint 0/14 · OpenAlex 오픈액세스 위치 1/14(그마저 봇 차단).
#     Measurement·Displays·Applied Soft Computing·Solar Energy 같은 산업공학
#     저널은 preprint 문화가 없다. **이건 우리가 고칠 수 있는 문제가 아니다.**
#     (검색 방법부터 검증했다 — 처음엔 하이픈 때문에 arXiv 에 있는 논문도
#     못 찾아서 2/3 이었다. 고친 뒤에도 0/14 다.)
#
# (2) **그런데 arXiv 에 관련 논문이 있는데 순위 밖으로 밀린다.** 같은 창의
#     arXiv 델타 193편을 채점하니 `Multi-View Reflective Surface Inspection`
#     (★★, 반사 금속 표면 검사 — 팀 표적 그 자체)과 `FuDU`(★★, 결함 검출)가
#     6~7위였다. 저널 논문이 표적 키워드를 **두 개씩** 맞혀서 위를 다 차지한다.
#
# 그 결과 09-04 메일은 상위 14편이 전부 저널이었고 **검증 라벨 0건·재현 라벨
# 0건**이었다 — ④⑤⑦ 이 통째로 안 돈 것이다.
#
# **§8-33 의 실수를 반복하지 않는다.** 그때는 "본문 확보 가능"을 관련도보다
# **앞에** 놓아서 ★★★ 팀 표적 논문이 맨 아래로 밀렸다(§8-44 로 되돌림).
# 여기서는 순서를 바꾸지 않는다 — **자리 몇 개를 보장**할 뿐이고, 최종 목록은
# 다시 관련도로 정렬한다. "가장 관련 있는 것"과 "깊이 볼 수 있는 것"을
# 맞바꾸지 않고 둘 다 넣는다.
#
# arxiv_id 만 본다. `open_access_pdf` 는 있어도 실제로는 HTML 이 오는 경우가
# 대부분이라(§8-41 실측 5편 중 5편 실패) 보장의 근거가 못 된다.
FULL_TEXT_RESERVED = 2

# 본문을 못 받는(arXiv ID 도 오픈액세스 PDF 도 없는) 논문을 다이제스트에 몇 편까지
# 목록으로 보여줄지. 요약이 없으니 한 줄씩만 차지한다 — 넉넉해도 메일이 길어지지 않는다.
TITLE_ONLY_MAX_ITEMS = 8


def is_weekly_review_day(now: datetime | None = None) -> bool:
    """오늘이 주간 리뷰를 붙이는 날인가. 함수로 뺀 이유는 테스트가
    이것만 바꿀 수 있게 하기 위해서다 — datetime.now 전체를 갈아끼우면
    record_run 등 다른 시각 사용까지 깨진다(실제로 한 번 깨뜨렸다)."""
    # **읽는 사람의 요일로 센다**(2026-09-08, §8-71). 그전에는 UTC 였는데
    # 배달은 05:00 KST = **전날 20:00 UTC** 에 일어난다. 그래서 의도한 월요일
    # 아침 메일에는 안 붙고(그때 UTC 로는 일요일) **화요일 아침 메일에 붙었다.**
    # 여태 아무도 못 본 이유는 §8-70 ① 때문이다 — 주간 리뷰가 HTML 메일에
    # 아예 닿지 않아서 요일이 어긋난 것도 드러나지 않았다.
    #
    # 요일은 읽는 사람 기준이고, 읽는 사람은 KST 로 산다 — `READER_TZ` 로 명시
    # 변환한다. 인자 없는 `astimezone()` 은 **이 컴퓨터**의 시간대라 KST 머신에서만
    # 우연히 맞았다(§8-93 ①). naive 값이 오면 이 컴퓨터 시각으로 본다.
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(READER_TZ).weekday() == WEEKLY_REVIEW_WEEKDAY


def _primary_keyword(paper: dict) -> str:
    """이 논문을 대표하는 핵심 키워드 — 가장 무거운 적중.

    판정은 `profile_scoring.score_paper` 가 한다(가중치를 아는 곳이 거기뿐이다).
    여기서는 읽기만 한다.

    **처음엔 여기서 `hits[0]` 로 때웠고 그건 틀렸다**(2026-09-06 지적).
    `core_hits` 는 가중치 순이 아니라 `core_topics` 순이라, 표적어를 맞힌
    논문이 동향어 이름으로 세어졌다 — `["embodied AI", "defect detection"]`
    이면 embodied AI 로 잡혀 `defect detection` 의 자리 상한이 안 깎인다.
    자리 상한의 목적이 "수확량 큰 키워드가 독식하지 못하게"인데, 그 키워드가
    안 세어지면 목적이 빈다. **문서가 코드보다 강한 주장을 하고 있었다.**

    `primary_hit` 이 없는 구형·수기 `_score` 는 `core_hits` 첫 번째로 떨어진다
    (하위 호환 — 그 경우 정확히 옛 동작이다).
    """
    score = paper.get("_score") or {}
    primary = score.get("primary_hit")
    if primary is not None:
        return primary
    hits = score.get("core_hits") or []
    return hits[0] if hits else ""


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
    #
    # **지문은 소스마다 다르다**(2026-09-09, §8-79). 각 소스는 자기가 실제로
    # 던진 질의로만 과거를 봤으므로, "질의가 바뀌었나"도 그 질의 기준으로
    # 물어야 한다. arXiv 는 core_topics 전부를 OR 로 묶으니 core 지문이고,
    # S2 는 시드만 던지니 시드 지문이다. 하나로 합쳐 두면 **시드를 바꿔도
    # S2 커서가 리셋되지 않아 새 시드가 과거를 영영 못 본다** — §8-21 이
    # core 에서 막았던 사고가 시드에서 그대로 재발한다.
    signature = research_profile.topic_signature(profile["core_topics"])

    # **두 소스 중 더 뒤처진 쪽에 창을 맞춘다**(2026-09-08, §8-76).
    #
    # 그전에는 `next_since(db, pid, signature=...)` 하나였고 `source` 기본값이
    # "arxiv" 라, **S2 가 partial·failed 로 끝나도 창은 arXiv 기준으로 전진했다.**
    # S2 가 못 본 구간이 어디에도 남지 않는다. 지금까지는 5일 안전 창
    # (REINDEX_SAFETY_DAYS)이 최근 구간을 다시 훑어 우연히 덮어 줬을 뿐이고,
    # S2 장애가 5일보다 길어지면 그 구간의 논문은 실제로 사라진다.
    #
    # 비용을 먼저 쟀다(2026-09-08, 과거 search_runs 재생): S2 이력이 있는
    # 구간에서 실제 창 증가는 2회·최대 +8.2시간이었다. **다만 이 값은 S2 이력이
    # 있는 경우만 잰 것이다**(외부 검토 지적) — 이력이 없으면 next_since 가
    # 7일 규칙으로 떨어져 더 길어질 수 있다. 자세한 것은 PROGRESS §8-78.
    #
    # min 을 쓰는 이유: 커서는 "여기까지는 봤다"는 뜻이므로 **덜 본 쪽**을
    # 따라가야 못 본 구간이 안 생긴다.
    #
    # **다만 이번 실행에서 실제로 질의할 소스만 센다**(2026-09-08, §8-78 ①).
    # S2 커서를 무조건 합치면, 가중치를 낮춰 S2 를 끄거나 키워드가 비어
    # 건너뛰는 프로필에서 **옛 S2 커서가 영원히 남아 arXiv 창을 끈다** —
    # 그 소스는 앞으로 갱신되지 않으므로 커서가 늙기만 한다. 검토의 재현에서
    # 30일 전 S2 이력이 정상 arXiv 창을 30일로 늘렸다.
    s2_keywords = s2_delta.keywords_for_s2(profile)
    s2_signature = research_profile.topic_signature(s2_keywords)
    # **스캔 단위 실행 기록**(2026-09-11, B단계 §6.1). search_runs 는 출처마다
    # 행 하나라 "이 스캔"을 가리키는 ID 가 없었다. 프로필 스냅샷·정책 버전을
    # 여기 박아 두면 지문만으로 복원 못 하는 가중치·제외어·K 까지 남는다.
    scan_id = research_profile.begin_scan(db_path, profile_id, profile)
    s2_run_id = None
    signatures = {"arxiv": signature, "s2": s2_signature}
    sources = ["arxiv"] + (["s2"] if s2_keywords else [])
    since = min(
        research_profile.next_since(db_path, profile_id, src, signature=signatures[src])
        for src in sources
    )
    queries = _arxiv_queries_from_core_topics(profile["core_topics"])
    query = " ‖ ".join(queries)          # search_runs.query 에 남기는 표기 — 질의가 여러 개면 ‖ 로 잇는다

    # **한 소스가 죽어도 그날을 통째로 버리지 않는다**(2026-09-06 에 실제로
    # 그렇게 만들었다). 바로 아래 S2 절의 주석이 원래부터 그렇게 적혀 있었는데
    # **코드는 그렇게 안 돼 있었다** — arXiv 실패는 여기서 `raise` 로 나갔고
    # S2 쪽은 try/except 가 아예 없었다. 실측으로 확인했다: arXiv 가 429 를
    # 4회 재시도 끝에 포기하자 스캔 전체가 죽고 메일이 안 나갔다.
    #
    # 메일이 안 오는 날은 "새 논문이 없었다"가 아니라 "무언가 고장났다"로
    # 읽어야 한다(_deliver 주석). 그 신호를 일시적인 429 에 태우면 신호가
    # 못 쓰게 된다 — 진짜 고장과 구분이 안 된다.
    arxiv_papers: list[dict] = []
    arxiv_status = "failed"
    until = datetime.now(timezone.utc)
    arxiv_error: str | None = None
    try:
        result = await _search_arxiv_chunked(client, queries, since, page_size, max_pages)
    except Exception as e:  # noqa: BLE001 — 실패도 search_runs 에 남기고 계속 간다
        arxiv_error = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
        arxiv_run_id = research_profile.record_run(
            db_path, profile_id, "arxiv", query, since, until,
            "failed", 0, error_detail=str(e), signature=signature,
        )
        print(f"  [경고] arXiv 검색 실패 — S2 만으로 이어간다: {arxiv_error}")
    else:
        arxiv_papers = result["papers"]
        arxiv_status = result["status"]
        until = datetime.fromisoformat(result["until"])
        arxiv_run_id = research_profile.record_run(
            db_path, profile_id, "arxiv", result["query"], since, until,
            arxiv_status, len(arxiv_papers), signature=signature,
            error_detail=(" / ".join(result["chunk_errors"]) or None),
        )
        if result["chunks"] > 1:
            print(f"  [arXiv] 질의 {result['chunks']}개(키워드 {len(profile['core_topics'])}개) → {len(arxiv_papers)}편 ({arxiv_status})"
                  + (f" — 실패한 질의 {len(result['chunk_errors'])}개: " + " / ".join(result["chunk_errors"]) if result["chunk_errors"] else ""))

    # ── 두 번째 소스: Semantic Scholar (2026-09-02)
    #
    # 팀 표적 분야는 arXiv 가 아니라 저널에 실린다. 실측: 'surface inspection'
    # 최근 5일 표본 100건 중 **97건이 arXiv 에 없었다**(커버리지 3%). 그 97건이
    # 전부 openAccessPdf 와 DOI 를 갖고 있어 본문까지 받을 수 있다.
    #
    # 새 지휘자 계층이 아니다(CLAUDE.md 6) — 기존 진입점 안에서 소스를 하나 더
    # 부르고, 병합은 처음부터 그 용도로 있던 selection.dedupe 가 한다
    # ("S2 는 arxiv_id 가 없을 수 있다"고 그 docstring 에 적혀 있다).
    #
    # arXiv 가 실패해도 S2 는 시도한다 — 한 소스가 죽었다고 그날을 통째로
    # 버리지 않는다. 반대도 같다.
    s2_papers: list[dict] = []
    s2_status = "skipped"
    s2_result: dict | None = None
    # s2_keywords 는 위 창 계산에서 이미 구했다 — 같은 값을 두 번 계산하면
    # 한쪽만 바뀌었을 때 창과 실제 질의가 어긋난다.
    if s2_keywords:
        try:
            s2_result = await s2_delta.find_new_papers_since(
                client, s2_keywords, since, until,
            )
        except Exception as e:  # noqa: BLE001 — arXiv 쪽과 대칭이어야 한다
            s2_status = "failed"
            s2_run_id = research_profile.record_run(
                db_path, profile_id, "s2", f"S2 keywords×{len(s2_keywords)}", since, until,
                "failed", 0, error_detail=str(e), signature=s2_signature,
            )
            print(f"  [경고] S2 검색 실패 — arXiv 만으로 이어간다: "
                  f"{type(e).__name__}: {str(e).splitlines()[0][:150]}")
        else:
            s2_papers = s2_result["papers"]
            s2_status = s2_result["status"]
            s2_run_id = research_profile.record_run(
                db_path, profile_id, "s2", s2_result["query"], since, until,
                s2_status, len(s2_papers), signature=s2_signature,
            )
            print(f"  [S2] 키워드 {len(s2_keywords)}개 → {len(s2_papers)}편 "
                  f"(arXiv 밖 {sum(1 for x in s2_papers if not x.get('arxiv_id'))}편)")

    # **둘 다 죽었으면 그때는 올린다.** 그건 일시적 혼잡이 아니라 우리가
    # 아무것도 못 본 것이고, 그런 날의 "논문 0편" 메일은 "조용한 날"과
    # 구분이 안 돼 거짓말이 된다(규칙 8). 메일이 안 오는 것이 정직한 신호다.
    if arxiv_status == "failed" and s2_status in ("failed", "skipped"):
        raise RuntimeError(
            f"검색 소스가 전부 실패했다 — arXiv: {arxiv_error} / S2: {s2_status}")

    merged = selection.dedupe(arxiv_papers + s2_papers)

    # 이미 요약된 논문은 후보에서 뺀다(§8-26) — 창이 겹치므로 안 빼면 어제
    # 메일에 나간 논문이 오늘 또 나간다. 이 필터가 겹침의 비용을 0 으로 만든다.
    #
    # **그 필터 하나로는 저널 논문을 못 막는다**(2026-09-06). `summaries` 를
    # 보는데 저널 논문은 본문을 못 받아 요약이 저장되지 않고, arxiv_id 도
    # 없어서 조회 대상에조차 안 든다 — 아래 `not p.get("arxiv_id")` 절이
    # 그런 논문을 **무조건 통과**시킨다. 그래서 09-04 와 09-06 메일의 상위
    # 3편이 같았다. 두 번째 필터가 그 구멍을 막는다(research_profile.
    # mark_shown 주석에 실측이 있다).
    seen = _already_summarized([p.get("arxiv_id") for p in merged])
    # **소비의 기준은 "배달됐다" 하나다**(2026-09-08, §8-77).
    #
    # 예전에는 `seen`(=이미 요약됨)으로도 후보에서 뺐다. 그건 "요약됐다 =
    # 배달됐다"를 전제한 것인데, 발송이 실패한 날 그 전제가 깨진다 —
    # 요약은 남고 메일은 안 갔는데 다음 날 후보에서도 빠져 **영영 안 나간다.**
    # 이제 `profile_shown`(배달 기록) 하나로만 뺀다.
    #
    # 요약이 이미 있는 논문이 다시 후보가 되어도 **LLM 을 다시 부르지 않는다** —
    # `_summary_exists` 가 Deep Layer 재처리를 막고, 그 논문은 기존 요약을
    # 달고 내용 자리에 들어간다. 즉 되살아나는 비용은 사실상 0 이다.
    #
    # 이 변경 전에 `backfill_shown_from_summaries` 로 예전 요약을 소급
    # 기록했다(101편). 안 그러면 이미 나간 논문이 한꺼번에 되살아난다.
    shown = research_profile.already_shown(db_path, profile_id)
    fresh = [p for p in merged if research_profile.paper_key(p) not in shown]

    # **관련도 하나로 줄 세운다**(2026-09-04 개정).
    #
    # 2026-09-03 에는 "본문을 받을 수 있나"로 먼저 갈랐다. 그런 논문이 상위
    # 6칸을 먹고 요약 없이 나가는 걸 막으려던 것이었다. **그런데 그게 더
    # 나쁜 걸 만들었다** — 09-04 메일 실측:
    #
    #   상위 6칸(요약 자리) : ★★ 여섯 편, 그중 다섯이 본문 수집 실패
    #   맨 아래 목록        : ★★★ PhyHGNet, ★★★ 2-D Ambipolar
    #
    # **팀 표적 논문 두 편이 메일 맨 아래에 묻혔다.** "본문을 받을 수 있나"를
    # "우리 분야인가"보다 먼저 놓았기 때문이다. 순서가 거꾸로였다 — 읽는
    # 사람이 먼저 알아야 할 건 관련도지 우리 수집 사정이 아니다.
    #
    # 원래 걱정(본문 없는 논문이 자리만 먹는다)은 §8-41 로 사라졌다. 이제
    # 본문을 못 받아도 초록으로 정리가 나가므로 그 칸이 비지 않는다.
    #
    # 렌더링 깊이만 다르다 — 본문 요약 > 초록 정리 > 제목·링크. 자리는
    # 관련도가 정하고, 깊이는 확보한 것이 정한다.
    # **한 번만 채점한다.** 상위 목록도 "그 밖에" 목록도 같은 순위에서 자른다 —
    # 따로 채점하면 두 목록의 기준이 갈릴 수 있다.
    #
    # **선별은 정렬 하나로 끝난다**(2026-09-11, A단계 — docs/ASTRA_PLAN §5.6).
    # 그전에는 `자격(본문 링크) → 다양성(MMR·키워드 자리 상한) → 자르기` 세
    # 단계를 거쳤는데, 세 단계 모두 순위 계약("계층 우선, 같은 계층이면
    # 최신")을 깼다:
    #   · `_eligible_for_content` — 본문 링크 없는 상위 논문을 링크 있는 하위
    #     논문 뒤로 보냈다. 관련성과 수집 사정을 섞은 것이다(§5.5). 원래 걱정
    #     (링크 없는 논문이 자리만 먹는다)은 §8-41 초록 정리로 이미 사라졌다.
    #   · `_diversify_content` — `0.8×priority − 0.2×유사도` 로 재정렬. 합산이다.
    #   · `_spread_keywords` — 외부 점검(2026-09-11)이 합성 입력으로 실증:
    #     상한을 넘은 고계층 H4 를 미루고 저계층 L3 를 올렸다.
    # 셋 다 핵심 경로에서 뺐고, 2026-09-16 에 함수도 지웠다(Codex 구조 검토: 테스트만 남은 죽은 코드). 사유는 이 주석과 PROGRESS §8-86.
    # 다양성은 통계로 보여 주고(키워드별 적중 편수 절) 선정에 끼워 넣지 않는다.
    #
    # reserve 도 같은 순서다 — Deep Layer 는 `papers + reserve` 를 순서대로
    # 돌므로, 상위가 처리에 실패하면 **그 다음으로 관련 있는** 논문이 온다.
    scored = profile_scoring.score_and_rank(fresh, profile)
    ranked = scored["papers"]
    scored["papers"] = ranked[:profile["max_items"]]
    rest = ranked[profile["max_items"]:]
    dropped: list[dict] = []    # 문지기가 없으므로 자격 미달 목록도 없다
    listed = {"papers": rest[:TITLE_ONLY_MAX_ITEMS]}
    listed["scored_count"] = len(listed["papers"])
    scored["reserve"] = rest        # Deep Layer 가 처리에 실패한 자리를 이걸로 메운다

    # ① **선택 이전의 후보를 남긴다**(2026-09-07, 외부 검토서 §182).
    #
    # 그전까지 후보는 이 실행의 메모리에만 있었다 — 다이제스트에 실린 논문만
    # 흔적이 남고 밀린 논문은 사라졌다. 그래서 "왜 이 논문이 안 뽑혔나",
    # "저 저널 논문은 언제 처음 보였나", "채점 규칙을 바꾸면 뭐가 달라지나"에
    # 답할 수 없었다. citation_count·venue 는 검색 응답에만 있어서 나중에
    # 다시 받으려면 호출이 또 든다 — 지금이 공짜로 갖는 유일한 시점이다.
    #
    # **기록이 스캔을 막지 않는다.** 이건 관측이지 파이프라인이 아니다.
    # 버킷은 겹친다 — `dropped` 는 `rest` 에, `rest` 의 앞부분은 `listed` 에
    # 들어 있다. **먼저 오는 결말이 이긴다**: 각주로 실제로 실린 논문을
    # "자격 미달"로 덮어쓰면 기록이 화면과 어긋난다.
    fresh_keys = {research_profile.paper_key(p) for p in fresh}
    buckets = [
        (scored["papers"], research_profile.OUTCOME_CONTENT),
        (listed["papers"], research_profile.OUTCOME_TITLE_ONLY),
        (rest, research_profile.OUTCOME_RESERVE),
        (dropped, research_profile.OUTCOME_DROPPED),
        # **채점에서 조용히 사라진 후보**. score_and_rank 는 제외어에 걸렸거나
        # 핵심 키워드를 하나도 못 맞힌 논문을 `continue` 로 버린다 — 어느
        # 목록에도 안 남는다. "왜 이 논문이 안 뽑혔나"가 바로 이들을 묻는
        # 질문이므로, 여기서 점수를 다시 매겨 남긴다. 채점은 전부 로컬이라
        # 공짜다(네트워크·LLM 없음).
        ([{**p, "_score": profile_scoring.score_paper(p, profile)} for p in fresh],
         research_profile.OUTCOME_DROPPED),
        ([p for p in merged
          if research_profile.paper_key(p) not in fresh_keys],
         research_profile.OUTCOME_FILTERED),
    ]
    try:
        recorded, claimed = 0, set()
        for papers, outcome in buckets:
            batch = []
            for paper in papers:
                key = research_profile.paper_key(paper)
                if key in claimed:
                    continue
                claimed.add(key)
                batch.append(paper)
            recorded += research_profile.record_candidates(
                db_path, profile_id, batch, outcome,
                signature=signature, window=(since, until),
            )
        print(f"  [후보] {recorded}편을 선택 이전 모습으로 기록했다")
    except Exception as e:  # noqa: BLE001 — 관측 실패가 배달을 막으면 안 된다
        print(f"  [후보] 기록 실패(무시): {type(e).__name__}: {e}")

    # **실행별 관측**(2026-09-11, B단계 §6.1). 위 개체 기록은 다음 실행이
    # 덮어쓰지만 이건 쌓인다. 순위·탈락 사유·시드 귀속을 그 스캔의 정책
    # 버전과 함께 남겨, 정책을 바꾼 뒤에도 "당시 선택"을 재생할 수 있다.
    # 개체 기록과 **다른 try** 다 — 하나가 실패해도 다른 하나는 남아야 하고,
    # 실패했으면 scan_runs 에 그렇게 남는다(집계가 "0편"과 "저장 실패"를 가른다).
    # 여기 `content` 는 **초기 선정**이다. Deep Layer 의 처리 실패·예산 보류·
    # reserve 대체로 최종 목록은 달라진다 — 그건 profile_shown 이 말한다.
    try:
        obs, position, seen_keys = [], 0, set()
        for papers, outcome in buckets:
            for paper in papers:
                key = research_profile.paper_key(paper)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                sc = paper.get("_score") or {}
                row = dict(paper)
                row["outcome"] = outcome
                # 관측 시점의 적중을 같이 남긴다 — 제외·무적중이어도(§8-95).
                row["_hits"] = profile_scoring.raw_hits(paper, profile)
                if outcome == research_profile.OUTCOME_FILTERED:
                    row["filter_reason"] = research_profile.FILTER_ALREADY_SHOWN
                elif sc.get("excluded"):
                    row["filter_reason"] = research_profile.FILTER_EXCLUDE_HIT
                elif not sc.get("core_hits"):
                    row["filter_reason"] = research_profile.FILTER_NO_CORE_HIT
                else:
                    position += 1
                    row["rank_pos"] = position
                obs.append(row)
        n = research_profile.record_observations(
            db_path, scan_id, profile_id, obs,
            core_signature=signature, seed_signature=s2_signature)
        research_profile.finish_scan(
            db_path, scan_id, arxiv_run_id=arxiv_run_id, s2_run_id=s2_run_id,
            seed_attempts=(s2_result or {}).get("per_keyword") if s2_keywords else [],
            observations=n)
        print(f"  [관측] {n}편을 스캔 {scan_id} 에 묶어 남겼다")
        # 건강 지표 입력을 **지금** 얼린다(§8-96): anchor/auto 는 이 시점 이력으로, 반사실
        # 상위 K 는 이 시점 채점 코드로. 나중에 세면 둘 다 그 뒤 이력·코드에 따라 흔들린다.
        # 관측 저장이 확정된 **뒤** 별도 try — 얼리기 실패가 "관측 저장 실패"로 둔갑하면
        # 안 된다(외부 검토 2026-09-12). 실패는 stdout 에만 남고 scan_runs 는 건드리지 않는다.
        try:
            import profile_health
            profile_health.freeze_scan(db_path, scan_id, profile_id, obs, profile)
        except Exception as e:  # noqa: BLE001
            print(f"  [건강] 얼리기 실패(관측은 저장됨) {type(e).__name__}: {str(e)[:120]}", flush=True)
    except Exception as e:  # noqa: BLE001 — 관측 실패가 배달을 막으면 안 된다
        print(f"  [관측] 기록 실패(무시): {type(e).__name__}: {e}")
        try:
            research_profile.finish_scan(db_path, scan_id, arxiv_run_id=arxiv_run_id,
                                         s2_run_id=s2_run_id,
                                         observation_error=f"{type(e).__name__}: {str(e)[:200]}")
        except Exception:  # noqa: BLE001
            pass

    return {
        "profile_id": profile_id, "since": since.isoformat(), "until": until.isoformat(),
        "run_status": arxiv_status, "candidates_found": len(fresh),
        # 실패 사유를 배달까지 넘긴다(2026-09-14, 외부 검토 A·D) — 본문 수집의 장애 차단과
        # 메일의 "검색 장애" 표시가 이 값을 읽는다. 성공이면 None.
        "arxiv_error": arxiv_error,
        "title_only_papers": listed["papers"],
        "title_only_count": listed["scored_count"],
        # **"이미 보낸 논문" 수는 배달 기록으로 센다**(2026-09-08, §8-77 후속).
        # 필터를 배달 기준으로 바꿨는데 이 수는 요약 수(`seen`)를 그대로 쓰고
        # 있어서, 미발송 요약이 후보로 실리면서 동시에 "이미 보낸 논문 1건"으로
        # 표시됐다 — 메일이 자기 모순을 말하고 있었다(외부 검토가 잡았다).
        "retrieved_count": len(merged),
        "already_seen_count": sum(
            1 for p in merged if research_profile.paper_key(p) in shown),
        "arxiv_count": len(arxiv_papers), "s2_count": len(s2_papers),
        "s2_status": s2_status,
        "search_signatures": signatures,
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
    with storage.db() as con:
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
    with storage.db() as con:
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
    2026-09-10: 오늘 보낼 논문은 ⑦ 종료까지 기다린다. 기존 요약은 재사용하고
    재현 결과를 같은 메일에 붙인다. 대기 상한을 넘으면 이유를 표시하고 발송한다.
    """
    # §8-15: 실행 전체와 논문 한 편의 외부 API 호출 수를 실제로 센다.
    # 역산이 아니라 호출 지점에서 세는 값이다(api_usage 모듈 docstring 참고).
    run_scope = api_usage.Scope()
    run_scope.__enter__()

    # 0. 어제까지 눌린 반응을 먼저 가져오고, 오늘 스캔 전에 가중치에 반영한다(2026-09-15, 계획 v2 §3).
    # 반영이 스캔보다 먼저여야 오늘 순위가 어제 반응을 따른다. KST 하루 한 번(feedback_weight_runs)이라 하루 두 번 스캔해도
    # 두 번 오르지 않는다. 둘 다 실패해도 오늘 스캔·메일은 그대로 간다(CLAUDE.md 규칙 6).
    try:
        import feedback_links
        counts = await feedback_links.sync(db_path, client)
        if counts:
            print("  [반응] 수집: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    except Exception as e:  # noqa: BLE001
        print(f"  [반응] 수집 실패(무시): {type(e).__name__}")
    try:
        import feedback_weights
        moved = feedback_weights.update_profile(db_path, profile_id)
        if moved.get("changes"):
            print("  [반응] 가중치: " + ", ".join(f"{c['keyword']} {c['before']:g}→{c['after']:g}" for c in moved["changes"]))
    except Exception as e:  # noqa: BLE001
        print(f"  [반응] 가중치 반영 실패(무시): {type(e).__name__}")

    result = await scan_profile(db_path, profile_id, client, page_size, max_pages)
    search_calls = run_scope.total()
    print(f"  [계측] ③ 검색 단계: {run_scope.format_summary()}")

    # Deep Layer — result["papers"]는 score_and_rank가 이미 top_k=max_items로
    # 잘라놓은 목록이라 별도 상한을 두지 않는다.
    deep_started = time.monotonic()
    deferred: list[dict] = []

    # **수집에 실패한 논문은 내용 자리를 내놓는다**(2026-09-06).
    #
    # 문지기(_full_text_slots)는 `open_access_pdf` **링크 유무**로 판정하는데
    # 그 링크는 실측 30% 만 실제 PDF 를 준다(대형 출판사 0/12). 링크만 믿으면
    # 09-04·09-06 메일처럼 "처리 실패" 가 번호 붙은 자리를 차지한다.
    # 그래서 받아본 뒤 한 번 더 거른다 — 실패하면 그 자리를 다음 후보에게
    # 넘기고, 실패한 논문은 각주(제목만)로 내려간다.
    #
    # `abstract_only` 는 실패가 아니다(§8-33 과 같은 구분) — 초록 정리라는
    # 읽을 내용이 실제로 있으므로 자리를 지킨다. 다만 본문 요약보다 얕다는
    # 것은 다이제스트 라벨이 이미 말한다.
    #
    # **새 지휘자 계층이 아니다**(규칙 6). 같은 루프에 후보 목록을 조금 더
    # 길게 줄 뿐이고, ⑦ 트리거는 여전히 _process_paper 가 소유한다(규칙 5).
    max_items = len(result["papers"])
    queue = list(result["papers"]) + list(result.get("reserve") or [])
    content: list[dict] = []
    demoted: list[dict] = []
    # **arXiv 장애 차단기**(2026-09-14, 외부 검토 A). 본문 수집의 arXiv 재시도(429 대기 최대 7.5분)가
    # 이 예산과 따로 돌아, 막힌 날에는 상위 몇 편이 40분을 다 먹고 나머지 127편을 내일로 밀었다(이틀
    # 연속). 검색 단계에서 이미 arXiv 가 장애로 실패했거나, 본문 수집이 장애로 한 번 실패하면
    # 이번 실행의 남은 arXiv 논문은 본문 수집을 건너뛰고 초록 정리로 간다. 순위 순서는 그대로다.
    arxiv_blocked = bool(result.get("arxiv_error")) and batch_summarize.arxiv_outage(result.get("arxiv_error"))
    if arxiv_blocked:
        print("  [arXiv] 검색 단계에서 장애 확인 — 이번 실행은 arXiv 본문 수집을 건너뛰고 초록으로 정리한다")
    for paper in queue:
        if len(content) >= max_items:
            demoted.append(paper)
            continue
        arxiv_id = paper.get("arxiv_id")
        # arXiv 밖 논문(S2 경유 저널)은 arxiv_id 가 없다 — 오픈액세스 PDF
        # 링크가 있으면 _process_paper 가 그 경로로 본문을 받는다(2026-09-02).
        #
        # **여기서 미리 막지 않는다**(2026-09-04). 예전엔 링크가 없으면
        # `failed: 식별자도 오픈액세스 링크도 없음` 으로 잘라냈는데, 그러면
        # 초록이 있는 논문도 정리 한 줄 없이 나간다. `_process_paper` 가
        # 이제 그 갈래를 받아 초록 정리(§8-41)와 OpenAlex 보강(§8-49)까지
        # 간다 — **판단을 두 곳에 두지 않는다.**
        #
        # §8-50 에서 batch_summarize 안의 조기 반환 두 곳을 한 곳으로 모았는데,
        # **여기 세 번째가 남아 있었다.** 같은 결말로 가는 길이 여럿이면
        # 모이는 지점을 먼저 만들라는 교훈이 또 걸렸다.
        cached_summary = bool(arxiv_id and _summary_exists(arxiv_id))
        # 예산은 **논문을 시작하기 전에** 본다. 처리 중간에 끊으면 요약을
        # 반쯤 만들고 버리게 되고, 그 호출은 이미 무료 한도를 쓴 뒤다.
        elapsed = time.monotonic() - deep_started
        if not cached_summary and elapsed > DEEP_LAYER_BUDGET_SECONDS:
            paper["deep_status"] = "deferred: 시간 예산 초과"
            deferred.append(paper)
            print(f"  [예산] {arxiv_id} 이후를 내일로 미룸 "
                  f"({elapsed / 60:.0f}분 경과 > {DEEP_LAYER_BUDGET_SECONDS / 60:.0f}분)")
            continue
        paper_scope = api_usage.Scope()
        try:
            with paper_scope:
                # 차단기가 켜졌을 때만 인자를 넘긴다 — 평소 호출 모양은 그대로 둔다.
                outcome = await batch_summarize._process_paper(
                    client, arxiv_id or "", paper=paper, wait_for_repro=True,
                    **({"skip_arxiv_fetch": True} if arxiv_blocked else {}))
        except Exception as e:  # noqa: BLE001 — 한 편의 실패가 나머지를 막으면 안 됨
            paper["deep_status"] = f"failed: {str(e).splitlines()[0][:200]}"
            paper["api_calls"] = paper_scope.snapshot()
            print(f"  [계측] {arxiv_id} (실패): {paper_scope.format_summary()}")
            demoted.append(paper)
            continue
        if outcome.get("arxiv_outage") and not arxiv_blocked:
            arxiv_blocked = True
            result["arxiv_fetch_blocked"] = True
            print("  [arXiv] 본문 수집이 장애로 실패 — 남은 arXiv 논문은 초록으로 정리한다")
        # ⑦ 대기를 요약 API 예산으로 세면 뒤 논문이 불필요하게 내일로 밀린다.
        deep_started += outcome.get("repro_wait_seconds", 0.0)
        if outcome.get("reproduction") is not None:
            paper["repro_outcome"] = outcome["reproduction"]
            # ⑦ 사다리(2026-09-16): 재현이 끝난 뒤 코드 단계를 정한다 — 공식이 없으면 같은 과제의 참고 구현이라도 찾는다.
            # GitHub 검색 상한(분당 30)이 있어 논문당 최대 2회, 한 달 캐시. 실패해도 메일은 나간다.
            try:
                import code_ladder
                hits = [_primary_keyword(paper)] + [h for h in (paper.get("_score") or {}).get("core_hits") or []
                                                   if h != _primary_keyword(paper)]
                code_ladder.resolve(outcome.get("arxiv_id") or arxiv_id or "", [h for h in hits if h], db=db_path)
            except Exception as error:  # noqa: BLE001
                print(f"  [코드 사다리] {arxiv_id} 실패(무시): {type(error).__name__}")
        # fetch 실패는 예외가 아니라 status="fetch_failed" dict로 온다(재확인함)
        if outcome.get("status") == "done":
            paper["deep_status"] = "skipped: 이미 요약 저장됨" if cached_summary or outcome.get("skipped") else "ok"
            # 합성 ID 를 돌려받았으면(오픈액세스 경로) 이후 라벨 조회가
            # 그 ID 를 써야 한다 — 안 그러면 다이제스트가 검증·재현 결과를
            # 못 찾아 "데이터 없음"으로 나간다.
            if not paper.get("arxiv_id") and outcome.get("arxiv_id"):
                paper["arxiv_id"] = outcome["arxiv_id"]
        elif outcome.get("status") == "abstract_only":
            # 본문을 못 받았지만 초록으로 정리는 했다. **"실패"가 아니다** —
            # 페이월 뒤 논문을 우리 실패로 부르지 않는다(§8-33 과 같은 구분).
            # summaries 테이블에 안 들어가므로 eval 기준선과 무관하고,
            # ⑦ 재현도 안 탄다(초록에는 재현할 코드가 없다).
            paper["deep_status"] = "abstract_only"
            paper["repro_outcome"] = {"status": "not_attempted", "reason": "초록만 확보 — 코드 재현 미실행"}
            paper["abstract_brief"] = outcome.get("brief") or ""
        else:
            paper["deep_status"] = f"failed: {str(outcome.get('detail'))[:200]}"
        paper["api_calls"] = paper_scope.snapshot()
        print(f"  [계측] {arxiv_id}: {paper_scope.format_summary()}")
        (demoted if paper["deep_status"].startswith("failed") else content).append(paper)

    # 자리를 못 채웠거나 내놓은 논문을 정리한다.
    dropped = len(result["papers"]) - len([p for p in content if p in result["papers"]])
    if demoted:
        print(f"  [자리] 본문 수집에 실패한 {len(demoted)}편을 각주로 내렸다 — "
              f"내용 자리는 {len(content)}/{max_items}편")
    result["papers"] = content
    result["title_only_papers"] = (
        demoted + [p for p in (result.get("title_only_papers") or [])
                   if p not in demoted and p not in content])[:TITLE_ONLY_MAX_ITEMS]
    result["title_only_count"] = len(result["title_only_papers"])

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

    # 철회 조회가 밀린 논문을 하루 몫만큼 따라잡는다(§8 철회 미조회 83%).
    # 요약 저장 시점에만 조회하던 구조라 NULL 큐를 비우는 주체가 없었다.
    # 실패해도 다이제스트를 막지 않는다 — 부가 정보다.
    try:
        sweep_scope = api_usage.Scope()
        with sweep_scope:
            sweep = await server.sweep_retraction_status()
        print(f"  [철회] {sweep['checked']}편 조회 · {sweep['resolved']}편 확정 "
              f"· 철회 {sweep['retracted']}편 · 남은 미조회 {sweep['remaining']}편 "
              f"— {sweep_scope.format_summary()}")
    except Exception as e:  # noqa: BLE001
        print(f"  [철회] 따라잡기 실패(무시): {type(e).__name__}")

    profile = research_profile.get_profile(db_path, profile_id)

    # **매일 동향 서술**(2026-09-04). 그전까지 다이제스트의 "동향" 절은
    # `quantization 22 · vision-language-action 16 · …` 이라는 빈도표 한 줄이
    # 전부였다. 사용자 지적이 정확했다 — "동향을 알려줘야지 논문 제목에
    # 별표만 친 게 왜 동향이야?". 빈도표는 무엇이 몇 편인지만 말하고
    # **무엇이 어디로 가는지**는 말하지 않는다.
    #
    # 서술은 주간 리뷰(월요일)에만 있었는데, 이 시스템의 목적이 "매일 아침
    # 메일 하나로 이 분야가 어디로 가는지 아는 것"이다(CLAUDE.md 목적 절).
    # 주 1회로는 그 목적을 6일 동안 못 채운다. 하루 LLM 호출 1회면 된다.
    #
    # 인용망 조회(주간 리뷰의 비싼 부분)는 여기 안 붙인다 — 그건 주 1회 그대로다.
    research_profile.attach_observation_dates(db_path, profile_id,
        list(result.get("papers") or []) + list(result.get("title_only_papers") or []))
    if profile and result.get("papers"):
        try:
            shown = list(result["papers"]) + list(result.get("title_only_papers") or [])
            # **내용 자리 논문에 한해 원문 요약의 결과 절까지 보고 쓴다**
            # (2026-09-08). 초록은 저자가 쓴 홍보문이고 ④ 요약은 우리가 원문을
            # 읽고 뽑은 것이라 "그래서 무엇이 나왔나"가 거기 있다.
            #
            # 각주 논문에는 안 붙인다. 요약이 있는 논문만 깊어지면 그 논문들이
            # 서술을 독식하는데, 그건 §8-44 에서 한 번 데인 패턴이다("수집
            # 사정이 관련도를 뒤집는다"). 내용 자리 6편은 전부 요약이 있으므로
            # 그 안에서는 기울지 않는다.
            content_ids = [p.get("arxiv_id") for p in result["papers"] if p.get("arxiv_id")]
            excerpts = trend_report.result_excerpts(db_path, content_ids)
            evidence = trend_report.source_evidence(db_path, excerpts)
            import sota_claims
            for paper in shown:
                paper["_evidence"] = evidence.get(paper.get("arxiv_id"), [])
                # SOTA 주장(2026-09-16): 논문이 스스로 말한 문장만, "논문 자체 주장·미검증" 으로. 서술 근거(S번호)로도 넘겨
                # 서술이 자연스럽게 언급할 수 있게 한다 — 억지로 넣게 하지는 않는다(프롬프트 규칙).
                try:
                    claims, _src = sota_claims.claims_for(db_path, paper.get("arxiv_id"), paper.get("abstract"))
                except Exception:  # noqa: BLE001
                    claims = []
                if claims:
                    paper["_sota_claims"] = claims
                    known = {e["id"] for e in paper["_evidence"]}
                    paper["_evidence"] = paper["_evidence"] + [e for e in sota_claims.evidence_packets(claims) if e["id"] not in known]
            story = await trend_report.narrative(client, shown, profile, summaries=excerpts)
            if story:
                text, ungrounded, enriched = story
                result["narrative"] = (text, ungrounded)
                # 라벨이 "무엇을 보고 썼는지"를 말하려면 이 수가 필요하다(규칙 8).
                result["narrative_summaries"] = enriched
                corpus, _, _ = trend_report._narrative_corpus(shown, excerpts)
                result["citation_audit"] = trend_report.citation_audit(text, corpus)
                result["evidence_catalog"] = trend_report.evidence_catalog(shown, excerpts)
                print(f"  [동향] 오늘의 서술을 붙였다 (원문 요약 {enriched}편 반영)")
        except Exception as e:  # noqa: BLE001 — 서술이 실패해도 셈은 그대로 나간다
            print(f"  [동향] 서술 실패(무시): {type(e).__name__}")

    # 주간 동향 리뷰는 **주 1회만** 붙인다(월요일). 매일 붙이면 어제와 거의
    # 같은 표가 반복돼 읽히지 않고, 인용망 조회 비용도 매일 낼 이유가 없다.
    # 실패해도 다이제스트를 막지 않는다 — 부가 정보다.
    #
    # 2026-09-07 §8-70 고침: 예전에는 여기서 `digest_text` 에 문자열로
    # 이어붙였다. 그런데 _deliver 는 HTML 을 `result` 로 **다시 만들기**
    # 때문에 HTML 메일에는 이 절이 통째로 빠져 있었다 — 메일은
    # multipart/alternative 이고 Gmail 은 HTML 을 보여주므로, 2026-09-07 에
    # 처음 돌아간 주간 리뷰는 실행은 됐지만 사용자 화면에 닿지 않았다.
    # 이제 `result` 에 넣는다. 평문·HTML 두 렌더러가 같은 값을 읽으므로
    # 렌더링 위치가 갈라져도 입력은 하나다(§8-67 의 교훈).
    if profile and is_weekly_review_day():
        try:
            result["weekly_review"] = await trend_report.build(db_path, profile, client=client)
            print("  [동향] 주간 리뷰를 다이제스트에 붙였다")
        except Exception as e:  # noqa: BLE001
            print(f"  [동향] 주간 리뷰 실패(무시): {type(e).__name__}")

    # 2026-09-10: 과거 논문 재통지는 중단한다. 오늘 보낼 논문의 현재
    # 철회 상태만 점검하고 ⑦ 종료 결과는 위 처리 단계에서 이미 확보한다.
    try:
        import evidence_state
        import summarize_engine
        await evidence_state.prepare(db_path, profile_id, result, client,
            summarize_engine.ENV.get("OPENALEX_API_KEY"),
            summarize_engine.ENV.get("CROSSREF_MAILTO"))
    except Exception as error:
        result.pop("_evidence_states", None)
        for paper in result.get("papers") or []:
            paper.pop("_delivered_state", None)
        print(f"  [근거 상태] 관측 실패: {type(error).__name__}")

    digest_text = digest.generate_digest(result, profile["name"] if profile else profile_id)
    research_profile.save_digest(db_path, profile_id, digest_text)

    # **소비 처리는 여기서 하지 않는다**(2026-09-08, §8-77).
    #
    # 예전에는 여기서 `mark_shown` 을 불렀다. 주석은 "다이제스트를 저장한 뒤에
    # 한다 — 앞 단계에서 예외가 나면 메일이 안 나가는데 그때 소비 처리까지
    # 해버리면 그 논문은 영영 안 나간다"고 적혀 있었다. **의도는 맞는데 막는
    # 범위가 스캔 실패까지였다** — 발송은 `scan_all_profiles` 가 이 함수를
    # 끝낸 뒤에 하므로, SMTP 가 죽은 날에도 논문은 이미 소비돼 있었다.
    #
    # 이제 **배달에 성공한 뒤에만** 소비 처리한다(scan_all_profiles·main 참고).
    # 발송하지 않는 경로(review_app 의 수동 스캔)는 소비하지 않는다 — 화면에서
    # 본 것과 메일로 받은 것은 다르고, 이 기록의 이름은 "내보냈다"이다.

    run_scope.__exit__(None, None, None)
    result["api_calls"] = run_scope.snapshot()
    result["api_calls_total"] = run_scope.total()
    deep_calls = run_scope.total() - search_calls
    print(f"  [계측] 실행 합계: {run_scope.format_summary()}")
    print(f"  [계측]   └ ③ 검색 {search_calls}회 + Deep Layer {deep_calls}회 "
          f"(⑦ 재현은 별도 프로세스라 여기 안 잡힌다 — 재현 로그를 따로 볼 것)")
    return result, digest_text


# 발송 결과 문자열은 _deliver 가 소유한다. 바깥에서 실패를 판별할 때
# 이 접두사로만 본다 — 문자열이 흩어지면 "실패했는데 성공으로 읽는" 사고가
# 난다(2026-09-07, ⑨ 종료코드 전파).
DELIVERY_FAILED_PREFIX = "발송 실패"
DELIVERY_NO_RECIPIENT = "수신자 없음 — 발송 안 함"
DELIVERY_SENT_PREFIX = "발송 완료"


def delivery_failed(message: str | None) -> bool:
    """발송이 **실패**했는가. 수신자가 없는 것은 설정 상태지 실패가 아니다.
    종료코드는 이 판정을 쓴다 — 매일 울리는 경보는 무시되기 때문이다."""
    return bool(message) and str(message).startswith(DELIVERY_FAILED_PREFIX)


def delivery_reached_someone(message: str | None) -> bool:
    """메일이 **실제로 사람에게 갔는가**. 소비 처리는 이 판정을 쓴다.

    `delivery_failed` 와 나누는 이유(2026-09-08, 외부 검토가 잡았다):
    "수신자 없음"은 고장이 아니라 설정 상태라 **경보를 울리면 안 되지만**,
    메일이 안 간 것은 사실이므로 **소비 처리도 하면 안 된다.** 하나로 묶었더니
    수신자를 안 넣은 프로필에서 논문이 조용히 소비됐다 — 나중에 수신자를
    등록해도 그 논문은 후보에서 이미 빠져 있다.
    """
    return bool(message) and str(message).startswith(DELIVERY_SENT_PREFIX)


def field_label(profile_name: str) -> str:
    """메일 제목 괄호에 넣을 분야 이름 — 프로필 이름에서 ' — ' 앞부분. 설명을 길게 붙인 이름("우리팀 — 자율제조·…")은
    제목이 휴대폰에서 잘리므로 앞 이름만 쓴다."""
    return (profile_name or "").split(" — ")[0].strip() or (profile_name or "")


def mail_subject(profile_name: str, when: datetime | None = None) -> str:
    """제목은 읽는 사람 기준이다(2026-09-11). 2026-09-16 사용자 요청으로 분야를 **항상** 붙인다 — 분야별 프로필(로봇·에이전트·비전)을
    한 사람이 함께 받게 되면서 제목만 보고 어느 분야 메일인지 알아야 한다. 날짜는 받는 사람(KST) 기준(2026-09-14)."""
    day = (when or datetime.now(READER_TZ)).astimezone(READER_TZ).strftime("%Y-%m-%d")
    return f"[연구 동향 브리핑({field_label(profile_name)})] {day}"


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
        return DELIVERY_NO_RECIPIENT
    profile = research_profile.get_profile(db_path, profile_id)
    name = profile["name"] if profile else profile_id
    import evidence_state
    failures = []
    sent = 0
    content_keys = {research_profile.paper_key(p) for p in result.get("papers") or []}
    # 반응 버튼(2026-09-15): 이번 메일 회차 하나에 수신자마다 다른 서명 링크를 만든다. 설정(.env 의
    # FEEDBACK_WEBAPP_URL·FEEDBACK_HMAC_SECRET)이 없으면 빈 dict 라 메일은 예전 그대로다.
    import feedback_links
    issue_id = feedback_links.new_issue_id(profile_id)
    # 주간 관리 에이전트 보고(2026-09-15): 금요일 실행 뒤 첫 메일에 한 번 싣는다. 못 읽어도 메일은 나간다(규칙 6).
    agent_lines: list[str] = []
    agent_keys: list[tuple[str, str]] = []
    try:
        import agent_maintenance
        agent_lines, agent_keys = agent_maintenance.pending_report(db_path, profile_id)
    except Exception as error:  # noqa: BLE001
        print(f"  [에이전트] 보고 조회 실패(보고 없이 발송): {type(error).__name__}")
    # 수신자 하나가 거절돼도 다른 수신자의 기준 상태를 함께 전진시키면 안 된다.
    # 기존 SMTP 함수에 한 명씩 넘기므로 부분 거절도 그 수신자의 실패로 드러난다.
    subject = mail_subject(name)
    for recipient in recipients:
        try:
            view = dict(result)
            states = result.get("_evidence_states")
            # 이전 수신 이력을 다음 메일 내용으로 다시 조립하지 않는다.
            view.pop("state_updates", None)
            if agent_lines:
                view["agent_report"] = agent_lines
            try:
                links = feedback_links.issue_links(db_path, profile_id, issue_id, recipient, result.get("papers") or [])
            except Exception as error:  # noqa: BLE001 — 버튼을 못 만들어도 메일은 나가야 한다(CLAUDE.md 규칙 6)
                print(f"  [반응] 링크 생성 실패(버튼 없이 발송): {type(error).__name__}")
                links = {}
            if links:
                view["papers"] = [dict(p, _feedback_links=links.get(research_profile.paper_key(p)))
                                  for p in result.get("papers") or []]
            text = digest.generate_digest(view, name)
            digest_html = digest.generate_digest_html(view, name)
            email_delivery.send_digest_email(text, subject, [recipient], digest_html)
            sent += 1
            if links:
                feedback_links.mark_delivered(db_path, issue_id, recipient)
            if states is not None:
                visible = content_keys
                evidence_state.acknowledge(db_path, profile_id, recipient,
                    [i for i in states if i["paper_key"] in visible])
        except Exception as error:
            failures.append(str(error).splitlines()[0][:200])
    # 회차 기록(2026-09-16, 운영 화면용) — 실패한 회차도 남긴다. 기록 실패는 발송 결과를 바꾸지 않는다.
    try:
        import mail_ledger
        mail_ledger.record_issue(db_path, issue_id, profile_id, subject, result.get("papers") or [],
                                 len(recipients), sent)
    except Exception as error:  # noqa: BLE001
        print(f"  [발송 기록] 실패(무시): {type(error).__name__}")
    # 모든 수신자에게 나갔을 때만 '실림'으로 표시한다 — 한 명이라도 실패하면 다음 메일에 다시 싣는다(받은 사람은 한 번 더 보지만
    # 못 받은 사람이 영영 못 보는 것보다 낫다. 외부 검토 2026-09-15). 계속 거절되는 주소는 REPORT_TTL(14일)이 반복을 끊는다.
    if agent_keys and sent == len(recipients):
        try:
            agent_maintenance.mark_reported(db_path, agent_keys)
        except Exception as error:  # noqa: BLE001 — 표시 실패는 다음 메일에 한 번 더 실릴 뿐이다
            print(f"  [에이전트] 보고 표시 실패: {type(error).__name__}")
    if failures:
        return f"{DELIVERY_FAILED_PREFIX}: {sent}/{len(recipients)}명 전송 수락 · " + " / ".join(failures)
    return f"{DELIVERY_SENT_PREFIX} → {sent}명"


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
    # 매일 도는 프로필만(2026-09-15) — schedule_frequency='manual' 프로필은 cron 이 건드리지 않는다.
    for profile_id in research_profile.list_profiles(db_path, schedule="daily"):
        try:
            result, digest_text = await scan_and_digest(db_path, profile_id, client, max_pages=max_pages)
            entry = {
                "status": "ok", "run_status": result["run_status"],
                "s2_status": result.get("s2_status"),
                "candidates_found": result["candidates_found"],
                "scored_count": result["scored_count"],
            }
            if send:
                message = _deliver(db_path, profile_id, result, digest_text)
                entry["delivery"] = message
                # **실제로 사람에게 간 날만 소비 처리한다**(§8-77).
                # 실패한 날도, 수신자가 없어 안 보낸 날도 그 논문은 내일 후보로
                # 남아 다시 나갈 기회를 갖는다.
                if delivery_reached_someone(message):
                    research_profile.mark_shown(
                        db_path, profile_id, result.get("papers") or [])
            summary[profile_id] = entry
        except Exception as e:  # noqa: BLE001 — 한 프로필의 실패가 나머지를 막으면 안 됨
            summary[profile_id] = {"status": "error", "detail": str(e)}

    # 옛 주간 프로필 개선기(`profile_advisor.run_weekly`, 월요일·proposal_only)는 2026-09-17 에 지웠다 — 금요일 17:00 의
    # `agent_maintenance`(Claude 제안 → Codex 판정 → Python 검증·적용)가 같은 역할을 맡는다(운영 이력 1회, 제안 0건이었다).
    return summary


def _exit_message(summary: dict, code: int) -> str:
    """종료코드와 함께 stderr 에 남기는 한 줄. 2(저하)는 어느 프로필의 어느 소스가 죽었는지를 적는다 — 그전엔 1 의 문구를 그대로 타서
    "등록된 프로필 없음 — 종료코드 2" 로 찍혔다(2026-09-16 실측, 실제로는 arXiv 하나만 죽은 날). 로그만 보는 사람이 엉뚱한 원인을 쫓는다."""
    if code == 2:
        degraded = [f"{pid}(arXiv {e.get('run_status')} · S2 {e.get('s2_status')})" for pid, e in summary.items()
                    if e.get("run_status") == "failed" or e.get("s2_status") == "failed"]
        return f"[저하] 발송은 됐지만 검색 소스가 실패한 프로필: {', '.join(degraded) or '(불명)'} — 종료코드 2"
    failed = [pid for pid, e in summary.items() if e.get("status") != "ok" or delivery_failed(e.get("delivery"))]
    return f"[실패] {', '.join(failed) or '등록된 프로필 없음'} — 종료코드 {code}"


def _exit_code(summary: dict) -> int:
    """cron 이 읽을 종료코드. 0 = 그날 할 일을 다 했다.

    **왜 필요한가**(2026-09-07, ⑨): run_daily_scan.sh 가 status 를 로그에만
    찍고 항상 0 으로 끝나서 **실패한 날과 성공한 날을 바깥에서 구분할 수
    없었다.** 이 시스템은 "매일 오는 메일 자체가 파이프라인이 살아 있다는
    증거"라는 전제 위에 서 있는데(M8), 그 전제는 메일이 안 나간 날을 누군가
    알아챌 때만 성립한다. cron 종료코드가 그 신호다.

    실패로 치는 것: 프로필 스캔 예외(status=='error'), 발송 실패,
    그리고 **--all 인데 프로필이 하나도 없는 것** — cron 이 매일 도는데
    아무 일도 안 했다면 그건 조용한 날이 아니라 설정이 비어 있는 것이다.
    수신자가 없는 프로필은 실패가 아니다(의도된 설정 상태).

    **2 = 발송은 했지만 검색 소스 하나가 실패했다**(2026-09-14, 외부 검토 A). arXiv 가 11시간 넘게
    429 였던 날도 S2 만으로 메일이 나가 종료코드 0 이었다 — 바깥에서는 장애가 성공으로 보였다.
    한 소스 장애로 메일을 멈추지 않는 설계는 그대로 두고, 신호만 0 과 가른다(1 보다 약한 실패).
    """
    if not summary:
        return 1
    degraded = False
    for entry in summary.values():
        if entry.get("status") != "ok":
            return 1
        if delivery_failed(entry.get("delivery")):
            return 1
        if entry.get("run_status") == "failed" or entry.get("s2_status") == "failed":
            degraded = True
    return 2 if degraded else 0


def main() -> int:
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
        code = _exit_code(summary)
        if code:
            print(_exit_message(summary, code), file=sys.stderr)
        return code

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
        message = _deliver(db_path, args.profile_id, result, digest_text)
        print(message)
        if delivery_failed(message):
            return 1
        # --all 경로와 같은 규칙 — 실제로 간 뒤에만 소비 처리한다(§8-77).
        if delivery_reached_someone(message):
            research_profile.mark_shown(db_path, args.profile_id, result.get("papers") or [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
