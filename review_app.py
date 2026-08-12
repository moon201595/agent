"""review_app.py — ⑥ 사람 판단 UI (Streamlit).

키워드로 논문을 검색해 요약을 생성하고, 생성된 요약을 사람이 보고 승인·반려하는
화면. server.py 는 판단하지 않는다는 원칙을 그대로 지킨다 — 승인/반려 버튼을
누르는 게 "판단"이고, 이 파일은 그 결과를 server.set_review_status() 로 저장만
시킨다.

실행:
    streamlit run review_app.py

검증 실패(수치 불일치)는 "오류 확정"이 아니라 "사람이 확인" 신호라는 게 이
하네스의 원칙이다 (docs/PROGRESS.md §6). 그래서 반려 사유를 강제하지 않고,
불일치 항목을 원문 대조하기 쉽게 문맥과 함께 보여주는 데 집중했다.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import streamlit as st

import server
import summarize_engine as engine
import verify

st.set_page_config(page_title="논문 검색·분석 에이전트", layout="wide", page_icon="📄")


def _inject_custom_style() -> None:
    """하늘색·흰색 중심의 깔끔한 톤(2026-08-06). Streamlit 기본 테마만 쓰면
    버튼·경고 박스·탭이 전부 진한 채도의 기본색이라 "AI가 급하게 만든
    데모"처럼 보인다는 피드백을 받고 순수 시각 레이어만 추가했다 — 로직은
    전혀 안 건드림. 색상 기반은 .streamlit/config.toml, 카드·탭·여백 같은
    세부 모양은 여기서 담당한다. data-testid 셀렉터는 Streamlit이 공식
    문서화한 안정적인 훅이라 버전이 올라가도 잘 안 깨진다.
    """
    st.markdown(
        """
        <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.css');

        :root {
            --sky: #0EA5E9;
            --sky-dark: #0284C7;
            --sky-light: #EAF6FD;
            --sky-border: #D3EAF7;
            --text-main: #1E293B;
            --text-muted: #64748B;
        }

        html, body, [class*="css"] {
            font-family: 'PretendardVariable', -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
        }

        /* 기본 레이아웃 여백 — Streamlit 기본값은 위쪽이 휑하게 남는다 */
        .block-container { padding-top: 2.5rem; padding-bottom: 3rem; max-width: 1100px; }

        /* 제목 영역 */
        h1 { font-weight: 700; color: var(--text-main); letter-spacing: -0.01em; }
        h1 + div, h1 { margin-bottom: 0.3rem; }
        h2, h3 { color: var(--text-main); font-weight: 600; }

        /* 탭 — 밑줄 인디케이터 스타일로, 선택된 탭만 하늘색 */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 4px; border-bottom: 1px solid var(--sky-border);
        }
        [data-testid="stTabs"] button[data-baseweb="tab"] {
            color: var(--text-muted); font-weight: 500; border-radius: 8px 8px 0 0;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--sky-dark); font-weight: 700;
        }

        /* 버튼 — 각지고 진한 기본 톤 대신 둥근 모서리 + 옅은 하늘색 */
        [data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
            border-radius: 10px; border: 1px solid var(--sky-border);
            transition: all 0.15s ease;
        }
        [data-testid="stBaseButton-primary"] {
            background-color: var(--sky); border: none;
        }
        [data-testid="stBaseButton-primary"]:hover {
            background-color: var(--sky-dark);
        }
        [data-testid="stButton"] button:hover {
            border-color: var(--sky); color: var(--sky-dark);
        }

        /* 입력창·셀렉트·라디오 — 각진 기본 테두리를 둥글게, 포커스에 하늘색 */
        [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
        [data-testid="stFileUploaderDropzone"] {
            border-radius: 10px !important; border-color: var(--sky-border) !important;
        }
        [data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus {
            border-color: var(--sky) !important; box-shadow: 0 0 0 1px var(--sky) !important;
        }

        /* 요약 검토 카드(expander) — 흰 배경 + 옅은 그림자로 "카드"처럼 분리 */
        [data-testid="stExpander"] {
            border: 1px solid var(--sky-border) !important; border-radius: 12px !important;
            box-shadow: 0 1px 3px rgba(14, 165, 233, 0.06);
            background-color: #FFFFFF; margin-bottom: 0.6rem;
        }
        [data-testid="stExpander"] summary {
            font-weight: 600; color: var(--text-main);
        }

        /* 알림 박스(성공/경고/오류/정보) — 모서리만 둥글게, 성공=초록/경고=노랑/오류=빨강
           같은 의미별 색상은 Streamlit 기본값을 그대로 둔다(하늘색으로 덮으면 경고·오류
           박스까지 파랗게 보여서 오히려 의미 구분이 흐려진다). */
        [data-testid="stAlert"] {
            border-radius: 10px;
        }

        /* status 박스(진행 상황 로그) */
        [data-testid="stExpander"] [data-testid="stMarkdownContainer"] p {
            color: var(--text-main);
        }

        /* 캡션·보조 텍스트 톤 다운 */
        [data-testid="stCaptionContainer"] { color: var(--text-muted); }

        /* 요약 본문 리스트 — "값(조건/비교대상/지표) — 출처위치 [S번호] ★등급"
           형식이 한 줄에 다 붙어 있어 읽기 힘들다는 지적(2026-08-10)을 받아
           불릿 사이 간격을 넉넉히 벌리고 줄 간격도 늘렸다. */
        [data-testid="stMarkdownContainer"] li {
            margin-bottom: 0.6em; line-height: 1.65;
        }
        [data-testid="stMarkdownContainer"] li > ul,
        [data-testid="stMarkdownContainer"] li > ol {
            margin-top: 0.4em;
        }
        /* [S번호]·★등급 꼬리표(백틱 인라인 코드)를 하늘색 톤 칩으로 —
           본문 문장과 시각적으로 분리되어 한눈에 "출처 표시"로 읽힌다. */
        [data-testid="stMarkdownContainer"] code {
            background-color: var(--sky-light); color: var(--sky-dark);
            border-radius: 6px; padding: 0.15em 0.45em; font-size: 0.88em;
        }

        /* Streamlit 기본 푸터("Made with Streamlit") 숨김 */
        footer { visibility: hidden; }
        /* Deploy 버튼·⋮ 메뉴(Rerun/Clear cache/Print/Record screen 등)는
           streamlit.io 배포·공유용 기능이라 WSL 로컬 전용 내부 도구에는
           의미가 없다 — 그대로 두면 "범용 Streamlit 데모" 티가 나서
           숨긴다(2026-08-12, 실측: 실제 DOM에서 stAppDeployButton·
           stMainMenu testid 확인 후 반영). 실행 중 표시(stStatusWidget)는
           유용해서 남겨 둔다.
        */
        [data-testid="stAppDeployButton"] { display: none; }
        [data-testid="stMainMenu"] { display: none; }

        /* 사이드바 — "화면이 너무 하얗다"는 지적(2026-08-12)에 좌측에 색이
           들어간 영역을 둬서 구조를 준다. 참고로 보여준 결제 대시보드를
           그대로 베끼진 않고, "탐색 영역과 본문 영역이 색으로 구분된다"는
           느낌만 가져왔다. */
        [data-testid="stSidebar"] {
            background-color: #F8FAFC; border-right: 1px solid var(--sky-border);
        }
        [data-testid="stSidebar"] .sidebar-brand {
            padding: 0.4rem 0 1rem 0; font-size: 1.05rem; color: var(--text-main);
            border-bottom: 1px solid var(--sky-border); margin-bottom: 0.2rem;
        }
        [data-testid="stSidebar"] .sidebar-brand-sub {
            font-size: 0.8rem; color: var(--text-muted); font-weight: 400;
        }
        [data-testid="stSidebar"] .sidebar-nav-gap { height: 0.6rem; }
        /* 내비게이션 버튼 — primary(선택된 페이지)는 하늘색 채움,
           secondary는 투명해서 사이드바 배경과 섞이게 해 "지금 여기
           있다"는 게 자연히 드러난다. */
        [data-testid="stSidebar"] [data-testid="stButton"] button {
            justify-content: flex-start; text-align: left; font-weight: 500;
        }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
            background-color: transparent; border-color: transparent;
        }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
            background-color: var(--sky-light); border-color: var(--sky-border);
        }
        /* 현황 통계 — 숫자만 덩그러니 있지 않게 라벨·값을 한 줄에 양끝 정렬 */
        [data-testid="stSidebar"] .sidebar-stats div {
            display: flex; justify-content: space-between; align-items: baseline;
            padding: 0.3rem 0.1rem; font-size: 0.85rem; color: var(--text-muted);
        }
        [data-testid="stSidebar"] .sidebar-stats b {
            color: var(--text-main); font-size: 0.95rem;
        }

        /* 본문 상단 헤더 영역 — subheader를 옅은 하늘색 배경 띠로 감싸서
           참고 이미지의 상단 바처럼 "여기가 페이지 제목 영역"임을 표시 */
        /* st.subheader()는 h3로 렌더링된다(실측 확인, h2 아님) — 페이지
           최상단의 subheader에만 밑줄을 줘 "여기가 헤더"임을 표시한다. */
        [data-testid="stAppViewContainer"] .block-container > div:first-child h3:first-of-type {
            padding-bottom: 0.6rem; border-bottom: 1px solid var(--sky-border);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_custom_style()


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 공용 조회


def _fetch_review_rows(show_all: bool) -> list[dict]:
    with server._db() as con:
        query = """
            SELECT s.arxiv_id, p.title, s.path, s.numbers_total, s.numbers_matched,
                   s.review_status, s.review_note, s.created_at
            FROM summaries s JOIN papers p ON s.arxiv_id = p.arxiv_id
        """
        if not show_all:
            query += " WHERE s.review_status = 'pending' OR s.review_status IS NULL"
        query += " ORDER BY s.created_at DESC"
        rows = con.execute(query).fetchall()
    return [dict(r) for r in rows]


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


def _bold_r2_value(m: re.Match) -> str:
    value = m.group("value").strip()
    detail = m.group("detail").strip()
    loc = m.group("loc").strip()
    loc_part = f"{loc} " if loc else ""
    return f"{m.group('indent')}**{value}** _({detail})_ — {loc_part}{m.group('tag')}"


def _prettify_summary_markdown(text: str) -> str:
    """요약 마크다운을 화면 표시 직전에 다듬는다. 원본 저장 파일은 안 건드리고
    렌더링할 때만 바꾼다 — save_summary/verify.py 는 원본 그대로를 대조해야
    하므로 이 함수는 화면 표시 경로에서만 쓴다.
    """
    # "1~7절"의 물결표가 markdown 취소선(~text~)으로 오인되는 것부터 이스케이프
    text = text.replace("~", "\\~")
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
    with server._db() as con:
        row = con.execute(
            "SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    return verify.verify_numbers(summary_text, source)


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _render_image_gallery(arxiv_id: str) -> None:
    """표·그림 원본을 갤러리로 보여준다. HTML 출처는 실제 캡션이 라벨로 붙고,
    PDF 출처는 순서대로 '그림 N'만 붙는다 — PyMuPDF(AGPL)를 배제한 채로는
    pypdf 만으로 PDF 안에서 어떤 이미지가 정확히 몇 번 Figure인지 매칭할 수
    없어서다. 표(Table)는 PDF 안에서 대개 이미지가 아니라 벡터·텍스트로
    그려져 있어 이 방식으로는 거의 뽑히지 않는다.
    """
    img_dir = run_async(server.ensure_images_extracted(arxiv_id))
    files = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    if not files:
        st.caption("추출된 이미지 없음 (원문에 임베드된 이미지가 없거나 추출 실패)")
        return

    labels: dict[str, str] = {}
    labels_path = img_dir / "_labels.json"
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))

    cols = st.columns(3)
    for i, f in enumerate(files):
        caption = labels.get(f.name) or f"그림 {i + 1}"
        with cols[i % 3]:
            st.image(str(f), caption=caption, use_container_width=True)


