"""review_core.py — review_app 의 UI 없는 로직 (2026-09-02, §8-31).

Streamlit 화면을 그리지 않는 함수들만 모았다. **`st.` 을 쓰면 이 파일에
들어올 자격이 없다** — 데코레이터도 포함이다. `_translate_cached` 는
`@st.cache_data` 로 Streamlit 캐시에 묶여 있어 여기 안 왔다(처음에 놓쳤다가
문법 오류로 드러났다 — AST 의 lineno 가 def 줄을 가리켜 데코레이터를 빠뜨렸다).

**왜 나눴나**: review_app.py 는 1,902줄에 커버리지 0% 였고, 그 안에 ⑦ 자동
전이 지점(`_summarize_target`)이 들어 있었다. UI 와 코어가 한 파일에 섞여
있으면 Streamlit 을 띄우지 않고는 아무것도 부를 수 없어 커버리지를 올릴
방법이 없다.

**전이 지점이 여기로 왔다**: `_summarize_target` 은 ⑦ 자동 전이 2곳 중
하나다(CLAUDE.md 5). 규칙의 목적은 "전이 지점이 흩어지지 않는 것"이고 함수가
통째로 옮겨왔으므로 그 목적은 그대로다 — 오히려 테스트 가능한 곳으로 와서
감시가 쉬워졌다. 규칙 5 의 문구도 같이 갱신했다.

**분리 순서**: 파일을 쪼개기 전에 테스트를 먼저 썼다(test_review_core.py).
0% 커버 파일을 그물 없이 옮기는 건 §8-30 에서 "커버리지 39% 로는 리팩토링
못 한다"고 미뤘던 것보다 위험하다. 그 그물이 실제로 버그를 하나 잡았다
(`_pid_alive` 가 pid 없을 때 작업을 영원히 "진행 중"으로 남기던 것).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import docker_runner
import research_profile
import run_profile_scan
import sentence_grounding
import storage
import server
import summarize_engine as engine
import verify


# R2 형식("값(조건/비교대상/지표) — 출처위치 [S번호] ★등급")이 실제로 화면에
# 뽑아보니 한 줄에 괄호·대시·태그·별점이 다 붙어 읽기 힘들다는 지적을 받았다
# (2026-08-10, 실제 캡처 스크린샷 검토). 마크다운 구조 자체(절/불릿)는 안 건드리고
# "[S번호] ★등급" 꼬리표만 인라인 코드(백틱)로 묶어 본문 문장과 시각적으로
# 분리한다 — 백틱은 표준 마크다운이라 unsafe_allow_html 없이도 안전하게 렌더링된다.
_TAG_STAR_RE = re.compile(r"(\[S\d{4}\])\s*(★{1,3})")

# (?<!★)...(?!★)로 별점 런의 양끝을 고정해야 한다 — 그냥 (?<!`)(★{1,3})(?!`)만 쓰면
# 1단계에서 이미 `[S번호] ★★★`로 묶인 별 3개짜리를 여기서 다시 훑을 때, 정규식 엔진이
# 뒤 백틱을 피하려고 그리디 매칭을 3개→2개로 백트래킹해버려서 `[S0586] `★★`★`처럼
# 별이 2+1로 쪼개지는 실제 버그가 있었다(2026-08-10, repr()로 재현·확인). 런의 시작/끝에
# "다른 별이 인접하지 않음"을 강제하면 부분 매칭 자체가 봉쇄된다.
_STAR_ONLY_RE = re.compile(r"(?<!`)(?<!★)(★{1,3})(?!★)(?!`)")

# 템플릿(prompts/summary_template.md)의 "### 결론" 절은 ①②③④ 네 항목을 한 줄씩
# 개행으로만 구분해 내놓는다(줄바꿈 하나 — 마크다운은 이걸 문단 구분으로 안 보고
# 그대로 이어 붙여, ①부터 ④까지 한 문단으로 뭉쳐 렌더링된다). 저장된 20편 요약
# 전부 이 형식(grep으로 실측 확인: 전부 정확히 4개)이라, 원본을 고치는 대신 화면
# 표시 시점에 ②③④ 앞에 빈 줄을 넣어 문단을 분리한다. 이미 빈 줄이 있으면
# (?<!\n) 때문에 다시 안 건드려 — 두 번 적용해도 안전(idempotent).
_CONCLUSION_ITEM_RE = re.compile(r"(?<!\n)\n(?=[②③④])")

# ④ 결과 절 불릿이 "값(조건/비교대상/지표) — 출처위치 [S번호] ★등급" 형식을
# 한 줄에 다 몰아 쓰다 보니, 정작 중요한 "값"이 문장 속에 묻혀 눈에 안 띈다는
# 지적을 받았다(2026-08-10). 값과 조건 사이 경계는 이미 템플릿이 고정한
# 문법(값 바로 뒤 첫 "(" ~ 대시 "—" 직전 마지막 ")")이라 자연어 해석 없이
# 기계적으로 잘라낼 수 있다 — "필드별 검증"과 달리 숫자가 맞는지 판단하는
# 게 아니라 이미 정해진 구두점 구조를 그대로 재배치만 하는 것이라 서버가
# "판단"하는 것과는 다르다. detail 그룹을 그리디(.+)로 잡아야 "조건(대화형
# 및 비대화형)"처럼 괄호가 중첩된 경우도 마지막 ")"까지 올바르게 잡힌다.
# loc 에서 "[" 를 막지 않은 이유: "초록 [S0005] / 본문 4.2절 [S0158] ★★★"처럼
# 본문 앞에 다른 [S번호]가 먼저 나오는 이중 인용 줄도 있어(1810.04805 BERT
# 실측 확인) — 대괄호를 막으면 그 줄만 통째로 매치 실패한다.
# 저장된 20편 전체(불릿+태그 116줄)를 대조해 전부 매치되는 것까지 확인했다.
_R2_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*[-*]\s+)(?P<value>[^\n(]+?)\s*\((?P<detail>.+)\)\s*—\s*"
    r"(?P<loc>[^\n]*?)\s*(?P<tag>\[S\d{4}\]\s*★{1,3})\s*$",
    re.MULTILINE,
)

# "연구 개요"·"방법 상세"·"실험 설정"·"파싱 품질 노트"·결론 ①②③④ 등 템플릿
# 전반에 쓰이는 "- 레이블 : 설명" 불릿에서 레이블이 설명 글과 같은 굵기라
# 안 눈에 띈다는 지적(2026-08-12) — "무엇을 하려고 했는가", "데이터셋" 같은
# 레이블만 볼드로 만든다. 콜론 앞 텍스트가 곧 레이블이라는 건 템플릿이
# 이미 고정한 구두점 구조이지 자연어 해석이 아니라, R2 불릿 볼드 처리와
# 같은 "판단 아님" 성격이다. 레이블 길이를 26자로 제한해 일반 문장 중간의
# 콜론(드묾)까지 잘못 걸리는 걸 방지했고, 논문 제목 자체에 콜론이 있는
# "제목 : LF-YOLO: A Lighter..." 같은 경우도 첫 콜론까지만 레이블로 잡혀
# 문제없이 처리되는 것을 실측 확인(2026-08-12). 저장된 46편 전체를 대조해
# 1,375줄 중 1,125줄이 매치, R2 결과 불릿(콜론 없음)엔 오탐 없음을 확인했다.
_FIELD_LABEL_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:[-*]|[①②③④])\s+)"
    r"(?P<label>[^:：\n]{1,26}?)"
    r"\s*(?P<colon>[:：])\s*"
    r"(?P<rest>\S.*)$",
    re.MULTILINE,
)

def run_async(coro):
    return asyncio.run(coro)

def _fetch_review_rows() -> list[dict]:
    """⑥ 게이트가 없어져(2026-08-24) review_status로 거를 이유가 없다 —
    저장된 요약은 전부 이미 ④⑤ 끝난 것이고 곧 ⑦도 자동으로 붙는다."""
    with storage.db() as con:
        rows = con.execute(
            "SELECT s.arxiv_id, p.title, s.path, s.numbers_total, s.numbers_matched, "
            "s.created_at FROM summaries s JOIN papers p ON s.arxiv_id = p.arxiv_id "
            "ORDER BY s.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]

def _bold_r2_value(m: re.Match) -> str:
    value = m.group("value").strip()
    detail = m.group("detail").strip()
    loc = m.group("loc").strip()
    loc_part = f"{loc} " if loc else ""
    return f"{m.group('indent')}**{value}** _({detail})_ — {loc_part}{m.group('tag')}"

def _bold_field_label(m: re.Match) -> str:
    # 콜론 앞 공백은 템플릿 관례("레이블 : 내용")를 유지 — 볼드만 추가하고
    # 나머지 타이포그래피는 안 바꾼다.
    return f"{m.group('prefix')}**{m.group('label').strip()}** {m.group('colon')} {m.group('rest')}"

def _prettify_summary_markdown(text: str) -> str:
    """요약 마크다운을 화면 표시 직전에 다듬는다. 원본 저장 파일은 안 건드리고
    렌더링할 때만 바꾼다 — save_summary/verify.py 는 원본 그대로를 대조해야
    하므로 이 함수는 화면 표시 경로에서만 쓴다.
    """
    # "1~7절"의 물결표가 markdown 취소선(~text~)으로 오인되는 것부터 이스케이프
    text = text.replace("~", "\\~")
    # "레이블 : 설명" 불릿의 레이블을 볼드로 — R2 불릿(값이 콜론 없이 시작)과
    # 겹치는 줄이 없어 어느 순서로 해도 안전하지만, "구조 먼저" 순서로 앞에 둔다.
    text = _FIELD_LABEL_RE.sub(_bold_field_label, text)
    # ④ 결과류 불릿의 "값"을 볼드로, "(조건/비교대상/지표)"를 이탤릭으로 —
    # [S번호]★ 칩 래핑보다 먼저 해야 태그 원문(백틱 없는 상태)을 그대로 재사용할 수 있다
    text = _R2_LINE_RE.sub(_bold_r2_value, text)
    # [S번호]+별점을 하나의 칩으로 묶는다 (가장 흔한 R2/R3 형식)
    text = _TAG_STAR_RE.sub(lambda m: f"`{m.group(1)} {m.group(2)}`", text)
    # 태그 없이 별점만 있는 경우(그라운딩 안 된 항목·구형 요약)도 칩으로
    text = _STAR_ONLY_RE.sub(lambda m: f"`{m.group(1)}`", text)
    # 결론 절의 ①②③④를 문단별로 분리
    text = _CONCLUSION_ITEM_RE.sub("\n\n", text)
    return text

def _verify_detail(arxiv_id: str, summary_text: str) -> verify.VerificationReport:
    with storage.db() as con:
        row = con.execute(
            "SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    return verify.verify_numbers(summary_text, source)

def _pid_alive(pid: int) -> bool:
    """실측(2026-08-19)으로 걸린 함정: 이 프로세스(Streamlit)가 자식으로
    띄운 batch_summarize.py가 끝나도, 아무도 회수(reap)하지 않으면 좀비
    (`Zs <defunct>`)로 남는다 — Streamlit이 매 상호작용마다 스크립트를
    새로 실행할 뿐 그 사이에 이 자식 프로세스의 Popen 객체를 들고 있지
    않아서(다음 재실행에서는 pid 숫자만 파일에서 읽어올 뿐 원래 Popen
    객체가 없다) `.wait()`를 걸 대상이 없다. 문제는 `os.kill(pid, 0)`이
    좀비도 "존재한다"고 착각한다는 것 — 그러면 이미 끝난 작업이 화면에
    영원히 "진행 중"으로 남는다(실측으로 직접 재현·확인함). 그래서 먼저
    `waitpid(WNOHANG)`로 우리 자식이면 회수를 시도하고(논블로킹 — 아직
    안 끝났으면 즉시 (0,0)으로 돌아옴), 그다음에만 kill(pid,0)로 진짜
    생존 여부를 확인한다.

    2026-09-02: 양수 pid 인지 먼저 확인한다. 호출부가
    `job.get("pid", -1)` 로 **기본값 -1** 을 넘기는데, POSIX 에서 -1 은
    "아무 자식이나"를 뜻해서 `waitpid(-1)` 이 무관한 자식을 회수하고
    `kill(-1, 0)` 은 성공한다 — 즉 **pid 가 없으면 작업이 영원히 "진행 중"
    으로 남았다.** 이 함수가 막으려고 쓰인 바로 그 증상이 기본값으로 다시
    들어와 있었다. 0 도 같은 이유로(프로세스 그룹) 막는다. progress 파일이
    깨져 문자열이 들어와도 예외 대신 False 로 떨어진다 — 여기서 예외가
    나면 화면 전체가 죽는다."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False  # 방금 회수됨 — 이미 끝나 있었다
    except ChildProcessError:
        pass  # 우리 자식이 아님(서버 재시작 등) — 존재 여부만 그대로 확인
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _format_job_result_line(r: dict) -> str:
    """batch_summarize.py의 결과 딕셔너리 한 건을 사람이 읽을 한 줄로
    바꾼다 — 성공/실패 여부와 실패라면 이유까지(2026-08-19, "왜 실패
    했는지 사용자가 확인할 수 있어야 하지 않아" 지적)."""
    arxiv_id = r.get("arxiv_id", "?")
    status = r.get("status")
    if status == "done":
        ratio, matched, total_n = r.get("pass_ratio"), r.get("matched"), r.get("total_numbers")
        return f"✅ `{arxiv_id}` — {r.get('engine', '?')}, 통과율 {ratio} ({matched}/{total_n})"
    detail = r.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("error") or detail
    if status == "fetch_failed":
        return f"❌ `{arxiv_id}` — 원문 수집 실패: {detail}"
    return f"❌ `{arxiv_id}` — 처리 실패: {detail}"

