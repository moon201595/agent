"""digest.py — Fast Layer 다이제스트 생성 (설계 문서 §6).

LLM을 쓰지 않는다 — profile_scoring이 이미 계산해둔 core_hits/domain_hits/
venue_hit로 "왜 이 논문이 걸렸는지"를 결정론적으로 설명하고, 초록은 그대로
발췌해서 보여준다. "요약"이 아니라 "발췌"라고 정직하게 표시한다 — LLM
초록 요약은 아직 안 붙였다(붙이려면 이 함수의 발췌 자리만 LLM 호출로
바꾸면 되게 분리해뒀다). 지금은 API 호출 없이 다이제스트 생성 전체가
도는지부터 확인하는 게 우선이라 미룬다(2026-08-24, "GPU/API 얘기로 옆길로
새지 말고 전체 흐름부터" 지적과 같은 이유).

⑥ 원칙과의 관계: 이 다이제스트는 ⑥ 사람 승인을 대신하지 않는다 — "이런
논문이 나왔다"는 알림일 뿐이고 검증 전이라는 걸 항상 명시한다(설계 문서
§5-1: "발견은 빠르게, 검증은 필요한 것부터 깊게").
"""

from __future__ import annotations

from datetime import datetime, timezone

_ABSTRACT_EXCERPT_CHARS = 220

# profile_scoring.Weights 기본값(core_topic=1.0, domain_hit=0.3, venue_hit=0.3,
# recency=0.4) 기준으로 대략 잡은 구간이다 — 실측 데이터가 쌓이기 전 시작점일
# 뿐이라, 실제 분포를 보고 재조정해야 한다(설계 문서 §9 미확정과 같은 성격).
_STAR_THRESHOLDS = ((1.2, "★★★"), (0.7, "★★"))


def _stars(priority: float) -> str:
    for threshold, stars in _STAR_THRESHOLDS:
        if priority >= threshold:
            return stars
    return "★"


def _why_matched(score: dict) -> str:
    parts = []
    if score.get("core_hits"):
        parts.append("핵심 키워드: " + ", ".join(score["core_hits"]))
    if score.get("domain_hits"):
        parts.append("도메인 일치: " + ", ".join(score["domain_hits"]))
    if score.get("venue_hit"):
        parts.append("관심 venue")
    return " / ".join(parts) if parts else "(매칭 근거 없음)"


def _abstract_excerpt(paper: dict) -> str:
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        return "(초록 없음)"
    if len(abstract) <= _ABSTRACT_EXCERPT_CHARS:
        return abstract
    return abstract[:_ABSTRACT_EXCERPT_CHARS] + "…"


def _paper_entry(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = paper.get("arxiv_id", "?")
    return "\n".join([
        f"{idx}. [{_stars(score.get('priority', 0.0))}] {paper.get('title') or '(제목 없음)'}",
        f"   왜 걸렸나 : {_why_matched(score)}",
        f"   초록 발췌 : {_abstract_excerpt(paper)}",
        f"   [미검증 · 초록 기반]   https://arxiv.org/abs/{arxiv_id}",
    ])


def generate_digest(scan_result: dict, profile_name: str) -> str:
    """scan_result: run_profile_scan.scan_profile()의 반환값 그대로 받는다.
    returns 메일 본문으로 바로 쓸 수 있는 순수 텍스트(HTML 아님 — 렌더링
    실패 걱정 없이 항상 읽힌다는 걸 우선했다)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"[HARNESS Daily] {date_str} · {profile_name}"
    papers = scan_result.get("papers", [])
    candidates = scan_result.get("candidates_found", 0)

    if not papers:
        return (
            f"{header}\n\n오늘은 새로 걸린 논문이 없습니다"
            f"(후보 {candidates}건 중 프로필 조건에 맞는 것 없음).\n"
        )

    lines = [header, "", f"■ 오늘의 신규 논문 {len(papers)}편 (전체 후보 {candidates}건 중)", ""]
    for i, paper in enumerate(papers, start=1):
        lines.append(_paper_entry(i, paper))
        lines.append("")

    excluded = scan_result.get("excluded_count", 0)
    unmatched = scan_result.get("unmatched_count", 0)
    if excluded or unmatched:
        lines.append(f"■ 이번 실행에서 걸러진 것: 제외 규칙 {excluded}건, 조건 불일치 {unmatched}건")

    return "\n".join(lines).rstrip() + "\n"
