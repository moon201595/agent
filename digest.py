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
from pathlib import Path

import server

_ABSTRACT_EXCERPT_CHARS = 220

# 별점은 **총점이 아니라 핵심 키워드 가중치 합**으로 매긴다(2026-09-02).
#
# 총점 기준이 두 가지로 깨져 있었다. 첫째, ★★★ 문턱 1.0 이 도달 불가능했다 —
# 저장된 35편을 재채점하니 최댓값이 0.745 였다. 3단 눈금인데 맨 위가 영원히
# 안 뜨면 2단이나 마찬가지다. 둘째, 최신성(±0.15)이 계층 정보를 덮었다:
# "표적어 단독"이 0.500~0.645 로 흩어져 "동향어+도메인"(0.523~0.545)과
# 겹쳤다 — 같은 별점인데 하나는 우리 표적 도메인이고 하나는 아니다.
#
# 가중치 합을 쓰면 둘 다 사라진다. 읽는 사람이 별점에서 알고 싶은 건
# "이게 우리 주제인가"이지 "며칠 전 논문인가"가 아니고, 어차피 한 다이제스트
# 안의 논문은 전부 비슷하게 최신이다. 순위는 종전대로 총점이 정한다 —
# 별점은 순위가 아니라 **분류**다.
#
# **가중치 합이 아니라 최댓값**을 본다. 합으로는 "표적어를 맞혔나"를 알 수
# 없다 — 실측 분포에서 동향어 두 개(0.6+0.6=1.2)가 표적어 하나(1.0)보다
# 컸다. 별점이 "우리 표적 도메인인가"를 말해야 하는데 합을 쓰면 그게 뒤집힌다.
#
#   ★★★ 표적어를 맞혔고 핵심 적중이 2개 이상
#   ★★  표적어를 맞혔다               (가장 무거운 적중 ≥ 1.0)
#   ★   동향어·범용어만 맞혔다
#
# 실측 분포(35편): ★★ 6편(17%) · ★ 29편. ★★★ 은 아직 0편이지만 도달
# 가능하다 — 옛 총점 기준의 ★★★(문턱 1.0)은 최댓값이 0.745 라 원리적으로
# 불가능했다. 그 차이가 중요하다.
_TARGET_TIER_WEIGHT = 1.0


def _stars(score: dict) -> str:
    """score 는 profile_scoring.score_paper() 의 반환값 전체를 받는다.
    구형 호출부가 숫자를 넘기면 그대로 총점 기준으로 떨어진다(하위 호환)."""
    if not isinstance(score, dict):
        return "★★★" if score >= 1.0 else ("★★" if score >= 0.7 else "★")
    top = score.get("top_core_weight", 0.0) or 0.0
    if top < _TARGET_TIER_WEIGHT:
        return "★"
    return "★★★" if len(score.get("core_hits") or []) >= 2 else "★★"


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


# ---------------------------------------------------------------- 요약정리 발췌
#
# 여기서도 LLM을 부르지 않는다. ④가 이미 만들어 ⑤ 검증까지 통과한 요약
# 파일(data/summaries/<arxiv_id>.md)을 **읽기만** 한다 — 다이제스트가
# 무엇을 보여주든 그 근거는 이미 검증된 저장물이어야 한다는 원칙(CLAUDE.md 7)
# 이 그대로 유지된다. 메일에서 새로 생성되는 문장은 하나도 없다.
#
# 왜 필요했나: 2026-08-31 이전 다이제스트는 제목 + 초록 발췌만 보냈다.
# 논문마다 검증된 요약이 DB에 멀쩡히 있는데도 메일에는 안 실려서, 받는
# 사람이 결국 arXiv를 다시 열어야 했다.

_SECTION_RE = None  # 아래에서 정의(모듈 상단 import 순서 유지)

