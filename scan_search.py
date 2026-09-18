"""① 검색·② 선별 — 프로필 하나의 하루치 후보를 만든다(`scan_profile`). 2026-09-17 에 `run_profile_scan.py`(1,165줄)에서 갈라냈다(§8-155).

여기 있는 것: arXiv 질의 생성·분할 던지기(`ARXIV_TERMS_PER_QUERY`), 델타 창(`next_since`, 두 소스 중 뒤처진 쪽), arXiv·S2 검색, 후보 병합·
채점(`profile_scoring.score_and_rank`)·관측 기록, 이미 요약된 논문 걸러내기(`_already_summarized`·`_summary_exists`).
여기 없는 것: 본문·요약(④⑤, `batch_summarize`)·다이제스트·발송 — 그건 `run_profile_scan.scan_and_digest` 와 `scan_deliver` 다.
함수 이름·시그니처는 옮기기 전과 같다. `run_profile_scan` 이 같은 이름을 다시 내보내므로 옛 import 경로도 그대로 돈다.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

import find_new_papers
import profile_scoring
import research_profile
import s2_delta
import selection
import storage


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



# 본문을 못 받는(arXiv ID 도 오픈액세스 PDF 도 없는) 논문을 다이제스트에 몇 편까지
# 목록으로 보여줄지. 요약이 없으니 한 줄씩만 차지한다 — 넉넉해도 메일이 길어지지 않는다.
TITLE_ONLY_MAX_ITEMS = 8


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
        # 이 실행의 관측 묶음 id 를 결과에 실어 둔다 — 동향 서술 보관이 "어느 스캔의 글인지"를
        # 가리키려면 필요하다(2026-09-18, §8-162). 없으면 나중에 글과 관측을 못 잇는다.
        result["scan_id"] = scan_id
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
