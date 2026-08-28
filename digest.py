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

M2(2026-08-28): M1이 Deep Layer(④⑤⑦)를 붙이면서 이제 논문마다 실제
검증·재현 결과가 DB에 생긴다. 그 상태를 항목마다 라벨로 붙인다 —
"[미검증·초록 기반]"만 있던 다이제스트가 "검증·재현 상태가 달린
다이제스트"가 된다. 여기서도 LLM은 안 쓴다: DB SELECT 와 마커 파일
존재 확인뿐이고, 이 모듈은 아무것도 쓰지 않는다(읽기 전용).

**T+1 지연 보고(설계 결정)**: 다이제스트는 생성 시점의 DB 상태를 그대로
보여준다. ⑦ 재현은 별도 프로세스로 방금 트리거된 참이라 오늘 다이제스트
에서는 대부분 "실행중"으로 나가고, 내일 다이제스트에서 성공/실패로
바뀐다. 재현이 끝나기를 기다리는 폴링을 넣지 않는다 — 새벽 배치가 Docker
빌드를 기다리느라 몇 시간씩 늘어지는 것보다, 하루 늦게 정확한 상태를
보고하는 쪽이 낫다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import server

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


def verification_label(arxiv_id: str) -> str:
    """⑤ 검증 상태 라벨. summaries 의 numbers_total/numbers_matched 만 읽는다
    — flag 건수는 total-matched 로 나오므로 스키마를 늘릴 필요가 없었다
    (M2 착수 전 확인). verify.py 의 grounded 는 DB 에 저장되지 않는 참고용
    값이라 여기서 쓰지 않는다.

    정직성(CLAUDE.md 8): "데이터 없음"과 "통과"를 절대 같게 표시하지
    않는다. 특히 numbers_total=0 은 VerificationReport.pass_ratio 가 1.0 을
    돌려주는 자리라(실측: 저장된 요약 52편 중 1편이 이 경우) 그대로 쓰면
    "검증할 숫자가 하나도 없었다"가 "완벽 통과"로 둔갑한다 — 따로 표기한다.
    """
    try:
        with server._db() as con:
            row = con.execute(
                "SELECT numbers_total, numbers_matched FROM summaries WHERE arxiv_id=?",
                (arxiv_id,),
            ).fetchone()
    except sqlite3.Error:
        # DB·테이블이 아직 없는 환경(새 클론 등)에서도 다이제스트 생성은
        # 계속돼야 한다. 조용히 통과로 만드는 게 아니라 "데이터 없음"으로
        # 정직하게 떨어지는 것이라 이 예외 삼킴은 원칙과 안 부딪힌다.
        return "[검증 데이터 없음]"
    if row is None or row["numbers_total"] is None or row["numbers_matched"] is None:
        return "[검증 데이터 없음]"
    total, matched = row["numbers_total"], row["numbers_matched"]
    if total == 0:
        return "[검증할 수치 없음]"
    label = f"[검증 {matched}/{total} 통과]"
    flag = total - matched
    if flag > 0:
        label += f"  ⚠ flag {flag}건"
    return label


def repro_label(arxiv_id: str) -> str:
    """⑦ 재현 상태 라벨. 네 가지뿐이다 — 실행중/성공/실패/기록없음.
    "실행중"은 docker_runner.launch_background 가 만드는 .running 마커로
    판정한다(같은 규칙: arxiv_id 의 '/'를 '_'로 치환). 마커를 먼저 보는
    이유는, 재현이 도는 중에는 repro_results 에 아직 행이 없어서 DB만
    보면 "기록없음"과 구분이 안 되기 때문이다."""
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    if marker.exists():
        return "[재현 ⏳ 실행중]"
    try:
        with server._db() as con:
            rows = con.execute(
                "SELECT success FROM repro_results WHERE arxiv_id=?", (arxiv_id,)
            ).fetchall()
    except sqlite3.Error:
        return "[재현 –]"
    if not rows:
        return "[재현 –]"
    if any(r["success"] for r in rows):
        return "[재현 ✓]"
    return "[재현 ✗]"