async def _summarize_target(
    arxiv_id: str, client: httpx.AsyncClient, status_box, allow_title_backfill: bool = False
) -> bool:
    """③(원문 수집)까지 끝난 논문 하나에 ④⑤(요약·검증+저장)만 돌린다.
    키워드/ID/제목 검색과 PDF 업로드·오픈액세스 수집이 여기서부터 합류한다.

    템플릿(기본 v2 / 서베이 변형)은 저장된 제목으로 여기서 결정론적으로
    고른다 — engine.select_template 참고, LLM 판단 아님.

    allow_title_backfill: PDF·오픈액세스 경로에서 사용자가 제목을 직접
    타이핑하지 않아 서버가 추정(또는 자리표시자)한 경우에만 True로 온다
    (2026-08-12). "사람이 직접 쓴 제목은 절대 안 건드리고, 서버가 추측한
    제목은 더 나은 정보(요약문은 LLM이 원문 전체를 읽고 뽑은 값이라 ingest
    시점의 PDF 첫 줄 휴리스틱보다 신뢰도가 높음)가 생기면 계속 개선한다"는
    원칙 — arXiv 검색으로 들어온 논문은 이 값이 항상 False라 절대 안 건드림.
    """
    with storage.db() as con:
        row = con.execute("SELECT title FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    title = row["title"] if row and row["title"] else ""
    template = engine.select_template(title)

    # get_paper_text(MCP 도구)는 채팅 컨텍스트 절약용 80,000자 상한이 있다 —
    # 여기서는 원문 전체를 읽는다. 길면 summarize_engine 이 알아서 청크로 나눈다.
    paper_text = server.read_full_text(arxiv_id)
    status_box.write(f"④ [{arxiv_id}] 요약 생성 중...")
    summary, used_engine = await engine.summarize(client, paper_text, template)
    status_box.write(f"④ [{arxiv_id}] 완료 — {used_engine} 사용")

    status_box.write(f"⑤ [{arxiv_id}] 검증 + 저장 중...")
    save_result = json.loads(
        await server.save_summary(server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=summary))
    )
    v = save_result.get("verification", {})
    status_box.write(
        f"✅ [{arxiv_id}] 완료 — pass_ratio={v.get('pass_ratio')} "
        f"({v.get('matched')}/{v.get('total_numbers')})"
    )

    # ⑥ 승인 게이트 없이 저장 직후 곧바로 ⑦로 넘어간다(2026-08-24, batch_
    # summarize.py의 같은 변경과 동일 이유) — 별도 프로세스라 여기서 안 막힘.
    repro_msg = docker_runner.launch_background(arxiv_id)
    status_box.write(f"⑦ [{arxiv_id}] {repro_msg}")

    if allow_title_backfill:
        extracted = server.extract_title_from_summary(summary)
        if extracted and extracted != title:
            server.update_paper_title(arxiv_id, extracted)
            status_box.write(f"📝 제목 자동 채움: {extracted}")
    return True

