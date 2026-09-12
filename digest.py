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

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import storage

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

# 논문 한 편이 메일에서 차지하는 본문 길이(2026-09-05 개편).
# 목록은 훑는 것이라 한 줄이면 된다 — 종합은 맨 아래 동향 절이 맡는다.
_GIST_CHARS = 200


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# 절 제목 줄. `##`~`####` 를 다 받고 앞뒤 장식·굵게는 벗긴다.
#
# **2026-09-08 정정.** 예전에는 `line.startswith("### ")` 하나였다. 그래서
# `### **결과**` 는 제목이 "**결과**" 로 잡혀 조회에 안 걸렸고, `## 결과` 는
# 절 자체를 인식 못 해 요약이 통째로 비었다. **일부 절만 실패하면 나머지는
# 그대로 표시되므로 누락 안내조차 없다** — 화면이 LLM 출력 형태에 조용히
# 의존하던 가장 큰 덩어리였다(외부 검토가 재현).
_HEADING_LINE_RE = re.compile(r"^\s{0,3}(#{2,6})\s+(.+?)\s*$")
_HEADING_DEPTH_RE = re.compile(r"^\s{0,3}(#{2,6})\s+\S", re.MULTILINE)

# 절 이름의 별칭. 프롬프트가 시킨 이름을 모델이 조금씩 바꿔 쓴다.
_SECTION_ALIASES = {
    "핵심 결과": "결과", "주요 결과": "결과", "실험 결과": "결과",
    "한계": "논문의 한계점", "한계점": "논문의 한계점",
    "개요": "연구 개요", "방법": "방법 상세", "실험 셋업": "실험 설정",
}


def _canonical_section(title: str) -> str:
    """제목에서 장식을 벗기고 별칭을 표준 이름으로."""
    name = _MD_BOLD_RE.sub(r"\1", title).strip().strip("*_`").strip()
    name = re.sub(r"^[0-9]+[.)]\s*", "", name).strip()
    return _SECTION_ALIASES.get(name, name)


def _split_sections(markdown: str) -> dict[str, str]:
    """제목 단위로 쪼갠다. 표제 형식이 달라져도 내용이 사라지지 않게,
    굵게·번호·별칭을 정규화해 담는다.

    **절의 깊이는 문서가 정한다**(2026-09-08 정정). `##`~`####` 를 전부 절로
    보면 절 **안의 하위 제목**까지 최상위로 올라가 부모 절이 비어 버린다 —
    실측: `pdf-f9518e4d9d` 는 `### 결과` 아래에 `#### ...` 네 개를 두는데,
    그렇게 쪼개니 `결과` 의 내용이 0개가 됐다. 그래서 **문서에 나오는 가장
    얕은 제목 깊이**를 절로 삼고 그보다 깊은 제목은 본문으로 둔다. 모델이
    전체를 `##` 로 낮춰 써도, `###` 안에 `####` 를 써도 둘 다 맞게 나뉜다.

    같은 표준 이름이 두 번 나오면 **먼저 나온 것을 남긴다** — 뒤에 오는
    "파싱 품질 노트" 같은 부록이 본문을 덮어쓰지 않게.
    """
    levels = [len(m.group(1)) for m in _HEADING_DEPTH_RE.finditer(markdown)]
    if not levels:
        return {}
    top = min(levels)

    sections: dict[str, str] = {}
    current, buf = None, []

    def flush():
        if current and current not in sections:
            sections[current] = "\n".join(buf).strip()

    for line in markdown.splitlines():
        m = _HEADING_LINE_RE.match(line)
        if m and len(m.group(1)) == top:
            flush()
            current, buf = _canonical_section(m.group(2)), []
        elif current:
            buf.append(line)
    flush()
    return sections


# 불릿 기호. `-` 만 받다가 `*`·`•`·`·`·번호 목록에서 내용이 통째로 빠졌다.
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•·–—]|[0-9]+[.)])\s+(.*)$")


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
        m = _BULLET_LINE_RE.match(line)
        if m:
            out.append(_MD_BOLD_RE.sub(r"\1", m.group(1)).strip())
        elif out and not line.strip():
            break
    if out:
        return out
    # **불릿이 하나도 없으면 산문을 쓴다**(2026-09-08). 실측: `pdf-f9518e4d9d`
    # 의 `결과` 절은 `####` 하위 제목 아래 문단으로만 쓰여 있어 불릿이 0개였고,
    # 그 논문의 결과는 메일에도 동향에도 **한 번도 실린 적이 없다.** 형식이
    # 다르다고 내용을 버리지 않는다 — 하위 제목 줄은 본문이 아니므로 뺀다.
    for line in lines:
        text = line.strip()
        if not text or _HEADING_LINE_RE.match(line):
            continue
        out.append(_MD_BOLD_RE.sub(r"\1", text))
    return out


_BULLET_RE = re.compile(r"^\s*[-*•]\s+")


def _paragraphs(body: str) -> list[str]:
    """`결과` 절을 항목 단위로 자른다. 빈 줄로도 자르고 **불릿으로도 자른다**.

    처음엔 빈 줄로만 잘랐다 — "결과 절은 불릿이 아니라 산문 문단"이라는
    전제였다. **그 전제가 항상 참이 아니다**(2026-09-04 실측, 2608.28070):
    같은 템플릿인데 모델이 불릿 세 줄로 쓸 때가 있고, 그러면 빈 줄이 없어
    셋이 한 문단으로 뭉친 뒤 렌더러가 앞에 불릿을 하나 더 붙여 메일에
    `- - CF-YOLO는 … - 컴포넌트 소거 … - 외부 검증용 …` 이 찍혔다.

    모델이 어느 형식으로 쓸지에 의존하지 않게 **둘 다 받는다** — `_plain` 을
    넣은 것과 같은 이유다(형식이 모델의 지시 준수에 의존하면 안 된다).
    """
    out, buf = [], []

    def flush():
        if buf:
            out.append(" ".join(buf))
            buf.clear()

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif _BULLET_RE.match(line):
            flush()                              # 불릿은 언제나 새 항목이다
            buf.append(_BULLET_RE.sub("", stripped))
        else:
            buf.append(stripped)                 # 이어지는 줄은 같은 항목
    flush()
    return [p for p in out if p]