def retraction_label(arxiv_id: str) -> str:
    """⑧ 철회 상태 라벨(M5). 0(정상)·NULL(미조회)은 **아무것도 표시하지
    않는다** — "철회 아님"이라고 쓰면 조회조차 못 한 논문을 검증된 정상으로
    보이게 만든다(CLAUDE.md 8). 위험을 알릴 때만 말한다."""
    try:
        with server._db() as con:
            row = con.execute(
                "SELECT is_retracted FROM papers WHERE arxiv_id=?", (arxiv_id,)
            ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None or row["is_retracted"] is None:
        return ""
    if row["is_retracted"] == 1:
        return "[⚠ 철회된 논문]"
    if row["is_retracted"] == 2:
        return "[주의: 정정/우려 표명 이력]"
    return ""


def injection_label(arxiv_id: str) -> str:
    """③ 인젝션 사전 스캔 결과(M7). 걸린 게 없으면 빈 문자열.

    오탐이 구조적으로 존재한다는 걸 라벨 문구에 담는다 — 인젝션을 연구하는
    논문은 본문에 공격 문구를 그대로 인용하므로 정직하게 걸린다. "위험"이
    아니라 "확인 필요"로 쓴다(injection_scan.py 참고)."""
    try:
        with server._db() as con:
            row = con.execute(
                "SELECT injection_suspect FROM papers WHERE arxiv_id=?", (arxiv_id,)
            ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None or not row["injection_suspect"]:
        return ""
    return "[⚠ 본문에 모델 대상 지시로 보이는 패턴 — 확인 필요]"


def _paper_entry(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = paper.get("arxiv_id", "?")
    lines = [
        f"{idx}. [{_stars(score.get('priority', 0.0))}] {paper.get('title') or '(제목 없음)'}",
    ]
    # 경고는 제목 바로 밑, 다른 어떤 정보보다 먼저 보여준다(M5 철회 / M7 인젝션).
    for warning in (retraction_label(arxiv_id), injection_label(arxiv_id)):
        if warning:
            lines.append(f"   {warning}")
    lines += [
        f"   왜 걸렸나 : {_why_matched(score)}",
        f"   초록 발췌 : {_abstract_excerpt(paper)}",
    ]

    # Deep Layer(M1)가 실패한 논문만 예전의 "미검증 · 초록 기반"으로 남는다
    # — 나머지는 DB 에 실제 검증·재현 결과가 있으므로 그걸 그대로 보여준다.
    # deep_status 키 자체가 없는 경우(M1 이전 경로로 만들어진 결과)도
    # DB 조회 결과가 곧 사실이라 같은 경로로 보낸다.
    deep_status = str(paper.get("deep_status") or "")
    if deep_status.startswith("failed"):
        reason = deep_status.split(":", 1)[1].strip() if ":" in deep_status else "사유 미상"
        # S2 tldr 이 있으면 초록 발췌 대신 그걸 쓴다(M6) — 다만 S2 모델이 만든
        # 요약이지 우리 ⑤를 통과한 게 아니라 라벨을 다르게 단다. 검증된 요약과
        # 절대 같은 라벨을 쓰지 않는다(CLAUDE.md 8).
        tldr = paper.get("s2_tldr")
        if tldr:
            lines[-1] = f"   S2 한줄요약 : {tldr}"
            lines.append(f"   [미검증 · S2 TLDR] 처리 실패: {reason}")
        else:
            lines.append(f"   [미검증 · 초록 기반] 처리 실패: {reason}")
    else:
        lines.append(f"   {verification_label(arxiv_id)}   {repro_label(arxiv_id)}")

    lines.append(f"   https://arxiv.org/abs/{arxiv_id}")
    return "\n".join(lines)


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


# ---------------------------------------------------------------- HTML 다이제스트 (M3)
#
# 텍스트판(generate_digest)은 그대로 둔다 — multipart/alternative 의 plain
# part 로 계속 쓴다. HTML 을 못 읽거나 차단하는 환경에서도 내용이 그대로
# 읽혀야 하기 때문이다.
#
# 이메일 HTML 은 웹 HTML 과 제약이 다르다. 아래는 caniemail 기준으로 확인된
# 사실이고, 설계를 여기에 맞췄다:
#
# 1. <details>/<summary> 는 Gmail 전 플랫폼과 Outlook Windows(Word 엔진)에서
#    동작하지 않는다. Gmail 은 태그를 <u></u> 로 치환해 **항상 펼쳐진 상태**로
#    보인다. Apple Mail 만 토글이 실제로 접힌다. 따라서 접기는 있으면 좋은
#    장식이지 기능이 아니다 — "접힌 상태에서만 보이는 정보"를 두지 않는다.
#    우리 요구(flag 있는 항목은 펼침)는 이 fallback 과 방향이 같아서 오히려
#    잘 맞는다.
# 2. Gmail 은 HTML 이 약 102KB 를 넘으면 메시지를 잘라낸다(클리핑). 논문당
#    발췌 길이로 총량을 제어하고 회귀 테스트로 상한을 잠근다.
# 3. <style> 블록은 Gmail 에서 제한적이라 **인라인 CSS 만** 쓴다.
# 4. 외부 이미지·웹폰트·JS 는 차단되거나 프라이버시 경고를 띄운다 — 안 쓴다.
# 5. 다크모드에서 클라이언트가 색을 뒤집을 수 있다. 배경색과 전경색을
#    항상 **함께** 인라인으로 명시해 대비를 확보한다(색을 안 준 요소를
#    남기지 않는다).

_NAVY = "#12266B"
_INK = "#111111"
_MUTED = "#555555"
_LINE = "#DDDDDD"
_PAPER_BG = "#FFFFFF"
_FLAG_BG = "#FFF4E5"
_FLAG_INK = "#8A4B00"

_HTML_EXCERPT_CHARS = 400  # 텍스트판(220)보다 넉넉하되 102KB 상한을 지키는 선


def _esc(text: str) -> str:
    """HTML 이스케이프. 논문 제목·초록에는 &, <, > 가 실제로 들어온다
    (예: "A < B", "R&D") — 그대로 넣으면 레이아웃이 깨진다."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_excerpt(paper: dict) -> str:
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        return "(초록 없음)"
    if len(abstract) <= _HTML_EXCERPT_CHARS:
        return abstract
    return abstract[:_HTML_EXCERPT_CHARS] + "…"


def _status_chip(label: str, flagged: bool) -> str:
    bg, ink = (_FLAG_BG, _FLAG_INK) if flagged else ("#EEF1F8", _NAVY)
    return (
        f'<span style="display:inline-block;background-color:{bg};color:{ink};'
        f'font-size:12px;padding:2px 8px;border-radius:10px;'
        f'margin-right:6px;">{_esc(label)}</span>'
    )


def _paper_entry_html(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = str(paper.get("arxiv_id", "?"))
    title = paper.get("title") or "(제목 없음)"
    deep_status = str(paper.get("deep_status") or "")

    if deep_status.startswith("failed"):
        reason = deep_status.split(":", 1)[1].strip() if ":" in deep_status else "사유 미상"
        chips = _status_chip("미검증 · 초록 기반", flagged=True)
        detail = f'<div style="color:{_FLAG_INK};font-size:13px;">처리 실패: {_esc(reason)}</div>'
        needs_attention = True
    else:
        v_label = verification_label(arxiv_id)
        r_label = repro_label(arxiv_id)
        # flag 가 있거나 재현이 실패한 항목은 펼쳐서 보낸다. Gmail·Outlook 은
        # 어차피 항상 펼쳐 보여주므로 이 속성이 실제로 의미를 갖는 건
        # Apple Mail 뿐이다(위 주석 1번).
        needs_attention = ("flag" in v_label) or ("✗" in r_label)
        chips = _status_chip(v_label.strip("[]"), flagged="flag" in v_label)
        chips += _status_chip(r_label.strip("[]"), flagged="✗" in r_label)
        detail = ""

    # 철회 경고(M5)는 실패 여부와 무관하게 붙고, 붙으면 무조건 펼친다 —
    # 이 항목에서 가장 중요한 정보다.
    for warning in (retraction_label(arxiv_id), injection_label(arxiv_id)):
        if warning:
            chips = _status_chip(warning.strip("[]"), flagged=True) + chips
            needs_attention = True

    open_attr = " open" if needs_attention else ""
    return (
        f'<details{open_attr} style="background-color:{_PAPER_BG};color:{_INK};'
        f'border:1px solid {_LINE};border-radius:6px;padding:10px 12px;margin-bottom:10px;">'
        f'<summary style="color:{_INK};font-size:15px;font-weight:600;cursor:pointer;">'
        f'{idx}. [{_stars(score.get("priority", 0.0))}] {_esc(title)}</summary>'
        f'<div style="margin-top:8px;">{chips}</div>'
        f'{detail}'
        f'<div style="color:{_MUTED};font-size:13px;margin-top:8px;">'
        f'왜 걸렸나 : {_esc(_why_matched(score))}</div>'
        f'<div style="color:{_INK};font-size:13px;margin-top:6px;line-height:1.5;">'
        f'{_esc(_html_excerpt(paper))}</div>'
        f'<div style="margin-top:8px;font-size:13px;">'
        f'<a href="https://arxiv.org/abs/{_esc(arxiv_id)}" '
        f'style="color:{_NAVY};">arxiv.org/abs/{_esc(arxiv_id)}</a></div>'
        f"</details>"
    )


def generate_digest_html(scan_result: dict, profile_name: str) -> str:
    """텍스트판과 같은 입력으로 HTML 본문을 만든다. generate_digest()는
    그대로 두고(plain part 로 계속 쓴다) 이건 html part 전용이다.

    맨 위에 철회 경고용 슬롯을 비워둔다 — M5(retraction 체크)가 채울 자리다.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    papers = scan_result.get("papers", [])
    candidates = scan_result.get("candidates_found", 0)

    head = (
        f'<div style="background-color:{_NAVY};color:#FFFFFF;'
        f'padding:14px 16px;border-radius:6px;">'
        f'<div style="font-size:17px;font-weight:700;color:#FFFFFF;">HARNESS Daily</div>'
        f'<div style="font-size:13px;color:#DCE3F5;margin-top:2px;">'
        f'{_esc(date_str)} · {_esc(profile_name)}</div></div>'
    )

    # M5 철회 경고 슬롯 — 지금은 비어 있다(주석만 남긴다).
    retraction_slot = "<!-- retraction-warnings -->"

    if not papers:
        body = (
            f'<p style="background-color:{_PAPER_BG};color:{_INK};font-size:14px;">'
            f'오늘은 새로 걸린 논문이 없습니다 '
            f'(후보 {candidates}건 중 프로필 조건에 맞는 것 없음).</p>'
        )
    else:
        entries = "".join(
            _paper_entry_html(i, p) for i, p in enumerate(papers, start=1)
        )
        body = (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:13px;'
            f'margin:14px 0 10px;">오늘의 신규 논문 {len(papers)}편 '
            f'(전체 후보 {candidates}건 중)</p>{entries}'
        )

    excluded = scan_result.get("excluded_count", 0)
    unmatched = scan_result.get("unmatched_count", 0)
    footer = ""
    if excluded or unmatched:
        footer = (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
            f'border-top:1px solid {_LINE};padding-top:10px;">'
            f'이번 실행에서 걸러진 것: 제외 규칙 {excluded}건, '
            f'조건 불일치 {unmatched}건</p>'
        )

    return (
        f'<div style="background-color:#FFFFFF;color:{_INK};'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        f'max-width:680px;padding:8px;">'
        f"{head}{retraction_slot}{body}{footer}</div>"
    )
