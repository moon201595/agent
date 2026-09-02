"""trend_report.py — 주간 동향 리뷰 (2026-09-02).

"논문들을 쭉 넣은 다음 마지막에 동향 보고가 필요하다"는 요구에 대한 답이다.

**LLM 을 쓰지 않는다.** 전부 셈과 문자열 대조라 위조가 불가능하다
(CLAUDE.md 7). 서술형 리뷰는 검증할 수 없는 산출물이라 판정 경로에 두지
않고, 대신 "무엇이 몇 번 나왔나"를 정확히 센다 — 그게 동향의 실체다.

다이제스트의 "이번 창의 동향 신호" 한 줄이 그날치만 보여주는 것을, 여기서
주 단위로 넓히고 세 가지를 더한다:

  1. 키워드 추이 — 이번 주 vs 지난 주. 늘었나 줄었나가 한 줄로 보인다.
  2. venue·소스 분포 — 어디에 실리는 분야인가(arXiv 인가 저널인가).
  3. **공통 인용** — 이번 주 논문들이 함께 인용한 논문. 이 분야가 무엇을
     딛고 서 있는지를 보여주는 신호이고, 개별 논문 요약으로는 절대 안 나온다.

3번이 이 모듈의 핵심이다. s2_get_references 도구가 있는데 파이프라인에서
한 번도 안 쓰이고 있었다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import server

# 공통 인용을 볼 때 논문 하나당 받아올 참고문헌 수. S2 는 초당 1회라
# 논문 수만큼 요청이 나간다 — 주 1회 실행이라 감당할 만하다.
REFERENCES_PER_PAPER = 50

# 공통 인용으로 보고할 최소 횟수. 1이면 그냥 참고문헌 목록이지 "공통"이 아니다.
MIN_SHARED_CITATIONS = 2

# 인용망 조회에 쓸 벽시계 예산(초). 넘으면 **거기까지 모은 것으로** 보고한다.
#
# 실측(2026-09-02): 논문 8편으로 돌렸더니 7분을 넘겼다. S2 가 429 를 뱉으면
# 재시도 예산(server.RATE_LIMIT_*, 최대 450초)이 논문마다 붙기 때문이다.
# 동향 리뷰는 **있으면 좋은 부가 정보**지 다이제스트의 전제가 아니다 —
# 이것 때문에 메일이 늦어지면 주객이 전도된다.
#
# 부분 결과를 그대로 쓰는 게 정직한가: 그렇다. "N편 중 M편까지 봤다"를 같이
# 보고하므로 읽는 사람이 표본 크기를 안다. 조용히 전체인 척하지 않는다.
REFERENCE_BUDGET_SECONDS = 120.0


def _rows_between(db: Path, start: datetime, end: datetime) -> list[sqlite3.Row]:
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return con.execute(
            "SELECT p.arxiv_id, p.title, p.published, p.source, s.engine, s.coverage_ratio "
            "FROM papers p JOIN summaries s ON s.arxiv_id = p.arxiv_id "
            "WHERE s.created_at >= ? AND s.created_at < ? ORDER BY s.created_at",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def keyword_counts(rows: list[sqlite3.Row], profile: dict) -> Counter:
    """저장된 논문 제목에서 핵심 키워드 적중을 센다.

    초록이 아니라 제목만 본다 — 주간 추이는 "무엇에 대한 논문이 나왔나"이지
    "어떤 낱말이 본문 어딘가에 있었나"가 아니다. 제목이 훨씬 정확한 신호다.
    """
    import profile_scoring
    counts: Counter = Counter()
    for row in rows:
        hits, _total, _top = profile_scoring.core_hits_with_weight(
            {"title": row["title"], "abstract": ""}, profile)
        counts.update(hits)
    return counts


def source_mix(rows: list[sqlite3.Row]) -> Counter:
    """arXiv 인가 저널인가. S2 를 붙인 뒤 이 비율이 바뀌는지가 관심사다."""
    mix: Counter = Counter()
    for row in rows:
        src = row["source"] or ""
        mix["저널(오픈액세스)" if src.startswith("open-access") else
            "수동 업로드" if src.startswith("manual-pdf") else "arXiv"] += 1
    return mix


def engine_mix(rows: list[sqlite3.Row]) -> Counter:
    mix: Counter = Counter()
    for row in rows:
        mix[row["engine"] or "(미기록)"] += 1
    return mix


def partial_coverage(rows: list[sqlite3.Row], below: float = 0.98) -> list[tuple[str, float]]:
    """원문을 다 못 본 요약들. Groq 폴백 날에만 생긴다(§8-25)."""
    out = []
    for row in rows:
        ratio = row["coverage_ratio"]
        if ratio is not None and ratio < below:
            out.append((row["title"], float(ratio)))
    return sorted(out, key=lambda x: x[1])


async def shared_references(
    client: httpx.AsyncClient, rows: list[sqlite3.Row],
    limit: int = REFERENCES_PER_PAPER, budget_s: float = REFERENCE_BUDGET_SECONDS,
) -> tuple[list[tuple[str, int]], int, int]:
    """이번 주 논문들이 **함께 인용한** 논문. 이 분야가 뭘 딛고 서 있는지.

    returns (공통 인용 목록, 실제로 조회한 논문 수, 조회 대상이던 논문 수)
    — 표본 크기를 같이 돌려준다. 예산에 걸려 일부만 봤을 때 그걸 숨기면
    "전체를 본 결과"로 오해된다.

    arXiv ID 가 있는 논문만 조회한다 — S2 인용망은 arXiv ID 로 찾는다.
    실패는 조용히 건너뛴다: 동향 보고가 못 나온다고 다이제스트를 막으면 안 된다.
    """
    counter: Counter = Counter()
    targets = [r for r in rows
               if (r["arxiv_id"] or "") and not (r["arxiv_id"] or "").startswith("pdf-")]
    started = time.monotonic()
    examined = 0
    for row in targets:
        if time.monotonic() - started > budget_s:
            break
        try:
            raw = await server._s2_citation_graph(row["arxiv_id"], limit, "references")
            refs = json.loads(raw).get("papers") or []
        except Exception:  # noqa: BLE001
            continue
        examined += 1
        # 같은 논문 안에서 같은 참고문헌이 두 번 세지지 않게 제목으로 유일화
        for title in {(r.get("title") or "").strip() for r in refs if r.get("title")}:
            counter[title] += 1
    shared = [(t, n) for t, n in counter.most_common(20) if n >= MIN_SHARED_CITATIONS]
    return shared, examined, len(targets)


def format_report(this_week: list[sqlite3.Row], last_week: list[sqlite3.Row],
                  profile: dict, shared: list[tuple[str, int]] | None = None,
                  examined: int = 0, targets: int = 0) -> str:
    """사람이 메일에서 바로 읽는 형태. 숫자만 담는다."""
    now = keyword_counts(this_week, profile)
    prev = keyword_counts(last_week, profile)

    lines = ["■ 주간 동향 리뷰", ""]
    lines.append(f"처리한 논문 {len(this_week)}편 (지난주 {len(last_week)}편)")

    mix = source_mix(this_week)
    if mix:
        lines.append("  출처 : " + " · ".join(f"{k} {v}편" for k, v in mix.most_common()))
    emix = engine_mix(this_week)
    if emix:
        lines.append("  엔진 : " + " · ".join(f"{k} {v}편" for k, v in emix.most_common()))

    partial = partial_coverage(this_week)
    if partial:
        lines.append(f"  ⚠ 원문을 다 못 본 요약 {len(partial)}편 "
                     f"(최저 {partial[0][1] * 100:.0f}%) — Groq 폴백 영향(§8-25)")

    if now:
        lines += ["", "▶ 주제별 편수 (지난주 대비)"]
        for kw, n in now.most_common(12):
            was = prev.get(kw, 0)
            delta = n - was
            arrow = f"  ({was}→{n}, {delta:+d})" if was or delta else ""
            lines.append(f"   {kw} {n}{arrow}")

    gone = [kw for kw in prev if kw not in now]
    if gone:
        lines.append(f"   지난주엔 있었으나 이번주 없음: {', '.join(sorted(gone)[:8])}")

    if shared:
        scope = f"{examined}/{targets}편 조회" if targets else ""
        lines += ["", f"▶ 이번 주 논문들이 함께 인용한 논문 ({scope})"]
        if targets and examined < targets:
            lines.append("   (시간 예산으로 일부만 봤다 — 아래는 그 표본 기준이다)")
        for title, n in shared[:10]:
            lines.append(f"   {n}편이 인용 — {title[:70]}")
    elif shared is not None:
        lines += ["", f"▶ 공통 인용: 없음 ({examined}/{targets}편 조회 — "
                      "논문들이 서로 다른 토대를 쓰고 있다)"]

    return "\n".join(lines) + "\n"


async def build(db: Path, profile: dict, client: httpx.AsyncClient | None = None,
                days: int = 7, with_references: bool = True) -> str:
    """주간 리뷰 본문. client 를 안 주면 인용망 조회를 건너뛴다(네트워크 없음)."""
    end = datetime.now(timezone.utc)
    this_week = _rows_between(db, end - timedelta(days=days), end)
    last_week = _rows_between(db, end - timedelta(days=days * 2), end - timedelta(days=days))
    shared, examined, targets = None, 0, 0
    if client is not None and with_references and this_week:
        shared, examined, targets = await shared_references(client, this_week)
    return format_report(this_week, last_week, profile, shared, examined, targets)