def summary_sections(arxiv_id: str) -> dict:
    """저장된 요약에서 메일에 실을 부분만 뽑는다.

    returns {} 이면 호출부는 예전처럼 초록 발췌로 떨어진다 — 요약이 아직
    없거나(Deep Layer 실패) 파일이 사라진 경우다. "요약이 없다"를 조용히
    빈 요약으로 보여주지 않는다.
    """
    try:
        with storage.db() as con:
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

    # 저자 진술과 모델의 비판은 근거 수준이 다르다(2026-09-09).
    limits, author_limits = "", ""
    for line in sec.get("논문의 한계점", "").splitlines():
        plain = _plain(line)
        if ":" not in plain:
            continue
        label, value = plain.split(":", 1)
        if "요약자가 판단한 한계" in label:
            limits = _clip(value, _LIMIT_CHARS)
        elif "저자" in label and "한계" in label:
            author_limits = _clip(value, _LIMIT_CHARS)

    if not (one_liner or overview or results):
        return {}
    return {"one_liner": one_liner, "overview": overview, "method": method,
            "setup": setup, "results": results, "limits": limits,
            "author_limits": author_limits}


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
        with storage.db() as con:
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
        with storage.db() as con:
            row = con.execute(
                "SELECT coverage_ratio, coverage_kind FROM summaries WHERE arxiv_id=?",
                (arxiv_id,),
            ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None or row["coverage_ratio"] is None:
        return ""
    ratio = float(row["coverage_ratio"])
    if ratio >= _COVERAGE_WARN_BELOW:
        return ""
    # 값의 뜻을 같이 말한다(2026-09-07, §8-70). 'planned' 는 그 엔진 설정의
    # 상한이라 실제로 그만큼 봤다는 보장이 없다 — 실측과 같은 말투로 쓰면
    # 재지 않은 값을 잰 값이라 부르는 게 된다(규칙 8).
    # 'measured' 만 실측 말투를 쓴다. 'planned' 와 NULL(2026-09-07 이전에
    # 저장된 행 — 전부 계획값으로 계산됐다)은 "그 설정의 상한"이지 실제로
    # 그만큼 봤다는 근거가 아니다. 여기서 말투를 안 가르면 예전 거짓말이
    # 레거시 행을 통해 그대로 이어진다(규칙 8).
    kind = row["coverage_kind"] if "coverage_kind" in row.keys() else None
    if kind != "measured":
        return f"⚠ 원문 {ratio * 100:.0f}%까지만 볼 수 있는 설정 (실측 아님)"
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


def _verified_kind(arxiv_id: str) -> str:
    """성공한 재현이 **실제로 무엇을 확인했는지**. 로그를 읽기만 한다.

    2026-09-04 실측 — "재현을 진짜 하냐"는 질문에 성공 3건을 열어봤더니:

      2609.02212  `fudu --help`          → CLI 사용법이 출력됨 (2.6초)
      2110.15045  `python -c "import models"` → **출력 없음, exit 0** (1.3초)
      2405.15793  같은 형태

    **이건 "논문 결과가 재현됐다"가 아니다.** 설치가 되고 임포트가 되거나
    진입점이 응답한다는 뜻이다. 그런데 라벨은 그냥 `[재현 ✓]` 였다 —
    읽는 사람은 결과가 재현된 줄 안다.

    이 프로젝트가 계속 없애 온 뭉갬과 같은 종류다(§8-23·24·33·41):
    서로 다른 상태를 한 기호로 덮으면 안 된다. TSPulse 거짓 성공(§5)이
    바로 이 실패였고, 규칙 7 이 막으려는 것도 정확히 이것이다.

    `docker_runner` 를 고치지 않는다(민감 모듈, 규칙 13) — 이미 로그에
    `plan.run_cmd` 가 남아 있어서 읽기만 하면 된다.
    """
    path = Path(storage.REPRO_DIR) / f"{arxiv_id}.log"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # 로그 파일 앞부분은 평문이다(후보 탐색 로그 등). JSON 은 그 뒤에 붙는다 —
    # 실측(2609.02212): 통째로 파싱하면 실패해서 라벨이 조용히 뭉뚱그려졌다.
    data = None
    for start in (0, raw.find("\n{")):
        if start < 0:
            continue
        try:
            data = json.loads(raw[start:])
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        return ""
    for attempt in data.get("log") or []:
        if not attempt.get("success"):
            continue
        run_cmd = ((attempt.get("plan") or {}).get("run_cmd") or "")
        if "--help" in run_cmd:
            return "설치·진입점 확인"
        if run_cmd.startswith("python -c") and "import" in run_cmd:
            return "설치·임포트 확인"
        return ""
    return ""


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
    marker = storage.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    if marker.exists():
        return "[재현 ⏳ 실행중]"
    try:
        with storage.db() as con:
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
        # **무엇을 확인했는지까지 말한다.** 맨 `✓` 는 "결과가 재현됐다"로 읽힌다.
        kind = _verified_kind(arxiv_id)
        return f"[재현 ✓ {kind}]" if kind else "[재현 ✓]"

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
        with storage.db() as con:
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
        with storage.db() as con:
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
        with storage.db() as con:
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


# 초록 정리의 앞 라벨. 프롬프트가 시키는 문구라 목록으로 셀 수 있다.
_GIST_LABELS = (
    "무엇을 하려 했는가", "무엇을 하려 했다는가", "어떻게 했는가", "무엇을 보였는가",
    "무엇을·어떻게", "한 줄 요약", "요지", "개요", "핵심", "결론",
)


def _strip_leading_label(text: str) -> str:
    """`무엇을 하려 했는가 : ...` 같은 **라벨만** 떼고 본문은 지키다.

    **2026-09-08 정정.** 예전에는 첫 ` : ` · `: ` · ` — ` 앞을 라벨인지 확인하지
    않고 버렸다. 그래서 `기존 접근은 실패한다 — 제안 기법은 이를 해결한다.` 가
    `제안 기법은 이를 해결한다.` 로 잘렸다 — **문장의 앞 절반이 사라진 것**이고
    읽는 사람은 잘린 줄도 모른다(외부 검토가 재현).

    이제 앞부분이 (a) 알려진 라벨이거나 (b) 짧고 문장 부호가 없는 이름꼴일
    때만 뗀다. 판단이 안 서면 **그대로 둔다** — 지우는 쪽이 손해가 크다.
    """
    for sep in (" : ", ": ", " — "):
        head, found, rest = text.partition(sep)
        if not found or not rest.strip():
            continue
        label = head.strip().strip("*_`").strip()
        known = any(label.startswith(x) for x in _GIST_LABELS)
        # 이름꼴: 짧고, 문장을 끝내는 부호가 없고, 조사로 끝나지 않는다.
        name_like = (len(label) <= 14 and not re.search(r"[.!?。]", label)
                     and not re.search(r"(다|요|음|함)$", label))
        if known or name_like:
            return rest.strip()
        break      # 첫 구분자가 라벨이 아니면 뒤도 볼 것 없다
    return text


def _one_line_gist(paper: dict, sections: dict) -> str:
    """"이게 무슨 논문인가" 한 줄. 메일 본문에 논문마다 들어가는 전부다.

    **2026-09-05 개편.** 그전에는 논문마다 `무엇을·어떻게`·`방법 상세`·
    `실험 설정`·`핵심 결과`·`한계` 를 다 실었다. 논문 하나가 8~24줄이라
    6편이면 메일이 100줄을 넘었고, **읽는 사람이 "오늘 뭐가 있었나"를
    한눈에 못 봤다.**

    사용자 지적: "논문별로는 간단하게 뭐하는 논문인지만 써주고, 맨 아래에
    전체적인 동향 정리를 해줘야지."

    맞는 구조다. 논문 목록은 **훑는 것**이고 종합은 **읽는 것**이다. 둘을
    같은 밀도로 쓰면 둘 다 안 읽힌다. 자세한 내용은 저장된 요약 파일에
    그대로 남아 있고 링크로 원문에 간다 — 메일에서 빠질 뿐 사라지지 않는다.

    우선순위: 본문 요약의 한 줄 > 본문 요약의 첫 항목 > 초록 정리의 첫 항목.
    """
    if sections.get("one_liner"):
        return _plain(sections["one_liner"])
    if sections.get("overview"):
        return _plain(sections["overview"][0])
    brief = (paper.get("abstract_brief") or "").strip()
    for line in brief.splitlines():
        cleaned = _plain(_BULLET_RE.sub("", line.strip()))
        if not cleaned:
            continue
        cleaned = _strip_leading_label(cleaned)
        if cleaned:
            return cleaned
    return ""


def _paper_retraction_label(paper: dict) -> str:
    state = paper.get("_delivered_state")
    if state is None:
        return retraction_label(paper.get("arxiv_id", "?"))
    return {1: "[⚠ 철회된 논문]", 2: "[주의: 정정/우려 표명 이력]"}.get(state["retraction"], "")


def _paper_repro_label(paper: dict) -> str:
    outcome = paper.get("repro_outcome")
    if outcome is not None:
        status = outcome.get("status")
        if status == "timeout":
            return "[코드 재현 대기 시간 초과 — " + outcome["reason"] + "]"
        if status == "not_attempted":
            return "[" + outcome["reason"] + "]"
        if outcome.get("success"):
            reused = "기존 " if status == "cached" else ""
            return f"[{reused}코드 설치·실행 성공 — 논문 성능 수치 재현은 미확인]"
        if status == "unconfirmed":
            return "[코드 재현 결과 미확인 — 작업 종료 후 결과 기록 없음]"
        if outcome.get("reason") == "저장소 후보 없음":
            return "[코드 저장소를 찾지 못해 재현하지 못함]"
        attempts = outcome.get("log") or []
        last = attempts[-1] if attempts else {}
        detail = last.get("fail_detail") or outcome.get("reason") or "상세 기록 없음"
        return f"[코드 재현 실패 — {last.get('stage', '종료')} · {detail}]"
    state = paper.get("_delivered_state")
    if state is None:
        return repro_label(paper.get("arxiv_id", "?"))
    import evidence_state
    return "[" + evidence_state.state_label(state, "repro") + "]"


def _state_update_lines(result: dict) -> list[str]:
    """2026-09-10: 과거 상태 소식은 보내지 않는다. 오래된 미리보기 입력도 차단한다."""
    return []


def _date_label(paper: dict) -> str:
    """발표·관측·처리는 날짜가 같을 때도 서로 다른 사건이다."""
    def value(key: str) -> str:
        return str(paper.get(key) or "미기록")
    return (f"발표 {value('published')} · 최초 발견 {value('first_seen')} · "
            f"요약 생성 {value('summarized_at')}")


def _scope_lines(result: dict) -> list[str]:
    """집계 범위 밖의 연구 동향을 단정하지 않도록 매일 같은 경계를 알린다."""
    lines = ["■ 수집 범위와 해석 한계",
             "이번 수집 표본의 관찰이다. 분야 전체의 증가·감소를 뜻하지 않는다."]
    if result.get("since") or result.get("until"):
        lines.append(f"검색 창: {result.get('since', '미기록')} ~ {result.get('until', '미기록')}")
    for source, status_key, count_key in (("arxiv", "run_status", "arxiv_count"),
                                          ("s2", "s2_status", "s2_count")):
        if status_key in result:
            signature = (result.get("search_signatures") or {}).get(source, "미기록")
            lines.append(f"{source}: {result[status_key]} · 수집 {result.get(count_key, '미기록')}편 "
                         f"· 검색 지문 {signature}")
    lines.append("검색 설정·출처 장애·색인 지연에 따라 표본이 달라진다. done도 분야 전체 수집을 보장하지 않는다.")
    return lines


def _evidence_lines(result: dict) -> list[str]:
    """실제 인용된 자료만 대조용으로 싣는다. ID 존재를 의미 검증으로 부르지 않는다."""
    audit = result.get("citation_audit")
    if not audit:
        return []
    lines = ["■ 서술 근거 대조", "A=초록 · R=요약 결과 · S=원문 문장(인접 문장 포함)",
             f"인용 표시가 있는 본문 줄 {audit['cited_lines']}/{audit['lines']} · 주장 지지 여부는 미평가"]
    if audit["unknown"]:
        lines.append("⚠ 제공 자료에 없는 근거 ID: " + ", ".join(audit["unknown"]))
    if audit["title_only"]:
        lines.append("⚠ 제목만 인용한 주장 — 내용 근거가 아니다: " + ", ".join(audit["title_only"]))
    if audit["cited_lines"] < audit["lines"]:
        lines.append("⚠ 근거 ID가 없는 본문 줄이 있다. 원문 대조가 필요하다.")
    catalog = result.get("evidence_catalog") or {}
    for tag in audit["cited"]:
        item = catalog.get(tag)
        if item:
            snippet = _clip(item["text"], 600)
            if len(item["text"]) > 600:
                snippet += " (대조 발췌 일부)"
            lines.append(f"{tag} {item['title']} — {snippet}")
            if item.get("url"):
                lines.append(item["url"])
    return lines


def _paper_entry(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = paper.get("arxiv_id", "?")
    lines = [
        f"{idx}. [{_stars(score)}] {paper.get('title') or '(제목 없음)'}",
    ]
    # 경고는 제목 바로 밑, 다른 어떤 정보보다 먼저 보여준다(M5 철회 / M7 인젝션).
    for warning in (_paper_retraction_label(paper), injection_label(arxiv_id)):
        if warning:
            lines.append(f"   {warning}")
    lines.append(f"   왜 걸렸나 : {_why_matched(score)}")

    # Deep Layer(M1)가 실패한 논문만 예전의 "미검증 · 초록 기반"으로 남는다
    # — 나머지는 DB 에 실제 검증·재현 결과가 있으므로 그걸 그대로 보여준다.
    # deep_status 키 자체가 없는 경우(M1 이전 경로로 만들어진 결과)도
    # DB 조회 결과가 곧 사실이라 같은 경로로 보낸다.
    deep_status = str(paper.get("deep_status") or "")
    if deep_status == "abstract_only" and (paper.get("abstract_brief") or "").strip():
        # 본문을 못 받았지만 초록으로 정리는 했다(2026-09-04). **본문 요약과
        # 라벨을 절대 같이 쓰지 않는다**(규칙 8) — ⑤ 검증을 통과한 게 아니다.
        lines.append("   본문 비공개 — 초록만 보고 정리한 것이다")
        for ln in paper["abstract_brief"].strip().splitlines():
            if _plain(ln):
                lines.append(f"     {_plain(ln)}")
        lines.append("   [초록 기반 정리 · 본문 미확보 · 미검증]")
    elif deep_status.startswith("failed"):
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
            # 요지 한 줄 → 그 아래 요약본 전체.
            #
            # **평문 메일에는 토글이 없다**(2026-09-05). HTML 판은 <details> 로
            # 접어 두고 펼치면 요약본이 나오지만, 평문은 접을 수가 없다.
            # 그렇다고 한 줄만 남기면 **HTML 을 차단한 사람만 내용이 없는 메일을
            # 받는다** — 그건 안 된다. 평문은 길어도 다 싣는다.
            gist = _one_line_gist(paper, sections)
            if gist:
                lines.append(f"   {_clip(gist, _GIST_CHARS)}")
            for label, key in (("무엇을·어떻게", "overview"), ("방법 상세", "method"),
                               ("실험 설정", "setup"), ("핵심 결과", "results")):
                if sections.get(key):
                    lines.append(f"   {label} :")
                    lines += [f"     - {_plain(b)}" for b in sections[key]]
            if sections.get("author_limits"):
                lines.append(f"   저자가 명시한 한계 : {_plain(sections['author_limits'])}")
            if sections["limits"]:
                lines.append(f"   요약자의 해석 : {_plain(sections['limits'])}")
        else:
            lines.append(f"   초록 발췌 : {_abstract_excerpt(paper)}")
        labels = f"   {verification_label(arxiv_id)}   {_paper_repro_label(paper)}"
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


# ── "그 밖에 걸린 논문" 절을 뺐다 (2026-09-07, 사용자 지시: "있어서 뭐해 저거")
#
# 관련도 순위 밖 논문 8편을 제목·키워드·링크만 한 줄씩 붙이던 절이었다.
# 뺀 근거는 그 목록이 실제로 무엇이었는지다 — 09-06 발송분 8편 전부가
# ★ 한 개(=동향어만 맞힌 논문)였고, 제목 말고는 아무 정보가 없었다.
# 메일 길이의 상당 부분을 먹으면서 "이 분야가 어디로 가는가"에는 기여하지
# 않았다.
#
# **동향을 놓치는 게 아니다.** 그 논문들은 그대로 살아 있다:
#   · 키워드별 적중 편수 집계 — 후보 전체를 세므로 편수는 그대로 나온다
#   · 오늘의 동향 정리 — trend_report.narrative 가 이 목록까지 받아서 쓰고,
#     실제로 09-06 서술이 각주에 있던 ZETA·HINT·RoboTok 을 이름으로 언급했다
# 즉 제목 나열이 사라졌을 뿐 그 논문들이 메일에서 사라진 게 아니다.
# `title_only_papers` 는 계속 만들어져 서술 입력으로 쓰인다 — 렌더링만 뺐다.


_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# 평문 메일에서 읽히게 만드는 최소한의 LaTeX 정리(2026-09-04 실측).
# 2608.28070 요약에 이렇게 찍혔다:
#   "입력은 산업용 부품의 표면 이미( $I_i \in \mathbb{R}^{H \times W \times C}$ )이며"
# 메일에는 MathJax 가 없으니 이건 그냥 읽을 수 없는 글자다. 요약 자체는
# 정확하고 근거 태그까지 붙어 있는데 형식 때문에 못 읽히면 손해다.
#
# **수식을 지우지 않는다** — 정보를 버리는 것이기 때문이다. 흔한 명령만
# 사람이 읽는 기호로 바꾸고 나머지는 백슬래시와 중괄호만 벗긴다.
_TEX_SYMBOLS = {
    "in": "∈", "times": "×", "leq": "≤", "geq": "≥", "neq": "≠", "le": "≤", "ge": "≥",
    "alpha": "α", "beta": "β", "gamma": "γ", "lambda": "λ", "delta": "δ", "pi": "π",
    "sigma": "σ", "mu": "μ", "theta": "θ", "epsilon": "ε", "tau": "τ", "phi": "φ",
    "cdot": "·", "approx": "≈", "sum": "Σ", "rightarrow": "→", "leftarrow": "←",
    "to": "→", "infty": "∞", "pm": "±", "sim": "~", "ll": "≪", "gg": "≫",
    "subset": "⊂", "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃",
    "partial": "∂", "nabla": "∇", "prod": "∏", "int": "∫", "sqrt": "√",
}
_TEX_MATH_RE = re.compile(r"\$+([^$]+?)\$+")
# 껍데기만 벗기는 명령(뒤의 중괄호 내용은 남긴다).
_TEX_WRAPPER_RE = re.compile(
    r"\\(?:mathbb|mathcal|mathrm|mathbf|mathit|text|textbf|textit|left|right|operatorname)\b\s*")
# \frac{a}{b} → a/b. 분수 구조를 살린다.
_TEX_FRAC_RE = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
# 남은 명령 이름. **\w 경계로 잡는다** — 부분 문자열 치환을 하면 안 된다.
_TEX_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_TEX_BRACE_RE = re.compile(r"[{}]")


def _detex(fragment: str) -> str:
    r"""`$...$` 안쪽을 평문으로. 지우지 않고 읽히게만 만든다.

    **2026-09-08 정정.** 예전에는 `\in` → `∈` 같은 **부분 문자열 치환**을 했다.
    그래서 `\infty` 가 `∈fty` 가 되고 `\leftarrow` 가 `arrow` 가 됐다
    (`\left` 를 껍데기로 보고 지운 뒤 남은 `arrow`). `\frac{1}{2}` 는 중괄호만
    지워 `frac12` 였다. 강조가 빠지는 정도가 아니라 **무한대·방향·분수 구조가
    손상된 것**이라 읽는 사람이 뜻을 잘못 가져간다. 외부 검토가 재현했다.

    이제 명령을 **이름 단위로만** 바꾼다. 긴 이름이 짧은 이름에 먹히지 않는다.
    """
    out = _TEX_FRAC_RE.sub(r"(\1)/(\2)", fragment)
    out = _TEX_WRAPPER_RE.sub("", out)
    out = _TEX_CMD_RE.sub(lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(1)), out)
    out = _TEX_BRACE_RE.sub("", out)
    return re.sub(r"\s+", " ", out).strip()


def _plain(line: str) -> str:
    """평문 메일에 마크다운이 새지 않게 한다.

    2026-09-04 실측: 같은 프롬프트인데 gemini-flash-latest 는 `**무엇을 하려
    했는가**` 로, gemini-flash-lite-latest 는 굵게 없이 답했다. 모델을 회전
    시키니(§8-38-1) 한 메일 안에서 두 형식이 섞여 나온다. 프롬프트로
    "굵게 쓰지 마라"를 거는 것보다 받는 쪽에서 지우는 게 확실하다 —
    모델이 지시를 지키는지에 형식이 의존하면 안 된다.
    """
    out = _MD_BOLD_RE.sub(r"\1", line)
    out = _TEX_MATH_RE.sub(lambda m: _detex(m.group(1)), out)
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------- 서술이 부른 논문
#
# 2026-09-07 §8-68 에서 "그 밖에 걸린 논문" 목록을 뺐다. 그 논문들은 여전히
# `title_only_papers` 로 서술 입력에 들어가고, 실제로 09-06 서술은 각주에
# 있던 ZETA·HINT·RoboTok 을 **이름으로** 불렀다. 그런데 목록이 없으니
# **읽는 사람이 그 논문에 갈 방법이 사라졌다** — 내가 목록을 빼면서 만든
# 결함이다.
#
# 되살리는 건 목록이 아니라 **링크**다. 서술이 부른 것만 붙인다. 판정은
# 문자열 대조이고(규칙 7 이 허용하는 위조 불가능한 대조) LLM 을 다시 부르지
# 않는다. 부르지 않은 논문은 안 붙는다 — 목록을 뺀 이유가 그대로 유지된다.

# 제목 앞머리의 약칭(예: "ZETA: Zero-shot ..." 의 ZETA). 서술은 보통 이걸로
# 논문을 부른다. 두 글자 이하는 흔한 단어와 부딪히므로 안 쓴다.
_ACRONYM_RE = re.compile(r"^([A-Za-z][A-Za-z0-9\-]{2,19})\s*[:：]")


def _mention_keys(title: str) -> list[str]:
    """이 논문을 서술에서 찾을 때 쓸 열쇠들. 긴 것부터."""
    title = (title or "").strip()
    if not title:
        return []
    keys = [title]
    head = title.split(":")[0].split("：")[0].strip()
    # 콜론 앞이 여러 낱말이면 그 구절도 열쇠다 — 프롬프트가 "제목 앞부분을
    # 그대로 인용"하라고 하므로 서술이 `Deep Microcompression은…` 처럼 쓴다.
    # 짧은 구절은 흔한 말과 부딪히므로 12자 이상·2낱말 이상만 받는다.
    if head != title and len(head) >= 12 and len(head.split()) >= 2:
        keys.append(head)
    m = _ACRONYM_RE.match(title)
    if m:
        token = m.group(1)
        # **대문자 두 개 이상**이어야 약칭으로 본다(2026-09-08 정정).
        # 그전엔 "하나라도"였는데 그러면 `Towards: Better Detection` 의
        # "Towards" 가 통과해 평범한 문장의 "Towards efficient inference" 에
        # `(논문 1)` 이 붙었다 — 외부 검토가 재현했다. 주석은 막는다고
        # 말하고 있었지만 코드가 안 막고 있었다.
        if sum(1 for c in token if c.isupper()) >= 2:
            keys.append(token)
    return keys


def _is_mentioned(title: str, narrative_text: str) -> bool:
    """서술이 이 논문을 이름으로 불렀는가. 순수 문자열 대조."""
    if not narrative_text:
        return False
    for key in _mention_keys(title):
        if len(key) > 24:
            # 제목 전체는 서술에 그대로 나오는 일이 드물지만, 나오면 확실하다.
            if key.lower() in narrative_text.lower():
                return True
            continue
        # 약칭은 단어 경계로 본다. 대소문자를 구분한다 — 'HINT' 와 'hint' 는
        # 다른 것이고, 구분을 풀면 평범한 문장이 논문 이름으로 오인된다.
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", narrative_text):
            return True
    return False


def mentioned_papers(scan_result: dict) -> list[dict]:
    """서술이 이름으로 부른 논문 중 **본문에 상자가 없는 것**.

    내용 자리에 실린 논문(`papers`)은 이미 번호·제목·링크를 갖고 있으므로
    여기 또 넣지 않는다. 목록에서 빠진 `title_only_papers` 만 대상이다.
    """
    story = scan_result.get("narrative")
    if not story:
        return []
    text = story[0] if isinstance(story, (tuple, list)) else str(story)
    shown_ids = {p.get("arxiv_id") or p.get("doi") or (p.get("title") or "").lower()
                 for p in (scan_result.get("papers") or [])}
    out = []
    for paper in scan_result.get("title_only_papers") or []:
        key = (paper.get("arxiv_id") or paper.get("doi")
               or (paper.get("title") or "").lower())
        if key in shown_ids:
            continue
        if _is_mentioned(paper.get("title") or "", text):
            out.append(paper)
            shown_ids.add(key)
    return out


def _narrative_section(scan_result: dict) -> list[str]:
    """오늘의 동향 서술. 셈 절과 **섞지 않고** 라벨을 붙인다.

    2026-09-04: 그전까지 "동향" 절은 키워드 빈도표 한 줄이었다
    (`quantization 22 · vision-language-action 16 · …`). 무엇이 몇 편인지는
    알려주지만 무엇이 어디로 가는지는 못 말한다 — 사용자 지적("논문 제목에
    별표만 친 게 왜 동향이야?")이 정확했다.

    숫자는 위 빈도표가 정확히 갖고 있고, 여기는 글이다. 둘을 한 문단에 섞으면
    어디까지가 측정이고 어디부터가 해석인지 구분이 안 된다(§8-23·24·33).
    """
    story = scan_result.get("narrative")
    if not story:
        return []
    text, ungrounded = story
    lines = ["", "─" * 62,
             "■ 오늘의 동향 정리",
             f"   ({narrative_source_label(scan_result)})",
             "   기간 비교 없는 수집 표본의 관찰이며, 분야 전체의 변화는 판단하지 않았다.",
             ""]
    # 서술이 부른 논문 뒤에 `(논문 3)` 을 붙인다 — "이 정리가 어디서 왔나"를
    # 읽는 사람이 바로 알 수 있게(2026-09-08 사용자 요청).
    text = annotate_numbers(reflow_branch_lists(normalise_interpretation_marks(text)), numbered_mentions(scan_result), len(scan_result.get("papers") or []))
    audit = scan_result.get("citation_audit") or {}
    if audit.get("unknown") or audit.get("title_only") or audit.get("cited_lines", 0) < audit.get("lines", 0):
        lines.append("   ⚠ 일부 주장에 내용 근거 ID가 없거나 유효하지 않다 — 인용한 원문을 확인할 것")
    lines += [f"   {_plain(ln)}" for ln in text.strip().splitlines() if _plain(ln)]
    if ungrounded:
        lines.append(f"   ⚠ 원문에 없는 숫자가 섞여 있다: {', '.join(ungrounded)} — 믿지 말 것")

    named = mentioned_papers(scan_result)
    if named:
        lines += ["", "   ▸ 위에서 이름으로 부른 논문"]
        for paper in named:
            url = paper_link(paper)
            title = (paper.get("title") or "").strip()
            lines.append(f"      · {title}" + (f" — {url}" if url else ""))
    return lines


def generate_digest(scan_result: dict, profile_name: str) -> str:
    """scan_result: run_profile_scan.scan_profile()의 반환값 그대로 받는다.
    returns 메일 본문으로 바로 쓸 수 있는 순수 텍스트(HTML 아님 — 렌더링
    실패 걱정 없이 항상 읽힌다는 걸 우선했다)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 제목은 읽는 사람 기준이다(2026-09-11, 사용자 요청). 시스템 이름(HARNESS)이나
    # 프로필 내부 이름("우리팀 — …")은 받는 사람에게 정보가 아니다. 무엇인지
    # (연구 동향 브리핑)와 언제인지(날짜)만 둔다. 프로필 이름은 여러 프로필을
    # 받는 사람만 구분이 필요하므로 메일 제목에서만 조건부로 붙는다(_deliver).
    header = f"연구 동향 브리핑 · {date_str}"
    papers = scan_result.get("papers", [])
    candidates = scan_result.get("candidates_found", 0)

    title_only = scan_result.get("title_only_papers") or []
    empty = not papers and not title_only

    # **조기 반환을 쓰지 않는다.** 빈 다이제스트 갈래가 `return` 으로 빠져나가면
    # 뒤에 붙는 절(⑥ 주간 리뷰 등)을 그 갈래만 못 받는다 — 이 코드베이스에서
    # 조기 반환이 같은 병을 낸 게 §8-50 둘, §8-57 하나, §8-67 하나였다.
    # 갈래는 내용만 정하고 출구는 하나로 모은다(2026-09-07, §8-70 고치며).
    lines = [header, ""]
    if not empty:
        lines += _narrative_section(scan_result)
    if empty:
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
        lines.append(body)
    elif papers:
        lines += [f"■ 오늘의 신규 논문 {len(papers)}편 (전체 후보 {candidates}건 중)", ""]
        for i, paper in enumerate(papers, start=1):
            lines.append(_paper_entry(i, paper))
            lines.append("")
    else:
        lines += [f"■ 본문을 받을 수 있는 신규 논문은 없었습니다 (전체 후보 {candidates}건 중).", ""]

    # 빈 갈래는 아래 절들을 안 받는다 — 걸러진 내역은 위 본문에 이미 들어갔고,
    # 논문이 0편이면 키워드 편수·서술도 실을 것이 없다. 예전 조기 반환이
    # 만들던 출력과 같게 유지한다.
    if not empty:
        trend = _trend_line(scan_result)
        if trend:
            lines += [f"■ 이번 창의 키워드별 적중 편수 (후보 {candidates}건 기준)",
                      f"   {trend}"]
        if lines and lines[-1] != "":
            lines.append("")

        filtered = _filtered_line(scan_result)
        if filtered:
            lines.append(f"■ 이번 실행에서 걸러진 것: {filtered}")

    # ⑥ 주간 리뷰는 맨 아래에 붙는다(주 1회). **모든 갈래가 여기로 모인다** —
    # HTML 판도 같은 `weekly_review` 하나를 읽는다.
    lines += _weekly_review_lines(scan_result)

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


# 줄 머리의 "항목명 : " — 항목명은 30자 이내, 콜론 앞까지 콜론 없음, 콜론 뒤엔 공백이나 줄 끝
# (URL 의 "https:" 는 뒤가 '/' 라 안 잡힌다). 고정 목록(데이터셋·평가 지표…)이었을 땐 "조건 구분 :"
# "학습 목적 함수 :" 처럼 목록에 없는 항목만 안 굵어져 눈에 띄었다(2026-09-12 사용자 지적).
_SUMMARY_LABEL_RE = re.compile(r"^([^:：\n]{1,30}?\s*[:：])(?=\s|$)")


def _summary_label_html(text: str) -> str:
    """요약의 항목명(콜론까지)을 굵게 해 값과 구분한다(2026-09-10 사용자 요청, 2026-09-12 일반화)."""
    escaped = _esc(text)
    return _SUMMARY_LABEL_RE.sub(r"<strong>\1</strong>", escaped, count=1)


def _summary_block_html(arxiv_id: str, paper: dict, deep_status: str) -> str:
    """검증된 요약을 HTML 로. 없으면 예전처럼 초록 발췌로 떨어진다.

    텍스트판(_paper_entry)과 같은 판단을 쓴다 — 두 판이 다른 내용을 보여주면
    "HTML 을 차단한 사람만 다른 메일을 받는" 상황이 되므로, 분기 조건을
    한 곳(summary_sections 의 반환값 유무)으로 맞춰 둔다.
    """
    def para(text: str, *, muted: bool = False, top: int = 6) -> str:
        color = _MUTED if muted else _INK
        return (f'<div style="color:{color};font-size:13px;margin-top:{top}px;'
                f'line-height:1.5;">{_summary_label_html(text)}</div>')

    def bullets(label: str, items: list[str]) -> str:
        lis = "".join(
            f'<li style="color:{_INK};font-size:13px;line-height:1.5;'
            f'margin-bottom:12px;">{_summary_label_html(b)}</li>' for b in items
        )
        return (f'<div style="color:{_MUTED};font-size:12px;font-weight:600;'
                f'margin-top:16px; font-weight:700;"><strong>{_esc(label)}</strong></div>'
                f'<ul style="margin:4px 0 0;padding-left:18px;">{lis}</ul>')

    # 초록 기반 정리는 **여기서** 만든다(2026-09-07).
    #
    # 전에는 `_paper_entry_html` 맨 위의 **조기 반환**이 이 갈래를 통째로
    # 가로챘고, 그 반환값에는 `<details>` 도 `<summary>` 도 제목도 번호도
    # 링크도 없었다. 그래서 실제 메일에서 초록 기반 논문의 본문이 **앞 논문
    # 상자 밖에 벌거벗은 채로** 붙었고, 번호가 1 → 3 으로 건너뛰었다.
    # 사용자가 "맨 위 논문 아래 있는 저건 보강용이냐"고 물은 게 이것이다 —
    # 2번 논문의 내용이 1번에 붙은 것처럼 보였다.
    #
    # 평문 판(`_paper_entry`)은 처음부터 **분기**로 짜여 있어 멀쩡했다.
    # HTML 판만 조기 반환이라 깨졌다 — "HTML 을 차단한 사람만 다른 메일을
    # 받는다"의 정반대 버전이다.
    #
    # **이 코드베이스에서 조기 반환이 같은 병을 낸 네 번째다**(§8-50 에서
    # batch_summarize 둘, §8-57 에서 run_profile_scan 하나). 같은 결말로 가는
    # 길이 여럿이면 모이는 지점을 먼저 만들라는 교훈이 또 걸렸다.
    brief = (paper.get("abstract_brief") or "").strip()
    if deep_status == "abstract_only" and brief:
        body = "".join(
            f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
            f'margin:12px 0;">{_summary_label_html(_plain(ln))}</div>'
            for ln in brief.splitlines() if _plain(ln))
        return (f'<div style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
                f'margin-top:8px;">본문 비공개 — 초록만 보고 정리한 것이다</div>{body}')

    if deep_status.startswith("failed"):
        tldr = paper.get("s2_tldr")
        return para(tldr) if tldr else para(_html_excerpt(paper))

    sections = summary_sections(arxiv_id)
    if not sections:
        return para(_html_excerpt(paper))

    # one_liner 는 <summary>(접힌 줄)에 이미 있다 — 여기서 또 쓰면 중복이다.
    out = ""
    for label, key in (("무엇을·어떻게", "overview"), ("방법 상세", "method"),
                       ("실험 설정", "setup"), ("핵심 결과", "results")):
        if sections.get(key):
            out += bullets(label, [_plain(b) for b in sections[key]])
    if sections.get("author_limits"):
        out += para("저자가 명시한 한계 : " + _plain(sections["author_limits"]), muted=True, top=8)
    if sections["limits"]:
        out += para("요약자의 해석 : " + _plain(sections["limits"]), muted=True, top=8)
    return out


def _paper_entry_html(idx: int, paper: dict) -> str:
    score = paper.get("_score", {})
    arxiv_id = str(paper.get("arxiv_id", "?"))
    title = paper.get("title") or "(제목 없음)"
    deep_status = str(paper.get("deep_status") or "")

    if deep_status == "abstract_only" and (paper.get("abstract_brief") or "").strip():
        # 검증·재현 라벨을 절대 같이 쓰지 않는다(규칙 8) — ⑤ 를 통과한 게 아니다.
        chips = _status_chip("초록 기반 정리 · 본문 미확보 · 미검증", flagged=False)
        detail = ""
        needs_attention = False
    elif deep_status.startswith("failed"):
        reason = deep_status.split(":", 1)[1].strip() if ":" in deep_status else "사유 미상"
        chips = _status_chip("미검증 · 초록 기반", flagged=True)
        detail = f'<div style="color:{_FLAG_INK};font-size:13px;">처리 실패: {_esc(reason)}</div>'
        needs_attention = True
    else:
        v_label = verification_label(arxiv_id)
        r_label = _paper_repro_label(paper)
        # flag 가 있거나 재현이 실패한 항목은 펼쳐서 보낸다. Gmail·Outlook 은
        # 어차피 항상 펼쳐 보여주므로 이 속성이 실제로 의미를 갖는 건
        # Apple Mail 뿐이다(위 주석 1번).
        c_label = coverage_label(arxiv_id)
        needs_attention = ("flag" in v_label) or ("✗" in r_label) or bool(c_label)
        # strip("[]") 은 양끝만 벗긴다 — "[검증 27/29 통과]  ⚠ flag 2건" 은 앞 괄호만
        # 벗겨져 "통과]" 가 남았다(2026-09-11 메일에서 실제로 보였다). 안쪽 괄호까지 뺀다.
        chips = _status_chip(v_label.replace("[", "").replace("]", "").strip(), flagged="flag" in v_label)
        chips += _status_chip(r_label.replace("[", "").replace("]", "").strip(), flagged="✗" in r_label)
        if c_label:
            # 커버리지 경고는 flag 취급한다 — "검증 통과"만 보고 요약을
            # 그대로 믿으면 안 되는 상황이라 눈에 띄어야 한다.
            chips += _status_chip(c_label, flagged=True)
        detail = ""

    # 철회 경고(M5)는 실패 여부와 무관하게 붙고, 붙으면 무조건 펼친다 —
    # 이 항목에서 가장 중요한 정보다.
    for warning in (_paper_retraction_label(paper), injection_label(arxiv_id)):
        if warning:
            chips = _status_chip(warning.strip("[]"), flagged=True) + chips
            needs_attention = True

    # **토글은 전부 닫힌 채로 간다**(2026-09-12 사용자 요청). 예전엔 검증 flag·재현 실패가 있는
    # 논문만 열어 뒀는데, 어떤 건 열리고 어떤 건 닫힌 채 오면 오히려 어수선하다. 주의가 필요한
    # 것은 칩(needs_attention → 칩 강조)으로 보이고, 펼치는 건 읽는 사람이 한다.
    open_attr = ""

    # **텍스트 판과 같은 조건을 쓴다.** `== "ok"` 로 좁혔더니 deep_status 가
    # 비어 있는 구형 결과에서 평문에는 요약이 실리고 HTML 에는 안 실렸다 —
    # "HTML 을 차단한 사람만 다른 메일을 받는" 상황의 정반대 버전이다.
    sections = {} if deep_status == "abstract_only" else summary_sections(arxiv_id)
    gist = _one_line_gist(paper, sections)
    gist_html = (f'<div style="color:{_MUTED};font-size:13px;font-weight:400;'
                 f'margin-top:4px;">{_esc(_clip(gist, _GIST_CHARS))}</div>') if gist else ""
    return (
        f'<details{open_attr} style="background-color:{_PAPER_BG};color:{_INK};'
        f'border:1px solid {_LINE};border-radius:6px;padding:10px 12px;margin-bottom:10px;">'
        f'<summary style="color:{_INK};font-size:15px;font-weight:600;cursor:pointer;">'
        f'{idx}. [{_stars(score)}] {_esc(title)}'
        # **접힌 상태에서 보이는 한 줄**(2026-09-05). 제목만으로는 무슨 논문인지
        # 모르고, 절을 다 펼쳐 두면 목록을 훑을 수가 없다. 제목 + 한 줄이
        # 목록이고, 펼치면 요약본 전체가 나온다.
        # 칩(검증 n/m · 재현 · 철회 · 원문 N%)도 **접힌 상태에서 보여야 한다**(2026-09-12). 토글을
        # 전부 닫아 보내기로 하면서, 예전처럼 주의 논문만 자동으로 펼쳐 칩을 드러내는 길이 없어졌다.
        f'{gist_html}<div style="margin-top:6px;font-weight:400;">{chips}</div></summary>'
        f'{detail}'
        f'<div style="color:{_MUTED};font-size:13px;margin-top:8px;">'
        f'왜 걸렸나 : {_esc(_why_matched(score))}</div>'
        f'{_summary_block_html(arxiv_id, paper, deep_status)}'
        f'<div style="margin-top:8px;font-size:13px;">'
        f'<a href="{_esc(paper_link(paper))}" '
        f'style="color:{_NAVY};">{_esc(paper_link(paper).replace("https://", ""))}</a></div>'
        f"</details>"
    )


# 동향 서술의 소제목 — trend_report._NARRATIVE_PROMPT 가 시키는 네 개 그대로다.
# LLM 이 "■ " 를 붙일 때도 있고 안 붙일 때도 있어서(실측: 09-06 두 실행이
# 서로 달랐다) 앞의 장식을 떼고 대조한다. 프롬프트가 정한 문구와 **문자열
# 대조**만 하므로 판정이 아니다(규칙 7).
_NARRATIVE_HEADINGS = frozenset({
    "오늘 눈에 띄는 것", "갈래", "우리 분야와 만나는 지점", "아직 밖에 있지만 넘어올 것",
})
_HEADING_ORNAMENT_RE = re.compile(r"^[■□▪●•\-*#\s]+|[:：\s]+$")


def _is_narrative_heading(line: str) -> bool:
    return _HEADING_ORNAMENT_RE.sub("", line.strip()) in _NARRATIVE_HEADINGS


# ---------------------------------------------------------------- 가독성 보조 (2026-09-08)
#
# 사용자 요청 다섯 가지를 한 곳에 모은다. 전부 **문자열 대조**이고 무엇이 좋은
# 논문인가 같은 판단을 하지 않는다(규칙 7). LLM 을 다시 부르지도 않는다.
#
# 실물 메일을 보고 나온 요청이라 근거가 분명하다 — 서술이 "첫째/둘째/셋째"로
# 갈래를 나누는데 그 표시가 본문과 같은 굵기라 갈래가 안 보였고, 편수 증감이
# `(0→6, +6)` 처럼 숫자로만 있어 늘었는지 줄었는지 눈으로 안 잡혔다.

# 서술이 갈래를 여는 말.
#
# **2026-09-08 실측으로 넓혔다.** 처음엔 "첫째|둘째|셋째"만 잡았는데, 같은
# 프롬프트로 만든 두 글을 재보니 하나는 "첫 번째는"으로 썼고 다른 하나는
# 아예 안 썼다 — 이 규칙이 실제 출력에서 **한 번도 발화하지 않았다.**
# 프롬프트로 "첫째,"를 쓰라고 지시하되(형식은 그쪽에서 강제한다) 받는 쪽은
# 변이를 견디게 넓힌다. 모델이 지시를 지키는지에 표시가 의존하면 안 된다
# — `_plain()` 이 마크다운을 지우는 것과 같은 이유다.
_ENUM_RE = re.compile(
    r"^(첫\s*째|둘\s*째|셋\s*째|넷\s*째|다섯\s*째|여섯\s*째|일곱\s*째"
    r"|첫\s?번째|두\s?번째|세\s?번째|네\s?번째|다섯\s?번째|여섯\s?번째|일곱\s?번째)"
    r"([,.·:]?)")

# 편수 증감. **`(3→5, +2)` 형태 안에서만** 잡는다. `[+-]\d+` 를 통째로 잡으면
# 서술에 나오는 지수 표기(`10^-3`)나 하이픈 붙은 이름까지 물들인다 —
# 색이 뜻을 잃는다.
# 뒤에 숫자·소수점·지수가 이어지면 그건 증감이 아니다(`+2e-3` 의 `+2`).
_DELTA_RE = re.compile(r"(→\s*\d+,\s*)([+\-−]\d+)(?![\d.eE])")

_UP_COLOR = "#0B7A3B"     # 늘어난 것
_DOWN_COLOR = "#B00020"   # 줄어든 것


def _emphasise_enum(escaped: str) -> str:
    """이스케이프된 본문에서 '첫째,' 같은 갈래 표시를 굵게."""
    return _ENUM_RE.sub(lambda m: f"<strong>{m.group(1)}{m.group(2)}</strong>", escaped)


def _colour_delta(escaped: str) -> str:
    """`+2` 는 초록, `-3` 은 빨강. 숫자를 바꾸지 않고 색만 입힌다."""
    def paint(m):
        head, token = m.group(1), m.group(2)
        # `(1→1, +0)` 은 **변화 없음**이다. 초록으로 칠하면 늘어난 것처럼 보인다.
        if token.lstrip("+-−") == "0":
            return m.group(0)
        colour = _DOWN_COLOR if token[0] in "-−" else _UP_COLOR
        return f'{head}<span style="color:{colour};font-weight:600;">{token}</span>'
    return _DELTA_RE.sub(paint, escaped)


def _emphasise_label(escaped: str) -> str:
    """`출처 : ...`, `엔진 : ...` 처럼 앞에 라벨이 붙은 줄은 라벨만 굵게."""
    # 갈래 표시로 시작하는 줄은 라벨이 아니다 — `첫째, 압축 : …` 을 라벨로
    # 보면 `<strong>첫째, 압축</strong>` 이 돼 설명까지 굵어진다(외부 검토).
    if _ENUM_RE.match(escaped):
        return escaped
    head, sep, rest = escaped.partition(" : ")
    if sep and len(head) <= 12 and head.strip():
        return f"<strong>{head}</strong>{sep}{rest}"
    return escaped


def numbered_mentions(scan_result: dict) -> list[tuple[str, int]]:
    """서술이 부른 이름 → 메일에 실린 논문 번호. 긴 이름부터 본다.

    "이 동향 정리가 어디서 왔나"를 읽는 사람이 바로 알 수 있게 하려는 것이다
    (2026-09-08 사용자 요청). 판정은 §8-68 고침과 **같은 함수**(_mention_keys)를
    쓴다 — 두 곳이 다른 규칙으로 논문을 찾으면 한쪽만 맞는 일이 생긴다.
    """
    owners: dict[str, set[int]] = {}
    for i, paper in enumerate(scan_result.get("papers") or [], start=1):
        for key in _mention_keys(paper.get("title") or ""):
            owners.setdefault(key, set()).add(i)
    # **두 논문이 같은 이름을 쓰면 그 이름은 버린다**(2026-09-08). 등장 순서를
    # 논문 식별 근거로 삼으면 첫 번째 언급에 1번, 두 번째에 2번이 붙는데
    # 그건 근거 없는 배정이다 — 모르면 안 붙이는 쪽이 맞다.
    out = [(k, next(iter(v))) for k, v in owners.items() if len(v) == 1]
    return sorted(out, key=lambda kv: -len(kv[0]))


_CITE = r"\[P\d+:[^\]]+\]"
_BRANCH_TAIL_RE = re.compile(r"\s*(?:등의?\s*)?논문(?:이|들이|은|들은|도)?\s*(?:이에|여기에|이|여기)\s*(?:해당한다|속한다|묶인다|들어간다|포함된다)[.。]?\s*$")


def reflow_branch_lists(text: str) -> str:
    """갈래 문단 안에 "제목 [P1:A], 제목 [P4:A, P4:R] 논문이 이에 해당한다" 처럼 **문장 속에 나열된
    논문**을 설명 문장 + "- 제목 [근거]" 줄로 푼다(2026-09-12 사용자 요청: 문장 속 나열은 읽기 어렵다).
    프롬프트도 그렇게 쓰라고 하지만 모델이 지시를 지키는지에 형식이 의존하면 안 된다 — 받는
    쪽에서 결정적으로 푼다. 근거 ID 가 둘 이상 문장 안에 있을 때만 건드리고, 못 풀면 그대로 둔다."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        first = re.search(_CITE, stripped)
        if not stripped or stripped.startswith(("■", "-", "•")) or not first:
            out.append(line); continue
        # 첫 근거 ID 가 있는 자리에서 그 제목의 시작을 찾는다: 직전 문장 끝(". ") 다음부터.
        # (항목이 둘 이상이어야 푼다 — 아래 len(items) 검사. 근거 수 선검사는 그와 겹쳐 뺐다.)
        head_end = stripped.rfind(". ", 0, first.start())
        if head_end < 0:
            out.append(line); continue
        lead, rest = stripped[:head_end + 1], stripped[head_end + 2:]
        rest = _BRANCH_TAIL_RE.sub("", rest).strip()
        items = [it.strip(" ,") for it in re.split(r"(?<=\])\s*,\s*", rest) if it.strip(" ,")]
        if len(items) < 2 or not all(re.search(_CITE + r"$", it) for it in items):
            out.append(line); continue
        indent = line[:len(line) - len(line.lstrip())]
        out.append(indent + lead)
        out.extend(f"{indent}- {it}" for it in items)
    return "\n".join(out)


# "~므로 해석이다" — 모델이 '해석' 표시를 서술어로 써서 문장이 안 된다("범위를 넘어서므로
# 해석이다", "달성될 수 있으므로 해석이다"; 2026-09-12 사용자 지적, 프롬프트로 두 번 막았는데도
# 나왔다). 한국어 활용을 되돌릴 수는 없으니 **자주 나오는 꼴만** 문장으로 고치고 "(해석)" 을 붙인다.
_INTERP_FIXES = [
    (re.compile(r"범위를 넘어서므로 해석이다"), "범위를 넘어선다 (해석)"),
    (re.compile(r"(있|없)으므로 해석이다"), r"\1다 (해석)"),
    (re.compile(r"이므로 해석이다"), "이다 (해석)"),
    (re.compile(r"하므로 해석이다"), "한다 (해석)"),
    (re.compile(r"므로 해석이다"), "므로, 이는 해석이다"),   # 그 밖의 활용 — 최소한 문장은 되게
]


def normalise_interpretation_marks(text: str) -> str:
    for pat, rep in _INTERP_FIXES:
        text = pat.sub(rep, text)
    return text


def annotate_numbers(text: str, mentions: list[tuple[str, int]], total: int | None = None) -> str:
    """서술 안에서 부른 논문 뒤에 `(요약 논문 3/6)` 을 붙인다. 원문을 안 지운다.

    "논문 3" 만으로는 아래 요약 목록의 3번째라는 걸 모른다(2026-09-12 사용자 지적) —
    "요약 논문 3/6" 으로 어디의 몇 번째인지 적는다. total 이 없으면 "요약 논문 3".
    한 번 붙은 이름은 다시 안 붙인다 — 같은 논문이 문단마다 나오면 번호가
    도배된다. 이미 번호가 붙은 자리는 건너뛴다.
    """
    if not text or not mentions:
        return text
    done: set[int] = set()
    taken: list[tuple[int, int]] = []     # 이미 어떤 논문이 차지한 구간
    inserts: list[tuple[int, int]] = []
    for key, num in mentions:             # 긴 이름부터 — 긴 쪽이 구간을 먼저 잡는다
        if num in done or len(key) < 3:
            continue
        pattern = (re.escape(key) if len(key) > 24
                   else rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])")
        flags = re.IGNORECASE if len(key) > 24 else 0
        for m in re.finditer(pattern + r"(?!\s*\((?:요약 )?논문)", text, flags):
            # **이미 잡힌 구간 안이면 건너뛴다**(2026-09-08). 안 그러면
            # `HINT++: Better` 안의 `HINT` 가 다른 논문으로 잡혀
            # `HINT (논문 1)++: Better (논문 2)` 가 된다 — 외부 검토가 재현했다.
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            inserts.append((m.end(), num))
            done.add(num)
            break
    for pos, num in sorted(inserts, reverse=True):   # 뒤에서부터 넣어야 위치가 안 밀린다
        label = f"요약 논문 {num}/{total}" if total else f"요약 논문 {num}"
        text = f"{text[:pos]} ({label}){text[pos:]}"
    return text


