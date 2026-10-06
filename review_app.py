"""⑨ 운영 화면 — 최신 연구 동향 모니터링 에이전트 (Streamlit). 관리자(운영자) 한 사람이 전체 상황을 보는 곳이다.

2026-10-01 개편(사용자 요청 + 판단): 메일과 같은 디자인·같은 순서. 켜자마자 **개요**(오늘의 연구 흐름 → 핵심 논문 → 이번 주)가 나온다 —
첫 화면의 질문은 "cron 이 돌았나"가 아니라 "오늘 내 분야에서 뭐가 중요한가"다. 메뉴는 다섯 — 개요 / 논문(저장된 논문 전체) /
프로필(키워드·가중치·추이·변경 이력·주간 관리·설정) / 활동 기록(보낸 메일·반응) / 시스템(실행 기록·프로필 표·cron·DB·로그).
프로필은 사이드바에서 고르고 개요·프로필·활동 기록이 함께 쓴다. 그전(9/16)은 운영 현황 하나에 탭 일곱이었다. 숫자는 전부 `ops_dashboard.py` 가 만들고 이 파일은 그리기만 한다 — `st.` 을 쓰는 코드와 안 쓰는
코드가 섞이면 Streamlit 없이는 테스트할 수 없다(§8-31).

옛 화면(검색·요약·검토·수동 재현 버튼)의 로직은 git 이력에 남아 있다(`review_core.py` 는 2026-09-16 에 지웠다). 논문 검색·요약은 MCP 서버(`server.py`)와
새벽 스캔이 맡는다.

실행:
    .venv/bin/streamlit run review_app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import streamlit as st

import mail_ledger
import ops_dashboard
import research_profile
import run_profile_scan
import server
import textutil
from ui_helpers import _relative_time, run_async

APP_TITLE = "최신 연구 동향 모니터링 에이전트"
ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title=APP_TITLE, layout="wide", page_icon=":material/monitoring:")




def _inject_custom_style() -> None:
    """순수 시각 레이어 — 로직은 건드리지 않는다. 기준은 `docs/DESIGN.md`(2026-10-01 4차 개정): **사용자 시안 다섯 장의 여백·색을 그대로**.
    사내 연구 정보 시스템처럼 — 옅은 청회색 바탕 위 흰 카드(얇은 선·모서리 6px·그림자 없음), 카드 제목은 카드 **안**, 강조색은 차분한 청록
    하나(선택 메뉴·오늘의 흐름 상자), 파랑은 링크에만, 초록·빨강은 상태에만. 글꼴 맑은 고딕, 굵기 400/700.
    셀렉터는 Streamlit 이 문서화한 data-testid 와 `key=` 훅(.st-key-*)만 쓴다 — [style*=border] 같은 내부 구조 추측은 1.60 에서 안 맞았다."""
    st.markdown(
        """
        <style>
        :root {
            --accent: #1F7A72; --accent-bar: #2B8A82; --accent-bg: #E3F1EF; --accent-soft: #EEF6F5;
            --ink: #1E2B37; --ink2: #3A4856; --muted: #6B7A88; --line: #E3E8EC; --line2: #EEF1F4;
            --app: #F3F6F8; --side: #FAFBFC; --card: #FFFFFF; --head: #F6F8FA; --link: #2563EB;
            --ok: #1E7B4F; --ok-bg: #E7F5EC; --bad: #B42318; --bad-bg: #FDECEC; --chip: #F1F3F5; --chip-ink: #4B5865;
            /* 옛 이름 — 화면 코드의 인라인 style 이 아직 쓴다 */
            --text-muted: var(--muted); --text-main: var(--ink); --sky: var(--accent);
        }
        html, body, [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] *:not([data-testid="stIconMaterial"]) {
            font-family: "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans KR", Arial, sans-serif !important;
        }
        /* 표는 캔버스로 글자를 그린다 — 자간이 먹으면 낱말 사이 빈칸이 사라졌다(10/1 캡처) */
        canvas, [data-testid="stDataFrame"], [data-testid="stDataFrame"] * { letter-spacing: normal !important; }
        [data-testid="stAppViewContainer"] { color: var(--ink); }
        [data-testid="stMain"], [data-testid="stAppViewContainer"], [data-testid="stHeader"] { background-color: var(--app); }
        [data-testid="stAppDeployButton"], [data-testid="stMainMenu"], footer { display: none; }
        .block-container { padding: 2.6rem 2.4rem 3rem 2.4rem; max-width: 100%; }
        [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { line-height: 1.7; }
        a, a:visited { color: var(--link); }

        /* 제목 — 페이지 제목 28px, 카드 제목 17px */
        h1, h2, h3, h4 { color: var(--ink); letter-spacing: -0.02em; }
        [data-testid="stAppViewContainer"] h3 { font-size: 1.75rem; font-weight: 700; padding: 0 0 .2rem 0; }
        [data-testid="stAppViewContainer"] h4 { font-size: 1.06rem; font-weight: 700; padding: .1rem 0 .6rem 0; }
        [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted); font-size: .9rem; }
        .rm-page-sub { color: var(--muted); font-size: .95rem; margin: -.2rem 0 1.1rem; }

        /* 탭 — 밑줄, 선택만 청록 */
        [data-testid="stTabs"] [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--line); }
        [data-testid="stTabs"] button[data-baseweb="tab"] { color: var(--ink2); padding: .7rem 1.2rem; }
        [data-testid="stTabs"] button[data-baseweb="tab"] p { font-size: 1rem; }
        [data-testid="stTabs"] button[aria-selected="true"] p { color: var(--accent); font-weight: 700; }
        [data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: var(--accent); height: 2px; }

        /* 버튼·입력 — 흰 바탕 + 얇은 선 */
        [data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {
            border-radius: 4px; border: 1px solid var(--line); background: var(--card); color: var(--ink2); box-shadow: none; }
        [data-testid="stButton"] button:hover { border-color: #C9D3DA; background: #F7F9FA; color: var(--ink); }
        [data-testid="stBaseButton-primary"] { background-color: var(--accent) !important; border: none !important; color: #fff !important; }
        [data-testid="stBaseButton-primary"] p { color: #fff; }
        [data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"], [data-baseweb="select"] > div,
        [data-testid="stTextInputRootElement"], [data-testid="stTextInputRootElement"] > div {
            background-color: #FFFFFF !important; border-color: var(--line) !important; border-radius: 4px !important; }
        [data-testid="stTextInputRootElement"] { border: 1px solid var(--line) !important; }
        [data-baseweb="input"] input, [data-baseweb="base-input"] input, [data-baseweb="textarea"] textarea { background-color: #FFFFFF !important; }
        [data-testid="stExpander"] { border: 1px solid var(--line) !important; border-radius: 6px !important; background: var(--card); box-shadow: none; }
        [data-testid="stExpander"] summary p { font-size: .98rem; }
        [data-testid="stMarkdownContainer"] code { background: var(--chip); color: var(--chip-ink); border-radius: 3px; padding: .05em .35em; font-size: .85em; }
        [data-testid="stMarkdownContainer"] th { white-space: nowrap; }
        [data-testid="stAlert"] { border-radius: 6px; }

        /* 카드 = st.container(border=True, key="box_…") — 흰 바탕, 얇은 선, 모서리 6px */
        [class*="st-key-box_"] { background: var(--card); border-color: var(--line) !important; border-radius: 6px !important; padding: 14px 18px; }

        /* 사이드바 — 거의 흰색 + 오른쪽 선, 글자 메뉴, 선택은 옅은 청록 + 왼쪽 막대 */
        [data-testid="stSidebar"] { background-color: var(--side); border-right: 1px solid var(--line); width: 264px !important; }
        [data-testid="stSidebar"] .sidebar-brand { font-size: 1.3rem; font-weight: 700; color: var(--ink); letter-spacing: -.02em; }
        [data-testid="stSidebar"] .sidebar-brand-sub { display: block; font-size: .85rem; color: #5F7182; font-weight: 400; margin-top: 2px; }
        [data-testid="stSidebar"] .sidebar-rule { border-top: 1px solid var(--line); margin: 1rem 0 .3rem; }
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-size: .85rem; color: var(--muted); }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size: .85rem; color: var(--muted); }
        [class*="st-key-nav_"] { margin-bottom: -.6rem; }
        [data-testid="stSidebar"] [data-testid="stButton"] button {
            justify-content: flex-start; border: none; border-left: 3px solid transparent; background: transparent;
            padding: .62rem 1rem; border-radius: 0 4px 4px 0; }
        [data-testid="stSidebar"] [data-testid="stButton"] button p { font-size: 1.05rem; color: var(--ink2); }
        [data-testid="stSidebar"] [data-testid="stButton"] button > div { width: 100%; justify-content: flex-start; text-align: left; }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover { background: #F1F4F6; }
        [data-testid="stSidebar"] [data-testid="stBaseButton-primary"], [data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
            background: var(--accent-bg) !important; border-left: 3px solid var(--accent-bar) !important; }
        [data-testid="stSidebar"] [data-testid="stBaseButton-primary"] p { color: var(--accent) !important; font-weight: 700; }

        /* 공통 구성 요소 */
        .rm-eyebrow { color: var(--muted); font-size: .82rem; letter-spacing: .05em; }
        .rm-title { color: var(--ink); font-size: 1.9rem; font-weight: 700; line-height: 1.3; margin: 2px 0 0; letter-spacing: -.03em; }
        .rm-sub { color: var(--muted); font-size: 1.08rem; margin: 4px 0 18px; }
        .rm-card { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 20px 24px; }
        .rm-ct { font-size: 1.06rem; font-weight: 700; color: var(--ink); margin: 0 0 12px; display: flex; align-items: baseline; gap: 10px; }
        .rm-ct small { font-weight: 400; color: var(--muted); font-size: .82rem; }
        .rm-ct .r { margin-left: auto; }
        .rm-gap { height: 16px; }
        .rm-grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
        .rm-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 16px; }
        .rm-kpi { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 18px 22px; }
        .rm-kpi .l { color: var(--muted); font-size: .9rem; } .rm-kpi .v { color: var(--ink); font-size: 1.6rem; font-weight: 700; margin-top: 6px; }
        .rm-lead { background: var(--accent-soft); border: 1px solid var(--line); border-left: 4px solid var(--accent-bar); border-radius: 6px; padding: 18px 24px; }
        .rm-lead p { margin: 0 0 4px; font-size: .98rem; line-height: 1.8; color: var(--ink2); }
        .rm-thread h5 { margin: 0 0 8px; font-size: 1.02rem; font-weight: 700; color: var(--ink); }
        .rm-thread h5 i { font-style: normal; margin-right: 14px; }
        .rm-thread .body { font-size: .95rem; line-height: 1.75; color: var(--ink2); padding-bottom: 12px; border-bottom: 1px solid var(--line); margin-bottom: 10px; }
        .rm-ul { margin: 0; padding-left: 18px; } .rm-ul li { font-size: .95rem; line-height: 1.7; margin: 3px 0; color: var(--ink2); }
        .rm-ul li::marker { color: #9AA6B1; }
        .rm-ko { display: block; color: var(--muted); font-size: .85rem; line-height: 1.5; }
        .rm-link { color: var(--link) !important; text-decoration: underline; text-underline-offset: 2px; }
        .rm-chip { display: inline-block; background: var(--chip); color: var(--chip-ink); border-radius: 4px; font-size: .82rem;
                   padding: 2px 8px; margin: 1px 4px 1px 0; line-height: 1.5; white-space: nowrap; }
        .rm-chip.ok { background: var(--ok-bg); color: var(--ok); } .rm-chip.bad { background: var(--bad-bg); color: var(--bad); }
        .rm-chip.kw { background: var(--chip); color: var(--chip-ink); }
        .rm-t { width: 100%; border-collapse: collapse; font-size: .92rem; }
        .rm-t th { background: var(--head); color: #4B5865; font-weight: 700; text-align: left; padding: 9px 14px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); white-space: nowrap; }
        .rm-t td { padding: 9px 14px; border-bottom: 1px solid var(--line2); vertical-align: top; color: var(--ink2); }
        .rm-t td.c, .rm-t th.c { text-align: center; } .rm-t td.r, .rm-t th.r { text-align: right; } .rm-t td.n { color: var(--muted); width: 2.5em; }
        .rm-t tbody tr:hover { background: #F8FAFB; }
        .rm-box { border: 1px solid var(--line); border-radius: 6px; padding: 14px 18px; background: var(--card); }
        .rm-box .l { color: var(--muted); font-size: .88rem; display: flex; align-items: center; gap: 8px; }
        .rm-box .v { color: var(--ink); font-size: 1.25rem; font-weight: 700; margin-top: 6px; line-height: 1.35; }
        .rm-box .v.sm { font-size: 1rem; font-weight: 400; color: var(--ink2); }
        .rm-box .s { color: var(--muted); font-size: .85rem; margin-top: 4px; }
        /* 아래 1rem — Streamlit 마크다운 블록은 아래 여백이 -1rem 이라 상자 줄이 바깥 카드 아래 선을 넘었다(10/1 사용자 캡처) */
        .rm-boxes { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 1rem; }
        [class*="st-key-box_"] .rm-t, [class*="st-key-box_"] .rm-grid3 { margin-bottom: 1rem; }
        .rm-row { display: flex; align-items: center; gap: 12px; font-size: .95rem; padding: 6px 0; border-bottom: 1px solid var(--line2); }
        .rm-row:last-child { border-bottom: none; }
        .rm-row .k { flex: 1; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rm-row .v { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; min-width: 3.2em; text-align: right; }
        .rm-up { color: var(--ok); } .rm-down { color: var(--bad); } .rm-new { color: var(--accent); font-weight: 700; }
        .rm-bar { width: 110px; height: 6px; background: var(--line2); position: relative; border-radius: 3px; }
        .rm-bar span { position: absolute; left: 0; top: 0; bottom: 0; background: var(--accent-bar); border-radius: 3px; }
        .rm-bar span.dn { background: #C9A9A0; }
        .rm-empty { color: var(--muted); font-size: .95rem; line-height: 1.7; }
        .rm-foot { color: var(--muted); font-size: .88rem; margin-top: 24px; }
        .rm-ref { color: #98A4AE; font-size: .78em; }
        .rm-grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 12px; }
        .rm-lcard { border: 1px solid var(--line); border-radius: 6px; background: var(--card); overflow: hidden; }
        .rm-lcard .hd { background: var(--head); border-bottom: 1px solid var(--line); padding: 12px 20px; font-weight: 700; font-size: 1rem; }
        .rm-lcard .hd span { color: var(--muted); font-weight: 400; margin-left: 10px; }
        .rm-lcard .bd { padding: 14px 20px; font-size: .95rem; line-height: 1.9; color: var(--ink2); }
        .rm-lcard .bd small { color: var(--muted); font-size: .82rem; } .rm-lcard .bd small.o { color: var(--accent); }
        /* 상세 */
        .rm-crumb { color: var(--muted); font-size: .9rem; margin-bottom: 6px; } .rm-crumb a { color: var(--muted) !important; text-decoration: none; }
        .rm-meta { color: var(--muted); font-size: .92rem; display: flex; flex-wrap: wrap; gap: 0; margin: 8px 0 12px; }
        .rm-meta span + span::before { content: "|"; color: var(--line); margin: 0 12px; }
        .rm-kv { display: inline-flex; border: 1px solid var(--line); border-radius: 4px; overflow: hidden; margin: 0 8px 6px 0; font-size: .88rem; background: var(--card); }
        .rm-kv b { font-weight: 400; color: var(--muted); padding: 4px 10px; border-right: 1px solid var(--line); background: var(--head); }
        .rm-kv span { padding: 4px 10px; color: var(--ink2); } .rm-kv.ok span { background: var(--ok-bg); color: var(--ok); }
        .rm-detail { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 16px; margin-top: 8px; }
        .rm-detail .col { display: flex; flex-direction: column; gap: 16px; }
        .rm-info td:first-child { color: var(--muted); width: 5.5em; white-space: nowrap; } .rm-info td { padding: 8px 6px; border-bottom: 1px solid var(--line2); font-size: .92rem; vertical-align: top; }
        .rm-info { width: 100%; border-collapse: collapse; }
        .rm-abs { font-size: .92rem; line-height: 1.75; color: var(--ink2); }
        /* 달력 */
        .rm-cal-title { text-align: center; font-size: 1.15rem; font-weight: 700; padding-top: 6px; }
        .rm-cal-wd { text-align: center; color: var(--muted); font-size: .9rem; padding: 2px 0; }
        .rm-cal-out { text-align: center; color: #B8C4CC; padding: 12px 0 18px; font-size: .98rem; }
        [class*="st-key-cal_20"] button { min-height: 50px; border: none !important; background: transparent !important; padding: 2px; border-radius: 6px; }
        [class*="st-key-cal_20"] button p { font-size: .98rem; color: var(--ink2); line-height: 1.2; }
        [class*="st-key-cal_20"] .stMarkdownColoredText { display: block; font-size: .5rem; line-height: .7rem; color: var(--accent-bar) !important; }
        [class*="st-key-cal_20"] [data-testid="stBaseButton-secondary"]:hover { background: #F1F4F6 !important; }
        [class*="st-key-cal_20"] [data-testid="stBaseButton-primary"] { background: var(--accent-bg) !important; }
        [class*="st-key-cal_20"] [data-testid="stBaseButton-primary"] p { color: var(--accent) !important; font-weight: 700; }
        [class*="st-key-calnav_"] button, [class*="st-key-daynav_"] button { border: none !important; background: transparent !important; font-size: 1.2rem; color: var(--ink2); }
        [class*="st-key-calnav_"] button:hover, [class*="st-key-daynav_"] button:hover { background: #F1F4F6 !important; }
        [class*="st-key-calnav_"] button p, [class*="st-key-daynav_"] button p { font-size: 1.5rem; line-height: 1; color: var(--ink2); font-weight: 700; }
        /* 수식($…$)은 고정폭 — 맑은 고딕은 역슬래시를 ₩ 로 그려 "\\mathcal" 이 "₩mathcal" 로 보였다(캡처). 글꼴 강제 규칙보다 구체적이어야 이긴다. */
        [data-testid="stAppViewContainer"] code.rm-math { font-family: Consolas, "Courier New", monospace !important; background: var(--head); color: var(--ink2); }
        .rm-sum .row { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 2px; border-bottom: 1px solid var(--line2); font-size: .98rem; }
        .rm-sum .row:last-child { border-bottom: none; } .rm-sum b { font-size: 1.1rem; } .rm-sum small { display: block; text-align: right; color: var(--muted); font-size: .82rem; font-weight: 400; }
        .rm-mailhead { display: flex; align-items: center; gap: 10px; font-size: 1.02rem; font-weight: 700; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
        .rm-mailmeta { display: grid; grid-template-columns: 6em 1fr; row-gap: 8px; padding: 14px 4px; font-size: .92rem; }
        .rm-mailmeta b { font-weight: 700; color: var(--ink2); } .rm-mailmeta span { color: var(--ink2); }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_custom_style()


# ---------------------------------------------------------------- 리서치 프로필 (오케스트레이터 관리자 화면)
#
# review_app.py의 다른 탭(검색·검토)은 "논문 하나"를 다루지만, 이 탭은
# "프로필(=관심 분야 설정) 하나"를 다룬다 — 2026-08-24, delta 검색·
# 스코어링·다이제스트(find_new_papers.py/profile_scoring.py/digest.py)를
# 실제 arXiv로 라이브 검증까지 끝낸 뒤, "다이제스트가 터미널/파일로만
# 나오고 화면 어디에도 안 보인다"는 지적을 반영해 붙인다.
#
# 이 화면은 운영자(나) 전용이라고 봤다 — 최종 사용자는 다이제스트를
# 메일(나중에)로만 받고, 이 화면에서 프로필을 만들고 "지금 상황"(실행
# 이력·상태)을 보는 사람은 따로 있다는 전제. 승인/반려 게이트는 하네스
# 전체에서 없앴으므로(2026-08-24) 여기도, 요약 검토 탭도 마찬가지로 없다 —
# ④⑤⑦이 저장 직후 자동으로 끝나고, 사람은 결과를 읽기만 한다.




def _parse_terms(text: str) -> list[str]:
    """줄바꿈·쉼표 어느 쪽으로 나눠도 같은 목록(빈 항목·중복 제거, 순서 유지)."""
    seen: list[str] = []
    for chunk in text.replace("\r", "\n").split("\n"):
        for k in chunk.split(","):
            k = k.strip()
            if k and k not in seen:
                seen.append(k)
    return seen


def _render_profile_form(db_path, existing: dict | None) -> None:
    """existing이 None이면 새 프로필 생성 폼, 아니면 그 프로필의 현재 값을
    채워 넣은 수정 폼 — research_profile.create_profile이 항상 전체
    덮어쓰기라(모듈 docstring 참고) 생성·수정이 같은 코드 경로로 충분하다.
    키워드는 콤마로 구분한 텍스트로 입력받는다 — 프로필 하나에 키워드가
    수십 개씩 붙는 상황은 아직 없어서, 행 단위 UI(추가·삭제 버튼)보다
    지금은 이 편이 빠르고 실수도 적다."""
    is_new = existing is None
    key_prefix = "new_profile" if is_new else f"edit_{existing['profile_id']}"

    profile_id = st.text_input(
        "프로필 ID (영문·숫자·언더스코어, 한 번 정하면 안 바꾸는 게 좋음)",
        value="" if is_new else existing["profile_id"],
        disabled=not is_new,  # ID는 여러 테이블의 키라 수정 중엔 못 바꾸게 막는다
        key=f"{key_prefix}_id",
    )
    name = st.text_input("표시 이름", value="" if is_new else existing["name"], key=f"{key_prefix}_name")
    # 한 줄 쉼표 입력이었을 때 사고(2026-09-16 16:17, §8-149): 커서가 "world model" 의 마지막 글자 앞에 놓인 채 ", neuromorphic" 을
    # 쳐서 "world mode, neuromorphicl" 이 두 번 저장됐다 — 키워드 하나가 조용히 사라지고 오타가 1.0 으로 들어갔다. 한 줄에 하나씩
    # 쓰는 칸으로 바꾸고, 기존 키워드가 사라지면 아래에서 삭제 확인을 받는다.
    core_topics = st.text_area(
        "핵심 키워드 (한 줄에 하나 — 쉼표도 됨. OR 조건, 하나만 걸려도 후보)",
        value="\n".join(existing["core_topics"]) if existing else "",
        key=f"{key_prefix}_core", height=240,
    )
    target_domain = st.text_input(
        "관심 도메인 (콤마로 구분, 있으면 가점만 — 필수 아님)",
        value=", ".join(existing["target_domain"]) if existing else "",
        key=f"{key_prefix}_domain",
    )
    exclude = st.text_input(
        "제외 키워드 (콤마로 구분, 하나라도 걸리면 무조건 제외)",
        value=", ".join(existing["exclude"]) if existing else "",
        key=f"{key_prefix}_exclude",
    )
    s2_seeds = st.text_input(
        "S2 검색 시드 (콤마로 구분, 비우면 가중치 1.0 이상을 쓴다)",
        value=", ".join(existing.get("s2_seeds") or []) if existing else "",
        key=f"{key_prefix}_seeds",
        help="Semantic Scholar 에 **질의할** 단어다. 하나당 API 호출이라 "
             "예산(300초)을 나눠 쓴다. 중요도(가중치)와 다른 개념이다 — "
             "중요한 키워드를 시드로 안 둬도 arXiv 로는 검색된다.",
    )
    venues = st.text_input(
        "관심 venue (콤마로 구분, 선택 — S2가 venue 데이터를 아직 안 줘서 지금은 거의 안 씀)",
        value=", ".join(existing["venues"]) if existing else "",
        key=f"{key_prefix}_venues",
    )
    max_items = st.number_input(
        "다이제스트에 담을 최대 편수", min_value=1, max_value=50,
        value=existing["max_items"] if existing else research_profile.DEFAULT_MAX_ITEMS, key=f"{key_prefix}_max",
    )

    label = "프로필 만들기" if is_new else "수정 저장"
    new_core = _parse_terms(core_topics)
    # 네 목록 전부 전후를 대조한다(Codex 검토 2026-09-16 P1: 제외어·S2 시드를 비워 저장해도 확인이 없었다 — 제외어가 빠지면 걸러내던 논문이
    # 다시 들어오고, 시드가 빠지면 S2 검색 범위가 조용히 바뀐다). 삭제가 하나라도 있으면 확인 체크 전까지 저장 버튼을 잠근다.
    lists = {"핵심 키워드": (existing["core_topics"] if existing else [], new_core),
             "관심 도메인": (existing["target_domain"] if existing else [], _parse_terms(target_domain)),
             "제외 키워드": (existing["exclude"] if existing else [], _parse_terms(exclude)),
             "S2 시드": (existing["s2_seeds"] if existing else [], _parse_terms(s2_seeds))}
    removed_all = {name: [k for k in before if k not in after] for name, (before, after) in lists.items()}
    added_all = {name: [k for k in after if k not in before] for name, (before, after) in lists.items()}
    n_removed = sum(len(v) for v in removed_all.values())
    confirmed = True
    if existing and (n_removed or any(added_all.values())):
        parts = []
        for name in lists:
            if removed_all[name]:
                parts.append(f"{name} 삭제: {', '.join(removed_all[name])}")
            if added_all[name]:
                parts.append(f"{name} 추가: {', '.join(added_all[name])}" + (" (가중치 1.0 으로 들어갑니다)" if name == "핵심 키워드" else ""))
        st.info("바뀌는 것 — " + " · ".join(parts))
    if n_removed:
        confirmed = st.checkbox(f"위 {n_removed}개 항목 삭제를 확인합니다(오타로 사라지는 것이 아닌지 보세요)", key=f"{key_prefix}_confirm_rm")
    if st.button(label, key=f"{key_prefix}_submit", type="primary", disabled=not confirmed):
        pid = profile_id.strip()
        if not pid or not name.strip():
            st.warning("프로필 ID와 이름은 비워둘 수 없음")
        else:
            # **가중치를 같이 넘긴다**(2026-09-09, §8-76). 안 넘기면
            # create_profile 이 전부 1.0 으로 채워서, 여기서 오탈자 하나
            # 고치고 저장하는 것만으로 프로필의 등급이 통째로 날아갔다.
            # 화면에 가중치 입력이 없으므로 기존 값을 그대로 실어 보낸다.
            kept_weights = (existing or {}).get("core_weights") or {}
            # 주기도 같이 넘긴다(2026-09-16) — 안 넘기면 create_profile 기본값 daily 로 되돌아가 수동 프로필이 새벽 cron 에 들어간다.
            freq, at = research_profile.get_schedule(db_path, pid) if not is_new else ("daily", "05:00")
            research_profile.create_profile(
                db_path, pid, name.strip(),
                core_topics=new_core, schedule_frequency=freq, schedule_time=at,
                target_domain=_parse_terms(target_domain),
                exclude=_parse_terms(exclude),
                venues=_parse_terms(venues),
                max_items=int(max_items),
                core_weights={k: kept_weights[k] for k in new_core if k in kept_weights},
                s2_seeds=_parse_terms(s2_seeds),
            )
            st.session_state["_pending_profile"] = pid
            st.success(f"'{pid}' 저장됨")
            st.rerun()


def _render_recipients(db_path, profile_id: str) -> None:
    recipients = research_profile.get_recipients(db_path, profile_id)
    if recipients:
        for email in recipients:
            r_col, x_col = st.columns([5, 1])
            r_col.caption(email)
            if x_col.button("해제", key=f"unsub_{profile_id}_{email}"):
                research_profile.add_recipient(db_path, profile_id, email, active=False)
                st.rerun()
    else:
        st.caption("등록된 수신자 없음 — 메일이 나가지 않습니다")
    new_email = st.text_input("수신자 추가", key=f"add_recipient_{profile_id}", placeholder="a@example.com")
    if st.button("추가", key=f"add_recipient_btn_{profile_id}") and new_email.strip():
        research_profile.add_recipient(db_path, profile_id, new_email.strip())
        st.rerun()


_KIND_LABELS = {"core": "핵심 키워드", "s2_seed": "검색어", "target": "도메인", "exclude": "제외어"}


_h = textutil.esc            # unsafe_allow_html 에 넣는 동적 값은 전부 여기를 거친다 — 키워드·note 에 외부 논문 용어가 섞인다
_ORIGIN_SHORT = {"user": "사용자", "feedback": "반응", "agent": "에이전트", "advisor": "제안기", "rule": "규칙"}


def _stat_cards(items: list[tuple]) -> str:
    """(라벨, 값, 보조[, 상태 칩 글자, 칩 종류]) 상자 줄 — 시안의 운영 상태·DB 상자. 상태는 값 옆 작은 칩(정상 초록·실패 빨강)으로만 색을 준다.
    st.metric 은 긴 값을 "…" 로 잘랐다("2026-W40 실패 — Codex C…", 10/1 사용자 지적) — 줄바꿈되게 직접 그린다."""
    def box(label: str, value: str, sub: str = "", chip: str = "", kind: str = "", small: bool = False) -> str:
        return (f"<div class='rm-box'><div class='l'>{_h(label)}{_chip(chip, kind) if chip else ''}</div>"
                f"<div class='v{' sm' if small else ''}'>{_h(value)}</div>" + (f"<div class='s'>{_h(sub)}</div>" if sub else "") + "</div>")
    return "<div class='rm-boxes'>" + "".join(box(*item) for item in items) + "</div>"


def _render_status_strip(status: dict) -> None:
    """운영 상태 상자 넷 — 새벽 실행·다음 실행·주간 에이전트·반응 버튼. 숫자는 ops_dashboard 가 센다."""
    d = status["daily"]
    if d.get("started_at"):
        if d.get("finished_at") is None:
            verdict, kind = "진행 중", ""
        elif d.get("exit") == 0:
            verdict, kind = "정상", "ok"
        elif d.get("exit") == 2:
            verdict, kind = "소스 장애", "bad"
        elif d.get("exit") == "stopped":
            verdict, kind = "수동 중지", ""
        else:
            verdict, kind = f"실패 (exit {d.get('exit')})", "bad"
        daily = (ops_dashboard._kst(d["started_at"]), "" if status["daily_ran_today"] else "오늘 실행 기록 없음 — PC(WSL)가 켜져 있어야 cron 이 돕니다",
                 verdict, kind)
    else:
        daily = ("기록 없음", "", "", "")
    w = status["weekly"]
    head, _, tail = (ops_dashboard.agent_status_label(w) if w else "아직 실행 없음").partition(" — ")
    week, _, state = head.partition(" ") if head.startswith("20") else ("", "", head)
    wkind = "bad" if "실패" in state else ("ok" if ("적용" in state or "없음" in state) else "")
    st.markdown(_stat_cards([
        ("마지막 새벽 실행", daily[0], daily[1], daily[2], daily[3]),
        ("다음 새벽 실행", status["next_daily_kst"], f"주간 관리 {status['next_weekly_kst']}"),
        ("주간 에이전트", " · ".join(x for x in (week, tail) if x) or state, "", state, wkind, True),
        ("반응 버튼", "메일에 버튼이 붙습니다" if status["buttons_configured"] else "FEEDBACK_* 설정 없음", "",
         "활성" if status["buttons_configured"] else "비활성", "ok" if status["buttons_configured"] else "bad", True),
    ]), unsafe_allow_html=True)


# 꺾은선 한 줄 = 키워드 하나. 정체(identity)를 색으로 가르므로 **범주형** 팔레트를 정해진 순서로 쓴다(순환하지 않는다, dataviz 규칙).
# 파랑을 첫 색으로 두고, 색약에서도 인접 색이 갈리도록 색상·명도를 번갈아 놓았다.
_LINE_PALETTE = ["#086C75", "#E8590C", "#12266B", "#9C36B5", "#2F9E44", "#E64980", "#F08C00", "#1098AD", "#845EF7", "#5C940D"]
_DIRECT_LABEL_MAX = 6      # 이 수까지는 선 끝에 키워드 이름을 바로 붙인다 — 그 이상은 범례·툴팁·범례 클릭 강조로 읽는다


def _weight_chart(moving: dict[str, list[tuple[str, int, float]]]) -> None:
    """가중치가 움직인 키워드만 꺾은선으로 — 가로 revision, 세로 가중치, 선 하나가 키워드 하나(2026-09-16 사용자 요청).
    한 번은 점 그래프(y=키워드)로 갔다가 "너무 보기 힘들다"는 지적을 받았다. 이번엔 (1) 안 움직인 키워드는 아예 안 그리고(호출부가 거른다),
    (2) **궤적이 똑같은 키워드는 선 하나로 묶는다** — 실측(team_ai_advance): rPPG·photoplethysmography·wearable biosensor 가 셋 다 1.0→0.4 라
    세 선이 정확히 겹쳐 하나만 보였고 범례는 일곱 줄이었다. 묶으면 선 수 = 서로 다른 궤적 수이고 이름은 " · " 로 잇는다.
    (3) 선 끝에 이름을 붙이며(선 6개까지 — 같은 값으로 끝나면 한 줄에), (4) 범례를 클릭하면 그 선만 진하게 남긴다."""
    import altair as alt
    import pandas as pd
    # 궤적(revision→가중치 튜플)이 같은 키워드를 하나의 선으로. 이름은 가중치 큰 순이 아니라 키워드 사전순으로 이어 붙인다.
    groups: dict[tuple, list[str]] = {}
    for kw in sorted(moving, key=str.lower):
        groups.setdefault(tuple((int(r), round(float(w), 3)) for _, r, w in moving[kw]), []).append(kw)
    series: dict[str, list[tuple[str, int, float]]] = {" · ".join(kws): moving[kws[0]] for kws in groups.values()}
    rows = []
    for name, pts in series.items():
        for created, rev, w in pts:
            rows.append({"키워드": name, "revision": int(rev), "가중치": round(float(w), 3), "시점": ops_dashboard._kst(created)})
    df = pd.DataFrame(rows)
    # 변화 폭이 큰 선이 범례 위쪽 — 색은 그 순서로 고정된다(선이 늘거나 줄어도 같은 이름은 같은 색).
    moving = series
    order = sorted(moving, key=lambda k: (-abs(moving[k][-1][2] - moving[k][0][2]), k.lower()))
    revs = sorted(df["revision"].unique())
    lo, hi = float(df["가중치"].min()), float(df["가중치"].max())
    domain = [max(0.3, lo - 0.1), min(2.05, hi + 0.1)]
    # 선 끝 라벨 — 마지막 revision 에서 같은 값으로 끝나는 키워드는 겹치므로 한 라벨로 묶는다.
    last_rev = revs[-1]
    ends: dict[float, list[str]] = {}
    for kw in order:
        last = moving[kw][-1]
        if int(last[1]) == last_rev:
            ends.setdefault(round(float(last[2]), 3), []).append(kw)
    label_rows = [{"revision": last_rev, "가중치": w, "라벨": " · ".join(ks)} for w, ks in ends.items()]

    pick = alt.selection_point(fields=["키워드"], bind="legend")
    base = alt.Chart(df).encode(
        x=alt.X("revision:O", title="revision", sort=revs,
                axis=alt.Axis(labelAngle=0, labelColor="#64748B", titleColor="#64748B", grid=False, ticks=False, domainColor="#DDE3EE")),
        y=alt.Y("가중치:Q", title="가중치", scale=alt.Scale(domain=domain),
                axis=alt.Axis(labelColor="#64748B", titleColor="#64748B", grid=True, gridColor="#EEF1F6", ticks=False, domain=False,
                              format=".1f", tickMinStep=0.1)),           # 0.05 눈금이면 "0.6 / 0.6" 이 두 번 찍힌다(실측)
        color=alt.Color("키워드:N", sort=order, scale=alt.Scale(domain=order, range=_LINE_PALETTE[:len(order)]),
                        legend=None if len(order) == 1 else alt.Legend(         # 선 하나면 끝 라벨이 곧 이름이다 — 범례는 군더더기
                            title="키워드 (클릭하면 그 선만)", orient="bottom", direction="vertical", labelLimit=900,
                            symbolType="stroke", symbolStrokeWidth=3)),
        opacity=alt.condition(pick, alt.value(1.0), alt.value(0.15)),
    ).add_params(pick)
    line = base.mark_line(strokeWidth=2.5, interpolate="linear")
    dots = base.mark_circle(size=70, stroke="#FFFFFF", strokeWidth=1.5).encode(
        tooltip=[alt.Tooltip("키워드:N"), alt.Tooltip("revision:O"), alt.Tooltip("가중치:Q", format=".2f"), alt.Tooltip("시점:N")])
    layers = [line, dots]
    if len(order) <= _DIRECT_LABEL_MAX and label_rows:
        labels = alt.Chart(pd.DataFrame(label_rows)).mark_text(align="left", dx=10, fontSize=12, color="#1B2036").encode(
            x=alt.X("revision:O", sort=revs), y="가중치:Q", text="라벨:N")
        layers.append(labels)
    height = 340 + 20 * len(order)          # 범례가 아래에 붙어 그림 높이를 먹는다 — 선 수만큼 더 준다
    # 선 끝 라벨은 그림 영역 밖으로 뻗는다 — 오른쪽 여백을 비워 두고(범례는 아래로) 라벨이 범례와 겹치지 않게 한다.
    chart = (alt.layer(*layers).properties(height=height, padding={"left": 5, "top": 5, "right": 300, "bottom": 5})
             .configure_view(strokeWidth=0).configure(font="Malgun Gothic, sans-serif"))
    st.altair_chart(chart, width="stretch")


def _render_keywords(db_path, pid: str) -> None:
    import pandas as pd
    rows = ops_dashboard.keyword_table(db_path, pid)
    core = [r for r in rows if r["kind"] == "core"]
    if core:
        df = pd.DataFrame([{
            "키워드": r["term"], "가중치": r["weight"],
            "변화": ("—" if r["first_weight"] is None or abs(r["first_weight"] - r["weight"]) < 1e-9
                    else f"{r['first_weight']:g} → {r['weight']:g}"),
            "출처": _ORIGIN_SHORT.get(r["origin"], r["origin"]), "최근 28일 적중": r["hits_28d"],
            "긍정 반응": r["more"] + r["useful"], "부정 반응": r["out"],
        } for r in core]).sort_values(["가중치", "최근 28일 적중"], ascending=[False, False])
        # 가중치 칸만 고칠 수 있다(2026-09-16 관리자 기능). 저장하면 사용자 revision 하나 — 변경 이력 탭에서 되돌릴 수 있고,
        # 피드백 조정은 이 값을 새 기준선으로 삼는다(feedback_weights: 직접 바꾼 revision 이 기준선을 다시 잡는다).
        original = {r["term"]: r["weight"] for r in core}
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key=f"kw_editor_{pid}",
            disabled=[c for c in df.columns if c != "가중치"],
            column_config={"가중치": st.column_config.NumberColumn(format="%.2f", min_value=0.35, max_value=2.0, step=0.05,
                                                                 help="0.35~2.0. 0.1 구간이 순위 계층이다(1.0·0.6·0.4 …)"),
                           "최근 28일 적중": st.column_config.NumberColumn(help="최근 28일 후보 관측에서 이 키워드에 걸린 논문 수")})
        changed = {row["키워드"]: float(row["가중치"]) for row in edited.to_dict("records")
                   if row["가중치"] is not None and abs(float(row["가중치"]) - float(original[row["키워드"]])) > 1e-9}
        if changed:
            st.info("바뀐 가중치: " + ", ".join(f"{k} {original[k]:g} → {v:g}" for k, v in changed.items()))
            if st.button("가중치 저장", key=f"kw_save_{pid}", type="primary"):
                try:
                    rev = research_profile.update_core_weights(db_path, pid, changed)
                except ValueError as e:
                    st.error(f"저장 실패: {e} — 그 사이 다른 변경이 있었을 수 있습니다. 새로고침 뒤 다시 하세요.")
                else:
                    st.success(f"rev {rev} 로 저장했습니다.")
                    st.rerun()
    st.caption("가중치 칸을 눌러 고친 뒤 저장할 수 있습니다. 긍정 = 이 키워드에 걸린 논문에 온 '더 보고 싶음'·'유용함', 부정 = '관심 밖'.")
    others = [r for r in rows if r["kind"] != "core"]
    if others:
        # 목록 셋 — 시안처럼 회색 머리 띠(이름 · 개수) + 흰 본문 카드 세 칸
        cards = []
        for kind in ("s2_seed", "target", "exclude"):
            terms = [r for r in others if r["kind"] == kind]
            items = "".join(
                f"<div>{_h(r['term'])}" + (f" <small>적중 {r['hits_28d']}</small>" if r["hits_28d"] else "")
                + (f" <small class='o'>{_h(_ORIGIN_SHORT.get(r['origin'], r['origin']))}</small>" if r["origin"] != "user" else "") + "</div>"
                for r in terms) or "<div class='rm-empty'>없음</div>"
            cards.append(f"<div class='rm-lcard'><div class='hd'>{_KIND_LABELS[kind]}<span>{len(terms)}</span></div><div class='bd'>{items}</div></div>")
        st.markdown("<div class='rm-grid3'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def _render_issues(db_path, pid: str) -> None:
    issues = ops_dashboard.issues_with_reactions(db_path, pid, limit=40)
    if not issues:
        st.caption("아직 보낸 메일이 없습니다.")
        return
    for issue in issues:
        status = {"sent": "", "partial": " · 일부 수신자 실패", "failed": " · 발송 실패"}.get(issue["status"], "")
        legacy = " · 기록 복원(그날 처음 나간 논문)" if issue["source"] == "legacy" else ""
        head = f"{issue['day']} · {issue['paper_count']}편" + (f" · 반응 {issue['reactions']}" if issue["reactions"] else "") + status + legacy
        with st.expander(head, expanded=issue is issues[0]):
            if issue.get("subject"):
                st.caption(issue["subject"])
            lines = ["| # | 논문 | 키워드 | 반응 |", "|---|---|---|---|"]
            for it in issue["items"]:
                title = it["title"].replace("|", "\\|")
                link = f"[{title}]({it['link']})" if it["link"] else title
                rx = it.get("reactions") or {}
                marks = " · ".join(f"{k} {v}" for k, v in (("긍정", rx.get("more", 0) + rx.get("useful", 0)), ("부정", rx.get("out", 0))) if v)
                lines.append(f"| {it['position']} | {link} | {', '.join(it['core_hits']) or '—'} | {marks} |")
            st.markdown("\n".join(lines))


def _render_reactions(db_path, pid: str) -> None:
    import pandas as pd
    log = ops_dashboard.reaction_log(db_path, pid)
    if not log:
        st.caption("아직 받은 반응이 없습니다. 메일의 [더 보고 싶음] [유용함] [관심 밖] 버튼을 누르면 다음 새벽 수집 때 여기 나타납니다.")
        return
    df = pd.DataFrame([{"시각": r["when"], "논문": r["title"], "반응": r["action"], "상태": r["status"]} for r in log])
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("'격리'는 메일 보안 스캐너가 링크를 먼저 연 것으로 보아 학습에서 뺀 반응입니다. 잘못 격리됐다면 메일에서 다시 누르면 됩니다.")


def _render_history(db_path, pid: str) -> None:
    hist = ops_dashboard.revision_history(db_path, pid)
    current = hist[0]["revision"] if hist else 0
    for h in hist:
        st.divider()
        head, body = st.columns([5, 1])
        head.markdown(f"**rev {h['revision']}** · {_h(h['when'])} · {_h(h['origin_label'])}"
                      + (f" <span style='color:var(--text-muted)'>— {_h(h['note'])}</span>" if h["note"] else ""), unsafe_allow_html=True)
        head.markdown("<div style='font-size:1rem;line-height:1.8;margin-left:8px'>" + "<br>".join(_h(c) for c in h["changes"]) + "</div>",
                      unsafe_allow_html=True)
        if h["revision"] != current:
            if body.button("이 상태로 되돌리기", key=f"rollback_{pid}_{h['revision']}"):
                try:
                    res = research_profile.rollback_to_revision(db_path, pid, h["revision"], reason=f"ui rollback to rev {h['revision']}")
                except Exception as e:  # noqa: BLE001 — 화면이 통째로 죽지 않게 실패 사유를 보인다(외부 검토 2026-09-16)
                    res = {"rolled_back": False, "reason": f"{type(e).__name__}: {e}"}
                if res.get("rolled_back"):
                    st.success(f"rev {h['revision']} 상태를 rev {res['revision']} 으로 복원했습니다 — 이력은 지우지 않습니다.")
                    st.rerun()
                else:
                    st.error(f"되돌리기 실패: {res.get('reason')}")


def _render_agent(db_path, pid: str) -> None:
    runs = ops_dashboard.agent_history(db_path, pid)
    if not runs:
        st.caption("아직 주간 에이전트 실행 기록이 없습니다. 그 주 첫 근무일 새벽 일일 스캔 직전에 돌고, 볼 자료가 없는 주는 모델을 부르지 않습니다.")
        return
    for run in runs:
        with st.expander(f"{ops_dashboard.agent_status_label(run)} · {run['when']}", expanded=run is runs[0]):
            def _fmt(a: dict) -> str:
                w = f" {a['weight']:g}" if a.get("weight") is not None else ""
                return f"**{_ACTION_OPS.get(a.get('op'), a.get('op'))}** {a.get('term')}{w} — {ops_dashboard.display_text(a.get('reason', ''))}"
            if run["proposed"]:
                st.markdown("Claude 제안")
                for a in run["proposed"]:
                    st.markdown(f"- {_fmt(a)}")
            if run["reviews"]:
                st.markdown("Codex 판정")
                for v in run["reviews"]:
                    st.markdown(f"- #{v.get('index')} {_VERDICTS.get(v.get('verdict'), v.get('verdict'))} — {ops_dashboard.display_text(v.get('reason', ''))}")
            if run["applied"]:
                st.markdown("적용됨")
                for a in run["applied"]:
                    st.markdown(f"- {_fmt(a)}")
            if run["rejected"]:
                st.markdown("검증에서 기각")
                for x in run["rejected"]:
                    a = x.get("action") or {}
                    st.markdown(f"- {a.get('op')} {a.get('term')} — 사유 `{x.get('reason')}`")
            if not (run["proposed"] or run["applied"] or run["rejected"]):
                st.caption("제안 없음")


_ACTION_OPS = {"add_keyword": "키워드 추가", "set_weight": "가중치", "remove_keyword": "키워드 삭제",
               "add_seed": "검색어 추가", "remove_seed": "검색어 삭제", "add_exclude": "제외어 추가"}
_VERDICTS = {"accept": "채택", "modify": "수정 채택", "reject": "기각"}


_STATUS_COLORS = {"done": "#1E9E5A", "partial": "#E08A2E", "failed": "#D0433B"}     # 상태 팔레트(DESIGN.md §2) — 상태에만 쓴다


def _status_legend() -> None:
    """상태 원의 뜻 — 색만으로 뜻을 전하지 않게 표 위에 한 줄로 둔다."""
    st.markdown(" &nbsp; ".join(f"<span style='color:{c};font-size:1rem'>●</span> <span style='font-size:.9rem;color:var(--text-muted)'>{t}</span>"
                                for c, t in ((_STATUS_COLORS["done"], "완료"), (_STATUS_COLORS["partial"], "일부"), (_STATUS_COLORS["failed"], "실패"))),
                unsafe_allow_html=True)


def _render_settings(db_path, pid: str, profile: dict) -> None:
    """설정 탭. 한 칸(좁게)에 위에서 아래로 — 수신자 → 발송 주기 → 최근 검색 실행 → 수동 스캔. 2026-09-16 사용자 지적: 오른쪽 칸의 검색
    실패 사유(URL 전체)가 길어 아래 펼침 메뉴 셋이 스크롤 밖으로 밀렸다. 사유는 코드·호스트만 남긴 짧은 표로 보인다."""
    import pandas as pd
    col, _spare = st.columns([3, 2])
    with col:
        st.markdown("**수신자**")
        _render_recipients(db_path, pid)
        freq, _at = research_profile.get_schedule(db_path, pid)
        new_freq = st.selectbox("발송 주기", ["daily", "manual"], index=0 if freq == "daily" else 1,
                                format_func=lambda v: "매일 새벽 05:00 스캔·발송" if v == "daily" else "수동 실행만",
                                key=f"sched_{pid}")
        if new_freq != freq:
            research_profile.set_schedule(db_path, pid, new_freq)
            st.rerun()

        st.markdown("**최근 검색 실행**")
        runs = research_profile.list_runs(db_path, pid, limit=5)
        if runs:
            _status_legend()
            table = pd.DataFrame([{
                "결과": "⬤ " + {"done": "완료", "partial": "일부", "failed": "실패"}.get(r["status"], r["status"]),   # 색 원 + 글자(색만으로 뜻을 전하지 않는다)
                "건수": r.get("retrieved_count") or 0,
                "검색 창": f"{(r.get('window_from') or '')[5:10]} ~ {(r.get('window_to') or '')[5:10]}",
                "시각": _relative_time(r["started_at"]) if r.get("started_at") else "?",
                "사유": ops_dashboard.short_error(r.get("error_detail")) if r["status"] == "failed" else "",
            } for r in runs])
            colors = [_STATUS_COLORS.get(r["status"], "#94A3B8") for r in runs]
            styled = table.style.apply(lambda col: [f"color: {c}; font-weight: 600;" for c in colors], subset=["결과"])
            st.dataframe(styled, hide_index=True, width="stretch")
        else:
            st.caption("아직 실행 이력 없음")
        if st.button("지금 스캔 실행 (메일 없음)", key=f"scan_now_{pid}"):
            if not profile["core_topics"]:
                st.error("핵심 키워드가 없어서 검색어를 만들 수 없음")
            else:
                with st.spinner("검색·요약 중… (몇 분 걸릴 수 있음)"):
                    async def _scan() -> tuple[dict, str]:
                        async with httpx.AsyncClient() as client:
                            return await run_profile_scan.scan_and_digest(db_path, pid, client, max_pages=10)
                    try:
                        run_async(_scan())
                    except Exception as e:  # noqa: BLE001 — 실패도 화면에 명확히 보여준다
                        st.error(f"스캔 실패: {e}")
                    else:
                        st.rerun()
    with col:
        with st.expander("키워드·설정 직접 수정"):
            _render_profile_form(db_path, existing=profile)
        latest = research_profile.get_latest_digest(db_path, pid)
        if latest:
            digest_text, generated_at = latest
            with st.expander(f"최신 다이제스트 본문 ({_relative_time(generated_at)})"):
                st.text(ops_dashboard.display_text(digest_text))
        with st.expander("평가 자료(논문용 라벨링)"):
            st.caption("평가는 로컬에만 저장한다. 승인 관문이나 자동 순위 변경에 쓰지 않는다.")
            papers = research_profile.feedback_papers(db_path, pid)
            if papers:
                by_key = {p["paper_key"]: p["title"] for p in papers}
                with st.form(f"briefing_feedback_{pid}"):
                    key = st.selectbox("평가할 논문", list(by_key), format_func=lambda k: by_key[k])
                    useful = st.radio("읽는 데 도움이 되었나", list(research_profile.FEEDBACK_LABELS),
                                      format_func=research_profile.FEEDBACK_LABELS.get)
                    claim = st.text_area("근거와 대조할 메일의 주장 문장 (선택)")
                    support = st.selectbox("원문 근거가 이 주장을 지지하는가",
                                           list(research_profile.SUPPORT_LABELS),
                                           format_func=research_profile.SUPPORT_LABELS.get)
                    if st.form_submit_button("평가 저장"):
                        try:
                            research_profile.record_feedback(db_path, pid, key, useful, claim, support)
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.success("평가를 저장했다.")
                rows = research_profile.list_feedback(db_path, pid)
                if rows:
                    import csv
                    import io
                    buffer = io.StringIO()
                    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                    st.download_button("평가 자료 CSV", buffer.getvalue().encode("utf-8-sig"),
                                       file_name="briefing_feedback.csv", mime="text/csv")
            else:
                st.caption("아직 배달 기록이 없다.")


# ---------------------------------------------------------------- 개요 — 메일과 같은 순서(2026-10-01 개편)
# 첫 화면은 "cron 이 돌았나"가 아니라 "오늘 내 분야에서 뭐가 중요한가"다. 순서는 메일 그대로: 머리말·숫자 줄 → 오늘의 연구 흐름 →
# 오늘의 핵심 논문 → 이번 주(7일 흐름·프로필 변화) → 맨 아래 한 줄 운영 상태. 숫자는 전부 ops_dashboard.research_overview 가
# DB 에서 센 값이다 — 건강 점수 같은 새 지표, 검증 통과 수치(2026-09-14 메일에서 뺐다)는 싣지 않는다(DESIGN.md §6).
_DEPTH_SHOW = {"원문 분석": "원문 분석 완료", "부분 분석": "원문 일부 분석", "초록 기반": "초록 기반 · 미검증"}   # 메일 카드 문구(digest.DEPTH_*)
_ENGINE_SHOW = {"codex": "Codex", "gemini": "Gemini", "groq": "Groq"}


def _chip(text: str, kind: str = "") -> str:
    return f"<span class='rm-chip {kind}'>{_h(text)}</span>"


def _detail_href(paper_id: str, pid: str | None = None) -> str:
    """앱 안 논문 상세 주소 — 같은 탭에서 연다(쿼리 파라미터로 상세 화면·프로필을 되살린다)."""
    from urllib.parse import urlencode
    return "?" + urlencode({k: v for k, v in (("paper", paper_id), ("profile", pid)) if v})


def _title_link(title: str, paper_id: str = "", pid: str | None = None, url: str = "") -> str:
    """제목 → 파란 링크(앱 안 상세, 없으면 원문). 둘 다 없으면 글자만. 주소는 http(s)·상대(?…)만."""
    t = _h(title)
    if paper_id:
        return f"<a class='rm-link' href='{_h(_detail_href(paper_id, pid))}' target='_self'>{t}</a>"
    if url.startswith(("https://", "http://")):
        return f"<a class='rm-link' href='{_h(url)}' target='_blank'>{t}</a>"
    return t


def _story_html(story: dict, pid: str | None = None, meta: str = "") -> str:
    """되읽은 동향 글 → 시안 모양. 오늘의 흐름 상자(옅은 청록, 제목 안) → 갈래 카드 두 칸 → 우리 연구에서 볼 것 · 주변 신호 두 칸."""
    parts: list[str] = []
    lead = [x for x in (story.get("headline"), story.get("relation")) if x]
    parts.append("<div class='rm-lead'><div class='rm-ct'>오늘의 연구 흐름" + (f"<small class='r'>{_h(meta)}</small>" if meta else "") + "</div>"
                 + ("".join(f"<p>{_h(x)}</p>" for x in lead) if lead else "<p class='rm-empty'>오늘의 한 줄이 없는 글입니다.</p>") + "</div>")
    threads = story.get("threads") or []
    if threads:
        cells = []
        for i, t in enumerate(threads, start=1):
            items = "".join(
                f"<li>{_title_link(it.get('title') or '', it.get('paper_id') or '', pid)}"
                + (f" {_chip(it['depth'], 'ok' if it['depth'] == '원문 분석' else '')}" if it.get("depth") else "")
                + (f"<span class='rm-ko'>{_h(it['title_ko'])}</span>" if it.get("title_ko") else "") + "</li>"
                for it in t.get("items") or [])
            cells.append(f"<div class='rm-card rm-thread'><h5><i>갈래 {i}</i>{_h(t.get('name') or '')}</h5>"
                         + (f"<div class='rm-history' style='font-size:0.9rem;color:var(--text-muted);margin-bottom:8px'>{_h(t['history_note'])}</div>" if t.get("history_note") else "")
                         + f"<div class='body'>{_h(t.get('body') or '')}</div>" + (f"<ul class='rm-ul'>{items}</ul>" if items else "") + "</div>")
        parts.append("<div class='rm-grid2'>" + "".join(cells) + "</div>")
    boxes = []
    if story.get("implications"):
        boxes.append("<div class='rm-card'><div class='rm-ct'>우리 연구에서 볼 것</div><ul class='rm-ul'>"
                     + "".join(f"<li>{_h(x)}</li>" for x in story["implications"]) + "</ul></div>")
    if story.get("side_signals"):
        boxes.append("<div class='rm-card'><div class='rm-ct'>주변 신호<small>오늘 카드 밖의 논문</small></div><ul class='rm-ul'>"
                     + "".join("<li>" + (_title_link(x["title"], x.get("paper_id") or "", pid)
                                         + (f"<span class='rm-ko'>{_h(x['title_ko'])}</span>" if x.get("title_ko") else "")
                                         + (f" — {_h(x['note'])}" if x.get("note") else "")
                                         if x.get("title") else _h(x.get("note") or "")) + "</li>"
                               for x in story["side_signals"]) + "</ul></div>")
    if boxes:
        parts.append("<div class='rm-grid2'>" + "".join(boxes) + "</div>")
    return "".join(parts)


def _papers_table_html(papers: list[dict], pid: str | None) -> str:
    """오늘의 핵심 논문 — 시안의 표: # · 논문 제목(파란 링크 → 상세, 한국어 제목) · 핵심 키워드(회색 칩) · 깊이 · 발표일."""
    rows = []
    for paper in papers:
        depth = paper.get("depth")
        rows.append(
            f"<tr><td class='n'>{int(paper.get('position') or 0)}</td>"
            f"<td>{_title_link(paper.get('title') or '', paper.get('paper_id') or '', pid, paper.get('link') or '')}"
            + (f"<span class='rm-ko'>{_h(paper['title_ko'])}</span>" if paper.get("title_ko") else "") + "</td>"
            f"<td>{''.join(_chip(k, 'kw') for k in paper.get('core_hits') or [])}</td>"
            f"<td>{_chip(_DEPTH_SHOW.get(depth, depth), 'ok' if depth == '원문 분석' else '') if depth else ''}</td>"
            f"<td class='c'>{_h(paper.get('published') or '')}</td></tr>")
    return ("<table class='rm-t'><thead><tr><th>#</th><th>논문 제목</th><th>핵심 키워드</th><th>깊이</th><th class='c'>발표일</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _week_html(o: dict) -> str:
    """이번 주 — 왼쪽 7일 키워드 흐름, 오른쪽 검색 프로필 변화. 둘 다 주간 브리프와 같은 계산이다."""
    boxes: list[str] = []
    mv = o.get("movement")
    if mv and (mv.get("up") or mv.get("down")):
        rows = mv.get("up", []) + mv.get("down", [])
        top = max((abs(r["delta"]) for r in rows), default=1) or 1
        body = "".join(
            f"<div class='rm-row'><span class='{'rm-up' if r['delta'] > 0 else 'rm-down'}'>{'▲' if r['delta'] > 0 else '▼'}</span>"
            f"<span class='k'>{_h(r['keyword'])}</span><span class='rm-bar'><span class='{'' if r['delta'] > 0 else 'dn'}' "
            f"style='width:{max(2, int(abs(r['delta']) / top * 100))}%'></span></span>"
            f"<span class='v'>{r['delta']:+d}</span></div>" for r in rows)
        note = "" if mv.get("comparable", True) else "<div class='rm-empty'>이전 7일과 관측 일수가 달라 비교는 참고용이다.</div>"
        boxes.append(f"<div class='rm-card'><div class='rm-ct'>최근 7일 키워드 흐름<small>{_h(mv.get('window') or '')} · 이전 7일 대비 편수</small></div>{body}{note}</div>")
    changes = (o.get("week") or {}).get("changes") or []
    if changes:
        def _row(c: dict) -> str:
            mark = {"up": ("rm-up", "▲"), "down": ("rm-down", "▼"), "new": ("rm-new", "NEW"), "removed": ("rm-down", "삭제")}.get(c.get("kind"), ("", "·"))
            before, after = c.get("before"), c.get("after")
            val = (f"{before:.2f} → {after:.2f}" if before is not None and after is not None else (f"{after:.2f}" if after is not None else ""))
            origin = " · ".join(_ORIGIN_SHORT.get(x.strip(), x.strip()) for x in (c.get("origin") or "").split("·") if x.strip())
            return (f"<div class='rm-row'><span class='{mark[0]}'>{mark[1]}</span><span class='k'>{_h(c.get('keyword') or '')}</span>"
                    f"<span class='v'>{_h(val)}</span>" + (_chip(origin) if origin else "") + "</div>")
        boxes.append(f"<div class='rm-card'><div class='rm-ct'>검색 프로필 변화<small>{_h((o.get('week') or {}).get('window') or '')}</small></div>"
                     + "".join(_row(c) for c in changes) + "</div>")
    elif boxes:
        boxes.append("<div class='rm-card'><div class='rm-ct'>검색 프로필 변화</div><div class='rm-empty'>지난 보고 이후 바뀐 키워드·가중치가 없습니다."
                     " 반응이 쌓이면 매일 새벽, 주간 관리는 그 주 첫 근무일에 바꿉니다.</div></div>")
    return "<div class='rm-grid2'>" + "".join(boxes) + "</div>" if boxes else ""


@st.cache_data(ttl=600, show_spinner="개요를 만드는 중…")
def _overview_cached(db: str, pid: str) -> dict | None:
    """개요는 새벽 스캔 때만 바뀐다. 7일 창 재채점이 프로필당 1~5초라(2026-10-01 실측, 논문 약 3천 편) 10분 캐시한다."""
    return ops_dashboard.research_overview(Path(db), pid)


def render_overview_page(pid: str) -> None:
    """개요 — 사용자 시안(2026-10-01) 그대로: 눈썹 글·제목·프로필 → 숫자 카드 넷 → 오늘의 연구 흐름 상자 → 갈래 카드 두 칸 →
    우리 연구에서 볼 것 · 주변 신호 → 오늘의 핵심 논문 표 → 이번 주. 카드 제목은 카드 안. 숫자는 research_overview 가 DB 에서 센 값만."""
    db_path = server.DB_PATH
    try:
        o = _overview_cached(str(db_path), pid)
    except Exception as e:  # noqa: BLE001 — 개요가 깨져도 다른 메뉴는 쓸 수 있어야 한다
        st.error(f"개요를 만들지 못했습니다: {type(e).__name__}: {e}")
        return
    if not o:
        st.info("프로필을 찾지 못했습니다.")
        return
    prof = o["profile"]
    kpis = "".join(f"<div class='rm-kpi'><div class='l'>{_h(k)}</div><div class='v'>{_h(v)}</div></div>" for k, v in o.get("kpis") or [])
    story = o.get("story")
    meta = ""
    if story:
        meta = " · ".join(x for x in (story.get("reader_date") or "",
                                       (_ENGINE_SHOW.get(story.get("engine"), story.get("engine")) + " 작성") if story.get("engine") else "",
                                       "이전 형식 글" if story.get("format") == "v1" else "") if x)
    html = (f"<div class='rm-eyebrow'>RESEARCH MONITOR · {_h(o.get('date') or '')}</div>"
            f"<div class='rm-title'>연구 동향 브리핑</div><div class='rm-sub'>{_h(prof.get('name') or '')}</div>"
            + (f"<div class='rm-kpis'>{kpis}</div>" if kpis else "")
            + (_story_html(story, pid, meta) if story else "<div class='rm-card'><div class='rm-ct'>오늘의 연구 흐름</div><div class='rm-empty'>아직 저장된 동향 글이 없습니다.</div></div>"))
    papers = o.get("papers") or []
    if papers:
        html += (f"<div class='rm-gap'></div><div class='rm-card'><div class='rm-ct'>오늘의 핵심 논문 {len(papers)}편<small>가장 최근 보낸 메일 · 제목을 누르면 논문 상세</small></div>"
                 + _papers_table_html(papers, pid) + "</div>")
    html += _week_html(o)
    sysinfo = o.get("system") or {}
    if sysinfo:
        html += (f"<div class='rm-foot'>마지막 새벽 실행 {_h(sysinfo.get('last_daily') or '기록 없음')} · {'정상' if sysinfo.get('ok') else '확인 필요'} · "
                 f"다음 실행 {_h(sysinfo.get('next_daily') or '—')} — 자세한 기록은 시스템 메뉴</div>")
    st.markdown(html, unsafe_allow_html=True)


def render_profile_page(pid: str) -> None:
    """프로필 — 키워드·가중치(고칠 수 있다)·가중치 추이·변경 이력(되돌리기)·주간 관리·설정. 옛 운영 현황의 탭 다섯을 여기 모았다."""
    db_path = server.DB_PATH
    profile = research_profile.get_profile(db_path, pid)
    o = ops_dashboard.profile_overview(db_path, pid)
    st.subheader("프로필 관리")
    st.markdown(f"<div class='rm-page-sub'>관심 키워드와 도메인을 관리합니다 — <b style='color:var(--ink2)'>{_h(o['name'])}</b> "
                f"<code>{_h(pid)}</code> · rev {o['revision']}</div>", unsafe_allow_html=True)
    # 맨 위 — 2026-10-01 사용자 지적("새 프로필 만들기는 당연히 맨 위… 꽁꽁 숨겨놓을 생각이야?"). 그전엔 탭 아래 맨 끝이었다.
    with st.expander("＋ 새 프로필 만들기"):
        _render_profile_form(db_path, existing=None)
    tabs = st.container(border=True, key="box_profile_tabs").tabs(["키워드·가중치", "가중치 추이", "변경 이력", "주간 관리 결과", "설정"])
    with tabs[0]:
        _render_keywords(db_path, pid)
    with tabs[1]:
        history = ops_dashboard.weight_history(db_path, pid)
        moving = {k: v for k, v in history.items() if len({round(x[2], 3) for x in v}) > 1}
        if moving:
            _weight_chart(moving)
            st.caption("가중치가 한 번이라도 바뀐 키워드만 그린다. 가로는 프로필 revision(변경 시점), 점에 마우스를 올리면 시각이 보인다.")
        else:
            st.caption("아직 가중치가 움직인 키워드가 없습니다 — 반응이 서로 다른 논문 2편 이상 쌓이면 매일 새벽 조금씩 움직입니다.")
    with tabs[2]:
        _render_history(db_path, pid)
    with tabs[3]:
        _render_agent(db_path, pid)
    with tabs[4]:
        _render_settings(db_path, pid, profile)


_WEEKDAYS = ("일", "월", "화", "수", "목", "금", "토")
_LOCAL_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|pdf-[0-9a-f]{6,})$")     # papers 표에 있는 키 — doi:·title: 키는 상세가 없다


def _set_cal(day=None, month=None) -> None:
    if day is not None:
        st.session_state["act_day"] = day
        st.session_state["act_month"] = (day.year, day.month)
    if month is not None:
        st.session_state["act_month"] = month


def _render_calendar(by_day: dict, year: int, month: int, selected) -> None:
    """달력 한 달 — 시안 모양: "‹ 2026. 10 ›", 메일 보낸 날은 숫자 밑 청록 점, 고른 날은 옅은 청록 칸. 날짜를 누르면 오른쪽이 그날로."""
    import calendar
    prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    left, mid, right = st.columns([1, 4, 1])
    left.button("‹", key="calnav_prev", on_click=_set_cal, kwargs={"month": prev_m}, width="stretch")
    mid.markdown(f"<div class='rm-cal-title'>{year}. {month:02d}</div>", unsafe_allow_html=True)
    right.button("›", key="calnav_next", on_click=_set_cal, kwargs={"month": next_m}, width="stretch")
    head = st.columns(7)
    for col, name in zip(head, _WEEKDAYS):
        col.markdown(f"<div class='rm-cal-wd'>{name}</div>", unsafe_allow_html=True)
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(year, month):
        cols = st.columns(7)
        for col, day in zip(cols, week):
            if day.month != month:
                col.markdown(f"<div class='rm-cal-out'>{day.day}</div>", unsafe_allow_html=True)
                continue
            info = by_day.get(day.isoformat())
            col.button(f"{day.day}" + (" :primary[●]" if info else ""), key=f"cal_{day.isoformat()}", width="stretch",
                       on_click=_set_cal, kwargs={"day": day}, type="primary" if day == selected else "secondary")


def _day_detail_html(day, info: dict | None, pid: str | None) -> str:
    """고른 날 — 시안 모양: 상자 셋(보낸 메일·발송한 논문·사용자 반응) → 메일 카드(발송 일시·수신 대상·메일 제목) → 첨부 논문 표."""
    if not info:
        return "<div class='rm-empty' style='padding:12px 0'>이날 보낸 메일이 없습니다 — 주말·공휴일은 쉽니다.</div>"
    pos = sum(it["reactions"]["more"] + it["reactions"]["useful"] for iss in info["issues"] for it in iss["items"])
    neg = sum(it["reactions"]["out"] for iss in info["issues"] for it in iss["items"])
    html = _stat_cards([("보낸 메일", f"{len(info['issues'])}통"), ("발송한 논문", f"{info['papers']}편"),
                        ("사용자 반응", f"{info['reactions']}건", f"긍정 {pos} · 부정 {neg}")])
    for iss in info["issues"]:
        status = {"partial": " · 일부 수신자 실패", "failed": " · 발송 실패"}.get(iss.get("status"), "")
        rows = []
        for it in iss["items"]:
            rx = it.get("reactions") or {}
            p_, n_ = rx.get("more", 0) + rx.get("useful", 0), rx.get("out", 0)
            react = (_chip(f"긍정 {p_}", "ok") if p_ else "") + (_chip(f"부정 {n_}", "bad") if n_ else "") or "-"
            rows.append(f"<tr><td class='n'>{int(it.get('position') or 0)}</td>"
                        f"<td>{_title_link(it.get('title') or '', it['paper_key'] if _LOCAL_ID_RE.match(it.get('paper_key') or '') else '', pid, it.get('link') or '')}</td>"
                        f"<td>{_h(', '.join(it.get('core_hits') or []))}</td><td class='c'>{react}</td></tr>")
        html += ("<div class='rm-gap'></div><div class='rm-card' style='padding:16px 20px'>"
                 f"<div class='rm-mailhead'>{_h(iss.get('subject') or '메일')}</div>"
                 "<div class='rm-mailmeta'>"
                 f"<b>발송 일시</b><span>{_h(ops_dashboard._kst(iss.get('sent_at')))}{_h(status)}</span>"
                 f"<b>수신 대상</b><span>{int(iss.get('recipients_sent') or 0)}명</span>"
                 f"<b>메일 제목</b><span>{_h(iss.get('subject') or '')}</span></div>"
                 f"<div class='rm-ct' style='margin-top:6px'>첨부 논문 {int(iss.get('paper_count') or 0)}편</div>"
                 "<table class='rm-t'><thead><tr><th>#</th><th>논문</th><th>키워드</th><th class='c'>반응</th></tr></thead><tbody>"
                 + "".join(rows) + "</tbody></table></div>")
    return html


def render_activity_page(pid: str) -> None:
    """활동 기록 — 시안 모양: 왼쪽 달력 카드 + 월 요약 카드, 오른쪽 고른 날 카드. 2026-10-01 사용자: 날짜별 펼침 목록은 가독성이
    떨어진다, 달력에서 눌러 본다. 받은 반응 전체 표는 두 번째 탭."""
    import datetime as _dt
    db_path = server.DB_PATH
    o = ops_dashboard.profile_overview(db_path, pid)
    r = o["reactions"]
    st.subheader("활동 기록")
    st.markdown(f"<div class='rm-page-sub'>메일 {o['mails']['issues']}통 · 논문 {o['mails']['papers']}편 · 반응 {r['valid']}건"
                f"(긍정 {r['more'] + r['useful']} · 부정 {r['out']})</div>", unsafe_allow_html=True)
    tabs = st.tabs(["보낸 메일", "받은 반응"])
    with tabs[0]:
        issues = ops_dashboard.issues_with_reactions(db_path, pid)
        by_day = ops_dashboard.issues_by_day(issues)
        latest = max(by_day) if by_day else None
        if st.session_state.get("act_pid") != pid:          # 프로필을 바꾸면 그 프로필의 마지막 메일 날로
            st.session_state["act_pid"] = pid
            st.session_state.pop("act_day", None)
            st.session_state.pop("act_month", None)
        day = st.session_state.get("act_day") or (_dt.date.fromisoformat(latest) if latest else _dt.date.today())
        year, month = st.session_state.get("act_month") or (day.year, day.month)
        cal_col, detail_col = st.columns([2, 3], gap="medium")
        with cal_col:
            with st.container(border=True, key="box_calendar"):
                _render_calendar(by_day, year, month, day)
            m = ops_dashboard.month_summary(by_day, year, month)
            pos = sum(it["reactions"]["more"] + it["reactions"]["useful"] for d, v in by_day.items() if d.startswith(f"{year:04d}-{month:02d}-")
                      for iss in v["issues"] for it in iss["items"])
            st.markdown(f"<div class='rm-card' style='margin-top:4px'><div class='rm-ct'>{year}년 {month}월 요약</div><div class='rm-sum'>"
                        f"<div class='row'><span>보낸 메일</span><b>{m['mails']}통</b></div>"
                        f"<div class='row'><span>메일 보낸 날</span><b>{m['days']}일</b></div>"
                        f"<div class='row'><span>발송한 논문</span><b>{m['papers']}편</b></div>"
                        f"<div class='row'><span>사용자 반응</span><b>{m['reactions']}건<small>긍정 {pos} · 부정 {m['reactions'] - pos}</small></b></div>"
                        "</div></div>", unsafe_allow_html=True)
        with detail_col:
            with st.container(border=True, key="box_daydetail"):
                days = sorted(by_day)
                earlier = [d for d in days if d < day.isoformat()]
                later = [d for d in days if d > day.isoformat()]
                t, b1, b2 = st.columns([8, 1, 1])
                t.markdown(f"<div class='rm-title' style='font-size:1.3rem;margin:4px 0 0'>{day.year}년 {day.month}월 {day.day}일 "
                           f"({_WEEKDAYS[(day.weekday() + 1) % 7]})</div>", unsafe_allow_html=True)
                b1.button("‹", key="daynav_prev", width="stretch", disabled=not earlier, help="이전 메일",
                          on_click=_set_cal, kwargs={"day": _dt.date.fromisoformat(earlier[-1]) if earlier else None})
                b2.button("›", key="daynav_next", width="stretch", disabled=not later, help="다음 메일",
                          on_click=_set_cal, kwargs={"day": _dt.date.fromisoformat(later[0]) if later else None})
                st.markdown(_day_detail_html(day, by_day.get(day.isoformat()), pid), unsafe_allow_html=True)
    with tabs[1]:
        _render_reactions(db_path, pid)


def _render_profiles_table(db_path) -> None:
    """프로필 전체를 한 표로(시안의 시스템 > 프로필). 주간 관리 결과는 칩(실패 빨강·적용 초록)."""
    rows = []
    for pid in research_profile.list_profiles(db_path):
        o = ops_dashboard.profile_overview(db_path, pid)
        if not o:
            continue
        m, r = o["mails"], o["reactions"]
        label = ops_dashboard.agent_status_label(o["last_agent"])
        kind = "bad" if "실패" in label else ("ok" if "적용" in label else "")
        rows.append(f"<tr><td>{_h(o['field'])}</td><td>{_h(pid)}</td><td>{'매일' if o['schedule'] == 'daily' else '수동'}</td>"
                    f"<td class='r'>{m['issues']}</td><td class='r'>{m['papers']}</td><td class='r'>{r['valid']}</td>"
                    f"<td class='r'>{o['keywords']['core']}</td><td class='r'>{len(o['recipients'])}</td><td class='r'>{o['revision']}</td>"
                    f"<td>{_chip(label, kind)}</td></tr>")
    if rows:
        st.markdown("<table class='rm-t'><thead><tr><th>분야</th><th>ID</th><th>주기</th><th class='r'>메일</th><th class='r'>논문</th>"
                    "<th class='r'>반응</th><th class='r'>핵심 키워드</th><th class='r'>수신자</th><th class='r'>rev</th><th>주간 관리</th></tr></thead>"
                    "<tbody>" + "".join(rows) + "</tbody></table>", unsafe_allow_html=True)


_DETAIL_SECTIONS = (("연구 개요", "연구 개요"), ("방법 상세", "방법론"), ("실험 설정", "실험 구성"), ("결과", "주요 결과"),
                    ("논문의 한계점", "연구 한계"), ("결론", "결론"))
_REF_RE = re.compile(r"\s*\[(S\d{3,5}(?:\s*,\s*S\d{3,5})*)\]")


def _summary_line_html(line: str) -> str:
    """요약 한 줄 → <li>. "- 항목 : 값 [S0006]" 의 항목은 굵게, 근거 번호는 작은 회색(원문 문장 번호 — 쪽수가 아니다)."""
    import mail_document
    head = re.match(r"^\s{0,3}#{2,6}\s+(.+?)\s*#*\s*$", line)
    if head:
        # 요약의 하위 제목(`#### 학습 과정`)은 상위 절 본문으로 들어온다(`ops_dashboard.summary_sections` — 절에서 빠지지 않게).
        # 그대로 그리면 `####` 가 글자로 보였다(2026-10-06 독립 검토 P3-3) — 기호를 떼고 글머리 없는 굵은 소제목으로 그린다.
        label = re.sub(r"\*\*(.+?)\*\*", r"\1", head.group(1))
        return f"<li class='rm-subhead' style='list-style:none;margin:10px 0 2px -18px'><b>{_h(label)}</b></li>"
    text = mail_document.nominal_line(line.strip())
    sub = line.startswith(("  -", "    -", "\t-"))
    text = re.sub(r"^[-•]\s*", "", text)
    m = _CARD_LABEL_RE.match(text)
    html = f"<b>{_h(m.group(1))}</b> {_h(text[m.end():])}" if m else _h(text)
    html = _REF_RE.sub(lambda mm: f" <span class='rm-ref'>[{mm.group(1)}]</span>", html)
    html = re.sub(r"\$([^$]{1,200})\$", lambda mm: f"<code class='rm-math'>{mm.group(1)}</code>", html)
    return f"<li style='margin-left:{18 if sub else 0}px'>{html}</li>"


_CARD_LABEL_RE = re.compile(r"^([^:：\n]{1,30}?)\s*[:：]\s+")


def render_paper_detail(arxiv_id: str, pid: str | None, crumb: bool = True) -> None:
    """논문 상세 — 사용자 시안(2026-10-01): 경로 · 제목 · 메타 줄 · 상태 칩 줄 → 왼쪽 요약 절 카드, 오른쪽 기본 정보·코드·재현·초록.
    시안의 "근거 문장(p.1)"은 쪽수가 우리 기록에 없어 만들지 않는다 — 요약 줄의 [S번호](원문 문장 번호)를 작게 그대로 보인다."""
    import json as _json
    db_path = server.DB_PATH
    d = ops_dashboard.paper_detail(db_path, arxiv_id)
    if not d:
        st.warning(f"논문을 찾지 못했습니다: {arxiv_id}")
        return
    cat = next((r for r in ops_dashboard.paper_catalog(db_path, query=arxiv_id) if r["arxiv_id"] == arxiv_id), {})
    names = {p: (research_profile.get_profile(db_path, p) or {}).get("name", p).split(" — ")[0] for p in cat.get("profiles") or []}
    def _list(raw: str) -> list[str]:
        try:
            v = _json.loads(raw) if raw else []
            return [str(x) for x in v] if isinstance(v, list) else [str(v)]
        except (TypeError, ValueError):
            return [x.strip() for x in str(raw).split(",") if x.strip()]
    authors, cats = _list(d.get("authors")), _list(d.get("categories"))
    hits = ops_dashboard.paper_core_hits(db_path, arxiv_id)
    link = d.get("link") or ""
    meta = [d.get("source") or "", (d.get("published") or "")[:10]]
    if names:
        meta.append("보낸 프로필 " + ", ".join(names.values()))
    head = (f"<div class='rm-crumb'><a href='?page=papers' target='_self'>논문 DB</a> &nbsp;›&nbsp; 논문 상세</div>" if crumb else "")
    head += f"<div class='rm-title' style='font-size:1.6rem'>{_h(d['title'])}</div><div class='rm-meta'>" + "".join(
        f"<span>{_h(x)}</span>" for x in meta if x) + (f"<span><a class='rm-link' href='{_h(link)}' target='_blank'>원문 보기 ↗</a></span>"
                                                       if link.startswith(("https://", "http://")) else "") + "</div>"
    kv = [("요약", "있음" if d["summary_md"] else "없음", "ok" if d["summary_md"] else ""), ("재현", cat.get("repro") or "—", ""),
          ("코드", _TIER_SHORT.get(cat.get("code_tier"), cat.get("code_tier")) or "—", "")]
    if hits:
        kv.append(("키워드", ", ".join(hits), ""))
    head += "<div>" + "".join(f"<span class='rm-kv {k}'><b>{_h(a)}</b><span>{_h(b)}</span></span>" for a, b, k in kv) + "</div>"
    left: list[str] = []
    sections = dict(ops_dashboard.summary_sections(d["summary_md"]))
    import mail_document
    def section_card(title: str, lines: list[str]) -> str:
        """명칭을 통일해도 UI의 기존 요약 카드 구조는 유지한다."""
        return (f"<div class='rm-card'><div class='rm-ct'>{_h(title)}</div><ul class='rm-ul'>"
                + "".join(_summary_line_html(x) for x in lines) + "</ul></div>")
    for key, title in _DETAIL_SECTIONS:
        lines = [x for x in sections.get(key, []) if x.strip()]
        notes: list[str] = []
        if key == "논문의 한계점":
            notes = [line for line in lines if re.match(r"^분석 메모\s*[:：]", mail_document.nominal_line(re.sub(r"^\s*[-•]\s*", "", line)))]
            lines = [line for line in lines if line not in notes]
        if lines:
            left.append(section_card(title, lines))
        if notes:
            left.append(section_card("분석 메모", notes))
        if key == "결과" and d.get("comparisons"):
            body = ''.join("<li>" + (f"<a href='{_h(url)}' target='_blank'>{_h(text)} ↗</a>" if url else _h(text))
                           + "</li>" for text, url in d["comparisons"])
            left.append("<div class='rm-card'><div class='rm-ct'>성능 비교</div><ul class='rm-ul'>" + body + "</ul></div>")
    if left:
        left.append(mail_document.evidence_footer(d["summary_md"]))
    if not left:
        left.append("<div class='rm-card'><div class='rm-ct'>초록<small>본문 요약이 없는 논문 — 초록만 정리됐거나 본문을 받지 못했다</small></div>"
                    f"<div class='rm-abs'>{_h(d['abstract'] or '초록 없음')}</div></div>")
    shown_authors = ", ".join(authors[:6]) + (f" 외 {len(authors) - 6}명" if len(authors) > 6 else "")
    info = [("저자", shown_authors), ("발표일", (d.get("published") or "")[:10]), ("출처", d.get("source") or ""), ("분야", ", ".join(cats)),
            ("ID", arxiv_id), ("처음 발송", cat.get("first_sent") or ""),
            ("요약 엔진", (d.get("summary_engine") or "") + (f" · 원문 {d['coverage'] * 100:.0f}% 확인" if d.get("coverage") is not None else ""))]
    right = ["<div class='rm-card'><div class='rm-ct'>기본 정보</div><table class='rm-info'>"
             + "".join(f"<tr><td>{a}</td><td>{_h(b)}</td></tr>" for a, b in info if b) + "</table></div>"]
    code_bits = [x for x in (d.get("code_line"), d.get("sota_line")) if x]
    repro = "".join(f"<li>{'성공' if r['success'] else '실패'} · {_h(r['stage'] or '')} · {_h(r['repo_url'] or '')}"
                    + (f" <span class='rm-ref'>{_h(ops_dashboard.short_error(r['fail_detail']))}</span>" if r.get("fail_detail") else "") + "</li>"
                    for r in d["repro"])
    if code_bits or repro:
        right.append("<div class='rm-card'><div class='rm-ct'>코드 · 재현</div>" + "".join(f"<div class='rm-abs'>{_h(x)}</div>" for x in code_bits)
                     + (f"<ul class='rm-ul' style='margin-top:8px'>{repro}</ul>" if repro else "") + "</div>")
    if d["summary_md"] and d.get("abstract"):
        right.append(f"<div class='rm-card'><div class='rm-ct'>초록</div><div class='rm-abs'>{_h(d['abstract'])}</div></div>")
    st.markdown(head + "<div class='rm-detail'><div class='col'>" + "".join(left) + "</div><div class='col'>" + "".join(right) + "</div></div>",
                unsafe_allow_html=True)


_TIER_SHORT = {"official": "공식", "author": "저자 연관", "third_party": "제3자", "analogous": "유사 구현", "none": "없음", None: "—"}


def render_papers_page() -> None:
    """저장된 논문 전체를 한 표로 — 검색어·프로필로 거르고, 한 편을 골라 요약·재현·코드·SOTA 주장을 본다."""
    import pandas as pd
    st.subheader("논문 DB")
    head = st.empty()                                   # "검색 결과 N편" — 필터를 읽은 뒤 채운다(사용자 시안: 제목 바로 아래)
    db_path = server.DB_PATH
    profiles = research_profile.list_profiles(db_path)
    names = {pid: (research_profile.get_profile(db_path, pid) or {}).get("name", pid).split(" — ")[0] for pid in profiles}
    filt = st.container(border=True, key="box_paper_filter")       # 검색·필터를 흰 카드 하나로(사용자 시안)
    c1, c2, c3, c4, c5 = filt.columns([3, 1.4, 1.1, 1.1, 1.1])
    query = c1.text_input("검색", placeholder="제목·초록·arXiv ID 로 검색", key="papers_query")
    pick = c2.selectbox("보낸 프로필", ["(전체)"] + profiles, format_func=lambda v: "전체" if v == "(전체)" else names.get(v, v),
                        key="papers_profile")
    source = c3.selectbox("출처", ["전체", "arXiv", "저널(OA)", "업로드 PDF"], key="papers_source")
    summary = c4.selectbox("요약", ["전체", "있음", "없음"], key="papers_summary")
    period = c5.selectbox("저장 기간", ["전체 기간", "최근 7일", "최근 30일", "최근 90일"], key="papers_period")
    rows = ops_dashboard.paper_catalog(db_path, query=query, profile_id=None if pick == "(전체)" else pick)
    rows = ops_dashboard.filter_catalog(rows, source=source, summary=summary,
                                        days={"최근 7일": 7, "최근 30일": 30, "최근 90일": 90}.get(period))
    head.caption(f"검색 결과 {len(rows)}편 · 최신 저장 순")
    if not rows:
        st.info("조건에 맞는 논문이 없습니다.")
        return
    per_page = 50
    pages = (len(rows) + per_page - 1) // per_page
    sig = (query, pick, source, summary, period)
    if st.session_state.get("papers_sig") != sig:          # 조건을 바꾸면 첫 쪽으로
        st.session_state["papers_sig"] = sig
        st.session_state["papers_page"] = 1
    page = min(max(1, st.session_state.get("papers_page", 1)), pages)
    shown = rows[(page - 1) * per_page: page * per_page]
    df = pd.DataFrame([{
        "제목": r["title"], "발표": r["published"], "저장": r["fetched"], "출처": r["source"],
        "요약": "있음" if r["summarized"] else "", "재현": r["repro"], "코드": _TIER_SHORT.get(r["code_tier"], r["code_tier"]),
        "보낸 프로필": ", ".join(names.get(p, p) for p in r["profiles"]) or "—", "처음 발송": r["first_sent"],
        "원문": (f"https://arxiv.org/abs/{r['arxiv_id']}" if r["source"] == "arXiv" else ""),   # None 이면 표에 "None" 글자가 찍혔다
        "주의": ", ".join(r["flags"]), "ID": r["arxiv_id"],
    } for r in shown])
    table = st.container(border=True, key="box_paper_table")
    # 제목이 잘려 논문을 못 알아본다는 지적(Codex 검토 2026-09-16) — 제목 칸을 넓게, 짧은 칸은 좁게 고정한다.
    event = table.dataframe(df, hide_index=True, width="stretch", height=min(len(shown), 15) * 35 + 40, on_select="rerun",
                            selection_mode="single-row", key=f"papers_table_{page}",
                            column_config={"제목": st.column_config.TextColumn(width="large"),
                                           "원문": st.column_config.LinkColumn(display_text="열기 ↗", width="small"),
                                           "요약": st.column_config.TextColumn(width="small"), "재현": st.column_config.TextColumn(width="small"),
                                           "코드": st.column_config.TextColumn(width="small"), "주의": st.column_config.TextColumn(width="small"),
                                           "ID": st.column_config.TextColumn(width="small")})
    f1, f2, f3, f4 = table.columns([6, 1, 1.2, 1])
    f1.caption(f"{len(rows)}편 중 {(page - 1) * per_page + 1}–{min(page * per_page, len(rows))} 표시 · 한 줄을 고르면 아래에 요약·재현·코드가 나옵니다")
    f2.button("‹ 이전", key="papers_prev", width="stretch", disabled=page <= 1,
              on_click=lambda: st.session_state.update(papers_page=page - 1))
    f3.markdown(f"<div style='text-align:center;padding-top:6px'><b>{page}</b> / {pages}쪽</div>", unsafe_allow_html=True)
    f4.button("다음 ›", key="papers_next", width="stretch", disabled=page >= pages,
              on_click=lambda: st.session_state.update(papers_page=page + 1))
    selected = event.selection.rows[0] if getattr(event, "selection", None) and event.selection.rows else None
    if selected is None:
        return
    pick_id = shown[selected]["arxiv_id"]
    st.markdown(f"<div class='rm-gap'></div><a class='rm-link' href='{_h(_detail_href(pick_id))}' target='_self'>이 논문 상세 화면으로 ↗</a>",
                unsafe_allow_html=True)
    with st.container(border=True, key="box_paper_inline"):
        render_paper_detail(pick_id, None, crumb=False)


# ---------------------------------------------------------------- 시스템
_EXIT_LABELS = {0: "정상", 2: "발송됨 · 소스 장애", 1: "실패", "stopped": "수동 중지", "skipped": "스킵(이전 실행 중)", None: "종료 기록 없음(진행 중·중단)"}
_CRON_HINTS = {"run_daily_scan.sh": "평일 새벽 스캔·메일 발송(그 주 첫 근무일엔 주간 관리 먼저)", "run_weekly_agent.sh": "손으로 돌리는 주간 관리",
               "check_daily_mail.py": "새벽 메일 부재 감시"}


def render_system_page() -> None:
    """실행 기록·예약 작업·DB·백업·로그 — "어젯밤에 무슨 일이 있었나"를 터미널 없이 본다.
    2026-10-01 사용자 지적("눈 아파서 들어오겠냐"): 소제목과 표가 한 면에 이어져 구역이 안 갈렸다 — 구역마다 테두리 상자로 묶는다."""
    import pandas as pd
    st.subheader("시스템")
    db_path = server.DB_PATH
    with st.container(border=True, key="box_5"):
        st.markdown("#### 운영 상태")
        try:
            _render_status_strip(ops_dashboard.system_status(db_path, ROOT))
        except Exception as e:  # noqa: BLE001 — 상태 줄이 깨져도 나머지는 뜬다
            st.caption(f"상태 조회 실패: {type(e).__name__}")
    with st.container(border=True, key="box_6"):
        st.markdown("#### 프로필")
        _render_profiles_table(db_path)

    dbs = ops_dashboard.db_status(Path(db_path))
    t = dbs["tables"]
    r = dbs["retention"]
    with st.container(border=True, key="box_7"):
        st.markdown("#### DB · 백업")
        st.markdown(_stat_cards([
            ("DB 크기", ops_dashboard.fmt_bytes(dbs["bytes"]), f"WAL {ops_dashboard.fmt_bytes(dbs['wal_bytes'])}"),
            ("저장 논문", f"{t.get('papers') or 0}편", f"요약 {t.get('summaries') or 0}편 · 재현 시도 {t.get('repro_results') or 0}건"),
            ("최신 백업", dbs["backups"][0]["when"] if dbs["backups"] else "없음",
             f"보관 {len(dbs['backups'])}개 · 최신 {ops_dashboard.fmt_bytes(dbs['backups'][0]['bytes'])}" if dbs["backups"] else ""),
            ("마지막 DB 정리", r["when"] if r else "아직 없음",
             (f"행 {r['rows_deleted']} · 파일 {r['files_deleted']} 삭제" + (f" · 오류 {r['error']}" if r["error"] else "")) if r
             else "그 주 첫 근무일 새벽 일일 스캔 직전에 돕니다"),
        ]), unsafe_allow_html=True)

    with st.container(border=True, key="box_8"):
        st.markdown("#### 새벽 실행 기록")
        runs = ops_dashboard.recent_runs(ROOT, limit=14)
        if runs:
            def _run_row(x: dict) -> str:
                label = _EXIT_LABELS.get(x["exit"], str(x["exit"]))
                kind = "ok" if x["exit"] == 0 else ("bad" if x["exit"] in (1, 2) else "")
                sent = " / ".join(f"{pid.replace('team_', '')}: {v.get('delivery', v.get('status', ''))}" for pid, v in x["profiles"].items()) or "—"
                mins = "—" if x["minutes"] is None else f"{x['minutes']:g}"
                return (f"<tr><td>{_h(x['when'])}</td><td class='r'>{mins}</td>"
                        f"<td class='c'>{_chip(label, kind)}</td><td class='r'>{x['warnings']}</td>"
                        f"<td class='r'>{'—' if x['api_calls'] is None else x['api_calls']}</td><td>{_h(sent)}</td></tr>")
            st.markdown("<table class='rm-t'><thead><tr><th>시작(KST)</th><th class='r'>소요(분)</th><th class='c'>결과</th><th class='r'>경고</th>"
                        "<th class='r'>API 호출</th><th>발송</th></tr></thead><tbody>" + "".join(_run_row(x) for x in runs) + "</tbody></table>",
                        unsafe_allow_html=True)
        else:
            st.caption("logs/daily_scan.log 기록 없음")

    with st.container(border=True, key="box_9"):
        st.markdown("#### 주간 에이전트")
        agent_rows = []
        for pid in research_profile.list_profiles(db_path):
            for run in ops_dashboard.agent_history(db_path, pid, limit=4):
                agent_rows.append({"주차": run["week"], "프로필": pid, "결과": ops_dashboard.agent_status_label(run),
                                   "제안": len(run["proposed"]), "적용": len(run["applied"]), "기각": len(run["rejected"]), "시각": run["when"]})
        if agent_rows:
            body = "".join(
                f"<tr><td>{_h(x['주차'])}</td><td>{_h(x['프로필'])}</td>"
                f"<td>{_chip(x['결과'], 'bad' if '실패' in x['결과'] else ('ok' if '적용' in x['결과'] else ''))}</td>"
                f"<td class='r'>{x['제안']}</td><td class='r'>{x['적용']}</td><td class='r'>{x['기각']}</td><td>{_h(x['시각'])}</td></tr>"
                for x in sorted(agent_rows, key=lambda x: (x["주차"], x["프로필"]), reverse=True))
            st.markdown("<table class='rm-t'><thead><tr><th>주차</th><th>프로필</th><th>결과</th><th class='r'>제안</th><th class='r'>적용</th>"
                        "<th class='r'>기각</th><th>시각</th></tr></thead><tbody>" + body + "</tbody></table>", unsafe_allow_html=True)
        else:
            st.caption("아직 실행 없음 — 그 주 첫 근무일 새벽 첫 실행. 볼 자료가 없는 프로필은 모델을 부르지 않습니다.")

    left, right = st.columns(2)
    with left.container(border=True, key="box_10"):
        st.markdown("#### 예약 작업(cron)")
        entries = ops_dashboard.cron_entries(ROOT)
        if entries is None:
            st.caption("crontab 을 읽지 못했습니다.")
        elif not entries:
            st.warning("이 저장소를 부르는 cron 이 없습니다 — 자동 실행이 멈춰 있습니다.")
        else:
            for e in entries:
                hint = next((v for k, v in _CRON_HINTS.items() if k in e), "")
                sched = " ".join(e.split()[:5])
                st.markdown(f"- `{sched}` — {hint}")
            st.caption("PC(WSL)가 꺼져 있으면 cron 도 돌지 않습니다.")
        st.markdown("#### 백업 파일")
        if dbs["backups"]:
            st.dataframe(pd.DataFrame([{"파일": x["name"], "크기": ops_dashboard.fmt_bytes(x["bytes"]), "시각": x["when"]} for x in dbs["backups"]]),
                         hide_index=True, width="stretch")
        else:
            st.caption("백업 없음")
    with right.container(border=True, key="box_11"):
        st.markdown("#### DB 표")
        st.dataframe(pd.DataFrame([{"표": k, "행": v if v is not None else "없음"} for k, v in t.items()]),
                     hide_index=True, width="stretch", height=360)

    with st.container(border=True, key="box_12"):
        st.markdown("#### 로그 보기")
        logs = sorted(p.name for p in (ROOT / "logs").glob("*") if p.is_file() and p.suffix in (".log", ".txt", ".json"))
        if logs:
            default = logs.index("daily_scan.log") if "daily_scan.log" in logs else 0
            l1, l2 = st.columns([3, 1])
            name = l1.selectbox("파일", logs, index=default, key="log_pick")
            n = l2.number_input("마지막 줄 수", min_value=20, max_value=2000, value=200, step=50, key="log_lines")
            st.code(ops_dashboard.log_tail(ROOT, name, int(n)) or "(비어 있음)", language="text")


# ---------------------------------------------------------------- 메인

_PAGES = (("overview", "개요"), ("papers", "논문"), ("profile", "프로필"), ("activity", "활동 기록"), ("system", "시스템"))
# 메뉴는 글자만 — 아이콘은 10/1 에 넣었다가 뺐다(외부 의견 "연구관리 도구는 텍스트 + 선택 막대로 충분", 9/17 사용자 결정과 같다).

_ALL_PAGES = {k for k, _ in _PAGES} | {"paper"}        # paper = 논문 상세(메뉴에는 없다 — 제목 링크로 들어온다)
if st.session_state.get("nav_page") not in _ALL_PAGES:
    st.session_state.nav_page = "overview"          # 켜자마자 연구 동향(2026-10-01 — 그전엔 운영 현황)


def _go(page: str) -> None:
    st.session_state.nav_page = page
    # URL도 현재 선택을 담아야 링크 이동·뒤로가기에서 같은 화면을 복원한다(2026-10-02).
    params = {"page": page}
    if st.session_state.get("_research_selected_profile"):
        params["profile"] = st.session_state["_research_selected_profile"]
    st.query_params.from_dict(params)


def _select_profile() -> None:
    """위젯 콜백에서 주소를 맞춰 다음 실행이 옛 URL로 선택을 되돌리지 않게 한다."""
    st.query_params["profile"] = st.session_state["_research_selected_profile"]


_profile_ids = research_profile.list_profiles(server.DB_PATH)
# 제목 링크(같은 탭) — ?paper=ID[&profile=PID] 는 논문 상세, ?page=papers 는 논문 DB. 새 세션으로 열리므로 보던 프로필도 주소로 넘긴다.
_qp = st.query_params
# 폼은 사이드바 뒤에 실행된다. 위젯 생성 이후에는 그 키를 쓸 수 없으므로 다음 실행에서 적용한다.
_pending = st.session_state.pop("_pending_profile", None)
if _pending in _profile_ids:
    _qp["profile"] = _pending
if _qp.get("paper"):
    st.session_state.nav_page = "paper"
    st.session_state["detail_id"] = _qp.get("paper")
else:
    st.session_state.nav_page = _qp.get("page") if _qp.get("page") in dict(_PAGES) else "overview"
if _qp.get("profile") in _profile_ids:
    st.session_state["_research_selected_profile"] = _qp.get("profile")
if _profile_ids and st.session_state.get("_research_selected_profile") not in _profile_ids:
    st.session_state["_research_selected_profile"] = max(
        _profile_ids, key=lambda pid: (mail_ledger.counts(server.DB_PATH, pid)["issues"], pid == "team_ai_advance"))
_names = {pid: (ops_dashboard.profile_overview(server.DB_PATH, pid) or {}).get("field", pid) for pid in _profile_ids}

with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand">최신 연구 동향'
        '<span class="sidebar-brand-sub">Research Monitor</span></div><div class="sidebar-rule"></div>',
        unsafe_allow_html=True,
    )
    if _profile_ids:
        # 프로필은 개요·프로필·활동 기록이 함께 쓴다 — 메뉴를 옮겨도 보던 프로필이 유지된다.
        st.selectbox("프로필", _profile_ids, key="_research_selected_profile", format_func=lambda v: _names.get(v, v),
                     on_change=_select_profile)
    st.markdown("<div class='sidebar-rule'></div>", unsafe_allow_html=True)
    for key, label in _PAGES:
        # on_click 콜백은 재실행 **앞에서** 돌아 그 한 번의 실행이 이미 새 페이지를 그린다. 그전(`if st.button():` 뒤에 명시적 재실행 호출)에는
        # 클릭 재실행 + 명시적 재실행으로 전환마다 스크립트가 두 번 돌았다(2026-09-17 실측: 브라우저 전환 1.6~2.8초, 첫 방문 4~9초).
        current = "papers" if st.session_state.nav_page == "paper" else st.session_state.nav_page    # 상세에서는 "논문"이 선택돼 보인다
        st.button(label, key=f"nav_{key}", width="stretch", on_click=_go, args=(key,),
                  type="primary" if current == key else "secondary")
    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    try:
        _status = ops_dashboard.system_status(server.DB_PATH, ROOT)
        st.caption(f"다음 새벽 실행 {_status['next_daily_kst']}")
        st.caption(f"주간 관리 {_status['next_weekly_kst']}")
        if not _status["daily_ran_today"]:
            st.caption("오늘 새벽 실행 기록 없음")
    except Exception:  # noqa: BLE001 — 사이드바 상태가 깨져도 화면은 뜬다
        pass

_page = st.session_state.nav_page
_selected = st.session_state.get("_research_selected_profile")
if _page == "paper":
    render_paper_detail(str(st.session_state.get("detail_id") or ""), _selected)
elif _page == "papers":
    render_papers_page()
elif _page == "system":
    render_system_page()
elif not _selected:
    st.info("아직 프로필이 없습니다.")
    with st.expander("새 프로필 만들기", expanded=True):
        _render_profile_form(server.DB_PATH, existing=None)
elif _page == "profile":
    render_profile_page(_selected)
elif _page == "activity":
    render_activity_page(_selected)
else:
    render_overview_page(_selected)
