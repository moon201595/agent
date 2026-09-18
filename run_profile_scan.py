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
import research_profile
import trend_report
import server


# 2026-09-17 분할(§8-155): 검색·선별은 scan_search, 발송은 scan_deliver. 아래 이름들은 옛 import 경로 호환과 테스트의 monkeypatch
# (`rps._deliver`·`rps._summary_exists`·`rps.scan_profile`)를 위해 이 모듈 이름공간에 그대로 둔다 — 조정기 함수는 이 전역을 통해 부른다.
from scan_search import (                                                   # noqa: F401
    ARXIV_TERMS_PER_QUERY, TITLE_ONLY_MAX_ITEMS, _already_summarized, _arxiv_queries_from_core_topics,
    _arxiv_query_from_core_topics, _search_arxiv_chunked, _summary_exists, scan_profile,
)
from scan_deliver import (                                                  # noqa: F401
    DELIVERY_FAILED_PREFIX, DELIVERY_NO_RECIPIENT, DELIVERY_SENT_PREFIX, _deliver, delivery_failed,
    delivery_reached_someone, field_label, mail_subject,
)



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
            # 최근 7일 창 — 서술 앞에서 먼저 센다. 서술에는 늘어난 "말"만 맥락으로 주고(수치는 안 준다),
            # 수치 자체는 Python 이 메일의 별도 절에 싣는다(2026-09-18, 사용자 요청 ①).
            try:
                movement = trend_report.window_movement(db_path, profile)
            except Exception as e:  # noqa: BLE001 — 창 집계가 실패해도 서술·메일은 나간다
                movement = None
                print(f"  [동향] 최근 창 집계 실패(무시): {type(e).__name__}")
            if movement:
                result["trend_window"] = movement
                cmp_label = "직전 7일 대비" if movement["comparable"] else "직전 구간 관측 없음 — 비교 안 함"
                print(f"  [동향] 최근 {movement['days']}일 {movement['papers'][0]}편 ({cmp_label})")
            # 자리 밖으로 밀린 후보(reserve)를 버리지 않고 집계만이라도 싣는다(2026-09-18, 사용자 요청 ②).
            # 관측은 이미 `scan_search` 가 저장했으므로 이 프로필의 최신 실행이 곧 이번 실행이다.
            try:
                import observation_signals
                reserve = observation_signals.reserve_terms(db_path, profile_id)
            except Exception as e:  # noqa: BLE001 — 집계가 실패해도 메일은 나간다
                reserve = None
                print(f"  [동향] 자리 밖 후보 집계 실패(무시): {type(e).__name__}")
            if reserve:
                result["reserve_terms"] = reserve
                print(f"  [동향] 자리 밖 후보 {reserve['count']}편에서 용어 {len(reserve['terms'])}개")
            # 지난 5일치 **동향 서술**을 맥락으로 준다(2026-09-18 사용자 결정 — 논문을 다시 읽히지 않는다).
            try:
                import narrative_store
                past = narrative_store.recent(db_path, profile_id, narrative_store.DAILY,
                                              days=5, before=narrative_store.reader_date())
            except Exception as e:  # noqa: BLE001 — 지난 글을 못 읽어도 오늘 글은 쓴다
                past = []
                print(f"  [동향] 지난 서술 조회 실패(무시): {type(e).__name__}")
            if past:
                print(f"  [동향] 지난 서술 {len(past)}일치를 맥락으로 넣는다 ({past[-1]['reader_date']}~{past[0]['reader_date']})")
            story = await trend_report.narrative(client, shown, profile, summaries=excerpts,
                                                 movement=movement, past=past)
            if story:
                text, ungrounded, enriched, engine = story
                result["narrative_engine"] = engine
                result["narrative"] = (text, ungrounded)
                # 라벨이 "무엇을 보고 썼는지"를 말하려면 이 수가 필요하다(규칙 8).
                result["narrative_summaries"] = enriched
                corpus, _, _ = trend_report._narrative_corpus(shown, excerpts)
                result["citation_audit"] = trend_report.citation_audit(text, corpus)
                result["evidence_catalog"] = trend_report.evidence_catalog(shown, excerpts)
                print(f"  [동향] 오늘의 서술을 붙였다 (원문 요약 {enriched}편 반영)")
                # 쌓아 둔다 — 내일 서술의 맥락이 되고, 나중에 "그때 뭐라고 했나"를 다시 읽을 수 있다(§8-162).
                try:
                    narrative_store.save(db_path, profile_id, narrative_store.DAILY, text,
                                         scan_id=result.get("scan_id"), engine=result.get("narrative_engine"),
                                         audit=result.get("citation_audit"),
                                         papers_seen=len(shown), window_days=1)
                except Exception as e:  # noqa: BLE001 — 보관 실패가 메일을 막지 않는다
                    print(f"  [동향] 서술 보관 실패(무시): {type(e).__name__}")
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
            try:
                import narrative_store
                narrative_store.save(db_path, profile_id, narrative_store.WEEKLY,
                                     result["weekly_review"], scan_id=result.get("scan_id"), window_days=7)
            except Exception as e:  # noqa: BLE001
                print(f"  [동향] 주간 리뷰 보관 실패(무시): {type(e).__name__}")
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