def narrative_source_label(scan_result: dict) -> str:
    """서술이 **무엇을 보고 쓴 것인지** 한 줄로. 라벨이 입력과 어긋나면 규칙 8 위반이다.

    2026-09-08 이전에는 언제나 "제목·초록만 보고 쓴 것"이었다. 이제 내용 자리
    논문에 한해 원문 요약의 결과 절까지 넣으므로(§8-73), **실제로 몇 편에
    붙었는지**를 세어 말한다. 0 편이면 예전 문장 그대로다 — 요약이 없는 날에
    "요약까지 봤다"고 하면 그게 거짓말이다.
    """
    n = int(scan_result.get("narrative_summaries") or 0)
    if n <= 0:
        return "LLM 이 제목·초록만 보고 쓴 것."
    return f"LLM 이 제목·초록과, 그중 {n}편은 원문 요약의 결과까지 보고 쓴 것."


def _narrative_line_html(line: str) -> str:
    """서술 한 줄을 HTML 로. 소제목이면 굵게 키우고 위에 여백을 준다.

    2026-09-07 사용자 지적: 소제목이 본문과 같은 크기·굵기라 네 절이 한 덩어리로
    보였다. 글의 뼈대가 안 보이면 "그래서 무슨 일이 벌어지나"를 훑을 수가 없다.
    """
    text = _plain(line)
    # **정규화한 뒤 판정한다**(2026-09-08 정정). 원래 줄로 보면 `**갈래**` 가
    # 화면엔 "갈래"로 나오는데 소제목 강조만 빠졌다 — 주간 렌더러는 정규화
    # 뒤에 보고 있어 같은 글이 두 곳에서 다르게 보였다.
    if _is_narrative_heading(text):
        return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:14px;'
                f'font-weight:700;margin:14px 0 4px;">{_esc(text)}</div>')
    # 갈래의 논문 목록 — "- 제목 [P3:A]" 한 줄에 하나(2026-09-12). 들여쓰고 글머리를 단다.
    if re.match(r"^[-•]\s+", text):
        return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
                f'margin:2px 0 2px 18px;line-height:1.55;">• {_esc(re.sub(r"^[-•]\s+", "", text))}</div>')
    # 같은 줄에 나열된 갈래도 문단으로 분리해 항목 사이 한 줄 여백을 둔다.
    parts = re.split(r"(?<=[.!?。])\s+(?=(?:첫|둘|셋|넷|다섯|여섯|일곱)\s*째|(?:첫|두|세|네)\s*번째)", text)
    return "".join(
        f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
        f'margin:{"14px" if _ENUM_RE.match(part) else "3px"} 0;line-height:1.6;">'
        f'{_emphasise_enum(_esc(part))}</div>' for part in parts)