# 2026-09-02: 메일이 요약의 25%만 싣고 있었다. 절 단위로 재보니 4,000자
# 요약에서 1,212자만 쓰였고, **수치가 들어 있는 본문 `결과` 절(875자)을
# 통째로 건너뛰고** 결론의 압축본만 실었다. `방법 상세`·`실험 설정`도
# 안 실렸다. 받는 사람이 결국 arXiv 를 열어야 하는 상태였다.
#
# 상한은 Gmail 클리핑(약 102KB)에서 역산한다 — 논문당 약 3,000자면 8편에
# 24,000자, UTF-8 한글로 약 72KB 에 마크업을 더해도 들어간다. 회귀 테스트가
# 실제 요약 8편으로 이 상한을 잠근다.
_ONE_LINER_CHARS = 300
_OVERVIEW_BULLETS, _OVERVIEW_CHARS = 3, 350
_METHOD_BULLETS, _METHOD_CHARS = 4, 300
_SETUP_BULLETS, _SETUP_CHARS = 4, 300
_RESULT_BULLETS, _RESULT_CHARS = 3, 500
_LIMIT_CHARS = 350


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _split_sections(markdown: str) -> dict[str, str]:
    """'### 제목' 단위로 쪼갠다. 템플릿 v2 형식이 전제지만, 없는 절은 그냥
    빠질 뿐이라 형식이 달라져도 다이제스트가 깨지지 않는다."""
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in markdown.splitlines():
        if line.startswith("### "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = line[4:].strip(), []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def _bullets(body: str, after: str | None = None) -> list[str]:
    lines = body.splitlines()
    if after is not None:
        for i, line in enumerate(lines):
            if after in line:
                lines = lines[i + 1:]
                break
        else:
            return []
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif out and not stripped:
            break
    return out


def _paragraphs(body: str) -> list[str]:
    """문단 단위로 자른다. `결과` 절은 불릿이 아니라 산문 문단이라
    _bullets 로는 한 줄도 못 건진다."""
    out, buf = [], []
    for line in body.splitlines():
        if line.strip():
            buf.append(line.strip())
        elif buf:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return [p for p in out if p]


def summary_sections(arxiv_id: str) -> dict:
    """저장된 요약에서 메일에 실을 부분만 뽑는다.

    returns {} 이면 호출부는 예전처럼 초록 발췌로 떨어진다 — 요약이 아직
    없거나(Deep Layer 실패) 파일이 사라진 경우다. "요약이 없다"를 조용히
    빈 요약으로 보여주지 않는다.
    """
    try:
        with server._db() as con:
            row = con.execute(
                "SELECT path FROM summaries WHERE arxiv_id=?", (arxiv_id,)
            ).fetchone()
    except sqlite3.Error:
        return {}
    if row is None or not row["path"]:
        return {}
    try:
        markdown = Path(row["path"]).read_text(encoding="utf-8")
    except OSError:
        return {}

    sec = _split_sections(markdown)
    conclusion = sec.get("결론", "")

    one_liner = ""
    for line in conclusion.splitlines():
        if "한 줄 요약" in line and ":" in line:
            one_liner = _clip(line.split(":", 1)[1], _ONE_LINER_CHARS)
            break

    overview = [_clip(b, _OVERVIEW_CHARS)
                for b in _bullets(sec.get("연구 개요", ""))[:_OVERVIEW_BULLETS]]
    method = [_clip(b, _METHOD_CHARS)
              for b in _bullets(sec.get("방법 상세", ""))[:_METHOD_BULLETS]]
    setup = [_clip(b, _SETUP_CHARS)
             for b in _bullets(sec.get("실험 설정", ""))[:_SETUP_BULLETS]]
    # "④ 결과" 는 불릿일 때도 있고 문단일 때도 있다(둘 다 실제 저장물에서
    # 관측됨 — 2026-08-31). 불릿만 보면 문단 형식 논문의 결과가 통째로
    # 빠지는데, 그게 메일에서 제일 중요한 줄이다.
    # 본문 `결과` 절을 우선한다 — 근거 태그가 붙은 실제 수치가 여기 있다.
    # 결론 ④ 는 그걸 한두 줄로 압축한 것이라, 그것만 실으면 메일에서 수치가
    # 거의 사라진다(2026-09-02 실측: 875자 절이 통째로 빠지고 있었다).
    results = [_clip(b, _RESULT_CHARS)
               for b in _paragraphs(sec.get("결과", ""))[:_RESULT_BULLETS]]
    if not results:
        conclusion_lines = conclusion.splitlines()
        for i, line in enumerate(conclusion_lines):
            if "결과" not in line or ":" not in line:
                continue
            inline = line.split(":", 1)[1].strip()
            found = [inline] if inline else _bullets("\n".join(conclusion_lines[i + 1:]))
            results = [_clip(b, _RESULT_CHARS) for b in found[:_RESULT_BULLETS]]
            break

    limits = ""
    for line in sec.get("논문의 한계점", "").splitlines():
        if "요약자가 판단한 한계" in line and ":" in line:
            limits = _clip(line.split(":", 1)[1], _LIMIT_CHARS)
            break

    if not (one_liner or overview or results):
        return {}
    return {"one_liner": one_liner, "overview": overview, "method": method,
            "setup": setup, "results": results, "limits": limits}


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


# 커버리지가 이 값 밑이면 표시한다. 1.0 미만을 전부 표시하면 문장 하나
# 차이로도 라벨이 붙어 노이즈가 된다.
_COVERAGE_WARN_BELOW = 0.98


def coverage_label(arxiv_id: str) -> str:
    """요약이 원문의 몇 할을 보고 쓰였는지 (§8-25).

    Groq 폴백 경로는 청크 상한에 걸리면 긴 논문의 뒤를 통째로 안 본다 —
    실측: 188,412자 논문이 원문 문장의 40.2% 만 보고 요약됐다. 그런데
    **⑤ 검증이 이걸 못 잡는다**: 검증기는 인용한 문장이 원문에 있는지만
    보므로 앞부분만 보고 쓴 요약도 pass_ratio 1.0 이 나온다. 그래서
    "검증 통과"와 "원문을 다 봤다"를 나란히 보여줘야 오해가 없다.

    엔진을 모르는 구형 요약은 커버리지도 NULL 이다 — 빈 문자열을 돌려
    아는 척하지 않는다.
    """
    try:
        with server._db() as con:
            row = con.execute(
                "SELECT coverage_ratio FROM summaries WHERE arxiv_id=?", (arxiv_id,)
            ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None or row["coverage_ratio"] is None:
        return ""
    ratio = float(row["coverage_ratio"])
    if ratio >= _COVERAGE_WARN_BELOW:
        return ""
    return f"⚠ 원문 {ratio * 100:.0f}%만 반영"


# 파이프라인이 어디까지 갔는지의 순서. 여러 후보를 시도했으면 **가장 멀리 간**
# 시도가 그 논문에 대해 제일 많은 것을 말해준다 — clone 도 못 한 후보 두 개보다
# 실제로 실행까지 간 후보 하나가 정보량이 크다.
# install_only 는 build 성공 뒤 실행 대상이 없어 멈춘 상태다 — build 실패보다
# 멀리 갔지만, 실제로 코드를 돌려본 run 보다는 정보량이 적다.
_STAGE_DEPTH = {"clone": 0, "no_target": 1, "build": 2, "install_only": 3, "run": 4}

# (stage, fail_detail) → 라벨. ✗ 와 – 의 구분이 이 표의 핵심이다:
#   ✗ = 코드를 실제로 돌렸는데 실패했다        → 저자 코드에 대한 판정
#   ◐ = 의존성 설치까지는 됐으나 실행 대상이 없다 → 판정 불가, 다만 "설치되는
#       진짜 코드"라는 약한 신호는 있다(2026-09-02)
#   – = 돌려보지도 못했다                      → 저자 코드에 대한 판정이 아님
# 2026-09-01 이전에는 둘 다 [재현 ✗] 였고, 그래서 "저자가 코드를 안 올렸다(404)"가
# "저자 코드가 안 돈다"로 읽혔다(실측: 2608.25176).
_REPRO_LABELS = {
    ("run", "run_network_suspected"): "[재현 ✗ 네트워크 차단 의심]",
    ("run", "run_timeout"): "[재현 ✗ 시간 초과]",
    ("run", "run_nonzero_exit"): "[재현 ✗ 실행 실패]",
    ("build", "build_failed"): "[재현 ✗ 설치 실패]",
    ("install_only", "install_only_no_run_target"): "[재현 ◐ 설치만 확인]",
    ("no_target", "no_install_target"): "[재현 – 실행 대상 없음]",
    ("clone", "repo_not_found"): "[재현 – 저장소 없음(404)]",
    ("clone", "clone_timeout"): "[재현 – 클론 시간 초과]",
    ("clone", "clone_failed"): "[재현 – 클론 실패]",
    ("clone", "unsupported_host"): "[재현 – 클론 불가 호스트]",
}

# fail_detail 이 없는 구형 행(2026-09-01 이전 29건)은 stage 만으로 판정한다.
# 데이터가 없는 것을 아는 척하지 않되, 그렇다고 전부 [재현 ✗] 로 되돌리지도
# 않는다 — stage 만으로도 "돌려봤는가"는 알 수 있기 때문이다.
_REPRO_LABELS_BY_STAGE = {
    "run": "[재현 ✗ 실행 실패]",
    "install_only": "[재현 ◐ 설치만 확인]",
    "build": "[재현 ✗ 설치 실패]",
    "no_target": "[재현 – 실행 대상 없음]",
    "clone": "[재현 – 클론 실패]",
}


def repro_label(arxiv_id: str) -> str:
    """⑦ 재현 상태 라벨.

    "실행중"은 docker_runner.launch_background 가 만드는 .running 마커로
    판정한다(같은 규칙: arxiv_id 의 '/'를 '_'로 치환). 마커를 먼저 보는
    이유는, 재현이 도는 중에는 repro_results 에 아직 행이 없어서 DB만
    보면 "기록없음"과 구분이 안 되기 때문이다.

    실패했을 때는 사유까지 말한다(2026-09-01). 예전에는 서로 완전히 다른
    네 가지 사실이 [재현 ✗] 하나로 뭉개졌다 — 저장소가 404 인 것, 클론은
    됐는데 실행 대상이 없는 것, 설치가 실패한 것, 실행이 네트워크 차단으로
    죽었을 수 있는 것. 앞의 둘은 저자 코드에 대한 판정이 아예 아닌데도
    "저자 코드가 안 돈다"로 읽혔다.
    """
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    if marker.exists():
        return "[재현 ⏳ 실행중]"
    try:
        with server._db() as con:
            rows = con.execute(
                "SELECT success, stage, fail_detail FROM repro_results WHERE arxiv_id=? "
                "ORDER BY attempt",
                (arxiv_id,),
            ).fetchall()
    except sqlite3.Error:
        # 구형 스키마(fail_detail 컬럼 없음)에서도 다이제스트는 계속 나가야 한다.
        return _repro_label_legacy(arxiv_id)
    if not rows:
        return "[재현 –]"
    if any(r["success"] for r in rows):
        return "[재현 ✓]"

    deepest = max(rows, key=lambda r: _STAGE_DEPTH.get(r["stage"], -1))
    stage, detail = deepest["stage"], deepest["fail_detail"]
    if detail:
        label = _REPRO_LABELS.get((stage, detail))
        if label:
            return label
    return _REPRO_LABELS_BY_STAGE.get(stage, "[재현 ✗]")


def _repro_label_legacy(arxiv_id: str) -> str:
    """fail_detail 컬럼이 아직 없는 DB용 폴백 — 예전 동작 그대로."""
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


def paper_link(paper: dict) -> str:
    """논문으로 가는 링크. arXiv 논문이면 abs 페이지, 아니면 DOI.

    2026-09-03 실측: S2 로 들어온 저널 논문에 arxiv_id 가 없어 메일에
    `https://arxiv.org/abs/None` 이 찍혔다 — 클릭하면 404 다. 소스가 둘이
    됐으면 링크도 둘이어야 한다.
    """
    arxiv_id = paper.get("arxiv_id")
    if arxiv_id and not str(arxiv_id).startswith("pdf-"):
        return f"https://arxiv.org/abs/{arxiv_id}"
    doi = paper.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    return paper.get("open_access_pdf") or ""


def _paper_entry(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = paper.get("arxiv_id", "?")
    lines = [
        f"{idx}. [{_stars(score)}] {paper.get('title') or '(제목 없음)'}",
    ]
    # 경고는 제목 바로 밑, 다른 어떤 정보보다 먼저 보여준다(M5 철회 / M7 인젝션).
    for warning in (retraction_label(arxiv_id), injection_label(arxiv_id)):
        if warning:
            lines.append(f"   {warning}")
    lines.append(f"   왜 걸렸나 : {_why_matched(score)}")

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
            lines.append(f"   S2 한줄요약 : {tldr}")
            lines.append(f"   [미검증 · S2 TLDR] 처리 실패: {reason}")
        else:
            lines.append(f"   초록 발췌 : {_abstract_excerpt(paper)}")
            lines.append(f"   [미검증 · 초록 기반] 처리 실패: {reason}")
    else:
        # 검증된 요약이 있으면 그걸 싣는다. 없으면(요약 파일 유실 등) 예전처럼
        # 초록 발췌로 떨어진다 — "요약 없음"을 빈 요약으로 보여주지 않는다.
        sections = summary_sections(arxiv_id)
        if sections:
            if sections["one_liner"]:
                lines.append(f"   한 줄 요약 : {sections['one_liner']}")
            for label, key in (("무엇을·어떻게", "overview"), ("방법 상세", "method"),
                               ("실험 설정", "setup"), ("핵심 결과", "results")):
                if sections.get(key):
                    lines.append(f"   {label} :")
                    lines += [f"     - {b}" for b in sections[key]]
            if sections["limits"]:
                lines.append(f"   한계 : {sections['limits']}")
        else:
            lines.append(f"   초록 발췌 : {_abstract_excerpt(paper)}")
        labels = f"   {verification_label(arxiv_id)}   {repro_label(arxiv_id)}"
        cov = coverage_label(arxiv_id)
        if cov:
            labels += f"   {cov}"
        lines.append(labels)

    link = paper_link(paper)
    if link:
        lines.append(f"   {link}")
    return "\n".join(lines)


_TREND_KEYWORDS_SHOWN = 12


def _trend_line(scan_result: dict) -> str:
    """상위 몇 편이 아니라 **후보 전체**에서 어떤 주제가 몇 편이었는지.

    "이번 주 무엇이 늘었나"는 상위 5편만 봐서는 알 수 없는데, 그 질문에는
    LLM 없이 셈만으로 답할 수 있다(CLAUDE.md 7). 실제로 이 값은
    profile_scoring.score_and_rank 가 top_k 로 자르기 전에 세어 둔 것이다.
    """
    counts = scan_result.get("core_hit_counts") or {}
    if not counts:
        return ""
    top = list(counts.items())[:_TREND_KEYWORDS_SHOWN]
    return " · ".join(f"{kw} {n}" for kw, n in top)


def _filtered_line(scan_result: dict) -> str:
    """이번 실행에서 무엇이 걸러졌는지. 겹치는 창을 다시 조회하게 되면서
    (§8-26) "이미 보낸 논문"이 새로운 항목으로 생겼다 — 조용히 빼면 후보
    수가 왜 줄었는지 설명이 안 된다."""
    parts = []
    deferred = scan_result.get("deferred_count", 0)
    if deferred:
        # 이건 "걸러졌다"가 아니라 "아직 안 했다"이므로 표현을 구분한다 —
        # 내일 후보에 다시 올라와 그때 제대로 실린다(§8-14, §8-26).
        parts.append(f"시간 예산으로 내일로 미룸 {deferred}건")
    seen = scan_result.get("already_seen_count", 0)
    if seen:
        parts.append(f"이미 보낸 논문 {seen}건")
    excluded = scan_result.get("excluded_count", 0)
    if excluded:
        parts.append(f"제외 규칙 {excluded}건")
    unmatched = scan_result.get("unmatched_count", 0)
    if unmatched:
        parts.append(f"조건 불일치 {unmatched}건")
    return ", ".join(parts)


def _title_only_section(scan_result: dict) -> list[str]:
    """본문을 못 받는 논문 목록. 제목·출처·링크만 한 줄씩.

    2026-09-03 실측: S2 를 붙이자 팀 표적 논문이 실제로 올라왔는데
    (PhyHGNet 등 ★★★ 2편) **59편 중 35편(59%)이 arXiv ID 도 오픈액세스
    PDF 도 없었다.** 그날 상위 6편 중 5편이 그런 논문이라 요약 없이
    "처리 실패"로 나갔다.

    **"실패"가 아니다.** 저자가 코드를 안 올린 것을 `[재현 ✗]` 로 부르면
    안 되는 것과 같은 이유로(§8-24), 페이월 뒤에 있는 논문을 "처리 실패"로
    부르면 안 된다 — 우리 쪽이 고장난 게 아니다. 기관 구독으로 볼 수 있는
    논문이니 **제목과 링크를 주는 것 자체가 정보**다.
    """
    papers = scan_result.get("title_only_papers") or []
    if not papers:
        return []
    lines = ["", f"■ 본문 비공개 — 제목만 확인 ({len(papers)}편)",
             "   (arXiv 에도 없고 오픈액세스 PDF 도 없다. 기관 구독으로 볼 수 있다)"]
    for paper in papers:
        score = paper.get("_score", {})
        lines.append(f"   [{_stars(score)}] {paper.get('title') or '(제목 없음)'}")
        why = _why_matched(score)
        venue = (paper.get("venue") or "").strip()
        lines.append(f"        {why}" + (f" / {venue}" if venue else ""))
        link = paper_link(paper)
        if link:
            lines.append(f"        {link}")
    return lines


def generate_digest(scan_result: dict, profile_name: str) -> str:
    """scan_result: run_profile_scan.scan_profile()의 반환값 그대로 받는다.
    returns 메일 본문으로 바로 쓸 수 있는 순수 텍스트(HTML 아님 — 렌더링
    실패 걱정 없이 항상 읽힌다는 걸 우선했다)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"[HARNESS Daily] {date_str} · {profile_name}"
    papers = scan_result.get("papers", [])
    candidates = scan_result.get("candidates_found", 0)

    title_only = scan_result.get("title_only_papers") or []

    if not papers and not title_only:
        # 빈 다이제스트일수록 **왜** 비었는지가 중요하다. 2026-09-01 에 후보
        # 0편 메일이 나갔을 때 사람이 제일 먼저 물은 게 "이게 정상이냐"였고,
        # 그 답이 메일 안에 없었다. 걸러진 내역을 여기에도 붙인다.
        reason = _filtered_line(scan_result)
        body = f"오늘은 새로 걸린 논문이 없습니다(후보 {candidates}건)."
        if reason:
            body += f"\n걸러진 것: {reason}"
        if candidates == 0 and not reason:
            body += ("\n검색 자체가 0건이었습니다 — arXiv 색인이 며칠 뒤처지므로 "
                     "최근 며칠은 다음 실행에서 다시 조회합니다.")
        return f"{header}\n\n{body}\n"

    lines = [header, ""]
    if papers:
        lines += [f"■ 오늘의 신규 논문 {len(papers)}편 (전체 후보 {candidates}건 중)", ""]
        for i, paper in enumerate(papers, start=1):
            lines.append(_paper_entry(i, paper))
            lines.append("")
    else:
        lines += [f"■ 본문을 받을 수 있는 신규 논문은 없었습니다 (전체 후보 {candidates}건 중).", ""]

    lines += _title_only_section(scan_result)
    if lines and lines[-1] != "":
        lines.append("")

    trend = _trend_line(scan_result)
    if trend:
        lines += [f"■ 이번 창의 동향 신호 (후보 {candidates}건에서 핵심 키워드별 적중 편수)",
                  f"   {trend}", ""]

    filtered = _filtered_line(scan_result)
    if filtered:
        lines.append(f"■ 이번 실행에서 걸러진 것: {filtered}")

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


def _summary_block_html(arxiv_id: str, paper: dict, deep_status: str) -> str:
    """검증된 요약을 HTML 로. 없으면 예전처럼 초록 발췌로 떨어진다.

    텍스트판(_paper_entry)과 같은 판단을 쓴다 — 두 판이 다른 내용을 보여주면
    "HTML 을 차단한 사람만 다른 메일을 받는" 상황이 되므로, 분기 조건을
    한 곳(summary_sections 의 반환값 유무)으로 맞춰 둔다.
    """
    def para(text: str, *, muted: bool = False, top: int = 6) -> str:
        color = _MUTED if muted else _INK
        return (f'<div style="color:{color};font-size:13px;margin-top:{top}px;'
                f'line-height:1.5;">{_esc(text)}</div>')

    def bullets(label: str, items: list[str]) -> str:
        lis = "".join(
            f'<li style="color:{_INK};font-size:13px;line-height:1.5;'
            f'margin-bottom:3px;">{_esc(b)}</li>' for b in items
        )
        return (f'<div style="color:{_MUTED};font-size:12px;font-weight:600;'
                f'margin-top:8px;">{_esc(label)}</div>'
                f'<ul style="margin:4px 0 0;padding-left:18px;">{lis}</ul>')

    if deep_status.startswith("failed"):
        tldr = paper.get("s2_tldr")
        return para(tldr) if tldr else para(_html_excerpt(paper))

    sections = summary_sections(arxiv_id)
    if not sections:
        return para(_html_excerpt(paper))

    out = ""
    if sections["one_liner"]:
        out += para(sections["one_liner"], top=8)
    for label, key in (("무엇을·어떻게", "overview"), ("방법 상세", "method"),
                       ("실험 설정", "setup"), ("핵심 결과", "results")):
        if sections.get(key):
            out += bullets(label, sections[key])
    if sections["limits"]:
        out += para("한계 : " + sections["limits"], muted=True, top=8)
    return out


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
        c_label = coverage_label(arxiv_id)
        needs_attention = ("flag" in v_label) or ("✗" in r_label) or bool(c_label)
        chips = _status_chip(v_label.strip("[]"), flagged="flag" in v_label)
        chips += _status_chip(r_label.strip("[]"), flagged="✗" in r_label)
        if c_label:
            # 커버리지 경고는 flag 취급한다 — "검증 통과"만 보고 요약을
            # 그대로 믿으면 안 되는 상황이라 눈에 띄어야 한다.
            chips += _status_chip(c_label, flagged=True)
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
        f'{idx}. [{_stars(score)}] {_esc(title)}</summary>'
        f'<div style="margin-top:8px;">{chips}</div>'
        f'{detail}'
        f'<div style="color:{_MUTED};font-size:13px;margin-top:8px;">'
        f'왜 걸렸나 : {_esc(_why_matched(score))}</div>'
        f'{_summary_block_html(arxiv_id, paper, deep_status)}'
        f'<div style="margin-top:8px;font-size:13px;">'
        f'<a href="{_esc(paper_link(paper))}" '
        f'style="color:{_NAVY};">{_esc(paper_link(paper).replace("https://", ""))}</a></div>'
        f"</details>"
    )


def _title_only_html(scan_result: dict) -> str:
    """본문 비공개 논문 목록의 HTML 판. 근거는 _title_only_section 주석 참고."""
    papers = scan_result.get("title_only_papers") or []
    if not papers:
        return ""
    rows = []
    for paper in papers:
        score = paper.get("_score", {})
        link = paper_link(paper)
        title = _esc(paper.get("title") or "(제목 없음)")
        if link:
            title = (f'<a href="{_esc(link)}" style="color:{_NAVY};'
                     f'text-decoration:none;">{title}</a>')
        venue = (paper.get("venue") or "").strip()
        meta = _esc(_why_matched(score)) + (f" / {_esc(venue)}" if venue else "")
        rows.append(
            f'<li style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
            f'margin-bottom:6px;">{_esc(_stars(score))} {title}'
            f'<br><span style="color:{_MUTED};font-size:12px;">{meta}</span></li>'
        )
    return (
        f'<p style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
        f'font-weight:600;border-top:1px solid {_LINE};padding-top:10px;'
        f'margin-top:14px;margin-bottom:4px;">본문 비공개 — 제목만 확인 '
        f'({len(papers)}편)</p>'
        f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
        f'margin:0 0 8px;">arXiv 에도 없고 오픈액세스 PDF 도 없습니다. '
        f'기관 구독으로 볼 수 있습니다.</p>'
        f'<ul style="background-color:{_PAPER_BG};padding-left:18px;margin:0;">'
        + "".join(rows) + "</ul>"
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
    title_only = scan_result.get("title_only_papers") or []

    if not papers and not title_only:
        reason = _filtered_line(scan_result)
        extra = ""
        if reason:
            extra = f'<br>걸러진 것: {_esc(reason)}'
        elif candidates == 0:
            extra = ('<br>검색 자체가 0건이었습니다 — arXiv 색인이 며칠 뒤처지므로 '
                     '최근 며칠은 다음 실행에서 다시 조회합니다.')
        body = (
            f'<p style="background-color:{_PAPER_BG};color:{_INK};font-size:14px;">'
            f'오늘은 새로 걸린 논문이 없습니다 (후보 {candidates}건).{extra}</p>'
        )
    elif papers:
        entries = "".join(
            _paper_entry_html(i, p) for i, p in enumerate(papers, start=1)
        )
        body = (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:13px;'
            f'margin:14px 0 10px;">오늘의 신규 논문 {len(papers)}편 '
            f'(전체 후보 {candidates}건 중)</p>{entries}'
        )
    else:
        body = (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:13px;'
            f'margin:14px 0 10px;">본문을 받을 수 있는 신규 논문은 없었습니다 '
            f'(전체 후보 {candidates}건 중).</p>'
        )

    body += _title_only_html(scan_result)

    trend = _trend_line(scan_result)
    if trend:
        body += (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
            f'border-top:1px solid {_LINE};padding-top:10px;margin-top:14px;">'
            f'<span style="color:{_INK};font-weight:600;">이번 창의 동향 신호</span> '
            f'(후보 {candidates}건에서 핵심 키워드별 적중 편수)<br>{_esc(trend)}</p>'
        )

    filtered = _filtered_line(scan_result)
    footer = ""
    if filtered:
        footer = (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
            f'border-top:1px solid {_LINE};padding-top:10px;">'
            f'이번 실행에서 걸러진 것: {_esc(filtered)}</p>'
        )

    return (
        f'<div style="background-color:#FFFFFF;color:{_INK};'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        f'max-width:680px;padding:8px;">'
        f"{head}{retraction_slot}{body}{footer}</div>"
    )