async def _run_pdf_upload_and_summarize(pdf_bytes: bytes, title: str, status_box) -> list[str]:
    """arXiv 밖 저널 PDF를 직접 업로드해 ③(수동)→④⑤ 를 돈다. 이미 합법적으로
    접근 가능한 파일(기관 구독 등)을 사용자가 올리는 경로 — 페이월 우회 아님.

    title이 비어 있으면(2026-08-12부터 화면에서 필수 입력이 아님)
    server.ingest_local_pdf가 PDF 메타데이터·본문 첫 줄에서 자동 추정한다.
    """
    status_box.write("③ PDF 텍스트 추출 중...")
    try:
        result = server.ingest_local_pdf(pdf_bytes, title, source_note="manual-pdf: streamlit-upload")
    except ValueError as e:
        status_box.write(f"❌ 추출 실패: {e}")
        return []
    arxiv_id = result["arxiv_id"]
    status_box.write(f"③ [{arxiv_id}] 완료 — {result['text_chars']}자 ({result.get('note', '신규 저장')})")

    async with httpx.AsyncClient() as client:
        ok = await _summarize_target(
            arxiv_id, client, status_box, allow_title_backfill=result.get("title_auto", False)
        )
    return [arxiv_id] if ok else []

async def _run_open_access_and_summarize(doi_or_url: str, title: str, status_box) -> list[str]:
    """DOI 또는 PDF 직접 링크로 오픈액세스 논문을 받아 ③(자동)→④⑤ 를 돈다.
    DOI 형태(슬래시 포함, .pdf로 안 끝남)면 Unpaywall 로 먼저 합법적 PDF
    위치를 찾고, 이미 PDF 링크면 바로 받는다. 오픈액세스가 아니면 실패를
    정직하게 보고한다 — 페이월을 다른 방법으로 우회하지 않는다.

    title은 사용자가 직접 입력했으면 그걸 최우선으로 쓴다. 비어 있고
    DOI 경로면 Unpaywall 응답에 이미 제목이 들어 있어(2026-08-12) 그걸
    쓰고, 그것도 없으면 fetch_pdf_from_url→ingest_local_pdf의 자동 추정
    체인으로 넘어간다.
    """
    pdf_url = doi_or_url
    if not doi_or_url.lower().endswith(".pdf") and "/" in doi_or_url:
        status_box.write(f"DOI '{doi_or_url}' 로 오픈액세스 PDF 위치 조회 중 (Unpaywall)...")
        resolved = await server.resolve_unpaywall_pdf(doi_or_url)
        if not resolved:
            status_box.write("❌ 오픈액세스 버전을 찾지 못함 — 이 논문은 PDF 업로드로 들여와야 함")
            return []
        pdf_url = resolved["url"]
        if not title and resolved["title"]:
            title = resolved["title"]
            status_box.write(f"Unpaywall에서 제목 발견: {title}")
        status_box.write(f"오픈액세스 PDF 발견: {pdf_url}")

    status_box.write("③ PDF 다운로드·텍스트 추출 중...")
    try:
        result = await server.fetch_pdf_from_url(pdf_url, title=title, source_note=f"open-access: {doi_or_url}")
    except (ValueError, httpx.HTTPError) as e:
        status_box.write(f"❌ 수집 실패: {type(e).__name__}: {e}")
        return []
    arxiv_id = result["arxiv_id"]
    status_box.write(f"③ [{arxiv_id}] 완료 — {result['text_chars']}자 ({result.get('note', '신규 저장')})")

    async with httpx.AsyncClient() as client:
        ok = await _summarize_target(
            arxiv_id, client, status_box, allow_title_backfill=result.get("title_auto", False)
        )
    return [arxiv_id] if ok else []

