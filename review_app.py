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
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import streamlit as st

import sentence_grounding
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

        /* 참고 이미지(2026-08-14)에 맞춰 하늘색(cyan) 톤 → 진한 인디고
           블루 톤으로 색만 바꿨다("구조는 그대로 두고 색상이랑 느낌만"
           요청) — 변수 이름은 --sky로 남겨뒀다(코드 전체에 var(--sky)가
           널리 퍼져 있어 이름까지 바꾸면 손댈 곳이 훨씬 늘어나고 실수
           위험만 커짐, 값만 바꿔도 색은 똑같이 다 바뀐다). 브랜드
           아이콘(_BRAND_ICON)의 그라디언트(#5B8DEF→#1E3A8A)와 같은
           계열로 맞춰 아이콘과 UI 전체 톤이 일치하게 했다. */
        :root {
            --sky: #4C6EF5;
            --sky-dark: #3651D4;
            --sky-light: #EEF1FF;
            --sky-border: #D7DEFF;
            --text-main: #1B2036;
            --text-muted: #64748B;
        }

        /* [class*="css"]로 전체 폰트를 지정했었는데, 실측해보니(2026-08-14)
           Streamlit 1.60의 실제 클래스명은 st-emotion-cache-XXXX라 "css"라는
           부분 문자열 자체가 없어서 이 규칙이 단 한 곳에도 안 먹고 있었다
           (computed font-family가 Pretendard가 아니라 Streamlit 기본값
           "Source Sans"로 나오는 것 확인) — "글씨체가 이상하다"는 지적이
           실제 버그였다. 더 넓은 실제 루트 컨테이너에 !important로 걸어
           Streamlit 자체 규칙을 확실히 이긴다.
           *를 그대로 걸었더니 [data-testid="stIconMaterial"](화살표 등
           아이콘을 "keyboard_arrow_right" 같은 리터럴 글자를 전용 아이콘
           폰트로 그려서 만드는 요소)까지 Pretendard로 강제돼 아이콘이
           그 글자 그대로 깨져 보이는 회귀가 실제로 났다(사이드바 화살표
           확인) — 아이콘 폰트 요소는 :not()으로 제외한다. */
        html, body,
        [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] *:not([data-testid="stIconMaterial"]) {
            font-family: 'PretendardVariable', -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif !important;
        }

        /* 기본 레이아웃 여백 — Streamlit 기본값은 위쪽이 휑하게 남는다.
           max-width 1100px 때문에 넓은 화면에서 오른쪽 여백이 크게
           남았다("이미지처럼 꽉 채울 수 있잖아" 지적, 2026-08-14) —
           참고 이미지처럼 폭을 거의 다 쓰도록 넉넉하게 올렸다. 1600px로
           고정했더니 1920px보다 넓은 모니터(2K/울트라와이드 등)에서는
           여전히 오른쪽에 빈 여백이 남는다는 지적(2026-08-14 두 번째) —
           고정 상한을 없애고 사이드바를 뺀 나머지 폭을 그대로 쓰게
           바꿨다. 좌우에는 카드가 화면 끝에 바로 붙지 않도록 최소한의
           여백만 padding으로 남긴다. */
        .block-container {
            padding-top: 2.5rem; padding-bottom: 3rem;
            padding-left: 2rem; padding-right: 2rem;
            max-width: 100%;
        }

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

        /* 처음엔 본문도 사이드바와 같은 옅은 톤(#F8FAFC)을 줘서 흰 카드가
           그 위에 "떠 있는" 느낌을 냈는데(2026-08-12), "왼쪽(사이드바)은
           색 있게, 오른쪽(본문)은 흰색으로 나누자"는 요청(2026-08-13)을
           받아 본문은 순백으로 바꿨다 — 카드는 배경 대비가 아니라 자체
           테두리(border)·그림자(box-shadow)로 구분되므로 흰 배경이어도
           카드 경계가 여전히 보인다. 실측으로 확인한 메인 컨텐츠 전용
           testid(stMain, 사이드바와 분리된 것)만 건드려서 사이드바 자체
           배경색은 그대로 둔다. */
        [data-testid="stMain"] { background-color: #FFFFFF; }
        /* stMain은 내부적으로 flex-column + align-items:center라 카드
           (block-container, max-width 1100px)가 남는 공간 한가운데로
           밀려서 사이드바 바로 옆에 큰 여백이 생긴다 — 넓은 화면(1920px)
           에서 실측하니 좌우로 260px씩 붕 떠 있었다. "왼쪽으로, 사이드바
           옆에 붙게" 지적(2026-08-12)에 맞춰 좌측 정렬로 바꾼다. */
        [data-testid="stMain"] { align-items: flex-start !important; }
        /* 페이지 최상단 헤더 바(햄버거·Deploy 자리)를 본문과 같은 톤으로
           맞춰 이어지게 한다(2026-08-12) — 본문이 F8FAFC였다가 흰색으로
           바뀌면서(2026-08-13) 헤더도 같이 흰색으로 맞췄다. 안 맞추면
           헤더만 이전 톤(F8FAFC)으로 남아 본문 위에 옅은 띠가 보인다. */
        [data-testid="stHeader"] { background-color: #FFFFFF; }

        /* 요약 검토 카드(expander) — 흰 배경 + 그림자로 옅은 배경 위에 뜬
           "카드"처럼 분리. hover에서 살짝 떠오르게 해 클릭 가능함을 암시. */
        [data-testid="stExpander"] {
            border: 1px solid var(--sky-border) !important; border-radius: 12px !important;
            box-shadow: 0 1px 4px rgba(14, 165, 233, 0.08);
            background-color: #FFFFFF; margin-bottom: 0.6rem;
            transition: box-shadow 0.15s ease, transform 0.15s ease;
        }
        [data-testid="stExpander"]:hover {
            box-shadow: 0 4px 14px rgba(14, 165, 233, 0.14);
        }
        [data-testid="stExpander"] summary {
            font-weight: 600; color: var(--text-main);
        }

        /* 검색 카드 안의 "입력 방식"·"검색 키워드"·"선별할 편수" 같은
           위젯 라벨이 기본 14px라 다른 텍스트에 비해 작아 보인다는
           지적(2026-08-14 네 번째) — 이 카드 안 라벨만 16px로 키운다. */
        .st-key-search_card [data-testid="stWidgetLabel"] p {
            font-size: 1rem !important;
        }

        /* 라디오 그룹(입력 방식 선택 등) — 기본 회색 원형 대신 하늘색 계열로,
           선택된 항목의 라벨을 굵게 해서 지금 뭘 골랐는지 더 잘 드러나게 */
        [data-testid="stRadio"] label { font-weight: 400; }
        [data-testid="stRadio"] label:has(input:checked) {
            font-weight: 700; color: var(--sky-dark);
        }
        [data-testid="stRadio"] [role="radiogroup"] {
            gap: 0.4rem 1.2rem;
        }

        /* 검색 폼·최근 활동·입력 방식 안내 카드에 입체감(그림자)을 준다.
           예전엔 [data-testid="stVerticalBlockBorderWrapper"]를 썼는데
           실측해보니(2026-08-14) Streamlit 1.60에는 이 testid 자체가
           없다 — st.container(border=True)가 지금은 그냥 stVerticalBlock
           에 인라인 테두리만 준다. 이 testid는 카드가 아닌 다른 모든
           stVerticalBlock에도 두루 걸려 있어 개별 선택이 안 되므로,
           카드 3개에 key=를 직접 주고 그 훅(.st-key-*)으로 골라 스타일링
           한다(사이드바 내비 버튼과 같은 패턴, 이미 검증된 방식). */
        /* 테두리(파란)+그림자(파란)를 같이 쓰니 너무 튄다는 지적(2026-08-14)
           — 테두리는 중립 회색으로 낮추고, 그림자도 파란 색조 대신 중립
           슬레이트 톤으로 바꿔 "테두리는 선명하게, 그림자는 은은하게"로
           역할을 나눴다. 안쪽 여백도 0.2rem은 텍스트가 왼쪽 끝에 거의
           붙어 보일 만큼 좁았어서 넉넉하게 올렸다. */
        .st-key-search_card, .st-key-recent_card, .st-key-help_card {
            background-color: #FFFFFF; border-radius: 14px !important;
            border: 1px solid #E4E7EC !important;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06), 0 1px 2px rgba(15, 23, 42, 0.04);
            padding: 1.25rem 1.5rem;
        }

        /* "시작"·"취소" 버튼 — 오른쪽 정렬용 좁은 칸 안에서도 버튼 자체는
           글자 크기만큼만 좁게 그려져 칸 왼쪽에 붙어 있었다("더 늘리고 더
           오른쪽으로" 지적, 2026-08-14 네 번째). CSS로 width:100%를 줘
           봤지만 버튼을 감싼 Streamlit 래퍼(stButton, element-container)
           자체가 fit-content라 퍼센트가 먹지 않았다 — 대신 st.button의
           네이티브 width="stretch" 인자를 써서 래퍼째로 칸을 채운다
           (아래 button() 호출부, key="start_btn"/"hybrid_start_btn"/
           "cancel_btn" — "취소" 버튼도 같은 크기여야 한다는 요청,
           2026-08-19). 이 규칙은 세로 패딩만 키워 버튼을 살짝 더 크게
           보이게 한다. */
        .st-key-start_btn button, .st-key-hybrid_start_btn button, .st-key-cancel_btn button {
            padding-top: 0.6rem; padding-bottom: 0.6rem;
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
        /* 기본 폭(300px)에선 32자로 자른 논문 제목도 종종 두 줄로 넘쳐
           보였다("제목이 한 줄만 차지하게" 요청, 2026-08-13) — Streamlit
           사이드바 폭은 리사이즈 핸들이 넣는 인라인 style="width:300px"라
           !important로만 이긴다. 여전히 200~600px 사이에서 사용자가 드래그로
           더 늘리거나 줄일 수 있다(Streamlit 자체 제약, 그대로 둠). */
        [data-testid="stSidebar"] {
            width: 360px !important;
        }
        /* 브랜드 글자가 너무 작아 잘 안 보인다는 지적(2026-08-12) — 제목·
           부제·아이콘을 함께 키운다(아이콘만 그대로면 균형이 깨져서 같이). */
        [data-testid="stSidebar"] .sidebar-brand {
            padding: 0.4rem 0 1rem 0; font-size: 1.3rem; color: var(--text-main);
            border-bottom: 1px solid var(--sky-border); margin-bottom: 0.2rem;
        }
        [data-testid="stSidebar"] .sidebar-brand-sub {
            font-size: 0.9rem; color: var(--text-muted); font-weight: 400;
        }
        [data-testid="stSidebar"] .sidebar-brand-icon {
            width: 32px; height: 32px; vertical-align: middle; border-radius: 6px;
            margin-right: 3px; position: relative; top: -2px;
        }
        [data-testid="stSidebar"] .sidebar-nav-gap { height: 0.6rem; }
        /* 내비게이션 버튼 — 처음엔 항상 하늘색 채움(type="primary" 고정)
           이었는데, "상시로 말고 커서 올렸을 때만 더 연한 하늘색으로"라는
           지적(2026-08-12)을 받아 기본은 무채색(secondary), hover에서만
           옅은 하늘색이 뜨도록 바꿨다 — 파이썬 쪽은 type="primary" 제거,
           default 배경색은 아래 hover 규칙으로만 준다. 아이콘은 참고
           이미지 스타일을 재현한 인라인 SVG를 data URI로 만들어 버튼
           자체의 background-image로 얹는다 — st.button은 커스텀 이미지
           아이콘을 못 받는다(emoji/Material만 지원). primary가 아니게
           되면서 배경이 흰색 계열이라 아이콘 선 색을 흰색→sky-dark로
           다시 그렸다(흰 배경에 흰 선은 안 보임). 버튼별 고유 클래스
           (.st-key-nav_*)는 실측으로 실제 DOM에서 확인한 훅. */
        [data-testid="stSidebar"] [data-testid="stButton"] button {
            justify-content: flex-start; text-align: left; font-weight: 500;
        }
        .st-key-nav_search button, .st-key-nav_review button {
            padding-left: 2.4rem; background-repeat: no-repeat;
            background-size: 18px 18px; background-position: 14px center;
            transition: background-color 0.12s ease;
        }
        .st-key-nav_search button:hover, .st-key-nav_review button:hover {
            background-color: var(--sky-light); border-color: var(--sky);
        }
        .st-key-nav_search button {
            background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3QgeD0iMiIgeT0iMyIgd2lkdGg9IjE0IiBoZWlnaHQ9IjExIiByeD0iMiIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNyIvPgo8cGF0aCBkPSJNNiA2LjVMNCA4LjVsMiAyTTExIDYuNWwyIDItMiAyIiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS41IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPGNpcmNsZSBjeD0iMTcuNSIgY3k9IjE3LjUiIHI9IjQiIHN0cm9rZT0iIzAyODRDNyIgc3Ryb2tlLXdpZHRoPSIxLjciLz4KPHBhdGggZD0iTTIwLjUgMjAuNUwyMyAyMyIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNyIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPg==");
        }
        .st-key-nav_review button {
            background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTYgMmg5bDQgNHYxNkg2VjJ6IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CjxwYXRoIGQ9Ik0xNSAydjRoNCIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPgo8cGF0aCBkPSJNOSAxMi41aDZNOSAxNmg0IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS41IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggZD0iTTguNSAyMGwxLjggMS44TDE0IDE4IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9zdmc+");
        }
        /* 현황을 숫자만 보여주다가 "어떤 논문인지 안 보인다"는 지적을 받아
           (2026-08-12) 카테고리별 토글 + 실제 논문 목록으로 바꿨다. 사이드
           바 폭이 좁아서 카드 전용 스타일(굵은 테두리·큰 그림자)이 본문
           카드와 똑같으면 답답해 보여 사이드바 안에서만 더 가볍게 조정. */
        [data-testid="stSidebar"] [data-testid="stExpander"] {
            box-shadow: none; margin-bottom: 0.35rem;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] summary {
            font-size: 0.82rem; padding: 0.4rem 0.6rem;
        }
        [data-testid="stSidebar"] .sidebar-item {
            font-size: 0.8rem; color: var(--text-main); padding: 0.22rem 0.1rem;
            overflow-wrap: break-word;
        }

        /* 제목 아래 밑줄 — "이미지처럼 선을 없애고 그 자리에 회색 설명
           문구를 두자"는 요청(2026-08-14)으로 뺐다. 지금은 제목 바로
           아래에 st.caption()으로 부제를 두므로 그 여백만으로 충분히
           구분된다. */
        [data-testid="stAppViewContainer"] .block-container > div:first-child h3:first-of-type {
            margin-bottom: 0.3rem;
        }

        /* "최근 활동" 항목 — 처음엔 제목 아래에 상태·시간을 같이 뒀는데,
           참고 이미지는 제목과 상대시간이 한 줄(제목 왼쪽, 시간 오른쪽)
           이고 상태만 그 아래 별도 줄이다("칸을 늘리면 제목 옆에 시간도
           쓸 수 있다" 요청, 2026-08-14) — 폭을 넓힌 김에 그 배치로 맞췄다.
           이후 폭을 더 넓혔더니 상태만 있는 둘째 줄에 빈 공간이 남는 게
           눈에 띄어("상태를 상대 시간 옆으로 옮기자" 요청, 2026-08-14
           세 번째) 상태·시간을 한 그룹으로 묶어 제목과 같은 줄 오른쪽에
           둔다 — 항목당 한 줄로 줄어든다. 제목이 길면 이 한 줄 안에서
           말줄임(ellipsis)으로 잘리고, 상태·시간 그룹은 줄어들지 않는다. */
        .recent-item { margin-bottom: 0.6rem; line-height: 1.4; }
        .recent-title-row {
            display: flex; justify-content: space-between; align-items: center; gap: 0.6rem;
        }
        .recent-title-row .recent-title {
            flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .recent-meta { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }
        .recent-time { font-size: 0.76rem; color: var(--text-muted); white-space: nowrap; }

        /* 상태 배지 — review_status 3종만 실제로 있어(작성/처리중 같은
           중간 상태는 없음, 2026-08-14) 그 3개만 색을 준다. */
        .status-pill {
            display: inline-block; font-size: 0.75rem; font-weight: 600;
            padding: 0.12rem 0.55rem; border-radius: 999px; white-space: nowrap;
        }
        .status-pill.pending { background: #FEF3C7; color: #92400E; }
        .status-pill.approved { background: #DCFCE7; color: #166534; }
        .status-pill.rejected { background: #FEE2E2; color: #991B1B; }
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
    with server._db() as con:
        row = con.execute(
            "SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    return verify.verify_numbers(summary_text, source)


# 인용한 원문 문장(영어)을 참고용으로 한국어로 보여준다 — "번역 보기" 토글을
# 켰을 때만 호출해서, 평소 화면 펼치기 속도(2026-08-18 이전에 이미 한 번
# 느리다는 지적을 받아 lazy-load로 고친 부분)에 번역 API 왕복을 더하지 않는다.
# st.cache_data는 같은 문장이면 세션이 바뀌어도 재호출 없이 캐시를 쓴다 —
# 같은 논문을 여러 번 펼쳐 봐도 번역은 처음 한 번만 실제로 호출된다.
# 예외를 여기서 삼키지 않는다 — st.cache_data는 예외가 난 호출은 캐싱하지
# 않으므로, 실패(Gemini 일시적 503 등, 실제로 겪음)를 여기서 catch해 None을
# 반환해버리면 그 "실패"가 캐시에 영구히 박제돼 나중에 API가 복구돼도 계속
# 실패로 나온다. 실패 처리는 호출부(catch)에서만 한다.
@st.cache_data(show_spinner=False)
def _translate_cached(text: str) -> str:
    async def _call():
        async with httpx.AsyncClient() as client:
            return await engine.translate_ko(client, text)
    return run_async(_call())


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

# 키워드/ID/제목 검색은 batch_summarize.py를 별도 프로세스로 띄워 무인
# 실행한다(2026-08-19) — ⑦ 코드 재현과 같은 이유. 예전엔 이 세 모드도
# run_async()로 이 스크립트 실행 안에서 동기로 돌았는데, 그러면 "취소"
# 버튼을 화면에 그려도 그 실행이 끝나기 전까진 클릭 자체가 서버에
# 전달되지 않는다(Streamlit이 세션당 한 번에 한 스크립트만 처리해서) —
# "취소가 실제로 눌려야 하잖아" 요청을 지키려면 이 작업이 Streamlit
# 스크립트 실행 밖(별도 프로세스)에 있어야만 한다. PDF 업로드·오픈액세스
# 모드는 배치 스크립트 CLI가 받는 인자(파일 바이트·DOI)가 없어 이 경로를
# 못 타므로 기존 인라인 방식(_run_pdf_upload_and_summarize 등) 그대로 둔다.
#
# 진행 상황은 파일 두 개로 나눠 관리한다 — 쓰는 주체가 프로세스마다
# 다르면 공유 파일 하나로는 경쟁 조건(review_app.py가 초기값을 쓰는
# 도중에 batch_summarize.py가 이미 갱신값을 썼는데 덮어써버리는 등)이
# 생긴다. 파일마다 쓰는 쪽을 하나로 고정하면 그 문제 자체가 없어진다:
#   - META: review_app.py 만 쓴다(pid·입력값, 실행 시작할 때 한 번)
#   - PROGRESS: batch_summarize.py 만 쓴다(--progress-file, 진행될 때마다)
#
# 처음엔 이 셋을 고정 경로 상수로 뒀는데, 실측(2026-08-19)으로 실제
# 사고가 났다 — 사용자가 라이브(8501)에서 실제로 검색을 돌리는 동안
# 개발 중 테스트(다른 포트 8591)에서 새 검색을 시작했더니, 고정 경로를
# 공유해서 META 파일이 덮어써졌다. 그러자 사용자의 원래 작업은 UI
# 추적에서 빠진 채(프로세스 자체는 백그라운드에서 계속 돎) 고아가
# 됐고, 나중에 논문이 갑자기 "최근 활동"에 나타나거나 다른 하나는 영영
# 안 뜨는 것처럼 보였다 — "2개 처리 중인데 1개만 올라오고 이유를 모르
# 겠다"는 지적이 바로 이 충돌의 증상이었다(DB 확인 결과 데이터 자체는
# 안전하게 저장돼 있었음 — 유실은 없었고 추적만 엉켰다). 브라우저 세션
# 마다(다른 탭·다른 포트·다른 사용자 전부 포함) 완전히 분리된 파일을
# 쓰도록 고친다 — session_state에 한 번만 만든 무작위 ID를 파일명에
# 넣는다.
def _search_job_paths() -> tuple[Path, Path, Path]:
    if "_job_session_id" not in st.session_state:
        st.session_state["_job_session_id"] = uuid.uuid4().hex[:12]
    sid = st.session_state["_job_session_id"]
    return (
        server.DATA_DIR / f"search_job_{sid}.meta.json",
        server.DATA_DIR / f"search_job_{sid}.progress.json",
        server.DATA_DIR / f"search_job_{sid}.log",
    )


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
    생존 여부를 확인한다."""
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


def _read_search_job() -> dict | None:
    """지금 실행 중인 배경 작업 정보. 실행 중이 아니면 None.

    "실행 중"의 기준은 META 파일의 존재가 아니라 그 안에 적힌 PID가
    실제로 살아있는가다 — 파일 내용만 믿지 않고 OS에 직접 물어본다
    ("서버는 판단하지 않는다"와 같은 결로, 사실 확인은 항상 실측 기준).
    프로세스가 이미 죽었는데(정상 종료·크래시 둘 다) 파일이 안 지워진
    경우를 여기서 잡아 정리한다."""
    meta_path, progress_path, _log_path = _search_job_paths()
    if not meta_path.exists():
        return None
    try:
        job = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not _pid_alive(job.get("pid", -1)):
        # 스스로 끝났다(취소가 아니다 — 취소는 _cancel_search_job이 파일을
        # 직접 지우니 여기로 안 옴). 지우기 전에 마지막 결과를
        # session_state에 남겨서 render_search_tab이 완료 요약으로 보여줄
        # 수 있게 한다 — "2편 처리 중인데 1편만 올라오고 왜 실패했는지
        # 모르겠다" 지적(2026-08-19): 실패해도 done 카운트는 그냥 올라가
        # 게이지바는 "완료"로 보이지만, 그게 성공인지 실패인지·왜 실패
        # 했는지는 이 결과 없이는 알 방법이 없었다.
        if progress_path.exists():
            try:
                final = json.loads(progress_path.read_text(encoding="utf-8"))
                st.session_state["_search_job_finished_results"] = final.get("results", [])
            except (json.JSONDecodeError, OSError):
                pass
        meta_path.unlink(missing_ok=True)
        progress_path.unlink(missing_ok=True)
        return None
    if progress_path.exists():
        try:
            job.update(json.loads(progress_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # batch_summarize.py가 쓰는 도중일 수 있음 — 다음 폴링에 다시 읽음
    return job


def _launch_search_job(mode: str, value: str, top_n: int) -> None:
    if mode == "keyword":
        args = ["--keyword", value, "--top-n", str(top_n)]
    elif mode == "id":
        args = ["--ids", *value.replace(",", " ").split()]
    else:  # title
        args = ["--title", value]

    meta_path, progress_path, log_path = _search_job_paths()
    server.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        proc = subprocess.Popen(
            [sys.executable, "batch_summarize.py", *args,
             "--progress-file", str(progress_path)],
            cwd=str(Path(__file__).resolve().parent),
            stdout=f, stderr=subprocess.STDOUT,
            start_new_session=True,  # 이 요청 처리가 끝나도 안 죽고, killpg 대상 그룹도 됨
        )
    progress_path.unlink(missing_ok=True)  # 이전 작업의 잔여 파일 제거
    meta_path.write_text(
        json.dumps({
            "pid": proc.pid, "mode": mode, "value": value, "top_n": top_n,
            "started_at": server._now(),
        }),
        encoding="utf-8",
    )


def _cancel_search_job(job: dict) -> None:
    """프로세스 그룹째로 죽이고 상태 파일을 정리한다. start_new_session=True로
    띄웠으므로 pid가 곧 프로세스 그룹 ID다 — killpg로 batch_summarize.py
    본체까지 한 번에 죽는다. SIGTERM으로 죽은 프로세스는 자기 finally
    블록을 못 돌리므로(잡을 새 없이 즉시 종료) 파일 정리는 여기서 대신
    한다. 그때까지 저장된 논문(save_summary가 매 편 끝날 때 커밋)은
    취소해도 그대로 남는다 — 순차 루프 중간에 멈추는 것뿐이라 자연스럽게
    그렇게 된다.

    SIGTERM을 보낸 뒤 반드시 waitpid로 직접 회수한다 — 실측(2026-08-19)
    으로 확인한 함정: 여기서 회수하지 않고 마커 파일만 지우면, 이 PID를
    다시 들여다볼 일이 앞으로 없어서(마커가 없으니 _read_search_job이
    아예 이 pid를 체크 안 함) 좀비(`Zs <defunct>`)가 영영 안 거둬진다.
    batch_summarize.py는 SIGTERM 핸들러를 따로 안 두므로 기본 동작(즉시
    종료)이 걸려 waitpid가 사실상 바로 반환된다 — 블로킹이어도 체감
    지연은 없다."""
    pid = job["pid"]
    try:
        os.killpg(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass
    meta_path, progress_path, _log_path = _search_job_paths()
    meta_path.unlink(missing_ok=True)
    progress_path.unlink(missing_ok=True)


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


def _render_search_job_progress(card, job: dict) -> None:
    """실행 중인 배경 작업의 진행률+취소 버튼을 그린다. 폴링(재실행)은
    여기서 하지 않는다 — render_search_tab() 맨 끝(입력 폼까지 전부 그린
    뒤)에서 한 번만 건다.

    실측(2026-08-19)으로 발견한 Streamlit 함정: st.rerun()으로 스크립트
    실행이 끊기면, "이번 실행에선 여기를 안 그린다"고 조건부로 건너뛴
    위치는 그 이전(끊기지 않고 자연스럽게 끝까지 돈) 실행 때 그 자리에
    있던 내용이 안 지워지고 그대로 남는다 — 취소해도 입력 폼이 예전
    내용 그대로 옆에 계속 떠 있는 버그로 실제로 나타났다(격리된 재현
    테스트로 원인 확정: 위젯 유무를 조건부로 바꾸는 자리는 st.rerun()이
    낀 실행에서 정리가 안 됨, 매 실행 같은 자리에 같은 호출을 하고
    "내용/활성화 여부"만 바꾸면 문제가 없어짐). 그래서 아래 입력 폼은
    실행 중에도 항상 그리되 disabled=로만 잠근다 — 이 함수는 그 폼과
    별개로 진행률 UI만 담당한다."""
    total, done = job.get("total"), job.get("done", 0)
    if total:
        card.progress(done / total, text=f"④ 요약 생성 중 · {done}/{total}편")
    else:
        card.progress(0.0, text="① 검색·선별 중...")
    _cancel_spacer, cancel_col = card.columns([4, 1])
    if cancel_col.button("취소", key="cancel_btn", width="stretch"):
        _cancel_search_job(job)
        st.session_state["_search_job_cancelled_msg"] = True
        st.rerun()

    # 시도한 논문마다(성공이든 실패든) 바로바로 한 줄씩 보여준다 — 게이지바
    # 숫자만으로는 "몇 번째 시도까지 끝났나"만 보이고 그게 성공인지 실패
    # 인지, 실패라면 왜인지 알 수 없었다(2026-08-19 지적: "2개 하고 있다는데
    # 1개만 올라오고, 오류인지 왜인지 확인할 수 있게 해줘야 하지 않아").
    for r in job.get("results", []):
        card.caption(_format_job_result_line(r))


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


def render_search_tab():
    st.subheader("논문 검색 및 요약본 생성")
    # 회색 부제("키워드로 논문을 검색하고...")가 원래 카드 안 맨 위에
    # 있었는데, 참고 이미지는 이 문구가 카드 밖 제목 바로 아래에 있다
    # ("맨 위에 있던 회색 텍스트를 제목 아래로 옮기는 것" 요청,
    # 2026-08-14) — 그 위치로 옮겼다.
    st.caption("키워드로 논문을 검색하고, 요약본을 자동으로 생성합니다.")
    if st.session_state.pop("_search_job_cancelled_msg", False):
        st.info("검색·요약 작업을 취소했습니다. 그때까지 완료된 논문은 저장되어 있습니다.")
    # 배경 작업이 스스로 끝났을 때(취소 아님)의 결과 요약 — _read_search_job이
    # 끝난 걸 감지하며 session_state에 남겨둔 것을 여기서 한 번만 보여준다.
    # "2편 처리 중인데 1편만 올라오고 왜인지 모르겠다" 지적(2026-08-19) —
    # 이제 실패해도 조용히 사라지지 않고 이유까지 여기 남는다.
    finished_results = st.session_state.pop("_search_job_finished_results", None)
    if finished_results is not None:
        ok = sum(1 for r in finished_results if r.get("status") == "done")
        fail = len(finished_results) - ok
        if fail:
            st.warning(f"검색·요약 완료 — 성공 {ok}편, 실패 {fail}편")
        else:
            st.success(f"검색·요약 완료 — {ok}편 저장됨")
        for r in finished_results:
            st.caption(_format_job_result_line(r))
    # 검토 리스트 카드와 같은 "흰 카드가 옅은 배경 위에 떠 있다" 표면
    # 언어를 검색 폼에도 주려고 st.container(border=True)로 감싼다.
    # with 블록으로 감싸면 안의 코드를 전부 재들여쓰기해야 해서 실수
    # 위험이 크다 — 대신 container 객체를 만들어 그 메서드로 위젯을
    # 그리는 방식(card.text_input(...) 등)을 쓰면 기존 로직 구조는
    # 그대로 두고 st. 호출부만 card. 로 바꾸면 된다(2026-08-12).
    # key="search_card"는 CSS에서 이 카드에만 그림자를 주기 위한 훅.
    card = st.container(border=True, key="search_card")
    # 배경에서 이미 도는 작업이 있으면(취소가 실제로 눌리게 하려고
    # 별도 프로세스로 띄운 것, 2026-08-19) 진행률+취소를 먼저 보여준다.
    # 아래 입력 폼은 이때도 계속 그린다(disabled=running으로 잠그기만
    # 함) — 조건부로 아예 안 그리면 Streamlit이 이전 실행 내용을 못
    # 지우는 함정이 있다(_render_search_job_progress 문서 참고). 또한
    # running 중 폼을 완전히 숨기면 같은 작업을 실수로 두 번 시작할
    # 방법이 없어져야 하는데, 잠그는 쪽이 "왜 안 보이지"보다 안전하다.
    job = _read_search_job()
    running = job is not None
    if job is not None:
        _render_search_job_progress(card, job)
    # 참고 이미지(2026-08-14)의 장식 일러스트 — 순수 장식이라 클릭 동작은
    # 없다. 처음엔 카드 맨 위에 이 그림만 있는 별도 줄로 뒀는데, 그 줄에는
    # 그림 하나뿐이라 위쪽에 여백만 덩그러니 남았다("일러스트만 맨 위에
    # 덩그러니 있어서 여백이 심하다" 지적, 2026-08-14 세 번째) — 빈 줄을
    # 없애고 "입력 방식" 라디오 행 오른쪽에 같이 배치한다.
    radio_col, illus_col = card.columns([7, 1])
    mode_label = radio_col.radio(
        "입력 방식",
        ["키워드 검색", "저장된 논문 재검색 (한글 가능)", "논문 ID 직접 지정", "제목으로 검색",
         "PDF 업로드", "DOI/URL (오픈액세스)"],
        horizontal=True,
        disabled=running,
    )
    illus_col.markdown(
        f'<img src="{_SEARCH_ILLUSTRATION}" style="width:100%;max-width:52px;'
        'display:block;margin-left:auto;margin-top:1.6rem;"/>',
        unsafe_allow_html=True,
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
    # hybrid 모드는 원래 여기서 바로 return 했었는데, 그러면 아래 "최근
    # 활동/입력 방식 안내" 카드까지 같이 건너뛰어 "저장된 논문 재검색"을
    # 고르면 그 두 카드가 안 보이는 문제가 있었다(2026-08-12, 사용자가
    # 실제로 이 모드를 골라보고 발견) — return을 없애고 if/else로 바꿔
    # 두 갈래 다 아래 카드 렌더링까지 도달하게 했다. 버튼 라벨도 다른
    # 모드와 다르게 "🔎 검색"이라 따로 놀았던 것("시작"으로 안 바뀌어
    # 있다는 지적)까지 "시작" + type="primary"로 맞췄다.
    if mode == "hybrid":
        card.caption(
            "이미 저장된 논문들 안에서 다시 찾는다(BM25+임베딩) — 새로 수집·요약하지 않음. "
            "한글 질의도 지원(임베딩이 다국어)."
        )
        hybrid_query = card.text_input(
            "검색어 (한글/영어 모두 가능)", placeholder="예: 온디바이스 AI / on-device AI",
            disabled=running,
        )
        hybrid_top_k = card.number_input("표시할 편수", min_value=1, max_value=20, value=5, disabled=running)
        # "시작" 버튼이 왼쪽에 붙어 있던 걸 오른쪽 정렬로 바꿔 달라는
        # 요청(2026-08-14 세 번째) — 넓은 스페이서 칸 + 좁은 버튼 칸으로
        # 나눠 버튼만 오른쪽 끝에 오게 한다. 그런데 버튼 칸 안에서도 버튼
        # 자체는 글자 크기만큼만 좁게 그려져 칸 왼쪽에 붙고 칸 오른쪽엔
        # 빈 공간이 남았다("더 늘리고 더 오른쪽으로" 지적, 2026-08-14
        # 네 번째) — key=로 훅을 걸어 버튼이 칸 폭을 꽉 채우게(width:100%)
        # CSS로 늘렸다. 칸을 다 채우면 자동으로 칸의 오른쪽 끝(=카드
        # 오른쪽 끝)까지 붙는다.
        _hybrid_btn_spacer, hybrid_btn_col = card.columns([5, 1])
        if hybrid_btn_col.button(
            "시작", type="primary", disabled=running or not hybrid_query,
            key="hybrid_start_btn", width="stretch",
        ):
            result = json.loads(
                run_async(
                    server.hybrid_search_local_papers(
                        server.HybridSearchInput(query=hybrid_query, top_k=hybrid_top_k)
                    )
                )
            )
            if not result["papers"]:
                card.info("저장된 논문 중 일치하는 게 없음.")
            else:
                if not result["embeddings_used"]:
                    card.warning("GOOGLE_API_KEY 없음/실패 — BM25(어휘 일치)만 사용됨. 한글 질의는 정확도가 떨어질 수 있음.")
                for p in result["papers"]:
                    card.markdown(
                        f"- **{p['title']}** (`{p['arxiv_id']}`) — "
                        f"BM25 {p['bm25_score']}, 코사인 {p['cosine_score']}, 합산 {p['fused_score']}"
                    )
                card.caption("검토·재요약은 '✅ 요약 검토' 탭에서.")
    else:
        top_n = 3
        uploaded_file = None
        pdf_title = ""
        if mode == "keyword":
            value = card.text_input(
                "검색 키워드", placeholder="예: LoRA fine-tuning summarization", disabled=running,
            )
            card.caption("⚠️ 외부 API(arXiv/Semantic Scholar) 자체 검색이라 영문 키워드 권장. "
                         "이미 저장된 논문에서 한글로 다시 찾으려면 '저장된 논문 재검색' 선택.")
            top_n = card.number_input("선별할 편수", min_value=1, max_value=10, value=3, disabled=running)
        elif mode == "id":
            value = card.text_input(
                "arXiv ID (공백/쉼표로 여러 개 가능)", placeholder="예: 2505.13033 2405.15793",
                disabled=running,
            )
        elif mode == "title":
            value = card.text_input("논문 제목", placeholder="예: TSPulse", disabled=running)
        elif mode == "pdf":
            card.caption("arXiv 밖 논문(저널·컨퍼런스) — 이미 기관 구독 등으로 합법적으로 접근 가능한 PDF만 올릴 것")
            uploaded_file = card.file_uploader("PDF 파일", type="pdf", disabled=running)
            # 제목을 직접 타이핑해야 하는 게 번거롭다는 지적(2026-08-12,
            # "PDF 제목 따라 입력하면 되잖아") — 필수 입력을 없애고 PDF
            # 메타데이터·본문에서 자동 추정하도록 바꿨다(server.
            # ingest_local_pdf 참고). 잘못 추정됐을 때 고칠 수 있게 입력창
            # 자체는 남겨 둔다.
            pdf_title = card.text_input(
                "제목 (선택 — 비우면 PDF에서 자동 추출)", placeholder="논문 제목", disabled=running,
            )
            value = "ok" if uploaded_file else ""
        else:  # oa
            card.caption("DOI를 넣으면 Unpaywall로 오픈액세스 PDF를 자동으로 찾는다. PDF 직접 링크도 가능.")
            value = card.text_input(
                "DOI 또는 PDF 직접 링크", placeholder="예: 10.1038/s41467-023-xxxxx-x", disabled=running,
            )
            # DOI 경로면 Unpaywall 응답에 제목이 이미 들어 있어(2026-08-12)
            # 대부분 자동으로 채워진다 — 그래도 안 채워지면 PDF 폴백 체인이
            # 이어받는다.
            pdf_title = card.text_input("제목 (선택 — 비우면 자동으로 찾음)", disabled=running)

        # 오른쪽 정렬 — 위 hybrid 분기와 같은 스페이서+버튼 칸 나누기.
        # [5,1] → [4,1]로 살짝 넓혀 버튼을 조금 더 왼쪽으로("취소" 버튼도
        # 같은 자리·같은 크기로 뜰 수 있게 여유를 준다, 2026-08-19 요청).
        # key="start_btn"로 CSS 훅을 걸어 버튼을 칸 폭만큼 늘린다(위
        # hybrid_start_btn과 같은 이유).
        _start_btn_spacer, start_btn_col = card.columns([4, 1])
        if start_btn_col.button(
            "시작", type="primary", disabled=running or not value,
            key="start_btn", width="stretch",
        ):
            if mode in ("keyword", "id", "title"):
                # 별도 프로세스로 띄우고 즉시 재실행 — 다음 실행부터는
                # _render_search_job_progress가 진행률+취소를 같이 보여준다
                # (위, running=True). PDF·오픈액세스는 CLI가 못 받는 인자
                # (파일 바이트·DOI)라 기존 인라인 방식 그대로.
                _launch_search_job(mode, value, top_n)
                st.rerun()
            else:
                status_box = card.status("진행 중...", expanded=True)
                if mode == "pdf":
                    done = run_async(
                        _run_pdf_upload_and_summarize(uploaded_file.getvalue(), pdf_title, status_box)
                    )
                else:  # oa
                    done = run_async(_run_open_access_and_summarize(value, pdf_title, status_box))
                if done:
                    status_box.update(label=f"완료 — {len(done)}편 처리됨", state="complete")
                    card.success(f"{len(done)}편 저장 완료. '요약 검토' 탭에서 확인하세요: {done}")
                else:
                    status_box.update(label="처리된 논문 없음", state="error")

    # 검색 폼 아래가 빈 흰 공간으로 휑하다는 지적(2026-08-12) — 장식용
    # 채우기가 아니라 실제로 쓸모 있는 두 카드로 채운다: "최근 활동"은
    # _fetch_review_rows가 이미 created_at 기준 내림차순으로 주는 걸 앞
    # N개만 잘라 쓰고(사이드바 카테고리 목록과 달리 "방금 뭘 했나"를 시간
    # 순으로 보여준다는 점에서 안 겹침), "입력 방식 안내"는 위 라디오 6개
    # 선택지 각각이 언제 쓰는 건지 짧게 설명하는 정적 텍스트다.
    col_recent, col_help = st.columns(2)

    # 최근 활동(항목 최대 6개, 2줄씩)이 입력 방식 안내(항목 6개, 1줄씩)보다
    # 원래 자연스럽게 더 길어서 카드 높이가 서로 달랐다("하나는 크고
    # 하나는 작고 이상하다" 지적, 2026-08-14) — st.container가 지원하는
    # height=로 두 카드 높이를 고정값으로 맞춘다(내용이 넘치면 카드
    # 안에서만 스크롤, 카드 자체 높이는 항상 동일).
    _CARD_HEIGHT = 300
    recent_card = col_recent.container(border=True, height=_CARD_HEIGHT, key="recent_card")
    recent_card.markdown("**🕓 최근 활동**")
    recent_rows = _fetch_review_rows(True)[:6]
    if not recent_rows:
        recent_card.caption("아직 저장된 논문 없음")
    else:
        # 절대 시각("2026-08-12 01:17")보다 "3분 전"이 한눈에 더 잘 들어와서
        # _relative_time으로 바꿨다(고정 문구 아니라 실제 타임스탬프 계산).
        # 처음엔 제목을 36자로 잘랐는데, 폭을 넓히면서("여백 채우기" 요청,
        # 2026-08-14) 카드도 넓어져 그만큼 자를 필요가 줄었다 — 70자로
        # 늘려 대부분의 논문 제목이 안 잘리게 했다(완전히 없애지 않은 건
        # 극단적으로 긴 제목이 한 줄을 넘겨 레이아웃을 깨는 걸 막기 위한
        # 안전판). 제목 한 줄, 오른쪽에 상태 배지·상대시간을 묶어 둔다
        # ("상태를 상대 시간 옆으로" 요청, 2026-08-14 세 번째). 제목을
        # 굵게 하니 상태 배지와 같이 있을 때 너무 튄다는 지적(2026-08-14
        # 네 번째) — 일반 글씨로 바꾼다.
        for r in recent_rows:
            status = r["review_status"] or "pending"
            dot = _STATUS_EMOJI.get(status, "🟡")
            label = _STATUS_LABEL.get(status, "검토 대기")
            rel = _relative_time(r["created_at"] or "")
            recent_card.markdown(
                f"<div class='recent-item'>"
                f"<div class='recent-title-row'>"
                f"<span class='recent-title'>{dot} {r['title'][:70]}</span>"
                f"<span class='recent-meta'>"
                f"<span class='status-pill {status}'>{label}</span>"
                f"<span class='recent-time'>{rel}</span>"
                f"</span></div></div>",
                unsafe_allow_html=True,
            )

    help_card = col_help.container(border=True, height=_CARD_HEIGHT, key="help_card")
    help_card.markdown("**ℹ️ 입력 방식 안내**")
    help_card.caption("**키워드 검색** — arXiv·Semantic Scholar에서 새로 찾음 (영문 권장)")
    help_card.caption("**저장된 논문 재검색** — 이미 모아둔 논문에서 한글로 다시 찾음")
    help_card.caption("**논문 ID 직접 지정** — arXiv ID를 이미 알고 있을 때")
    help_card.caption("**제목으로 검색** — 제목 일부만 알 때")
    help_card.caption("**PDF 업로드** — 접근 권한 있는 PDF를 직접 첨부")
    help_card.caption("**DOI/URL** — DOI로 오픈액세스 PDF를 자동으로 찾음")

    # 폴링(2초 간격 재실행)은 여기, 함수 맨 끝에서만 건다 — 입력 폼과
    # 아래 카드까지 전부 그린 뒤라 이 실행이 만든 화면 내용은 "끊기지
    # 않고 자연스럽게 끝까지 돈 실행"과 똑같다(_render_search_job_progress
    # 문서의 실측 결과 참고). st.rerun()이 여기서 실행을 끊어도 그 앞에서
    # 이미 전부 그렸으므로 스테일 DOM이 생기지 않는다.
    if running:
        time.sleep(2)
        st.rerun()


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
    return "코드 재현을 백그라운드에서 시작함"


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
        st.caption("코드 재현: 🔵 진행 중... (Docker로 후보 저장소 설치·실행 시도 — 새로고침해서 확인)")
        return
    if rows:
        best = next((r for r in rows if r["success"]), rows[0])
        if best["success"]:
            st.caption(f"코드 재현: 🟢 성공 ({best['repo_url']}, {best['attempt']}차 시도)")
            _render_code_browser(arxiv_id, best["local_path"])
        else:
            st.caption(
                f"코드 재현: 🔴 전부 실패 (시도 {len(rows)}건, 마지막 단계: {best['stage']}) "
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
        st.caption("코드 재현: 완료됐지만 결과를 못 읽음 — 로그 파일 확인 필요")
        return
    if outcome.get("success"):
        st.caption("코드 재현: 🟢 성공")
    else:
        # "시도 못함"이라고 쓰면 검색 자체가 안 된 것처럼 읽혀서 오해를 살 수
        # 있다는 지적(2026-08-12) — 실제로는 검색은 끝났고 후보가 0개였던
        # 것이므로 "검색 완료·후보 없음"으로 명확히 구분해서 쓴다.
        reason = outcome.get("reason", "저장소 후보를 찾지 못함")
        st.caption(
            f"코드 재현: 🟠 검색 완료 · 후보 없음 — {reason} "
            "(이 논문엔 공개된 관련 코드 저장소가 없을 수 있음, 설치·실행은 시도 안 함)"
        )


# 승인/반려/대기 상태를 색상 원으로 통일해 표시하는 데 검색 탭(최근 활동)과
# 검토 탭 둘 다에서 쓴다 — 원래 render_review_tab 안에 지역 변수로만 있었는데
# 검색 탭에도 같은 표시가 필요해져(2026-08-12) 모듈 상수로 뺐다.
_STATUS_EMOJI = {"pending": "🟡", "approved": "🟢", "rejected": "🔴", None: "🟡"}
# 참고 이미지(2026-08-14)의 "작성/처리중/검증완료/승인됨/분석중" 같은 세분화된
# 상태 라벨은 실제 DB에 없는 중간 상태(예: "처리중")까지 지어내는 셈이라
# 그대로 베끼지 않았다 — review_status가 실제로 갖는 값(pending/approved/
# rejected) 그대로만 라벨을 붙인다. 저장된 시점엔 이미 요약·검증까지 끝난
# 상태라 "검토 대기"가 정확한 표현이다.
_STATUS_LABEL = {"pending": "검토 대기", "approved": "승인됨", "rejected": "반려됨", None: "검토 대기"}


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


# ---------------------------------------------------------------- 탭 ②: 요약 검토


def render_review_tab():
    st.subheader("요약본")
    # 예전엔 체크박스로 "전체 보기"를 켜야 승인·반려된 것까지 보였다 —
    # 매번 체크해야 하는 게 번거롭다는 지적(2026-08-12)을 받아 항상 전체를
    # 보여주는 것으로 기본값을 바꿨다. _fetch_review_rows(show_all=False)
    # 경로(대기중만 필터)는 다른 데서 안 쓰여서 죽은 코드가 아니라 그냥
    # 이 화면에서 옵션 자체를 없앤 것 — 함수 시그니처는 그대로 둔다.
    rows = _fetch_review_rows(True)

    if not rows:
        st.info("저장된 요약이 없습니다.")
        return

    # 상단 검색·상태 필터(참고 이미지, 2026-08-14) — 장식이 아니라 실제로
    # 목록을 걸러낸다. 상태 값은 _STATUS_LABEL에 실제로 있는 3개뿐(작성·
    # 처리중 같은 중간 상태는 지어내지 않음).
    col_q, col_status = st.columns([3, 1])
    query = col_q.text_input(
        "검색", placeholder="🔍 제목, arXiv ID로 검색", label_visibility="collapsed",
    )
    status_choice = col_status.selectbox(
        "상태", ["전체 상태", "검토 대기", "승인됨", "반려됨"], label_visibility="collapsed",
    )
    if query:
        q = query.strip().lower()
        rows = [r for r in rows if q in r["title"].lower() or q in r["arxiv_id"].lower()]
    if status_choice != "전체 상태":
        label_to_status = {"검토 대기": "pending", "승인됨": "approved", "반려됨": "rejected"}
        target = label_to_status[status_choice]
        rows = [r for r in rows if (r["review_status"] or "pending") == target]

    if not rows:
        st.caption("검색·필터 조건에 맞는 요약이 없습니다.")
        return

    for row in rows:
        arxiv_id = row["arxiv_id"]
        status = row["review_status"] or "pending"
        emoji = _STATUS_EMOJI.get(status, "🟡")
        # "최근 활동"처럼 여기도 제목 옆에 상태·상대시간을 보여 달라는
        # 요청(2026-08-14 세 번째) — 단, st.expander의 label은 순수
        # 텍스트만 받고 HTML을 못 넣어(색 있는 배지 불가) 그 카드에서 쓴
        # status-pill 대신 이모지 + 일반 텍스트로 같은 정보를 붙인다.
        label = _STATUS_LABEL.get(status, "검토 대기")
        rel = _relative_time(row["created_at"] or "")
        header = (
            f"{emoji} {row['title']} ({arxiv_id}) — "
            f"{row['numbers_matched']}/{row['numbers_total']} · {label} · {rel}"
        )
        exp_key = f"exp_{arxiv_id}"

        # st.expander는 접혀 있어도 with 블록 안 파이썬 코드가 매 재실행마다
        # 그대로 실행된다(화면에 안 보일 뿐) — 그래서 47편 전부에 대해
        # _verify_detail·재현 상태 조회·마크다운 정리가 매번 다 돌아 "요약
        # 검토" 탭 전환이 눈에 띄게 느렸다(2026-08-14 지적: "저 아래 것들이
        # 사라지는데 오래걸리네"). key=를 주면 펼침 상태가 session_state에
        # 그대로 들어오므로, 접힌 항목은 무거운 계산 자체를 건너뛴다.
        # on_change 기본값('ignore')이면 펼치기·접기가 순수 클라이언트단
        # 동작이라 재실행이 안 일어나서, session_state[exp_key]가 이번 클릭을
        # 못 따라잡고 한 박자 늦게 반영됐다(실측: 펼쳤는데 "펼치면 표시됩니다"
        # 안내문만 보임) — 'rerun'으로 줘서 펼침 상태가 바뀔 때마다 즉시
        # 재실행되게 한다.
        with st.expander(header, key=exp_key, on_change="rerun"):
            if not st.session_state.get(exp_key, False):
                st.caption("펼치면 세부 내용이 표시됩니다.")
                continue

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
                # 원문 인용문이 영어라 매번 언어를 오가며 대조해야 한다는 지적
                # (2026-08-18) — 번역은 기본 꺼둔다(토글). 검증 근거는 항상
                # 영어 원문이고 번역은 참고용일 뿐이라, 켰을 때도 원문과
                # 나란히만 보여준다(번역으로 대체하지 않음).
                show_translation = st.toggle(
                    "🌐 인용 문장 번역 보기 (참고용, 원문과 함께 표시)",
                    key=f"transtoggle_{arxiv_id}",
                )
                for c in report.unmatched:
                    if c.grounded:
                        # [S번호]로 인용한 문장까지 찾아봤지만 그 안에 없었다 — 지어냈거나
                        # 엉뚱한 문장을 인용했을 가능성. 실제로 조회한 문장을 보여준다.
                        st.markdown(f"- **`{c.token}`** — 요약 문맥: _{c.context}_")
                        if not c.cited_text:
                            st.caption("(인용한 문장 번호가 원문 범위 밖 — 지어낸 번호일 수 있음)")
                            continue
                        st.caption(f"🔎 인용한 [S{c.sentence_id:04d}] 문장(±1):")
                        # sentence_lookup(sentence_grounding.py)은 ±1 문장을 공백
                        # 하나로 이어붙여 돌려준다 — verify.py 검증(숫자 대조)에는
                        # 그걸로 충분하지만, 그 사이에 PDF 표가 뭉개져 끼어 있으면
                        # (실측: Distilling 논문 Table 1이 "4 System Test Frame
                        # Accuracy WER Baseline 58.9% ..." 식으로 문장 사이에 그대로
                        # 끼어듦) 어디까지가 진짜 문장이고 어디가 표 잔해인지 안
                        # 보여서 3문장이 한 덩어리로 안 읽힌다는 지적(2026-08-19)
                        # — 같은 문장 분리기로 다시 나눠 문장별 줄로 보여준다.
                        for sent in sentence_grounding.segment_sentences(c.cited_text):
                            st.markdown(f"> {sent}")
                            if show_translation:
                                try:
                                    translated = _translate_cached(sent)
                                    st.markdown(f"> 🌐 _{translated}_")
                                except httpx.HTTPStatusError as e:
                                    # 429(분당 20회 무료 한도 초과)와 503(일시 과부하)은
                                    # 원인이 다르다 — 429는 재시도해도 한동안 확실히
                                    # 또 막히므로(실측: "Please retry in 29s" 같은 구체적
                                    # 대기시간이 옴), "일시적 오류"로 뭉뚱그리지 않고
                                    # 정확히 알려준다(2026-08-18, 리뷰 화면에서 번역이
                                    # 계속 다 실패한다는 지적 받고 원인 확인).
                                    if e.response.status_code == 429:
                                        st.caption("🌐 번역 실패 — 무료 API 분당 요청 한도 초과, 1분 뒤 재시도")
                                    else:
                                        st.caption("🌐 번역 실패(일시적 오류일 수 있음 — 다시 펼치면 재시도)")
                                except Exception:  # noqa: BLE001
                                    st.caption("🌐 번역 실패(일시적 오류일 수 있음 — 다시 펼치면 재시도)")
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

            # 예전엔 반려 사유 입력창이 "반려" 버튼 칸(col2) 안에만 있어서
            # 그 칸만 위로 한 줄 더 밀리고, 세 버튼이 승인/재생성은 위쪽 줄에
            # 반려만 아래쪽 줄에 있는 것처럼 어긋나 보였다(2026-08-12, 사용자
            # 스크린샷으로 지적: "위치가 중구난방"). 입력창을 버튼 행 위로
            # 통째로 빼서 버튼 3개가 같은 줄에서 승인·반려·재생성 순서로
            # 나란히 정렬되게 했다.
            #
            # st.columns(3)은 셋을 화면 전체 폭에 균등 배분해 버튼 사이가
            # 화면 폭만큼 벌어져 보였다("서로 너무 떨어져있다" 지적, 2026-08-12
            # 후속) — 버튼 폭만큼만 좁은 칸 3개를 만들고 남는 공간은 오른쪽
            # 여백 칸 하나로 몰아, 버튼들이 왼쪽에 붙어 서로 가깝게 보이도록
            # 바꿨다.
            reason = st.text_input("반려 사유 (선택)", key=f"reason_{arxiv_id}")
            col1, col2, col3, _spacer = st.columns([1, 1, 1, 5])
            with col1:
                if st.button("✅ 승인", key=f"approve_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "approved")
                    msg = _launch_reproduce_background(arxiv_id)
                    st.toast(f"⑥→⑦ {msg}")
                    st.rerun()
            with col2:
                if st.button("❌ 반려", key=f"reject_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "rejected", note=reason)
                    st.rerun()
            with col3:
                if st.button("🔄 재생성", key=f"regen_{arxiv_id}"):
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


def _fetch_sidebar_lists() -> dict:
    """사이드바 '현황' — 숫자만 있으면 어떤 논문인지 안 보인다는 지적을
    받아(2026-08-12), 카테고리별 실제 논문 목록을 반환한다. 카테고리는
    서로 배타적인 버킷이 아니라 "이 조건에 해당하는 논문이 뭐가 있나"를
    보여주는 4개의 서로 다른 렌즈다 — 예를 들어 승인됨이면서 동시에
    재현 성공인 논문은 두 목록에 다 뜬다(정상 — 강제로 하나만 고르게
    나누면 오히려 정보를 잃는다)."""
    with server._db() as con:
        pending = con.execute(
            "SELECT p.arxiv_id, p.title FROM papers p JOIN summaries s ON p.arxiv_id=s.arxiv_id "
            "WHERE s.review_status='pending' OR s.review_status IS NULL ORDER BY p.title"
        ).fetchall()
        approved = con.execute(
            "SELECT p.arxiv_id, p.title FROM papers p JOIN summaries s ON p.arxiv_id=s.arxiv_id "
            "WHERE s.review_status='approved' ORDER BY p.title"
        ).fetchall()
        repro_ok = con.execute(
            "SELECT DISTINCT p.arxiv_id, p.title FROM papers p "
            "JOIN repro_results r ON p.arxiv_id=r.arxiv_id "
            "WHERE r.success=1 ORDER BY p.title"
        ).fetchall()
        repro_attempted = {
            r["arxiv_id"] for r in con.execute("SELECT DISTINCT arxiv_id FROM repro_results").fetchall()
        }

    # "코드 없음" — repro_results에 행이 아예 없는 승인 논문 중 로그 파일이
    # "저장소 후보 없음"으로 끝난 것만(_render_repro_status 폴백과 동일 이유
    # — code_finder가 후보를 하나도 못 찾으면 save_repro_result가 안 불린다).
    # 후보는 있었는데 설치·실행에 실패한 경우는 여기 안 넣는다 — 그건
    # repro_results에 실패 행으로 남아 "코드가 없다"와는 다른 사실이라서다.
    no_code = []
    for r in approved:
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
        "pending": [dict(r) for r in pending],
        "approved": [dict(r) for r in approved],
        "repro_ok": [dict(r) for r in repro_ok],
        "no_code": no_code,
    }


def _render_sidebar_category(label: str, items: list[dict], dot: str) -> None:
    with st.expander(f"{label} ({len(items)})", expanded=False):
        if not items:
            st.caption("없음")
            return
        for it in items:
            title = it["title"] or "(제목 없음)"
            short = title if len(title) <= 32 else title[:32] + "…"
            st.markdown(
                f"<div class='sidebar-item'>{dot} {short}</div>",
                unsafe_allow_html=True,
            )


# 사이드바 브랜드 아이콘 — 사용자가 새로 준 참고 이미지(파란 그라디언트
# 배지 안에 arXiv 논문·돋보기 모티프)를 재현한 인라인 SVG(2026-08-14,
# 이전의 남색+금색 네트워크 아이콘을 교체). 3D 렌더링을 그대로 옮길 순
# 없어서(작은 사이드바 아이콘 크기에선 안 보임) "논문 + 돋보기"라는
# 핵심 모티프만 단순한 2D 플랫 아이콘으로 재해석했다.
_BRAND_ICON = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAzMiAzMiIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4"
    "KPGRlZnM+CjxsaW5lYXJHcmFkaWVudCBpZD0iZyIgeDE9IjAiIHkxPSIwIiB4Mj0iMzIiIHkyPSIzMiIgZ3JhZGllbnRVbml0cz0idXNlclNwYW"
    "NlT25Vc2UiPgo8c3RvcCBvZmZzZXQ9IjAiIHN0b3AtY29sb3I9IiM1QjhERUYiLz4KPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjMUUzQ"
    "ThBIi8+CjwvbGluZWFyR3JhZGllbnQ+CjwvZGVmcz4KPHJlY3Qgd2lkdGg9IjMyIiBoZWlnaHQ9IjMyIiByeD0iOCIgZmlsbD0idXJsKCNnKSIv"
    "Pgo8cmVjdCB4PSI3IiB5PSI2IiB3aWR0aD0iMTIiIGhlaWdodD0iMTYiIHJ4PSIxLjUiIGZpbGw9IndoaXRlIiBmaWxsLW9wYWNpdHk9IjAuOTQ"
    "iLz4KPGxpbmUgeDE9IjkuNSIgeTE9IjEwIiB4Mj0iMTYuNSIgeTI9IjEwIiBzdHJva2U9IiMxRTNBOEEiIHN0cm9rZS13aWR0aD0iMS4xIiBzdH"
    "Jva2UtbGluZWNhcD0icm91bmQiLz4KPGxpbmUgeDE9IjkuNSIgeTE9IjEzIiB4Mj0iMTYuNSIgeTI9IjEzIiBzdHJva2U9IiMxRTNBOEEiIHN0c"
    "m9rZS13aWR0aD0iMS4xIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPGxpbmUgeDE9IjkuNSIgeTE9IjE2IiB4Mj0iMTQiIHkyPSIxNiIgc3Ry"
    "b2tlPSIjMUUzQThBIiBzdHJva2Utd2lkdGg9IjEuMSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CjxjaXJjbGUgY3g9IjIwLjUiIGN5PSIxOS4"
    "1IiByPSI0LjMiIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIvPgo8bGluZSB4MT0iMjMuNiIgeTE9IjIyLjYiIH"
    "gyPSIyNyIgeTI9IjI2IiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIuMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPg=="
)

# 검색 카드 우측 장식 일러스트 — 참고 이미지(2026-08-14)의 "문서+돋보기+
# 추세선" 모티프를 순수 장식용 인라인 SVG로 재해석. 클릭 동작 없음.
_SEARCH_ILLUSTRATION = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHZpZXdCb3g9IjAgMCA4MCA4MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4"
    "KPGRlZnM+CjxsaW5lYXJHcmFkaWVudCBpZD0iZzIiIHgxPSIwIiB5MT0iMCIgeDI9IjgwIiB5Mj0iODAiIGdyYWRpZW50VW5pdHM9InVzZXJTcG"
    "FjZU9uVXNlIj4KPHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjRUFGMkZGIi8+CjxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0iI0Q2R"
    "TRGRiIvPgo8L2xpbmVhckdyYWRpZW50Pgo8L2RlZnM+CjxyZWN0IHg9IjIiIHk9IjIiIHdpZHRoPSI3NiIgaGVpZ2h0PSI3NiIgcng9IjE4IiBm"
    "aWxsPSJ1cmwoI2cyKSIvPgo8cmVjdCB4PSIxNiIgeT0iMTYiIHdpZHRoPSIzNCIgaGVpZ2h0PSI0NiIgcng9IjMiIGZpbGw9IndoaXRlIiBzdHJ"
    "va2U9IiNCOUNDRUYiIHN0cm9rZS13aWR0aD0iMS4yIi8+CjxsaW5lIHgxPSIyMiIgeTE9IjI2IiB4Mj0iNDQiIHkyPSIyNiIgc3Ryb2tlPSIjOE"
    "ZBOURFIiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CjxsaW5lIHgxPSIyMiIgeTE9IjMyIiB4Mj0iNDQiIHkyP"
    "SIzMiIgc3Ryb2tlPSIjOEZBOURFIiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CjxsaW5lIHgxPSIyMiIgeTE9"
    "IjM4IiB4Mj0iMzgiIHkyPSIzOCIgc3Ryb2tlPSIjOEZBOURFIiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjx"
    "wb2x5bGluZSBwb2ludHM9IjIyLDUyIDI4LDQ2IDMzLDQ5IDQwLDQyIiBmaWxsPSJub25lIiBzdHJva2U9IiM1QjhERUYiIHN0cm9rZS13aWR0a"
    "D0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPGNpcmNsZSBjeD0iNTQiIGN5PSI1MiIgcj0"
    "iMTMiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzFFM0E4QSIgc3Ryb2tlLXdpZHRoPSIzIi8+CjxsaW5lIHgxPSI2MyIgeTE9IjYxIiB4Mj0iNzAiIH"
    "kyPSI2OCIgc3Ryb2tlPSIjMUUzQThBIiBzdHJva2Utd2lkdGg9IjMuNCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPg=="
)

if "nav_page" not in st.session_state:
    st.session_state.nav_page = "search"

with st.sidebar:
    st.markdown(
        f'<div class="sidebar-brand"><img src="{_BRAND_ICON}" class="sidebar-brand-icon"/> '
        '<b>논문 검색·분석</b> '
        '<span class="sidebar-brand-sub">에이전트 하네스</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    # hover는 "상시 말고 커서 올렸을 때만"이라는 지적(2026-08-12)대로 유지.
    # 다만 "지금 보고 있는 페이지가 안 드러난다"는 후속 지적(2026-08-14)을
    # 받아, 현재 페이지 버튼만 type="primary"(채움)로 켠다 — hover 규칙과
    # 안 부딪힌다: primary는 자체 배경색이 있어서 secondary용 hover 규칙
    # (.st-key-nav_*:hover)이 안 먹어도 이미 파란색이라 상관없다.
    if st.button(
        "검색·요약 생성", key="nav_search", use_container_width=True,
        type="primary" if st.session_state.nav_page == "search" else "secondary",
    ):
        st.session_state.nav_page = "search"
        st.rerun()
    if st.button(
        "요약 검토", key="nav_review", use_container_width=True,
        type="primary" if st.session_state.nav_page == "review" else "secondary",
    ):
        st.session_state.nav_page = "review"
        st.rerun()

    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    st.caption("현황")
    lists = _fetch_sidebar_lists()
    _render_sidebar_category("저장된 논문", lists["pending"], "🟡")
    _render_sidebar_category("승인됨", lists["approved"], "🟢")
    _render_sidebar_category("재현 성공", lists["repro_ok"], "🟢")
    _render_sidebar_category("코드 없음", lists["no_code"], "🟠")

    # 검색·요약이 실제로 도는 동안만 나타나는 진행 표시(2026-08-14 요청,
    # 2026-08-19 배경 프로세스 전환에 맞춰 다시 배선) — 예전엔 render_search_tab()
    # 안의 루프가 st.empty() 슬롯을 직접 채우는 방식이었는데, ④가 "취소"를
    # 지원하려고 별도 프로세스로 옮겨가면서 그 루프 자체가 이 스크립트
    # 실행 안에 없다. 대신 _read_search_job()으로 상태 파일을 직접 읽는다
    # — 검토 탭에 가 있어도, 새로고침해도 항상 최신 상태(파일 기반이라
    # 이 스크립트의 한 번의 실행에 갇혀 있지 않음).
    job = _read_search_job()
    if job is not None:
        total, done = job.get("total"), job.get("done", 0)
        if total:
            st.caption(f"🔵 진행 중 · 요약 생성 중... {done}/{total}")
            st.progress(done / total)
        else:
            st.caption("🔵 진행 중 · ① 검색·선별 중...")
            st.progress(0.0)

if st.session_state.nav_page == "search":
    render_search_tab()
else:
    render_review_tab()