# ---------------------------------------------------------------- ⑥ 주간 리뷰
#
# 2026-09-07 §8-70: 주간 리뷰가 **평문 판에만** 있었다. run_profile_scan 이
# `digest_text` 에 문자열로 이어붙였고, _deliver 는 HTML 을 `result` 로 다시
# 만드는데 `result` 에는 그 값이 없었다. 메일은 multipart/alternative 이고
# Gmail 은 HTML 을 보여주므로 **사용자 화면에는 절 하나가 통째로 없었다** —
# §8-57 과 같은 종류의 평문/HTML 불일치다.
#
# 고침은 이 코드베이스가 이미 배운 교훈대로다(§8-67 "모이는 지점을 먼저
# 만들라"): 이어붙이기를 없애고 `scan_result["weekly_review"]` 하나를
# 평문·HTML 두 렌더러가 같이 읽는다. 렌더링 위치가 갈라져도 입력은 하나다.
#
# 여기서 하는 일은 trend_report.format_report 가 만든 줄 모양을 그대로
# 옮기는 것뿐이다 — 판정도 재계산도 하지 않는다(규칙 7).
_WEEKLY_NOTE_PREFIX = "※"
_WEEKLY_WARN_PREFIX = "⚠"


def _weekly_line_html(line: str) -> str:
    """주간 리뷰 한 줄을 HTML 로. 줄 모양은 trend_report.format_report 가 정한다.

    ■ 큰 제목 · ▶ 절 제목 · ※ 각주 · ⚠ 경고 · ─── 구분선 · 그 밖은 본문.
    들여쓰기는 HTML 에서 공백이 접히므로 padding-left 로 옮긴다.
    """
    raw = line.rstrip()
    text = _plain(raw)
    if not text:
        return ""
    stripped = text.strip()
    if set(stripped) == {"─"}:
        return (f'<div style="border-top:1px solid {_LINE};margin:12px 0 0;'
                f'font-size:1px;line-height:1px;">&nbsp;</div>')
    # **서술 소제목을 ■ 분기보다 먼저 본다**(2026-09-08 정정). format_report 는
    # 서술 소제목도 `   ■ 갈래` 로 내보내는데, ■ 분기가 먼저 걸려 주간 리뷰
    # 제목과 같은 15px + 상단 구분선이 붙었다 — 같은 내용의 계층이 장식 하나에
    # 따라 달라졌다. 외부 검토가 잡았다.
    if _is_narrative_heading(stripped):
        return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
                f'font-weight:700;margin:12px 0 3px;">'
                f'{_esc(_HEADING_ORNAMENT_RE.sub("", stripped))}</div>')
    if stripped.startswith("■"):
        return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:15px;'
                f'font-weight:700;margin:20px 0 6px;border-top:1px solid {_LINE};'
                f'padding-top:12px;">{_esc(stripped.lstrip("■ "))}</div>')
    if stripped.startswith("▶"):
        return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
                f'font-weight:700;margin:12px 0 3px;">{_esc(stripped.lstrip("▶ "))}</div>')
    if stripped.startswith(_WEEKLY_WARN_PREFIX):
        return (f'<div style="background-color:{_PAPER_BG};color:#B00020;font-size:12px;'
                f'margin:3px 0;">{_esc(stripped)}</div>')
    if stripped.startswith(_WEEKLY_NOTE_PREFIX):
        return (f'<div style="background-color:{_PAPER_BG};color:{_MUTED};font-size:11px;'
                f'margin:4px 0 2px;padding-left:8px;">{_esc(stripped)}</div>')
    indent = len(raw) - len(raw.lstrip(" "))
    pad = min(indent, 12) * 2
    body = _colour_delta(_emphasise_enum(_emphasise_label(_esc(stripped))))
    return (f'<div style="background-color:{_PAPER_BG};color:{_INK};font-size:12px;'
            f'margin:2px 0;padding-left:{pad}px;">{body}</div>')


