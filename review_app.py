"""⑨ 운영 화면 — 최신 연구 동향 모니터링 에이전트 (Streamlit). 관리자(운영자) 한 사람이 전체 상황을 보는 곳이다.

2026-09-16 개편(사용자 요청): 하네스 시절의 "검색·요약 생성"·"요약 검토" 탭을 없애고, 켜자마자 **운영 현황**이 나오게 했다.
페이지는 셋이다 — 운영 현황(프로필별 키워드·가중치·보낸 메일·반응·변경 이력·에이전트) / 논문 DB(저장된 논문 전체) / 시스템(새벽·주간
실행 기록, cron, DB·백업, 로그). 숫자는 전부 `ops_dashboard.py` 가 만들고 이 파일은 그리기만 한다 — `st.` 을 쓰는 코드와 안 쓰는
코드가 섞이면 Streamlit 없이는 테스트할 수 없다(§8-31).

옛 화면(검색·요약·검토·수동 재현 버튼)은 git 이력과 `review_core.py`(로직)에 남아 있다. 논문 검색·요약은 MCP 서버(`server.py`)와
새벽 스캔이 맡는다.

실행:
    .venv/bin/streamlit run review_app.py
"""

from __future__ import annotations

from pathlib import Path

import httpx
import streamlit as st

import mail_ledger
import ops_dashboard
import research_profile
import run_profile_scan
import server
from review_core import _relative_time, run_async