# ---------------------------------------------------------------- 탭 ①: 검색·요약


async def _run_search_and_summarize(mode: str, value: str, top_n: int, status_box) -> list[str]:
    if mode == "id":
        targets = [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    elif mode == "title":
        status_box.write(f"'{value}' 제목으로 arXiv 검색 중...")
        result = json.loads(
            await server.arxiv_search_papers(server.ArxivSearchInput(query=value, max_results=1))
        )
        papers = result.get("papers", [])
        if not papers:
            status_box.write("검색 결과 없음")
            return []
        targets = [papers[0]["arxiv_id"]]
        status_box.write(f"찾음: {papers[0]['title']}")
    else:  # keyword
        status_box.write(f"① '{value}' 키워드로 arXiv + Semantic Scholar 검색 중...")
        arxiv_res = json.loads(
            await server.arxiv_search_papers(
                server.ArxivSearchInput(query=value, max_results=top_n * 3)
            )
        )
        s2_res = json.loads(
            await server.s2_search_papers(server.S2SearchInput(query=value, limit=top_n * 3))
        )
        combined = arxiv_res.get("papers", []) + s2_res.get("papers", [])
        status_box.write(f"arXiv {len(arxiv_res.get('papers', []))}건, S2 {len(s2_res.get('papers', []))}건 발견")
        if not combined:
            status_box.write(f"검색 결과 없음 — 외부 API 한도 초과(429)일 수 있음: {arxiv_res} / {s2_res}")
            return []
        status_box.write("② 중복 제거·선별 중...")
        ranked = json.loads(
            await server.dedupe_and_rank_papers(
                server.SelectPapersInput(papers=combined, top_k=top_n)
            )
        )
        targets = [p["arxiv_id"] for p in ranked.get("papers", []) if p.get("arxiv_id")]
        status_box.write(f"선별됨: {targets}")

    if not targets:
        return []

    done = []
    async with httpx.AsyncClient() as client:
        for arxiv_id in targets:
            status_box.write(f"③ [{arxiv_id}] 원문 수집 중...")
            fetch_result = json.loads(
                await server.fetch_paper(server.FetchPaperInput(arxiv_id=arxiv_id))
            )
            if "error" in fetch_result:
                status_box.write(f"❌ [{arxiv_id}] 수집 실패: {fetch_result}")
                continue
            status_box.write(
                f"③ [{arxiv_id}] 완료 — {fetch_result.get('extract_method')}, "
                f"{fetch_result.get('text_chars')}자"
            )
            if await _summarize_target(arxiv_id, client, status_box):
                done.append(arxiv_id)

    return done


async def _summarize_target(arxiv_id: str, client: httpx.AsyncClient, status_box) -> bool:
    """③(원문 수집)까지 끝난 논문 하나에 ④⑤(요약·검증+저장)만 돌린다.
    키워드/ID/제목 검색과 PDF 업로드·오픈액세스 수집이 여기서부터 합류한다.

    템플릿(기본 v2 / 서베이 변형)은 저장된 제목으로 여기서 결정론적으로
    고른다 — engine.select_template 참고, LLM 판단 아님.
    """
    with server._db() as con:
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
    return True


async def _run_pdf_upload_and_summarize(pdf_bytes: bytes, title: str, status_box) -> list[str]:
    """arXiv 밖 저널 PDF를 직접 업로드해 ③(수동)→④⑤ 를 돈다. 이미 합법적으로
    접근 가능한 파일(기관 구독 등)을 사용자가 올리는 경로 — 페이월 우회 아님.
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
        ok = await _summarize_target(arxiv_id, client, status_box)
    return [arxiv_id] if ok else []


async def _run_open_access_and_summarize(doi_or_url: str, title: str, status_box) -> list[str]:
    """DOI 또는 PDF 직접 링크로 오픈액세스 논문을 받아 ③(자동)→④⑤ 를 돈다.
    DOI 형태(슬래시 포함, .pdf로 안 끝남)면 Unpaywall 로 먼저 합법적 PDF
    위치를 찾고, 이미 PDF 링크면 바로 받는다. 오픈액세스가 아니면 실패를
    정직하게 보고한다 — 페이월을 다른 방법으로 우회하지 않는다.
    """
    pdf_url = doi_or_url
    if not doi_or_url.lower().endswith(".pdf") and "/" in doi_or_url:
        status_box.write(f"DOI '{doi_or_url}' 로 오픈액세스 PDF 위치 조회 중 (Unpaywall)...")
        resolved = await server.resolve_unpaywall_pdf(doi_or_url)
        if not resolved:
            status_box.write("❌ 오픈액세스 버전을 찾지 못함 — 이 논문은 PDF 업로드로 들여와야 함")
            return []
        pdf_url = resolved
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
        ok = await _summarize_target(arxiv_id, client, status_box)
    return [arxiv_id] if ok else []


def render_search_tab():
    st.subheader("논문 검색 → 요약 생성")
    mode_label = st.radio(
        "입력 방식",
        ["키워드 검색", "저장된 논문 재검색 (한글 가능)", "논문 ID 직접 지정", "제목으로 검색",
         "PDF 업로드", "DOI/URL (오픈액세스)"],
        horizontal=True,
    )
    mode = {
        "키워드 검색": "keyword", "저장된 논문 재검색 (한글 가능)": "hybrid",
        "논문 ID 직접 지정": "id", "제목으로 검색": "title",
        "PDF 업로드": "pdf", "DOI/URL (오픈액세스)": "oa",
    }[mode_label]

    # "키워드 검색"은 arxiv_search_papers/s2_search_papers — 외부 API 자체가
    # 영문 키워드 매칭이라 한글 질의를 이해하지 못한다(2026-08-10, 사용자가
    # 직접 확인해 지적). hybrid_search_local_papers는 처음부터 한글도
    # 되도록 만들었지만(gemini-embedding-001가 다국어 임베딩 지원 + BM25
    # 토크나이저가 한글 음절도 인식, hybrid_search.py 참고) 이 화면에는
    # 연결이 안 돼 있었다 — 그래서 "그때 한글 되게 한다며" 검증이 어긋난
    # 것처럼 보였다: 사용자가 실제로 두드린 건 이 UI의 "키워드 검색"(외부
    # API)이지, 한글을 지원하도록 만든 하이브리드 검색이 아니었다. 이미
    # `fetch_paper`로 모아둔 로컬 논문 안에서 다시 찾는 용도라 새로 수집·
    # 요약하지 않는다 — 검색 결과만 보여주고, 실제 검토는 '요약 검토' 탭에서.
    if mode == "hybrid":
        st.caption(
            "이미 저장된 논문들 안에서 다시 찾는다(BM25+임베딩) — 새로 수집·요약하지 않음. "
            "한글 질의도 지원(임베딩이 다국어)."
        )
        hybrid_query = st.text_input("검색어 (한글/영어 모두 가능)", placeholder="예: 온디바이스 AI / on-device AI")
        hybrid_top_k = st.number_input("표시할 편수", min_value=1, max_value=20, value=5)
        if st.button("🔎 검색", disabled=not hybrid_query):
            result = json.loads(
                run_async(
                    server.hybrid_search_local_papers(
                        server.HybridSearchInput(query=hybrid_query, top_k=hybrid_top_k)
                    )
                )
            )
            if not result["papers"]:
                st.info("저장된 논문 중 일치하는 게 없음.")
            else:
                if not result["embeddings_used"]:
                    st.warning("GOOGLE_API_KEY 없음/실패 — BM25(어휘 일치)만 사용됨. 한글 질의는 정확도가 떨어질 수 있음.")
                for p in result["papers"]:
                    st.markdown(
                        f"- **{p['title']}** (`{p['arxiv_id']}`) — "
                        f"BM25 {p['bm25_score']}, 코사인 {p['cosine_score']}, 합산 {p['fused_score']}"
                    )
                st.caption("검토·재요약은 '✅ 요약 검토' 탭에서.")
        return

    top_n = 3
    uploaded_file = None
    pdf_title = ""
    if mode == "keyword":
        value = st.text_input("검색 키워드", placeholder="예: LoRA fine-tuning summarization")
        st.caption("⚠️ 외부 API(arXiv/Semantic Scholar) 자체 검색이라 영문 키워드 권장. "
                   "이미 저장된 논문에서 한글로 다시 찾으려면 '저장된 논문 재검색' 선택.")
        top_n = st.number_input("선별할 편수", min_value=1, max_value=10, value=3)
    elif mode == "id":
        value = st.text_input("arXiv ID (공백/쉼표로 여러 개 가능)", placeholder="예: 2505.13033 2405.15793")
    elif mode == "title":
        value = st.text_input("논문 제목", placeholder="예: TSPulse")
    elif mode == "pdf":
        st.caption("arXiv 밖 논문(저널·컨퍼런스) — 이미 기관 구독 등으로 합법적으로 접근 가능한 PDF만 올릴 것")
        uploaded_file = st.file_uploader("PDF 파일", type="pdf")
        pdf_title = st.text_input("제목", placeholder="논문 제목 (필수 — PDF에서 자동 추출 안 함)")
        value = "ok" if (uploaded_file and pdf_title) else ""
    else:  # oa
        st.caption("DOI를 넣으면 Unpaywall로 오픈액세스 PDF를 자동으로 찾는다. PDF 직접 링크도 가능.")
        value = st.text_input("DOI 또는 PDF 직접 링크", placeholder="예: 10.1038/s41467-023-xxxxx-x")
        pdf_title = st.text_input("제목 (선택 — 비우면 '(제목 미입력)'으로 저장)")

    if st.button("시작", type="primary", disabled=not value):
        status_box = st.status("진행 중...", expanded=True)
        if mode == "pdf":
            done = run_async(
                _run_pdf_upload_and_summarize(uploaded_file.getvalue(), pdf_title, status_box)
            )
        elif mode == "oa":
            done = run_async(_run_open_access_and_summarize(value, pdf_title, status_box))
        else:
            done = run_async(_run_search_and_summarize(mode, value, top_n, status_box))
        if done:
            status_box.update(label=f"완료 — {len(done)}편 처리됨", state="complete")
            st.success(f"{len(done)}편 저장 완료. '요약 검토' 탭에서 확인하세요: {done}")
        else:
            status_box.update(label="처리된 논문 없음", state="error")


# ---------------------------------------------------------------- ⑥→⑦ 연결
# "마무리" 슬라이드에 남은 유일한 우선순위로 적어 둔 항목: review_app.py에서
# 승인(⑥)한 결과를 docker_runner.reproduce()(⑦)로 넘기는 연결부가 그동안
# 수동(arxiv_id를 직접 CLI에 넣어 호출)이었다. 여기서 그 연결을 만든다.
#
# reproduce()는 Docker clone+install+run을 최대 3회 재시도하는 무거운 작업이라
# (최악의 경우 후보당 install 15분+run 2분 — INSTALL_TIMEOUT/RUN_TIMEOUT,
# docker_runner.py 참고) 승인 버튼 클릭 안에서 동기로 돌리면 화면이 그만큼
# 멈춘다. batch_summarize.py와 같은 패턴 — "사람이 실행은 시키지만 그 다음은
# 무인으로 돈다" — 그대로 따라, 승인 시 별도 프로세스로 무인 실행만 시키고
# 화면은 즉시 돌아온다. 진행 상황은 결과가 쌓이는 repro_results 테이블로
# 나중에 확인한다(server.save_repro_result — docker_runner.py가 이미 쓰고
# 있음, 이 파일은 그 결과를 조회만 한다 — server.py는 판단하지 않는다는
# 원칙과 동일하게 이 파일도 실행 여부만 트리거하고 성공 판정엔 관여 안 함).


def _fetch_repro_rows(arxiv_id: str) -> list[dict]:
    with server._db() as con:
        rows = con.execute(
            "SELECT repo_url, source, confidence, success, exit_code, stage, "
            "attempt, duration_s, created_at, local_path FROM repro_results "
            "WHERE arxiv_id=? ORDER BY created_at DESC",
            (arxiv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _reproduce_running(arxiv_id: str) -> bool:
    """docker_runner.py가 이 arxiv_id로 이미 떠 있는지 확인 — 승인 버튼을
    실수로 두 번 눌러도(재승인) 같은 재현을 중복 실행하지 않는다."""
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    return marker.exists()


def _launch_reproduce_background(arxiv_id: str) -> str:
    """⑦을 별도 프로세스로 무인 실행한다. 이미 성공 기록이 있으면(재현
    완료됨) 다시 돌리지 않고, 이미 실행 중이면 중복 실행하지 않는다."""
    rows = _fetch_repro_rows(arxiv_id)
    if any(r["success"] for r in rows):
        return "이미 성공 기록이 있어 재실행하지 않음"
    if _reproduce_running(arxiv_id):
        return "이미 실행 중"

    server.REPRO_DIR.mkdir(parents=True, exist_ok=True)
    marker = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.running"
    marker.write_text(server._now(), encoding="utf-8")
    log_path = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.log"

    # docker_runner.py 자체의 __main__은 마커 파일을 모르므로, 마커 정리까지
    # 포함한 짧은 래퍼를 셸로 실행한다 — docker_runner.py 코드 자체는 안 건드림.
    wrapper = (
        f'"{sys.executable}" docker_runner.py "{arxiv_id}"; '
        f'rm -f "{marker}"'
    )
    with open(log_path, "w", encoding="utf-8") as f:
        subprocess.Popen(
            ["/bin/bash", "-c", wrapper],
            cwd=str(Path(__file__).resolve().parent),
            stdout=f, stderr=subprocess.STDOUT,
            start_new_session=True,  # 이 스트림릿 요청 처리가 끝나도 안 죽게
        )
    return "⑦ 코드 재현을 백그라운드에서 시작함"


# 미리보기에 쓸 언어 힌트 — 있으면 문법 강조가 되고, 없으면 그냥 평문으로
# 보여준다(st.code의 language=None도 안전하게 동작).
_CODE_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".md": "markdown",
    ".sh": "bash", ".dockerfile": "dockerfile", ".txt": None, ".cfg": None,
    ".ini": None, ".cpp": "cpp", ".c": "c", ".h": "c", ".java": "java",
    ".go": "go", ".rs": "rust", ".sql": "sql",
}
_PREVIEW_SIZE_CAP = 200_000  # 200KB — 이보다 크면 브라우저가 버벅이므로 앞부분만


def _render_code_browser(arxiv_id: str, local_path: str) -> None:
    """성공한 재현의 clone 코드를 화면에서 직접 열어본다. docker_runner.py가
    성공 시 이 경로에 코드를 남겨 둔다(server.py의 local_path 컬럼) — 이 함수는
    그걸 읽기만 한다, 실행하지 않는다(승인 화면에서 임의 코드를 또 돌리는 건
    별개의 위험이라 스모크 테스트는 이미 끝난 결과만 보여준다)."""
    if not local_path:
        return
    root = Path(local_path)
    if not root.exists():
        st.caption("⚠️ 재현된 코드 경로를 찾을 수 없음 — 이후에 정리됐을 수 있음")
        return

    if not st.toggle("🗂️ 재현된 코드 보기", key=f"codetoggle_{arxiv_id}"):
        return

    st.caption(f"로컬 경로: `{local_path}`")
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    )
    if not files:
        st.caption("파일 없음")
        return
    if len(files) > 300:
        st.caption(f"파일 {len(files)}개 중 상위 300개만 표시")
        files = files[:300]

    rel_paths = [str(p.relative_to(root)) for p in files]
    # 흔히 먼저 보고 싶은 것부터: README, 진입점 스크립트류를 앞으로
    def _priority(name: str) -> tuple[int, str]:
        lower = name.lower()
        if "readme" in lower:
            return (0, lower)
        if lower.endswith((".py", ".sh")) and "/" not in name:
            return (1, lower)
        return (2, lower)

    rel_paths.sort(key=_priority)

    selected = st.selectbox("파일 선택", rel_paths, key=f"codefile_{arxiv_id}")
    target = root / selected
    try:
        size = target.stat().st_size
        raw = target.read_bytes()[:_PREVIEW_SIZE_CAP]
        text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        st.caption(f"읽기 실패: {e}")
        return

    lang = _CODE_EXT_LANG.get(target.suffix.lower(), None)
    if size > _PREVIEW_SIZE_CAP:
        st.caption(f"{size:,} bytes 중 앞 {_PREVIEW_SIZE_CAP:,} bytes만 표시")
    st.code(text, language=lang)


def _render_repro_status(arxiv_id: str) -> None:
    rows = _fetch_repro_rows(arxiv_id)
    if _reproduce_running(arxiv_id):
        st.caption("⑦ 코드 재현: 🔵 진행 중... (Docker로 후보 저장소 설치·실행 시도 — 새로고침해서 확인)")
        return
    if rows:
        best = next((r for r in rows if r["success"]), rows[0])
        if best["success"]:
            st.caption(f"⑦ 코드 재현: 🟢 성공 ({best['repo_url']}, {best['attempt']}차 시도)")
            _render_code_browser(arxiv_id, best["local_path"])
        else:
            st.caption(
                f"⑦ 코드 재현: 🔴 전부 실패 (시도 {len(rows)}건, 마지막 단계: {best['stage']}) "
                "— 승인을 다시 누르면 재시도"
            )
        return
    # repro_results에 행이 없는 경우 — docker_runner.reproduce()는 저장소 후보가
    # 아예 없으면(code_finder가 못 찾음) server.save_repro_result()를 한 번도
    # 안 부르고 조기 반환한다(그 경로엔 시도랄 게 없어서). 그래서 DB만 보면
    # "아직 실행 안 함"과 "실행은 했는데 후보가 없었음"을 구분할 수 없다 —
    # 실측으로 실제 발견(2026-08-12, pdf-* 논문 승인 후 재현이 조용히 끝남).
    # docker_runner.py __main__이 찍는 JSON 로그에 그 이유가 남으니 거기서 읽는다.
    log_path = server.REPRO_DIR / f"{arxiv_id.replace('/', '_')}.log"
    if not log_path.exists() or not log_path.read_text(encoding="utf-8").strip():
        return
    try:
        outcome = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        st.caption("⑦ 코드 재현: 완료됐지만 결과를 못 읽음 — 로그 파일 확인 필요")
        return
    if outcome.get("success"):
        st.caption("⑦ 코드 재현: 🟢 성공")
    else:
        # "시도 못함"이라고 쓰면 검색 자체가 안 된 것처럼 읽혀서 오해를 살 수
        # 있다는 지적(2026-08-12) — 실제로는 검색은 끝났고 후보가 0개였던
        # 것이므로 "검색 완료·후보 없음"으로 명확히 구분해서 쓴다.
        reason = outcome.get("reason", "저장소 후보를 찾지 못함")
        st.caption(
            f"⑦ 코드 재현: 🟠 검색 완료 · 후보 없음 — {reason} "
            "(이 논문엔 공개된 관련 코드 저장소가 없을 수 있음, 설치·실행은 시도 안 함)"
        )


# ---------------------------------------------------------------- 탭 ②: 요약 검토


def render_review_tab():
    st.subheader("요약 검토 (⑥ 사람 판단)")
    show_all = st.checkbox("전체 보기 (승인·반려 포함)", value=False)
    rows = _fetch_review_rows(show_all)

    if not rows:
        st.info("검토할 요약이 없습니다." if not show_all else "저장된 요약이 없습니다.")
        return

    status_emoji = {"pending": "🟡", "approved": "🟢", "rejected": "🔴", None: "🟡"}

    for row in rows:
        arxiv_id = row["arxiv_id"]
        status = row["review_status"] or "pending"
        emoji = status_emoji.get(status, "🟡")
        header = f"{emoji} {row['title']} ({arxiv_id}) — {row['numbers_matched']}/{row['numbers_total']}"

        with st.expander(header):
            summary_path = Path(row["path"])
            if not summary_path.exists():
                st.error(f"요약 파일을 찾을 수 없음: {summary_path}")
                continue
            summary_text = summary_path.read_text(encoding="utf-8")

            report = _verify_detail(arxiv_id, summary_text)
            ratio = report.matched / report.total if report.total else 1.0
            if ratio == 1.0:
                st.success(f"수치 검증: {report.matched}/{report.total} 전부 일치 (문장 단위 확인 {report.grounded}건)")
            else:
                st.warning(f"수치 검증: {report.matched}/{report.total} 일치 — 아래 불일치 항목 확인")
                for c in report.unmatched:
                    if c.grounded:
                        # [S번호]로 인용한 문장까지 찾아봤지만 그 안에 없었다 — 지어냈거나
                        # 엉뚱한 문장을 인용했을 가능성. 실제로 조회한 문장을 보여준다.
                        cited = c.cited_text or "(인용한 문장 번호가 원문 범위 밖 — 지어낸 번호일 수 있음)"
                        st.markdown(
                            f"- **`{c.token}`** — 요약 문맥: _{c.context}_\n\n"
                            f"  🔎 인용한 [S{c.sentence_id:04d}] 문장(±1): _{cited}_"
                        )
                    else:
                        st.markdown(f"- **`{c.token}`** — 문맥: _{c.context}_")

            if row["review_note"]:
                st.caption(f"이전 검토 메모: {row['review_note']}")

            if status == "approved":
                _render_repro_status(arxiv_id)

            if st.toggle("🖼️ 그림·표 이미지 보기", key=f"imgtoggle_{arxiv_id}"):
                _render_image_gallery(arxiv_id)

            st.markdown("---")
            # 화면 폭(wide layout)에 텍스트를 그대로 채우면 줄이 끝까지 늘어져서
            # 읽기 힘들다 — 가운데 컬럼으로 폭을 제한해 적당한 지점에서 줄바꿈되게 한다.
            _, mid, _ = st.columns([1, 4, 1])
            with mid:
                st.markdown(_prettify_summary_markdown(summary_text))
            st.markdown("---")

            col1, col2, col3 = st.columns([1, 2, 1])
            with col1:
                if st.button("✅ 승인", key=f"approve_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "approved")
                    msg = _launch_reproduce_background(arxiv_id)
                    st.toast(f"⑥→⑦ {msg}")
                    st.rerun()
            with col2:
                reason = st.text_input("반려 사유 (선택)", key=f"reason_{arxiv_id}")
                if st.button("❌ 반려", key=f"reject_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "rejected", note=reason)
                    st.rerun()
            with col3:
                if st.button("🔄 다시 생성", key=f"regen_{arxiv_id}"):
                    with st.spinner("재생성 중..."):
                        template = engine.select_template(row["title"] or "")
                        paper_text = server.read_full_text(arxiv_id)

                        async def _regen():
                            async with httpx.AsyncClient() as client:
                                return await engine.summarize(client, paper_text, template)

                        new_summary, used_engine = run_async(_regen())
                        run_async(
                            server.save_summary(
                                server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=new_summary)
                            )
                        )
                    st.rerun()


# ---------------------------------------------------------------- 메인


def _fetch_sidebar_stats() -> dict:
    """사이드바 현황 요약용 — 화면이 온통 흰 여백뿐이라 뭘 하는 앱인지
    한눈에 안 들어온다는 지적(2026-08-12)을 받아, 참고 이미지(결제
    대시보드)의 좌측 사이드바 구조를 그대로 베끼지는 않되 "구조·색 영역이
    있는 화면"이라는 느낌만 가져왔다. 숫자는 실제 DB 조회 — 장식이 아니다."""
    with server._db() as con:
        total = con.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
        pending = con.execute(
            "SELECT COUNT(*) c FROM summaries WHERE review_status='pending' OR review_status IS NULL"
        ).fetchone()["c"]
        approved = con.execute(
            "SELECT COUNT(*) c FROM summaries WHERE review_status='approved'"
        ).fetchone()["c"]
        repro_ok = con.execute(
            "SELECT COUNT(DISTINCT arxiv_id) c FROM repro_results WHERE success=1"
        ).fetchone()["c"]
    return {"total": total, "pending": pending, "approved": approved, "repro_ok": repro_ok}


if "nav_page" not in st.session_state:
    st.session_state.nav_page = "search"

with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand">📄 <b>논문 검색·분석</b><br>'
        '<span class="sidebar-brand-sub">에이전트 하네스</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    if st.button(
        "🔍 검색·요약 생성", key="nav_search", use_container_width=True,
        type="primary" if st.session_state.nav_page == "search" else "secondary",
    ):
        st.session_state.nav_page = "search"
        st.rerun()
    if st.button(
        "✅ 요약 검토", key="nav_review", use_container_width=True,
        type="primary" if st.session_state.nav_page == "review" else "secondary",
    ):
        st.session_state.nav_page = "review"
        st.rerun()

    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    st.caption("현황")
    stats = _fetch_sidebar_stats()
    st.markdown(
        f"""<div class="sidebar-stats">
        <div><span>저장된 논문</span><b>{stats['total']}</b></div>
        <div><span>검토 대기</span><b>{stats['pending']}</b></div>
        <div><span>승인됨</span><b>{stats['approved']}</b></div>
        <div><span>⑦ 재현 성공</span><b>{stats['repro_ok']}</b></div>
        </div>""",
        unsafe_allow_html=True,
    )

if st.session_state.nav_page == "search":
    render_search_tab()
else:
    render_review_tab()