def _weekly_review_html(scan_result: dict) -> str:
    """⑥ 주간 리뷰를 HTML 로. 없으면 빈 문자열 — 주 1회만 채워진다."""
    review = scan_result.get("weekly_review")
    if not review or not str(review).strip():
        return ""
    return "".join(_weekly_line_html(ln) for ln in str(review).splitlines())


def _weekly_review_lines(scan_result: dict) -> list[str]:
    """⑥ 주간 리뷰를 평문 다이제스트 맨 아래에 붙일 줄들."""
    review = scan_result.get("weekly_review")
    if not review or not str(review).strip():
        return []
    return ["", str(review).strip()]


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
        f'<div style="font-size:17px;font-weight:700;color:#FFFFFF;">연구 동향 브리핑</div>'
        f'<div style="font-size:13px;color:#DCE3F5;margin-top:2px;">{_esc(date_str)}</div></div>'
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

    trend = _trend_line(scan_result)
    if trend:
        body += (
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
            f'border-top:1px solid {_LINE};padding-top:10px;margin-top:14px;">'
            f'<span style="color:{_INK};font-weight:600;">이번 창의 키워드별 적중 편수</span> '
            f'(후보 {candidates}건 기준)<br>{_esc(trend)}</p>'
        )

    details_body = body
    # 과거 논문 상태나 근거 부록을 앞뒤에 붙이지 않는다(2026-09-10 사용자 요청).
    body = ""
    story = scan_result.get("narrative")
    if story:
        text, ungrounded = story
        text = annotate_numbers(reflow_branch_lists(normalise_interpretation_marks(text)), numbered_mentions(scan_result), len(scan_result.get("papers") or []))
        paras = "".join(_narrative_line_html(ln)
                        for ln in text.strip().splitlines() if _plain(ln))
        warn = ""
        if ungrounded:
            warn = (f'<div style="background-color:{_PAPER_BG};color:#B00020;font-size:12px;'
                    f'margin-top:4px;">⚠ 원문에 없는 숫자가 섞여 있다: '
                    f'{_esc(", ".join(ungrounded))} — 믿지 말 것</div>')
        audit = scan_result.get("citation_audit") or {}
        if audit.get("unknown") or audit.get("title_only") or audit.get("cited_lines", 0) < audit.get("lines", 0):
            warn += f'<p style="color:{_FLAG_INK};">⚠ 일부 주장에 내용 근거 ID가 없거나 유효하지 않다 — 인용한 원문을 확인할 것</p>'
        # 서술이 이름으로 부른 논문에 링크를 붙인다(2026-09-07, §8-68 이 만든
        # 결함). 목록을 되살리는 게 아니라 **부른 것만** 붙인다 — 평문 판과
        # 같은 판정(mentioned_papers)을 쓰므로 두 판이 갈라지지 않는다.
        named = ""
        for paper in mentioned_papers(scan_result):
            url = paper_link(paper)
            title = _esc((paper.get("title") or "").strip())
            label = (f'<a href="{_esc(url)}" style="color:{_NAVY};">{title}</a>'
                     if url else title)
            named += (f'<div style="background-color:{_PAPER_BG};color:{_INK};'
                      f'font-size:12px;margin:2px 0;padding-left:10px;">· {label}</div>')
        if named:
            named = (f'<div style="background-color:{_PAPER_BG};color:{_MUTED};'
                     f'font-size:12px;margin:10px 0 2px;">위에서 이름으로 부른 논문</div>'
                     f'{named}')

        body += (
            f'<p style="background-color:{_PAPER_BG};color:{_INK};font-size:13px;'
            f'font-weight:600;margin:18px 0 4px;">오늘의 동향 정리</p>'
            f'<p style="background-color:{_PAPER_BG};color:{_MUTED};font-size:12px;'
            f'margin:0 0 6px;">{_esc(narrative_source_label(scan_result))}</p>'
            f'<p style="color:{_MUTED};font-size:12px;">기간 비교 없는 수집 표본의 관찰이며, 분야 전체의 변화는 판단하지 않았다.</p>'
            f'{paras}{warn}{named}'
        )

    body += details_body
    body += _weekly_review_html(scan_result)

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