APP_TITLE = "최신 연구 동향 모니터링 에이전트"
ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title=APP_TITLE, layout="wide", page_icon=":material/monitoring:")


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
        /* 2026-09-16: 메뉴가 운영 현황·논문 DB·시스템 셋으로 바뀌었다. 아이콘이 없는 키는 선택(primary) 때 글자가 비어 보였다
           (실측 스크린샷) — 세 키 모두 같은 규칙과 아이콘을 준다. 선택된 버튼은 흰 글자·흰 아이콘 틀 위에 파란 배경. */
        .st-key-nav_research button, .st-key-nav_papers button, .st-key-nav_system button {
            padding-left: 2.4rem; background-repeat: no-repeat;
            background-size: 18px 18px; background-position: 14px center;
            transition: background-color 0.12s ease;
        }
        /* hover 는 선택 안 된(secondary) 버튼에만 — 선택된 버튼 위에 마우스가 있으면 흰 글자가 연한 배경에 묻혔다(실측). */
        .st-key-nav_research button[data-testid="stBaseButton-secondary"]:hover,
        .st-key-nav_papers button[data-testid="stBaseButton-secondary"]:hover,
        .st-key-nav_system button[data-testid="stBaseButton-secondary"]:hover {
            background-color: var(--sky-light); border-color: var(--sky);
        }
        [data-testid="stSidebar"] [data-testid="stBaseButton-primary"],
        [data-testid="stSidebar"] [data-testid="stBaseButton-primary"] p {
            color: #FFFFFF !important;
        }
        .st-key-nav_research button { background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iOSIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNiIvPgo8Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSI0LjUiIHN0cm9rZT0iIzAyODRDNyIgc3Ryb2tlLXdpZHRoPSIxLjYiLz4KPGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMS4zIiBmaWxsPSIjMDI4NEM3Ii8+CjxwYXRoIGQ9Ik0xMiAxdjNNMTIgMjB2M00xIDEyaDNNMjAgMTJoMyIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPg=="); }
        .st-key-nav_papers button { background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTYgMmg5bDQgNHYxNkg2VjJ6IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CjxwYXRoIGQ9Ik0xNSAydjRoNCIgc3Ryb2tlPSIjMDI4NEM3IiBzdHJva2Utd2lkdGg9IjEuNiIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPgo8cGF0aCBkPSJNOSAxMi41aDZNOSAxNmg0IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS41IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggZD0iTTguNSAyMGwxLjggMS44TDE0IDE4IiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9zdmc+"); }
        .st-key-nav_system button { background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB4PSIzIiB5PSIzIiB3aWR0aD0iMTgiIGhlaWdodD0iNyIgcng9IjIiIHN0cm9rZT0iIzAyODRDNyIgc3Ryb2tlLXdpZHRoPSIxLjYiLz48cmVjdCB4PSIzIiB5PSIxNCIgd2lkdGg9IjE4IiBoZWlnaHQ9IjciIHJ4PSIyIiBzdHJva2U9IiMwMjg0QzciIHN0cm9rZS13aWR0aD0iMS42Ii8+PGNpcmNsZSBjeD0iNyIgY3k9IjYuNSIgcj0iMS4xIiBmaWxsPSIjMDI4NEM3Ii8+PGNpcmNsZSBjeD0iNyIgY3k9IjE3LjUiIHI9IjEuMSIgZmlsbD0iIzAyODRDNyIvPjxwYXRoIGQ9Ik0xMSA2LjVoNk0xMSAxNy41aDYiIHN0cm9rZT0iIzAyODRDNyIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPjwvc3ZnPg=="); }
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

        /* 상태 배지 — 2026-08-24: ⑥ 승인 게이트를 없애면서 이 배지가 가리키는
           대상이 review_status(사람 판단)에서 ⑦ 재현 상태(_pipeline_status)로
           바뀌었다. 다섯 상태만 실제로 있다(중간 상태를 지어내지 않음). */
        .status-pill {
            display: inline-block; font-size: 0.75rem; font-weight: 600;
            padding: 0.12rem 0.55rem; border-radius: 999px; white-space: nowrap;
        }
        .status-pill.repro_pending { background: #FEF3C7; color: #92400E; }
        .status-pill.repro_running { background: #DBEAFE; color: #1E40AF; }
        .status-pill.repro_ok { background: #DCFCE7; color: #166534; }
        .status-pill.repro_failed { background: #FEE2E2; color: #991B1B; }
        .status-pill.no_code { background: #FFEDD5; color: #9A3412; }
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
    core_topics = st.text_input(
        "핵심 키워드 (콤마로 구분, OR 조건 — 하나만 걸려도 후보)",
        value=", ".join(existing["core_topics"]) if existing else "",
        key=f"{key_prefix}_core",
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
        value=existing["max_items"] if existing else 8, key=f"{key_prefix}_max",
    )

    label = "프로필 만들기" if is_new else "수정 저장"
    if st.button(label, key=f"{key_prefix}_submit", type="primary"):
        pid = profile_id.strip()
        if not pid or not name.strip():
            st.warning("프로필 ID와 이름은 비워둘 수 없음")
        else:
            # **가중치를 같이 넘긴다**(2026-09-09, §8-76). 안 넘기면
            # create_profile 이 전부 1.0 으로 채워서, 여기서 오탈자 하나
            # 고치고 저장하는 것만으로 프로필의 등급이 통째로 날아갔다.
            # 화면에 가중치 입력이 없으므로 기존 값을 그대로 실어 보낸다.
            kept_weights = (existing or {}).get("core_weights") or {}
            new_core = [k.strip() for k in core_topics.split(",") if k.strip()]
            # 주기도 같이 넘긴다(2026-09-16) — 안 넘기면 create_profile 기본값 daily 로 되돌아가 수동 프로필이 새벽 cron 에 들어간다.
            freq, at = research_profile.get_schedule(db_path, pid) if not is_new else ("daily", "05:00")
            research_profile.create_profile(
                db_path, pid, name.strip(),
                core_topics=new_core, schedule_frequency=freq, schedule_time=at,
                target_domain=[k.strip() for k in target_domain.split(",") if k.strip()],
                exclude=[k.strip() for k in exclude.split(",") if k.strip()],
                venues=[k.strip() for k in venues.split(",") if k.strip()],
                max_items=int(max_items),
                core_weights={k: kept_weights[k] for k in new_core if k in kept_weights},
                s2_seeds=[k.strip() for k in s2_seeds.split(",") if k.strip()],
            )
            st.session_state["_research_selected_profile"] = pid
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


def _h(value) -> str:
    """unsafe_allow_html 에 넣는 동적 값은 전부 여기를 거친다 — 키워드·note 에 외부 논문 용어가 섞인다."""
    import html
    return html.escape(str(value), quote=True)
_ORIGIN_SHORT = {"user": "사용자", "feedback": "반응", "agent": "에이전트", "advisor": "제안기", "rule": "규칙"}


def _render_status_strip(status: dict) -> None:
    """맨 위 한 줄 — 새벽 실행·다음 실행·주간 에이전트·반응 버튼. 숫자는 ops_dashboard 가 센다."""
    c1, c2, c3, c4 = st.columns(4)
    d = status["daily"]
    if d.get("started_at"):
        when = ops_dashboard._kst(d["started_at"])
        if d.get("finished_at") is None:
            verdict = "진행 중"
        elif d.get("exit") == 0:
            verdict = "정상"
        elif d.get("exit") == 2:
            verdict = "발송됨 · 소스 장애"
        elif d.get("exit") == "stopped":
            verdict = "수동 중지"
        else:
            verdict = f"실패 (exit {d.get('exit')})"
        c1.metric("마지막 새벽 실행", when)
        c1.caption(verdict if status["daily_ran_today"] else f"{verdict} · 오늘 실행 기록 없음 — PC(WSL)가 켜져 있어야 cron 이 돕니다")
    else:
        c1.metric("마지막 새벽 실행", "기록 없음")
    c2.metric("다음 새벽 실행", status["next_daily_kst"])
    c2.caption(f"주간 관리 {status['next_weekly_kst']}")
    w = status["weekly"]
    c3.metric("주간 에이전트", ops_dashboard.agent_status_label(w) if w else "아직 실행 없음")
    c3.caption("금요일 17:00 · Claude 제안 → Codex 판정")
    c4.metric("반응 버튼", "활성" if status["buttons_configured"] else "비활성")
    c4.caption("메일에 버튼이 붙습니다" if status["buttons_configured"] else "FEEDBACK_* 설정 없음")


def _render_profile_cards(db_path, profile_ids: list[str], selected: str) -> None:
    """프로필 카드 한 줄. 카드의 버튼이 선택을 바꾼다 — 현재 선택은 채운(primary) 버튼."""
    cols = st.columns(len(profile_ids))
    for col, pid in zip(cols, profile_ids):
        o = ops_dashboard.profile_overview(db_path, pid)
        if not o:
            continue
        with col.container(border=True):
            sched = "매일 발송" if o["schedule"] == "daily" else "수동"
            st.markdown(f"**{_h(o['field'])}** <span style='color:var(--text-muted);font-size:12px'>· {sched}</span>",
                        unsafe_allow_html=True)
            m = o["mails"]; r = o["reactions"]
            st.markdown(
                f"<div style='font-size:13px;line-height:1.7'>메일 <b>{m['issues']}</b>통 · 논문 <b>{m['papers']}</b>편<br>"
                f"반응 <b>{r['valid']}</b> <span style='color:var(--text-muted)'>(긍정 {r['more'] + r['useful']} · 부정 {r['out']})</span><br>"
                f"키워드 {o['keywords']['core']} · 수신자 {len(o['recipients'])}</div>", unsafe_allow_html=True)
            if st.button("보기", key=f"pick_{pid}", width="stretch",
                         type="primary" if pid == selected else "secondary"):
                st.session_state["_research_selected_profile"] = pid
                st.rerun()


def _render_overview(db_path, pid: str, o: dict) -> None:
    import pandas as pd
    a, b, c, d = st.columns(4)
    a.metric("보낸 메일", f"{o['mails']['issues']}통")
    a.caption(f"논문 {o['mails']['papers']}편" + (f" · 실패 {o['mails']['failed_issues']}회" if o["mails"]["failed_issues"] else ""))
    r = o["reactions"]
    b.metric("받은 반응", f"{r['valid']}건")
    b.caption(f"긍정 {r['more'] + r['useful']} · 부정 {r['out']}" + (f" · 격리 {r['other']}" if r["other"] else ""))
    c.metric("마지막 메일", ops_dashboard._kst(o["mails"]["last_sent_at"]))
    c.caption({"sent": "전원 발송", "partial": "일부 수신자 실패", "failed": "발송 실패", None: "—"}.get(o["mails"]["last_status"], ""))
    d.metric("프로필 revision", str(o["revision"]))
    d.caption("주간 에이전트: " + ops_dashboard.agent_status_label(o["last_agent"]))
    st.caption("수신자: " + (", ".join(o["recipients"]) or "없음 — 메일이 나가지 않습니다"))

    history = ops_dashboard.weight_history(db_path, pid)
    moving = {k: v for k, v in history.items() if len({round(x[2], 3) for x in v}) > 1}
    if moving:
        st.markdown("**가중치가 움직인 키워드**")
        _weight_chart(moving)
        st.caption("가중치가 한 번이라도 바뀐 키워드만 그린다. 가로는 프로필 revision(변경 시점), 점에 마우스를 올리면 시각이 보인다.")
    else:
        st.caption("아직 가중치가 움직인 키워드가 없습니다 — 반응이 서로 다른 논문 2편 이상 쌓이면 매일 새벽 조금씩 움직입니다.")


# 꺾은선 한 줄 = 키워드 하나. 정체(identity)를 색으로 가르므로 **범주형** 팔레트를 정해진 순서로 쓴다(순환하지 않는다, dataviz 규칙).
# 파랑을 첫 색으로 두고, 색약에서도 인접 색이 갈리도록 색상·명도를 번갈아 놓았다.
_LINE_PALETTE = ["#3B5BDB", "#E8590C", "#2F9E44", "#9C36B5", "#0CA678", "#E64980", "#F08C00", "#1098AD", "#845EF7", "#5C940D"]
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
             .configure_view(strokeWidth=0).configure(font="Pretendard, sans-serif"))
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
        cols = st.columns(3)
        for col, kind in zip(cols, ("s2_seed", "target", "exclude")):
            terms = [r for r in others if r["kind"] == kind]
            col.markdown(f"**{_KIND_LABELS[kind]}** ({len(terms)})")
            col.markdown("<div style='font-size:13px;line-height:1.8'>" + ("<br>".join(
                f"{_h(r['term'])}" + (f" <span style='color:var(--text-muted)'>· 적중 {r['hits_28d']}</span>" if r["hits_28d"] else "")
                + (f" <span style='color:var(--sky)'>· {_h(_ORIGIN_SHORT.get(r['origin'], r['origin']))}</span>" if r["origin"] != "user" else "")
                for r in terms) or "<span style='color:var(--text-muted)'>없음</span>") + "</div>", unsafe_allow_html=True)


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
    import profile_advisor
    hist = ops_dashboard.revision_history(db_path, pid)
    current = hist[0]["revision"] if hist else 0
    for h in hist:
        st.divider()
        head, body = st.columns([5, 1])
        head.markdown(f"**rev {h['revision']}** · {_h(h['when'])} · {_h(h['origin_label'])}"
                      + (f" <span style='color:var(--text-muted)'>— {_h(h['note'])}</span>" if h["note"] else ""), unsafe_allow_html=True)
        head.markdown("<div style='font-size:13px;line-height:1.7;margin-left:8px'>" + "<br>".join(_h(c) for c in h["changes"]) + "</div>",
                      unsafe_allow_html=True)
        if h["revision"] != current:
            if body.button("이 상태로 되돌리기", key=f"rollback_{pid}_{h['revision']}"):
                try:
                    res = profile_advisor.rollback(db_path, pid, h["revision"], reason=f"ui rollback to rev {h['revision']}")
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
        st.caption("아직 주간 에이전트 실행 기록이 없습니다. 금요일 17:00 에 돌고, 반응이 없는 주는 모델을 부르지 않습니다.")
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


_STATUS_COLORS = {"done": "#0ca30c", "partial": "#ec835a", "failed": "#d03b3b"}     # dataviz 상태 팔레트: good·serious·critical


def _status_legend() -> None:
    """상태 원의 뜻 — 색만으로 뜻을 전하지 않게 표 위에 한 줄로 둔다."""
    st.markdown(" &nbsp; ".join(f"<span style='color:{c};font-size:15px'>●</span> <span style='font-size:12px;color:var(--text-muted)'>{t}</span>"
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
                "결과": "⬤", "건수": r.get("retrieved_count") or 0,     # 표 격자는 글자 크기 지정을 무시한다 — 큰 원 글리프를 쓴다
                "검색 창": f"{(r.get('window_from') or '')[5:10]} ~ {(r.get('window_to') or '')[5:10]}",
                "시각": _relative_time(r["started_at"]) if r.get("started_at") else "?",
                "사유": ops_dashboard.short_error(r.get("error_detail")) if r["status"] == "failed" else "",
            } for r in runs])
            colors = [_STATUS_COLORS.get(r["status"], "#94A3B8") for r in runs]
            styled = table.style.apply(lambda col: [f"color: {c}; font-size: 18px;" for c in colors], subset=["결과"])
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


def render_research_tab() -> None:
    """운영 현황(2026-09-16 개편). 프로필별로 키워드·가중치·보낸 메일·반응·변경 이력·에이전트 실행을 본다.
    숫자는 전부 ops_dashboard 가 만든다 — 이 함수는 그리기만 한다."""
    st.subheader(APP_TITLE)
    db_path = server.DB_PATH
    root = Path(__file__).resolve().parent
    try:
        _render_status_strip(ops_dashboard.system_status(db_path, root))
    except Exception as e:  # noqa: BLE001 — 상태 줄이 깨져도 프로필 화면은 떠야 한다
        st.caption(f"상태 조회 실패: {type(e).__name__}")

    profile_ids = research_profile.list_profiles(db_path)
    with st.expander("새 프로필 만들기"):
        _render_profile_form(db_path, existing=None)
    if not profile_ids:
        st.info("아직 프로필이 없습니다 — 위에서 하나 만들어보세요.")
        return
    if st.session_state.get("_research_selected_profile") not in profile_ids:
        st.session_state["_research_selected_profile"] = max(
            profile_ids, key=lambda pid: (mail_ledger.counts(db_path, pid)["issues"], pid == "team_ai_advance"))
    selected = st.session_state["_research_selected_profile"]
    _render_profile_cards(db_path, profile_ids, selected)

    profile = research_profile.get_profile(db_path, selected)
    o = ops_dashboard.profile_overview(db_path, selected)
    st.markdown(f"### {_h(o['name'])}  <span style='color:var(--text-muted);font-size:13px'>`{_h(selected)}`</span>",
                unsafe_allow_html=True)
    tabs = st.tabs(["한눈에", "키워드·가중치", "보낸 메일", "반응", "변경 이력", "에이전트", "설정"])
    with tabs[0]:
        _render_overview(db_path, selected, o)
    with tabs[1]:
        _render_keywords(db_path, selected)
    with tabs[2]:
        _render_issues(db_path, selected)
    with tabs[3]:
        _render_reactions(db_path, selected)
    with tabs[4]:
        _render_history(db_path, selected)
    with tabs[5]:
        _render_agent(db_path, selected)
    with tabs[6]:
        _render_settings(db_path, selected, profile)




# ---------------------------------------------------------------- 논문 DB
_TIER_SHORT = {"official": "공식", "author": "저자 연관", "third_party": "제3자", "analogous": "유사 구현", "none": "없음", None: "—"}


def render_papers_page() -> None:
    """저장된 논문 전체를 한 표로 — 검색어·프로필로 거르고, 한 편을 골라 요약·재현·코드·SOTA 주장을 본다."""
    import pandas as pd
    st.subheader("논문 DB")
    db_path = server.DB_PATH
    profiles = research_profile.list_profiles(db_path)
    c1, c2 = st.columns([3, 2])
    query = c1.text_input("검색", placeholder="제목·초록·arXiv ID", key="papers_query")
    names = {pid: (research_profile.get_profile(db_path, pid) or {}).get("name", pid).split(" — ")[0] for pid in profiles}
    pick = c2.selectbox("보낸 프로필", ["(전체)"] + profiles, format_func=lambda v: v if v == "(전체)" else names.get(v, v),
                        key="papers_profile")
    rows = ops_dashboard.paper_catalog(db_path, query=query, profile_id=None if pick == "(전체)" else pick)
    st.caption(f"{len(rows)}편 · 최신 저장 순")
    if not rows:
        st.info("조건에 맞는 논문이 없습니다.")
        return
    df = pd.DataFrame([{
        "제목": r["title"], "발표": r["published"], "저장": r["fetched"], "출처": r["source"],
        "요약": "있음" if r["summarized"] else "", "재현": r["repro"], "코드": _TIER_SHORT.get(r["code_tier"], r["code_tier"]),
        "보낸 프로필": ", ".join(names.get(p, p) for p in r["profiles"]) or "—", "처음 발송": r["first_sent"],
        "주의": ", ".join(r["flags"]), "ID": r["arxiv_id"],
    } for r in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", height=420, on_select="rerun",
                         selection_mode="single-row", key="papers_table")
    selected = event.selection.rows[0] if getattr(event, "selection", None) and event.selection.rows else None
    if selected is None:
        st.caption("표에서 한 줄을 고르면 아래에 요약·재현·코드·SOTA 주장이 나옵니다.")
        return
    d = ops_dashboard.paper_detail(db_path, rows[selected]["arxiv_id"])
    if not d:
        return
    st.markdown(f"### {_h(d['title'])}", unsafe_allow_html=True)
    meta = [f"`{d['arxiv_id']}`", (d["published"] or "")[:10]]
    if d["link"]:
        meta.append(f"[원문]({d['link']})")
    st.markdown(" · ".join(m for m in meta if m))
    for line in (d["code_line"], d["sota_line"]):
        if line:
            st.caption(line)
    t_sum, t_abs, t_repro = st.tabs(["요약", "초록", "코드 재현 시도"])
    with t_sum:
        if d["summary_md"]:
            st.caption(f"요약 엔진: {d['summary_engine'] or '—'}")
            st.markdown(d["summary_md"])
        else:
            st.caption("요약 없음 — 초록만 정리됐거나 본문을 받지 못한 논문입니다.")
    with t_abs:
        st.write(d["abstract"] or "초록 없음")
    with t_repro:
        if d["repro"]:
            st.dataframe(pd.DataFrame([{"저장소": r["repo_url"], "찾은 곳": {"in_text": "논문 본문", "github_search": "GitHub 검색"}.get(r["source"], r["source"]),
                                        "결과": "성공" if r["success"] else "실패", "단계": r["stage"], "사유": r["fail_detail"] or "",
                                        "시각": ops_dashboard._kst(r["created_at"])} for r in d["repro"]]),
                         hide_index=True, width="stretch")
        else:
            st.caption("재현 시도 없음")


# ---------------------------------------------------------------- 시스템
_EXIT_LABELS = {0: "정상", 2: "발송됨 · 소스 장애", 1: "실패", "stopped": "수동 중지", "skipped": "스킵(이전 실행 중)", None: "종료 기록 없음(진행 중·중단)"}
_CRON_HINTS = {"run_daily_scan.sh": "매일 새벽 스캔·메일 발송", "run_weekly_agent.sh": "금요일 DB 정리 → 주간 관리 에이전트",
               "check_daily_mail.py": "새벽 메일 부재 감시"}


def render_system_page() -> None:
    """실행 기록·예약 작업·DB·백업·로그 — "어젯밤에 무슨 일이 있었나"를 터미널 없이 본다."""
    import pandas as pd
    st.subheader("시스템")
    db_path = server.DB_PATH
    dbs = ops_dashboard.db_status(Path(db_path))
    a, b, c, d = st.columns(4)
    a.metric("DB 크기", ops_dashboard.fmt_bytes(dbs["bytes"]))
    a.caption(f"WAL {ops_dashboard.fmt_bytes(dbs['wal_bytes'])}")
    t = dbs["tables"]
    b.metric("저장 논문", f"{t.get('papers') or 0}편")
    b.caption(f"요약 {t.get('summaries') or 0}편 · 재현 시도 {t.get('repro_results') or 0}건")
    c.metric("최신 백업", dbs["backups"][0]["when"] if dbs["backups"] else "없음")
    c.caption(f"보관 {len(dbs['backups'])}개 · 최신 {ops_dashboard.fmt_bytes(dbs['backups'][0]['bytes'])}" if dbs["backups"] else "")
    r = dbs["retention"]
    d.metric("마지막 DB 정리", r["when"] if r else "아직 없음")
    d.caption((f"행 {r['rows_deleted']} · 파일 {r['files_deleted']} 삭제" + (f" · 오류 {r['error']}" if r["error"] else "")) if r else "금요일 17:00 에 돕니다")

    st.markdown("#### 새벽 실행 기록")
    runs = ops_dashboard.recent_runs(ROOT, limit=14)
    if runs:
        st.dataframe(pd.DataFrame([{
            "시작(KST)": x["when"], "소요(분)": "—" if x["minutes"] is None else f"{x['minutes']:g}",
            "결과": _EXIT_LABELS.get(x["exit"], str(x["exit"])),
            "경고": x["warnings"], "API 호출": "—" if x["api_calls"] is None else str(x["api_calls"]),
            "발송": " / ".join(f"{pid.replace('team_', '')}: {v.get('delivery', v.get('status', ''))}" for pid, v in x["profiles"].items()) or "—",
        } for x in runs]), hide_index=True, width="stretch")
    else:
        st.caption("logs/daily_scan.log 기록 없음")

    st.markdown("#### 주간 에이전트")
    agent_rows = []
    for pid in research_profile.list_profiles(db_path):
        for run in ops_dashboard.agent_history(db_path, pid, limit=4):
            agent_rows.append({"주차": run["week"], "프로필": pid, "결과": ops_dashboard.agent_status_label(run),
                               "제안": len(run["proposed"]), "적용": len(run["applied"]), "기각": len(run["rejected"]), "시각": run["when"]})
    if agent_rows:
        st.dataframe(pd.DataFrame(sorted(agent_rows, key=lambda x: (x["주차"], x["프로필"]), reverse=True)), hide_index=True, width="stretch")
    else:
        st.caption("아직 실행 없음 — 금요일 17:00 첫 실행. 반응이 없는 프로필은 모델을 부르지 않습니다.")

    left, right = st.columns([3, 2])
    with left:
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
    with right:
        st.markdown("#### DB 표")
        st.dataframe(pd.DataFrame([{"표": k, "행": v if v is not None else "없음"} for k, v in t.items()]),
                     hide_index=True, width="stretch", height=300)

    st.markdown("#### 백업")
    if dbs["backups"]:
        st.dataframe(pd.DataFrame([{"파일": x["name"], "크기": ops_dashboard.fmt_bytes(x["bytes"]), "시각": x["when"]} for x in dbs["backups"]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("백업 없음")

    st.markdown("#### 로그 보기")
    logs = sorted(p.name for p in (ROOT / "logs").glob("*") if p.is_file() and p.suffix in (".log", ".txt", ".json"))
    if logs:
        default = logs.index("daily_scan.log") if "daily_scan.log" in logs else 0
        l1, l2 = st.columns([3, 1])
        name = l1.selectbox("파일", logs, index=default, key="log_pick")
        n = l2.number_input("마지막 줄 수", min_value=20, max_value=2000, value=200, step=50, key="log_lines")
        st.code(ops_dashboard.log_tail(ROOT, name, int(n)) or "(비어 있음)", language="text")


# ---------------------------------------------------------------- 메인
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

_PAGES = (("research", "운영 현황"), ("papers", "논문 DB"), ("system", "시스템"))

if st.session_state.get("nav_page") not in {k for k, _ in _PAGES}:
    st.session_state.nav_page = "research"          # 켜자마자 운영 현황(2026-09-16 사용자 요청)

with st.sidebar:
    st.markdown(
        f'<div class="sidebar-brand"><img src="{_BRAND_ICON}" class="sidebar-brand-icon"/> '
        '<b>최신 연구 동향</b> '
        '<span class="sidebar-brand-sub">모니터링 에이전트</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    for key, label in _PAGES:
        if st.button(label, key=f"nav_{key}", width="stretch",
                     type="primary" if st.session_state.nav_page == key else "secondary"):
            st.session_state.nav_page = key
            st.rerun()
    st.markdown("<div class='sidebar-nav-gap'></div>", unsafe_allow_html=True)
    try:
        _status = ops_dashboard.system_status(server.DB_PATH, ROOT)
        st.caption(f"다음 새벽 실행 {_status['next_daily_kst']}")
        st.caption(f"주간 관리 {_status['next_weekly_kst']}")
        if not _status["daily_ran_today"]:
            st.caption("오늘 새벽 실행 기록 없음")
    except Exception:  # noqa: BLE001 — 사이드바 상태가 깨져도 화면은 뜬다
        pass

if st.session_state.nav_page == "papers":
    render_papers_page()
elif st.session_state.nav_page == "system":
    render_system_page()
else:
    render_research_tab()