def _fetch_repro_rows(arxiv_id: str) -> list[dict]:
    with storage.db() as con:
        rows = con.execute(
            "SELECT repo_url, source, confidence, success, exit_code, stage, "
            "attempt, duration_s, created_at, local_path FROM repro_results "
            "WHERE arxiv_id=? ORDER BY created_at DESC",
            (arxiv_id,),
        ).fetchall()
    return [dict(r) for r in rows]

def _reproduce_running(arxiv_id: str) -> bool:
    """docker_runner.py가 이 arxiv_id로 이미 떠 있는지 — 화면 표시용."""
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    return marker.exists()

def _pipeline_status(arxiv_id: str) -> tuple[str, str, str]:
    """returns (css_key, emoji, label). 2026-08-24, ⑥ 승인 게이트 제거 이후
    화면에 보여줄 "지금 상태"는 review_status(사람 판단)가 아니라 ⑦ 재현
    상태다 — ④⑤는 저장되는 순간 이미 끝나 있어서 더 볼 게 없고, ⑦만 시간이
    걸리는 뒷단이라 진행 상태가 의미 있다.

    repro_results에 성공 행이 있으면 성공, 행은 있는데 성공이 없으면 실패,
    마커 파일이 있으면 진행 중, 로그가 "후보 없음"으로 끝났으면 코드 없음,
    그 무엇도 아니면 방금 저장돼 곧 시작될 것(launch_background 호출과
    첫 로그 기록 사이의 짧은 틈에만 보임) — 이 다섯 개가 실제로 있는
    전부다(중간 상태를 지어내지 않는다, _render_repro_status와 같은 원칙)."""
    if _reproduce_running(arxiv_id):
        return "repro_running", "🔵", "재현 중"
    rows = _fetch_repro_rows(arxiv_id)
    if any(r["success"] for r in rows):
        return "repro_ok", "🟢", "재현 성공"
    if rows:
        return "repro_failed", "🔴", "재현 실패"
    log_path = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.log"
    if log_path.exists() and log_path.read_text(encoding="utf-8").strip():
        try:
            outcome = json.loads(log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            outcome = {}
        if not outcome.get("success"):
            return "no_code", "🟠", "코드 없음"
    return "repro_pending", "🟡", "재현 대기"

def _relative_time(iso_ts: str) -> str:
    """'2026-08-14T05:12:33+00:00' 같은 UTC ISO 문자열을 'N분 전' 식으로
    바꾼다. 참고 이미지의 "3분 전" 표시를 실제 타임스탬프로 계산한 것 —
    화면에 고정 문구를 박아넣지 않는다."""
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = delta.total_seconds()
    if secs < 60:
        return "방금 전"
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    return f"{int(secs // 86400)}일 전"

def _format_run_row(row: dict) -> str:
    emoji = {"done": "✅", "partial": "🟡", "failed": "❌"}.get(row["status"], "⚪")
    when = _relative_time(row["started_at"]) if row.get("started_at") else "?"
    window = f"{row['window_from'][:10]} ~ {row['window_to'][:10]}"
    line = f"{emoji} {row['status']} · {row['retrieved_count']}건 · {window} · {when}"
    if row["status"] == "failed" and row.get("error_detail"):
        line += f"\n   사유: {row['error_detail']}"
    return line

def _fetch_sidebar_lists() -> dict:
    """사이드바 '현황' — 숫자만 있으면 어떤 논문인지 안 보인다는 지적을
    받아(2026-08-12), 카테고리별 실제 논문 목록을 반환한다. 카테고리는
    서로 배타적인 버킷이 아니라 "이 조건에 해당하는 논문이 뭐가 있나"를
    보여주는 서로 다른 렌즈다.

    2026-08-24: ⑥ 승인 게이트가 없어지면서 "저장된 논문(대기중)"/"승인됨"
    구분이 사라졌다 — 모든 저장된 논문이 곧 ⑦까지 자동으로 붙으므로,
    이제 의미 있는 축은 review_status가 아니라 ⑦ 재현 결과다. "코드 없음"
    판정도 예전엔 "승인된 논문 중"으로 좁혔던 걸 "저장된 논문 전체"로
    넓혔다 — 승인이라는 전제 자체가 없어졌으니 좁힐 이유가 없다."""
    with storage.db() as con:
        all_papers = con.execute(
            "SELECT p.arxiv_id, p.title FROM papers p JOIN summaries s ON p.arxiv_id=s.arxiv_id "
            "ORDER BY p.title"
        ).fetchall()
        repro_ok = con.execute(
            "SELECT DISTINCT p.arxiv_id, p.title FROM papers p "
            "JOIN repro_results r ON p.arxiv_id=r.arxiv_id "
            "WHERE r.success=1 ORDER BY p.title"
        ).fetchall()
        repro_failed_ids = {
            r["arxiv_id"] for r in con.execute(
                "SELECT DISTINCT arxiv_id FROM repro_results WHERE arxiv_id NOT IN "
                "(SELECT arxiv_id FROM repro_results WHERE success=1)"
            ).fetchall()
        }
        repro_attempted = {
            r["arxiv_id"] for r in con.execute("SELECT DISTINCT arxiv_id FROM repro_results").fetchall()
        }

    repro_failed = [dict(r) for r in all_papers if r["arxiv_id"] in repro_failed_ids]

    # "코드 없음" — repro_results에 행이 아예 없는 논문 중 로그 파일이
    # "저장소 후보 없음"으로 끝난 것만(_render_repro_status 폴백과 동일 이유
    # — code_finder가 후보를 하나도 못 찾으면 save_repro_result가 안 불린다).
    # 후보는 있었는데 설치·실행에 실패한 경우는 여기 안 넣는다 — 그건
    # repro_failed에 이미 잡힌다.
    no_code = []
    for r in all_papers:
        aid = r["arxiv_id"]
        if aid in repro_attempted:
            continue
        log_path = server.REPRO_DIR / f"{aid.replace('/', '_')}.log"
        if not log_path.exists():
            continue
        text = log_path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        try:
            outcome = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not outcome.get("success"):
            no_code.append(dict(r))

    return {
        "all": [dict(r) for r in all_papers],
        "repro_ok": [dict(r) for r in repro_ok],
        "repro_failed": repro_failed,
        "no_code": no_code,
    }
