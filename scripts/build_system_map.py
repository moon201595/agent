"""⑨ 설명용 구조도 — docs/system_map.html 을 만든다(계층으로 쌓은 상자·흐름·운영 근거).

연구동향 모니터링 에이전트가 무엇으로 이루어졌고, 논문 한 편·반응 하나·주간 조정·참고 자료가 어느 상자를 지나가며,
어디에 LLM 이 들어가고 어디가 결정적 코드인지를 한 화면에 보인다(2026-11-15, 제조 AI 구조도 방식을 이 저장소에 맞춰 다시 씀).

- 사람이 적는 것: 계층·상자의 문구·상태·흐름(아래 LAYERS·BLOCKS·SCENARIOS). 흐름의 점선은 설명용이지 실제 호출 추적이 아니다.
- 만들 때 코드에서 읽는 것: 파일 줄 수·대표 함수 시작 줄(AST)·관련 시험 파일(import 검색)·LLM 호출 모듈(호출 모양 검색)·커밋과
  커밋 안 된 파일의 해시·최근 운영 기록(아침 실행 로그·참고 자료 상태). 화면을 열 때 다시 읽지는 않는다 — 생성 시점 값이다.
- test_system_map.py 가 ① 모든 운영 모듈이 어느 상자에든 있는지 ② 가리키는 파일·함수가 있는지 ③ LLM 표시가 코드와 맞는지
  ④ 저장본이 지금 정의와 같은지 ⑤ 외부 자원을 끌어오지 않는지 본다.

    .venv/bin/python scripts/build_system_map.py                      # → docs/system_map.html (파일 하나로 열린다)
    .venv/bin/python scripts/build_system_map.py --artifact OUT.html   # 게시용 조각(문서 틀 없이 · 글꼴만 외부)
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tokenize
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "system_map.html"
KST = timezone(timedelta(hours=9))

# 상태: run 매일·매주 자동으로 돈다 / watch 돌지만 알려진 쟁점이 있다 / manual 손으로 돌리는 도구 / planned 예정
STATUS = {"run": "자동 운영 중", "watch": "운영 중 · 쟁점 있음", "manual": "손으로 쓰는 도구", "planned": "예정"}

LAYERS = [
    {"id": "ext", "name": "바깥 연결", "sub": "논문·코드 소스, 모델, 메일, 반응 버튼", "color": "#5b7fa6"},
    {"id": "ctl", "name": "운영 제어", "sub": "언제·어떤 순서로 돌고, 실패하면 어떻게 하나", "color": "#8a6d3b"},
    {"id": "find", "name": "① 검색 · ② 선별 · ③ 본문", "sub": "오늘 볼 논문을 고르고 본문을 받는다", "color": "#2f8f83"},
    {"id": "read", "name": "④ 요약 · ⑤ 검증 · ⑦ 재현", "sub": "읽고, 숫자를 대조하고, 서술한다", "color": "#7a5ca8"},
    {"id": "out", "name": "⑨ 배달 · ⑧ 축적 · ⑥ 판단", "sub": "메일로 보내고 기록·관찰한다", "color": "#b5544a"},
    {"id": "adapt", "name": "반응 · 주간 개선", "sub": "반응을 따라 관심 분야를 스스로 조정한다", "color": "#4f8a3a"},
]

# 상자. step 은 ①~⑨ 표기(없으면 ""), files 는 담당 파일, funcs 는 "파일:함수" 대표 진입점.
BLOCKS = [
    # ── 바깥 연결 ──
    {"id": "x_arxiv", "layer": "ext", "name": "arXiv API", "step": "①", "status": "run", "llm": False, "files": [], "funcs": [],
     "does": "키워드 질의로 새 프리프린트를 준다. 첫 요청이 429(요청 과다)를 자주 받는다.", "takes": "OR 질의(20개씩 분할)", "gives": "제목·초록·날짜",
     "note": "그날 첫 요청이 막히면 그 프로필을 뒤로 미뤄 다시 돈다(§275)."},
    {"id": "x_s2", "layer": "ext", "name": "Semantic Scholar", "step": "①", "status": "run", "llm": False, "files": [], "funcs": [],
     "does": "arXiv 밖 저널 논문까지 키워드로 찾는다.", "takes": "검색 시드 키워드", "gives": "논문 메타데이터·OA 링크", "note": ""},
    {"id": "x_full", "layer": "ext", "name": "본문 소스", "step": "③", "status": "run", "llm": False, "files": [], "funcs": [],
     "does": "arXiv HTML·Unpaywall·OA PDF 에서 본문을 받는다.", "takes": "논문 ID·DOI", "gives": "본문·표 HTML", "note": "받아 본 결과를 믿는다 — 링크 유무로 판단하지 않는다."},
    {"id": "x_code", "layer": "ext", "name": "GitHub · HF · PyPI", "step": "⑦", "status": "run", "llm": False, "files": [], "funcs": [],
     "does": "공식 코드·모델·데이터·패키지 사실을 준다.", "takes": "gh api 검색·저장소 조회", "gives": "저장소·라이선스·버전", "note": ""},
    {"id": "x_api", "layer": "ext", "name": "Gemini · Groq", "step": "④", "status": "run", "llm": True, "files": [], "funcs": [],
     "does": "무료 API 모델. 요약·번역·풀이에 쓴다(Gemini 우선, Groq 대체).", "takes": "프롬프트", "gives": "글", "note": "사내 문서·시크릿은 넣지 않는다."},
    {"id": "x_cli", "layer": "ext", "name": "Claude · Codex CLI", "step": "", "status": "run", "llm": True, "files": [], "funcs": [],
     "does": "구독 CLI 를 헤드리스로 부른다. 제안·판정·서술·선정·사실 대조는 도구 없이, 외부 정찰·외부 성능 근거 조사만 웹검색을 켠다.", "takes": "JSON 입력·스키마", "gives": "JSON 제안",
     "note": "어느 쪽도 결과를 적용하지 않는다 — Python 이 검증·적용한다."},
    {"id": "x_mail", "layer": "ext", "name": "메일 · 받는 사람", "step": "⑨", "status": "run", "llm": False, "files": [], "funcs": [],
     "does": "SMTP 로 프로필별 수신자에게 아침 메일을 보낸다.", "takes": "HTML·평문 메일", "gives": "도착", "note": ""},
    {"id": "x_react", "layer": "ext", "name": "반응 버튼 중계", "step": "⑨", "status": "run", "llm": False, "files": ["web/reaction/index.html"], "funcs": [],
     "does": "메일 카드의 반응 버튼(더 보고 싶음·유용함·관심 밖)을 받아 쌓아 둔다.", "takes": "서명된 반응 링크", "gives": "반응 이벤트", "note": ""},

    # ── 운영 제어 ──
    {"id": "chain", "layer": "ctl", "name": "아침 체인 · 근무일", "step": "", "status": "run", "llm": False,
     "files": ["run_daily_scan.sh", "work_calendar.py"], "funcs": ["work_calendar.py:day_kind"],
     "does": "월~금 05:00(공휴일 쉼) 하나의 락 안에서 주간 관리 → 일일 스캔 → 참고 자료 수집 순서로 돈다.", "takes": "달력·락",
     "gives": "실행 순서·로그", "note": "PC 절전이면 Windows 작업이 깨어난 뒤 돌린다. 열린 파일 한도 10240(§272)."},
    {"id": "orch", "layer": "ctl", "name": "조정기 · 순서 · 재시도", "step": "", "status": "watch", "llm": False,
     "files": ["run_profile_scan.py"], "funcs": ["run_profile_scan.py:scan_all_profiles", "run_profile_scan.py:scan_and_digest", "run_profile_scan.py:daily_order"],
     "does": "프로필을 우리팀 → 에이전트 → 로봇 → 비전 순서로 돌리고, arXiv 가 통째로 실패한 프로필은 맨 뒤에서 한 번 더 돈다.", "takes": "프로필 목록",
     "gives": "프로필별 결과·종료코드", "note": "쟁점: 스캔 번호(scan_id)가 결과에 실리지 않아 하류의 서술 보관·일일 링크가 번호 없이 돈다(§275, 결정 대기)."},
    {"id": "check", "layer": "ctl", "name": "부재 점검 · 아침 보고", "step": "⑨", "status": "run", "llm": False,
     "files": ["scripts/check_daily_mail.py", "scripts/morning_report.py"], "funcs": ["scripts/check_daily_mail.py:main"],
     "does": "메일이 안 나간 날을 찾아 운영자에게만 알리고, 하루 한 번 다시 돌린다.", "takes": "로그·발송 장부", "gives": "알림·재실행", "note": ""},
    {"id": "store", "layer": "ctl", "name": "저장소 · 스키마 가드", "step": "⑧", "status": "run", "llm": False,
     "files": ["storage.py", "schema_guard.py", "migrate.py"], "funcs": ["storage.py:db", "schema_guard.py:ensure"],
     "does": "SQLite 연결과 스키마의 단일 소유자. 승인 없는 DDL 은 운영 DB 에 안 돈다.", "takes": "DDL·연결 요청", "gives": "닫히는 연결",
     "note": "연결은 with closing(...) 로 연다 — 시험이 AST 로 감시한다(§272)."},
    {"id": "common", "layer": "ctl", "name": "공용 규약", "step": "", "status": "run", "llm": False,
     "files": ["config.py", "time_policy.py", "textutil.py", "pacing.py", "http_client.py", "api_usage.py", "lang_guard.py"],
     "funcs": ["http_client.py:throttled_arxiv_get"],
     "does": "설정·시각(UTC 저장, KST 표시)·이스케이프·호출 간격·재시도·호출 계수·언어 검사를 한 곳씩 둔다.", "takes": "—", "gives": "공용 함수",
     "note": "`.env` 를 읽는 곳은 config.py 하나다."},
    {"id": "mcp", "layer": "ctl", "name": "MCP 도구 · 로컬 검색", "step": "", "status": "run", "llm": False,
     "files": ["server.py", "hybrid_search.py"], "funcs": ["server.py:fetch_paper"],
     "does": "논문 받기·저장·검증 도구 12종과 저장 논문의 BM25+임베딩 검색.", "takes": "도구 호출", "gives": "결정적 결과", "note": "④ 요약·⑥ 판단·⑦ 재현은 이 서버의 일이 아니다."},
    {"id": "tools", "layer": "ctl", "name": "손으로 쓰는 도구", "step": "", "status": "manual", "llm": True,
     "files": ["eval.py", "delta_search.py", "scripts/count_arxiv.py", "scripts/probe_gemini.py", "scripts/import_local_embeddings.py",
               "scripts/injection_review.py", "scripts/build_system_map.py"], "funcs": [],
     "does": "요약 통과율 측정, 후보 수 세기, 모델 응답 확인, 임베딩 가져오기, 인젝션 검토, 이 구조도 만들기.", "takes": "사람의 실행", "gives": "보고", "note": "LLM 은 모델 응답 확인(probe_gemini) 하나만 부른다."},

    # ── 검색 · 선별 · 본문 ──
    {"id": "search", "layer": "find", "name": "델타 검색", "step": "①", "status": "run", "llm": False,
     "files": ["scan_search.py", "find_new_papers.py", "s2_delta.py"], "funcs": ["scan_search.py:scan_profile", "scan_search.py:_search_arxiv_chunked"],
     "does": "지난 실행 이후 새 논문만 arXiv·S2 에서 받는다. 한 소스가 죽어도 다른 소스로 이어간다.", "takes": "프로필 키워드·검색 창",
     "gives": "후보 논문", "note": "검색 창은 달력이 아니라 next_since 델타라 쉰 날 논문도 안 사라진다."},
    {"id": "select", "layer": "find", "name": "중복 제거 · 튜플 순위", "step": "②", "status": "run", "llm": False,
     "files": ["selection.py", "profile_scoring.py"], "funcs": ["profile_scoring.py:keyword_pattern", "profile_scoring.py:rank_key"],
     "does": "같은 논문을 합치고, (계층·날짜 띠·개념 폭·날짜…) 튜플로 순위를 정한다. 가중합이 아니다.", "takes": "후보·프로필",
     "gives": "카드 후보·관측 행", "note": "매처는 -s/-es/-ies 복수를 같은 말로 본다(match-v3, §273)."},
    {"id": "terms", "layer": "find", "name": "용어 위생", "step": "②", "status": "run", "llm": False,
     "files": ["term_hygiene.py"], "funcs": ["term_hygiene.py:reject_reason"],
     "does": "후보 용어에서 담화 구절·우산어·낱말 목록을 거른다.", "takes": "n-gram", "gives": "쓸 만한 용어", "note": ""},
    {"id": "full", "layer": "find", "name": "본문 확보 · 인젝션 표시", "step": "③", "status": "run", "llm": False,
     "files": ["injection_scan.py", "retraction.py"], "funcs": ["injection_scan.py:scan"],
     "does": "본문을 받고 프롬프트 인젝션 의심 문장을 표시한다(차단은 안 함). 철회 여부도 본다.", "takes": "논문 ID·DOI",
     "gives": "본문·표시", "note": "논문 속 명령문은 데이터로만 다룬다."},

    # ── 요약 · 검증 · 재현 ──
    {"id": "summ", "layer": "read", "name": "요약", "step": "④", "status": "run", "llm": True,
     "files": ["summarize_engine.py", "summary_parser.py", "sentence_grounding.py", "batch_summarize.py"], "funcs": ["summarize_engine.py:summarize", "batch_summarize.py:_process_paper"],
     "does": "긴 논문은 청크로 나눠 전문을 읽고, 문장마다 [S번호] 근거를 붙인 요약을 만든다.", "takes": "본문", "gives": "구조화 요약", "note": ""},
    {"id": "verify", "layer": "read", "name": "수치 대조", "step": "⑤", "status": "run", "llm": False,
     "files": ["verify.py"], "funcs": ["verify.py:verify_numbers"],
     "does": "요약의 숫자가 근거 문장(±1)에 글자 그대로 있는지 대조한다. LLM 을 쓰지 않는다.", "takes": "요약·원문", "gives": "통과·불일치", "note": ""},
    {"id": "repro", "layer": "read", "name": "코드 찾기 · 격리 재현", "step": "⑦", "status": "run", "llm": False,
     "files": ["code_finder.py", "code_ladder.py", "docker_runner.py"], "funcs": ["docker_runner.py:launch_background", "docker_runner.py:reproduce"],
     "does": "본문 링크와 GitHub 검색 후보(별 수 순)를 네트워크 없는 컨테이너에서 돌려 보고, 재현이 끝난 뒤 코드를 공식 → 저자 연관 → 제3자 → 유사 구현(표시만) → 없음 단계로 분류한다.", "takes": "논문", "gives": "코드 단계·재현 결과",
     "note": "시작점은 batch_summarize._process_paper 하나다."},
    {"id": "front", "layer": "read", "name": "성능 관측", "step": "⑤", "status": "run", "llm": False,
     "files": ["arxiv_tables.py", "performance_results.py", "research_frontier.py", "frontier_store.py", "method_links.py", "sota_claims.py"],
     "funcs": ["performance_results.py:table_results", "arxiv_tables.py:math_number_text"],
     "does": "논문 표의 (벤치마크·지표·값)을 뽑아 같은 표 안에서만 견주고, 다른 논문의 관측값과는 범위만 보인다.", "takes": "표 HTML", "gives": "성능 비교 절",
     "note": "회색 아래첨자 표준편차·계산 행·두 겹 구획을 읽는다(§274)."},
    {"id": "extev", "layer": "read", "name": "외부 성능 근거", "step": "⑤", "status": "run", "llm": True,
     "files": ["external_evidence.py"], "funcs": [],
     "does": "프로필·날짜마다 최대 5편(값 캐시·호출 예산 적용), 저자가 비교하지 않은 같은 벤치마크 결과를 에이전트가 웹에서 찾고 Python 이 출처 표에서 다시 확인한다.", "takes": "대표 결과", "gives": "외부 비교 행", "note": ""},
    {"id": "story", "layer": "read", "name": "연구 흐름 서술", "step": "⑨", "status": "run", "llm": True,
     "files": ["trend_report.py", "narrative_engine.py", "narrative_store.py", "trend_chains.py", "trend_history.py"],
     "funcs": ["trend_report.py:parse_story", "trend_report.py:render_story", "narrative_engine.py:generate"],
     "does": "모델은 글이 아니라 구조(JSON)를 내고, Python 이 흐름·근거 논문을 검증해 소제목과 목록을 쓴다.", "takes": "오늘 카드·지난 서술",
     "gives": "연구 흐름 절", "note": "서술 엔진 순서: Codex → Gemini → Groq."},
    {"id": "card", "layer": "read", "name": "카드 풀이 · 번역", "step": "⑨", "status": "run", "llm": True,
     "files": ["plain_ko.py", "research_value.py", "title_ko.py", "quote_ko.py"], "funcs": [],
     "does": "쉽게 말하면, 연구 가치(아이디어·활용 제안), 제목·인용문 한국어 병기. 숫자가 다르면 버린다.", "takes": "카드 논문", "gives": "카드 보조 글", "note": ""},
    {"id": "signal", "layer": "read", "name": "이용 신호 · 근거 상태", "step": "⑧", "status": "run", "llm": False,
     "files": ["adoption_signals.py", "paper_observations.py", "evidence_state.py", "daily_links.py"], "funcs": [],
     "does": "인용·공식 저장소·HF 를 성능과 섞지 않고 보이고, 보낸 뒤 근거 상태 변화를 알린다.", "takes": "논문", "gives": "관측 줄", "note": ""},

    # ── 배달 · 축적 · 판단 ──
    {"id": "digest", "layer": "out", "name": "메일 본문", "step": "⑨", "status": "run", "llm": False,
     "files": ["digest.py", "mail_layout.py", "mail_document.py", "saved_digest.py", "mail_reformat.py"], "funcs": ["digest.py:generate_digest_html"],
     "does": "연구 흐름 + 핵심 논문 카드 + (화·목) 참고 자료 + (주 첫 근무일) 이번 주 브리프를 HTML·평문으로 짠다.", "takes": "선정 결과", "gives": "메일 본문", "note": ""},
    {"id": "audit", "layer": "out", "name": "링크 감사", "step": "⑨", "status": "run", "llm": False,
     "files": ["link_policy.py"], "funcs": ["link_policy.py:audit_mail"],
     "does": "SMTP 직전에 메일의 모든 링크를 허용 목록·공인 주소·doi.org 착지로 다시 본다. 막힌 링크는 글자로 남긴다.", "takes": "메일 본문",
     "gives": "감사한 본문", "note": ""},
    {"id": "send", "layer": "out", "name": "발송 · 발송 장부", "step": "⑨", "status": "run", "llm": False,
     "files": ["scan_deliver.py", "email_delivery.py", "mail_ledger.py"], "funcs": ["scan_deliver.py:_deliver", "email_delivery.py:send_digest_email"],
     "does": "수신자마다 보내고, 회차·논문을 장부에 남긴다. 같은 운영일에 모두에게 보낸 회차가 있으면 다시 돌아도 보내지 않는다.", "takes": "본문·수신자", "gives": "발송 결과",
     "note": "일부 수신자만 성공한 회차는 다시 시도한다 — 그때 먼저 받은 사람은 두 번 받을 수 있다."},
    {"id": "prof", "layer": "out", "name": "프로필 · 관측 저장", "step": "⑧", "status": "run", "llm": False,
     "files": ["research_profile.py"], "funcs": ["research_profile.py:create_profile", "research_profile.py:next_since"],
     "does": "프로필(키워드·가중치·수신자)과 모든 변경 revision, 검색 이력·관측을 남긴다. 되돌릴 수 있다.", "takes": "변경·관측", "gives": "revision", "note": ""},
    {"id": "health", "layer": "out", "name": "관측 · 건강 · 영향", "step": "⑧", "status": "run", "llm": False,
     "files": ["observation_signals.py", "profile_health.py", "evaluation.py", "profile_impact.py", "weekly_profile_changes.py"], "funcs": ["profile_health.py:freeze_scan"],
     "does": "스캔별 관측에서 결정적 신호를 세고, 변경 전 영향과 적용 후 악화를 잰다.", "takes": "관측 행", "gives": "주간 브리프 표",
     "note": "매처 버전을 올리면 건강 점검은 새 기록이 14회·14일 찰 때까지 판정을 쉰다."},
    {"id": "keep", "layer": "out", "name": "보존 정리", "step": "⑧", "status": "run", "llm": False,
     "files": ["db_retention.py"], "funcs": [], "does": "주 1회 백업 뒤 보존표대로 오래된 행·캐시를 정리한다.", "takes": "보존표", "gives": "정리 보고", "note": ""},
    {"id": "ui", "layer": "out", "name": "운영 화면", "step": "⑥", "status": "run", "llm": False,
     "files": ["review_app.py", "ops_dashboard.py", "ui_helpers.py"], "funcs": ["ops_dashboard.py:profile_overview", "ops_dashboard.py:system_status"],
     "does": "운영 현황·논문 DB·시스템 세 페이지. 사람이 가중치를 고치고 되돌린다(127.0.0.1 전용).", "takes": "DB", "gives": "사람의 판단", "note": ""},
    {"id": "replay", "layer": "out", "name": "실행 이력 재생", "step": "", "status": "planned", "llm": False, "files": [], "funcs": [],
     "does": "특정 아침 실행에서 논문 하나가 실제로 지나간 상자를 순서대로 켜 보인다.", "takes": "scan_runs·search_runs·발송 장부", "gives": "실제 경로",
     "note": "예정. 스캔 번호가 결과에 실리는 것(위 조정기 쟁점)이 먼저다."},

    # ── 반응 · 주간 개선 ──
    {"id": "react", "layer": "adapt", "name": "반응 수집", "step": "⑨", "status": "run", "llm": False,
     "files": ["feedback_links.py"], "funcs": ["feedback_links.py:sync"],
     "does": "카드마다 서명된 반응 링크를 달고, 아침마다 쌓인 반응을 가져온다(중복 이벤트 제거).", "takes": "반응 이벤트", "gives": "반응 집계", "note": ""},
    {"id": "weight", "layer": "adapt", "name": "반응 가중치", "step": "②", "status": "watch", "llm": False,
     "files": ["feedback_weights.py"], "funcs": ["feedback_weights.py:update_profile"],
     "does": "스캔 직전 반응으로 키워드 가중치를 조정한다. 하루 한 번만. Python 만 쓴다.", "takes": "반응 집계", "gives": "revision(origin=feedback)",
     "note": "쟁점: 가중치 저장과 하루 한 번 보호 기록이 다른 트랜잭션이라 둘째만 실패하면 같은 날 두 번 움직일 수 있다(조건부, §275)."},
    {"id": "weekly", "layer": "adapt", "name": "주간 관리", "step": "②", "status": "run", "llm": True,
     "files": ["agent_maintenance.py", "agent_trend_evidence.py"], "funcs": ["agent_maintenance.py:validate", "agent_maintenance.py:run_week"],
     "does": "근거 브리프 → Claude 제안 → Codex 판정 → Python 검증(validate) → 적용. 두 모델이 동의해도 규칙을 어기면 거절한다.", "takes": "7일 반응·관측",
     "gives": "키워드·가중치·제외어 revision(origin=agent)", "note": "그 주 첫 근무일 아침, 스캔보다 먼저 돈다. 실패하면 다음 근무일에 한 번 더."},
    {"id": "scout", "layer": "adapt", "name": "외부 정찰", "step": "②", "status": "run", "llm": True,
     "files": ["external_scout.py"], "funcs": [],
     "does": "주 1회 검색 바깥의 중요한 연구를 웹에서 찾고, S2 로 신원을 확인해 무엇을 왜 놓쳤는지 잰다.", "takes": "프로필", "gives": "놓친 논문 근거", "note": ""},
    {"id": "discover", "layer": "adapt", "name": "용어 발견 · 격리 검색", "step": "②", "status": "run", "llm": False,
     "files": ["term_discovery.py", "shadow_search.py"], "funcs": [],
     "does": "키워드에 안 걸린 논문에서 후보 용어를 찾고, 검색을 바꾸는 변경안은 격리해서 실측한다.", "takes": "탈락 풀", "gives": "후보 용어·실측", "note": ""},
    {"id": "acol", "layer": "adapt", "name": "참고 자료 수집", "step": "⑨", "status": "watch", "llm": True,
     "files": ["research_assets_collect.py"], "funcs": ["research_assets_collect.py:run", "research_assets_collect.py:gather_candidates", "research_assets_collect.py:check_pick"],
     "does": "주 1회 과업별 GitHub·HF 검색 → 공식 사실 → Claude 선정 → Python 근거 대조 → Codex 사실 대조. 링크·라이선스·버전은 Python 이 API 값으로만 쓴다.",
     "takes": "과업 목록(tasks.json)", "gives": "ready 자료", "note": "쟁점: 모델이 소개문에 과업 맥락을 끼워 넣어 대표 저장소가 반려되는 일이 잦다(§276)."},
    {"id": "asend", "layer": "adapt", "name": "참고 자료 발송", "step": "⑨", "status": "run", "llm": False,
     "files": ["research_assets.py"], "funcs": ["research_assets.py:pick_for_mail"],
     "does": "화·목 메일에 안 보낸 ready 자료 최대 2건을 싣고, 전송이 수락된 뒤에만 장부에 적는다.", "takes": "ready 자료", "gives": "참고 자료 절", "note": ""},
]

# 설명용 흐름. 단계마다 지나가는 상자 — 실제 호출 추적이 아니다.
SCENARIOS = [
    {"id": "daily", "name": "논문 한 편의 하루", "steps": [
        ("아침 체인 시작", ["chain", "orch"], "근무일 05:00, 락을 잡고 고정 순서로 프로필을 돈다."),
        ("어제 반응 반영", ["x_react", "react", "weight"], "스캔 전에 반응을 가져와 가중치를 고친다 — 오늘 순위가 어제 반응을 따른다."),
        ("① 새 논문 검색", ["search", "x_arxiv", "x_s2"], "지난 실행 이후 새 논문만 두 소스에서 받는다."),
        ("② 합치고 순위", ["select", "prof"], "튜플 순위로 카드 후보를 고르고 관측 행을 남긴다."),
        ("③ 본문 받기", ["full", "x_full"], "받아 본 결과를 믿는다. 실패하면 초록으로 정리한다."),
        ("④ 요약 · ⑤ 숫자 대조", ["summ", "x_api", "verify"], "요약의 숫자는 근거 문장에서 글자 그대로 찾는다."),
        ("⑦ 코드 재현", ["repro", "x_code"], "격리 컨테이너에서 돌리고, 오늘 메일은 그 결과를 기다린다(상한 있음)."),
        ("성능 관측", ["front", "extev"], "표에서 성능을 읽고 같은 표 안에서만 견준다."),
        ("서술 · 카드 풀이", ["story", "x_cli", "card", "signal"], "흐름 구조를 Python 이 검증하고 카드 보조 글을 붙인다."),
        ("⑨ 메일 짜기 · 링크 감사 · 발송", ["digest", "audit", "send", "x_mail"], "SMTP 직전 링크를 다시 보고, 수신자마다 보낸다."),
        ("⑧ 기록", ["prof", "send", "health"], "보낸 논문만 '보여 줌'으로 소비하고, 관측·건강을 남긴다."),
    ]},
    {"id": "reaction", "name": "반응 하나가 순위가 되기까지", "steps": [
        ("카드의 반응 버튼", ["x_mail", "x_react"], "메일 카드에서 더 보고 싶음·유용함·관심 밖을 누른다."),
        ("다음 아침 수집", ["react"], "서명을 확인하고 중복 이벤트를 걸러 모은다."),
        ("가중치 계산", ["weight"], "Python 이 정해진 규칙으로 가중치를 움직인다(하루 한 번)."),
        ("revision 저장", ["prof"], "origin=feedback 으로 남겨 되돌릴 수 있다."),
        ("그날 순위에 반영", ["select", "digest"], "같은 아침 스캔의 순위와 메일 맨 아래 한 줄에 반영된다."),
    ]},
    {"id": "weekly", "name": "주간 관심사 조정", "steps": [
        ("백업 뒤 정리", ["chain", "keep"], "그 주 첫 근무일, 스캔보다 먼저 같은 락 안에서."),
        ("근거 브리프", ["weekly", "health", "scout", "discover"], "7일 반응·관측·외부 정찰·후보 용어를 Python 이 묶는다."),
        ("Claude 제안", ["weekly", "x_cli"], "키워드·가중치·검색어·제외어 변경을 JSON 으로 제안한다."),
        ("Codex 판정", ["weekly", "x_cli"], "같은 브리프로 제안마다 수락·거절·수정한다."),
        ("Python 검증", ["weekly"], "좋아요 근거·보호 규칙·범위를 validate 가 강제한다 — 두 모델이 동의해도 막는다."),
        ("적용 · 브리프 메일", ["prof", "digest"], "revision(origin=agent)으로 적용하고 그날 메일에 '이번 주 브리프'를 싣는다."),
    ]},
    {"id": "assets", "name": "참고 자료 수집 · 발송", "steps": [
        ("과업 고르기", ["acol"], "프로필당 주 2개, 가장 오래 안 모은 과업부터."),
        ("GitHub · HF 검색", ["acol", "x_code"], "검색어 3개로 저장소를 찾고 README·PyPI·arXiv 초록을 받는다."),
        ("Claude 선정", ["acol", "x_cli"], "0~2건을 고르고 소개문을 쓴다 — URL·라이선스·버전 칸은 없다."),
        ("Python 근거 대조", ["acol"], "근거 구절이 원문에 있는지, 판단 표현·링크·영어 문장이 없는지 본다."),
        ("Codex 사실 대조", ["acol", "x_cli"], "문장마다 원문과 맞는지 다시 본다. 문제 있으면 반려."),
        ("화·목 메일에", ["asend", "digest", "send"], "ready 자료 최대 2건을 핵심 논문 카드 뒤에 싣는다."),
    ]},
    {"id": "defer", "name": "arXiv 가 막힌 아침", "steps": [
        ("arXiv 전부 실패", ["orch", "search", "x_arxiv"], "갈라 던진 질의마다 429 재시도(요청당 누적 대기 90초까지)를 하고도 성공한 질의가 하나도 없으면 실패로 본다."),
        ("요약 전에 멈춤", ["search", "prof"], "첫 바퀴에서는 S2·요약·발송 전에 멈추고 실행 기록을 '미룸'으로 닫는다."),
        ("다른 프로필 먼저", ["orch", "digest", "send"], "나머지 프로필 메일이 먼저 나간다."),
        ("2분 뒤 다시", ["orch", "search", "x_arxiv", "x_s2"], "실패 시점에서 120초 이상 지나 한 번 더. 또 실패하면 S2 만으로 간다."),
        ("메일은 나간다", ["digest", "send", "check"], "그 프로필 메일이 맨 뒤로 갈 뿐 빠지지 않는다. 안 나가면 점검이 알린다."),
    ]},
]

# 모델을 부르는 모듈을 코드에서 찾는 모양 — 구조도 표시와 시험이 같은 기준을 쓴다.
LLM_CALL_RE = re.compile(r"\b(?:summarize_engine|se)\.(?:call_gemini|call_groq|complete|translate_ko|summarize_abstract|_post_gemini|_post_groq)\("
                         r"|narrative_engine\.generate\(|HeadlessRunner\(|_run_agent\(|\"codex\", \"exec\"|which\(\"claude\""
                         r"|async def call_gemini\(")


def _code_only(text: str) -> str:
    """주석을 지운 원문 — 문자열은 남긴다(\"codex\", \"exec\" 같은 호출 모양이 문자열이라서)."""
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type != tokenize.COMMENT]
        return tokenize.untokenize(toks)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return text


def production_modules() -> list[str]:
    """구조도에 들어가야 하는 운영 모듈 — 루트·scripts 의 *.py 에서 시험·conftest 를 뺀 것."""
    out = []
    for p in sorted([*ROOT.glob("*.py"), *ROOT.glob("scripts/*.py")]):
        if p.name.startswith("test_") or p.name == "conftest.py":
            continue
        out.append(p.relative_to(ROOT).as_posix())
    return out


def calls_llm(text: str) -> bool:
    return bool(LLM_CALL_RE.search(_code_only(text)))


def llm_modules() -> list[str]:
    return [f for f in production_modules() if calls_llm((ROOT / f).read_text(encoding="utf-8"))]


def _git(*args: str, strip: bool = True) -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True, timeout=10).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
    return out.strip() if strip else out


def _dirty_files() -> set[str]:
    """커밋과 다른 파일 — porcelain -z 는 항목을 NUL 로 가르고, 이름 바꾸기는 원래 이름이 다음 항목으로 따로 온다."""
    out, items = set(), _git("status", "--porcelain", "-z", "--untracked-files=all", strip=False).split("\0")
    i = 0
    while i < len(items):
        entry = items[i]
        if len(entry) > 3:
            out.add(entry[3:])
            if entry[0] in "RC":
                i += 1
        i += 1
    return out


def _head_files() -> set[str]:
    return set(_git("ls-tree", "-r", "--name-only", "HEAD").splitlines())


def _func_lines(rel: str) -> dict[str, int]:
    try:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return {}
    return {n.name: n.lineno for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _imports(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


def _related_tests(rel: str, tests: dict[str, str]) -> list[str]:
    """그 모듈을 직접 import 하거나 파일 이름으로 여는 시험 파일 — 다른 모듈을 거쳐 닿는 간접 경로는 못 본다."""
    if not rel.endswith(".py"):
        return []
    dotted = rel[:-3].replace("/", ".")
    # 파일 경로로 여는 모양만 센다 — ROOT / "scripts" / "x.py" 또는 "scripts/x.py". 이름 목록 속 "x.py" 는 여는 것이 아니다.
    by_name = re.compile(rf"""(?:/\s*["']|["'][\w./-]*/){re.escape(Path(rel).name)}["']""")
    return sorted(name for name, text in tests.items() if dotted in _imports(text) or by_name.search(text))


def _github_base() -> str:
    url = _git("remote", "get-url", "origin")
    m = re.match(r"(?:https://github\.com/|git@github\.com:)(.+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else ""


_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,40}")


def _ops(root: Path = ROOT) -> dict:
    """최근 운영 기록 — 아침 실행 로그와 참고 자료 상태. 게시본 원문에 남으므로 허용 목록 칸만, 형식을 확인한 값만 담는다
    (프로필 ID·상태 객체에 주소·키·경로가 섞여도 따라 나가지 않게, 2026-11-15 Codex 검토)."""
    ops: dict = {}
    try:
        lines = (root / "logs" / "daily_scan.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    starts = [i for i, l in enumerate(lines) if re.match(r"=== \S+ 시작", l)]
    if starts:
        i = starts[-1]
        j = next((k for k in range(i + 1, len(lines)) if re.match(r"=== \S+ (?:시작|종료)", lines[k])), None)
        ended = j is not None and "종료" in lines[j]
        window = lines[i:j + 1] if j is not None else lines[i:]

        def kst(line: str) -> str | None:
            try:
                return datetime.fromisoformat(line.split()[1].replace("Z", "+00:00")).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
            except (IndexError, ValueError):
                return None

        ops["daily_start"] = kst(lines[i])
        # 스캔 시작~종료 구간만 센다 — 종료 뒤 참고 자료 수집의 오류를 아침 스캔 오류로 보이지 않게. 값은 오류 횟수가 아니라 줄 수다.
        ops["dns_error_lines"] = sum(1 for x in window if "name resolution" in x)
        ops["daily_ended"] = ended
        if ended:
            m = re.search(r"exit (\d+)", lines[j])
            ops["daily_exit"] = int(m.group(1)) if m else None
        deg = next((x for x in window if x.startswith("[저하]")), "")
        ops["daily_degraded"] = sorted({pid for pid in re.findall(r"([A-Za-z0-9_.@+-]+)\(", deg) if _ID_RE.fullmatch(pid)})
        ops["deferred"] = sum(1 for x in window if "다른 프로필 뒤에 한 번 더 돈다" in x)
    base = root / "data" / "research_assets"
    try:
        run = json.loads((base / "collect_state.json").read_text(encoding="utf-8")).get("last_run") or {}
        day, status = str(run.get("day", "")), str(run.get("status", ""))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and re.fullmatch(r"[a-z_]{1,20}", status):
            ops["assets_run"] = {"day": day, "status": status}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    try:
        cfg = json.loads((base / "config.json").read_text(encoding="utf-8"))
        items = json.loads((base / "items.json").read_text(encoding="utf-8"))
        sent_path = base / "sent.jsonl"
        rows = [json.loads(x) for x in (sent_path.read_text(encoding="utf-8").splitlines() if sent_path.exists() else []) if x.strip()]
        # research_assets.pending 과 같은 기준 — 지금 발송 대상(audience)에게 보낸 것만 보낸 것으로 본다.
        audience = cfg.get("audience")
        sent = {(r.get("profile_id"), r.get("item_id")) for r in rows if r.get("audience") == audience}
        ops["assets_unsent"] = {pid: sum(1 for it in items if it.get("profile_id") == pid and it.get("status") == "ready"
                                         and (pid, it.get("item_id")) not in sent)
                                for pid in cfg.get("profiles", []) if isinstance(pid, str) and _ID_RE.fullmatch(pid)}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    try:
        tasks = json.loads((base / "tasks.json").read_text(encoding="utf-8"))
        ops["assets_tasks"] = sum(len(v) for v in tasks.values() if isinstance(v, list))
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    db = root / "data" / "papers.db"
    if db.exists():
        try:
            with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
                ops["profiles"] = int(con.execute("SELECT COUNT(*) FROM profiles WHERE COALESCE(schedule_frequency,'daily')='daily'").fetchone()[0])
                ops["papers"] = int(con.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
        except sqlite3.Error:
            pass
    return ops


def facts() -> dict:
    """만들 때 코드·로그에서 읽는 값. 저장본 비교 시험은 이 값을 뺀 정의만 견준다."""
    tests = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted(ROOT.glob("test_*.py"))}
    files = sorted({f for b in BLOCKS for f in b["files"]})
    dirty_files, head = _dirty_files(), _head_files()
    info = {}
    for f in files:
        p = ROOT / f
        text = p.read_bytes() if p.exists() else b""
        info[f] = {"lines": text.count(b"\n"), "tests": _related_tests(f, tests),
                   "changed": f in dirty_files, "in_head": f in head, "sha": hashlib.sha256(text).hexdigest()[:10]}
    funcs = {}
    for b in BLOCKS:
        for ref in b["funcs"]:
            f, _, name = ref.partition(":")
            funcs[ref] = _func_lines(f).get(name)
    return {
        "files": info, "funcs": funcs, "llm_modules": llm_modules(),
        "modules": len(production_modules()), "test_files": len(tests),
        "test_functions": sum(len(re.findall(r"^\s*(?:async\s+)?def test_", t, re.M)) for t in tests.values()),
        "commit": _git("rev-parse", "--short", "HEAD"), "commit_full": _git("rev-parse", "HEAD"),
        "commit_date": _git("log", "-1", "--format=%cs"), "dirty": sorted(f for f in dirty_files if f in info),
        "github": _github_base(),
        "open_prefix": (f"vscode://vscode-remote/wsl+{os.environ['WSL_DISTRO_NAME']}{ROOT}/" if os.environ.get("WSL_DISTRO_NAME") else ""),
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "ops": _ops(),
    }


def definition() -> dict:
    return {"status": STATUS, "layers": LAYERS, "blocks": BLOCKS,
            "scenarios": [{"id": s["id"], "name": s["name"], "steps": [{"name": n, "blocks": b, "text": t} for n, b, t in s["steps"]]}
                          for s in SCENARIOS]}


STYLE = r"""
:root{
  /* 계층으로 쌓은 판 위의 상자. 왼쪽 큰 그림, 오른쪽 흐름·설명. 색은 계층마다 하나, 상태는 모양(꽉 참·테두리·빗금·점선)으로 */
  --bg:#f3f4f1; --panel:#ffffff; --panel2:#eceee8; --ink:#1d2421; --muted:#5c6660; --line:#d6dad2; --accent:#2f6f5e;
  --llm:#8a3fb8; --watch:#c27a12; --hatch:rgba(0,0,0,.38); --halo:#ffffff;
  --f-body:"IBM Plex Sans KR","Noto Sans KR","Malgun Gothic",system-ui,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121614;--panel:#1a201d;--panel2:#222a26;--ink:#e3e8e4;--muted:#9aa59f;
  --line:#2f3833;--accent:#7cc4ad;--llm:#c58af0;--watch:#e8a33c;--hatch:rgba(255,255,255,.34);--halo:#121614;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121614;--panel:#1a201d;--panel2:#222a26;--ink:#e3e8e4;--muted:#9aa59f;--line:#2f3833;--accent:#7cc4ad;
  --llm:#c58af0;--watch:#e8a33c;--hatch:rgba(255,255,255,.34);--halo:#121614;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 var(--f-body)}
.page{padding-block:18px 32px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));max-width:1800px;margin:0 auto}
header{display:flex;flex-wrap:wrap;gap:10px 28px;align-items:flex-end;justify-content:space-between;margin-bottom:10px}
h1{font-size:26px;margin:0;letter-spacing:-.01em;text-wrap:balance}
.sub{color:var(--muted);font-size:15px;margin-top:2px;max-width:70ch}
.meta{color:var(--muted);font-size:13px;text-align:right;font-variant-numeric:tabular-nums}
.ops{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:8px 0 14px}
.op{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;min-width:0}
.op b{display:block;font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.02em}
.op span{font-size:15px;font-variant-numeric:tabular-nums}
.op.warn span{color:var(--watch)}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px;align-items:center}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:6px 13px;font:inherit;font-size:14px;cursor:pointer;display:inline-flex;gap:8px;align-items:center}
.chip:hover{border-color:var(--accent)} .chip[aria-pressed="true"]{background:var(--panel2);border-color:var(--accent)}
.chip:focus-visible,.step:focus-visible,.scn:focus-visible,#q:focus-visible,svg [tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
#q{font:inherit;font-size:14px;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--ink);min-width:0;width:16em;max-width:100%}
.dot{width:11px;height:11px;border-radius:3px;display:inline-block;flex:none}
.dot.run{background:var(--muted)} .dot.watch{background:var(--muted);box-shadow:0 0 0 2px var(--watch)}
.dot.manual{background:repeating-linear-gradient(45deg,var(--muted) 0 3px,transparent 3px 5px);outline:1px solid var(--muted)}
.dot.planned{outline:2px dashed var(--muted);outline-offset:-2px} .dot.llm{background:var(--llm);border-radius:50%}
.n{color:var(--muted);font-variant-numeric:tabular-nums}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:16px;align-items:start}
.stage{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow-x:auto;min-width:0}
svg{display:block;width:100%;min-width:900px;height:auto}
.side{position:sticky;top:env(safe-area-inset-top,0px);display:flex;flex-direction:column;gap:14px;max-height:100vh;overflow:auto;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;min-width:0}
.card h2{font-size:13px;margin:0 0 8px;color:var(--muted);font-weight:600;letter-spacing:.04em}
.scns{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.scn{border:1px solid var(--line);background:none;color:var(--ink);font:inherit;font-size:13px;border-radius:8px;padding:4px 10px;cursor:pointer}
.scn[aria-pressed="true"]{background:var(--panel2);border-color:var(--accent)}
.step{display:grid;grid-template-columns:26px 1fr;gap:8px;width:100%;text-align:left;border:1px solid transparent;background:none;color:var(--ink);font:inherit;font-size:15px;padding:4px 8px;border-radius:8px;cursor:pointer}
.step:hover{background:var(--panel2)} .step[aria-pressed="true"]{background:var(--panel2);border-color:var(--accent)}
.step .no{color:var(--muted);font-variant-numeric:tabular-nums}
.steptext{color:var(--muted);font-size:14px;margin:8px 2px 0;min-height:44px}
.caveat{font-size:12px;color:var(--muted);margin-top:6px}
#detail h3{margin:0 0 2px;font-size:20px} #detail .lay{font-size:13px;color:var(--muted)}
.badge{display:inline-flex;gap:6px;align-items:center;font-size:13px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:8px 6px 2px 0}
dl{margin:10px 0 0;display:grid;grid-template-columns:76px minmax(0,1fr);gap:6px 10px;font-size:15px} dt{color:var(--muted)} dd{margin:0}
.files{margin-top:12px;font-size:13px;display:flex;flex-direction:column;gap:6px}
.file{border-top:1px solid var(--line);padding-top:6px}
.file .p{font-family:var(--f-mono);word-break:break-all} .file a{color:var(--accent);text-decoration:none} .file a:hover{text-decoration:underline}
.file .m{color:var(--muted)} .file .chg{color:var(--watch)}
.fn{font-family:var(--f-mono);font-size:12.5px;margin-top:2px} .fn a{color:var(--accent);text-decoration:none}
.note{margin-top:10px;padding:8px 11px;background:var(--panel2);border-radius:10px;font-size:14px}
.note.w{box-shadow:inset 3px 0 0 var(--watch)}
table{border-collapse:collapse;width:100%;font-size:14px} th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top} th{color:var(--muted);font-weight:600}
td.st{white-space:nowrap} details{margin-top:16px} summary{cursor:pointer;color:var(--muted);padding:6px 0} .tbl{overflow-x:auto}
.lt{font-weight:700;font-size:17px} .ls{font-size:12px;fill:var(--muted)} .bn{font-size:12.5px;fill:var(--ink);paint-order:stroke;stroke:var(--halo);stroke-width:3px}
.bs{font-size:11px;fill:var(--muted);font-family:var(--f-mono);paint-order:stroke;stroke:var(--halo);stroke-width:3px}
.blk{cursor:pointer} .blk.dim{opacity:.14} .blk:hover .bn,.blk.sel .bn{font-weight:700}
.blk.sel .ring{opacity:1} .ring{opacity:0;fill:none;stroke:var(--ink);stroke-width:2}
.path{fill:none;stroke:var(--ink);stroke-width:2;stroke-dasharray:6 6;opacity:.85;pointer-events:none;animation:run 1.2s linear infinite}
@keyframes run{to{stroke-dashoffset:-24}} @media (prefers-reduced-motion:reduce){.path{animation:none}}
@media (max-width:1100px){.wrap{grid-template-columns:minmax(0,1fr)}.side{position:static;max-height:none}.meta{text-align:left}}
"""

BODY = r"""
<div class="page">
<header><div><h1>연구동향 모니터링 에이전트 구조도</h1>
<div class="sub">매일 새 논문을 찾아 읽고 메일로 보내며, 메일에 남긴 반응을 따라 관심 분야를 스스로 조정한다. 상자를 누르면 설명·파일·관련 시험이 나오고, 오른쪽 흐름의 단계를 누르면 그 단계가 지나가는 상자가 켜진다.</div></div>
<div class="meta" id="meta"></div></header>
<div class="ops" id="ops"></div>
<div class="bar" id="bar"><input id="q" type="search" placeholder="찾기: 이름·설명·파일" aria-label="상자 찾기"></div>
<div class="wrap"><div class="stage" id="stage"></div>
<aside class="side"><div class="card"><h2>흐름</h2><div class="scns" id="scns"></div><div id="flow"></div>
<div class="steptext" id="steptext"></div><div class="caveat">점선은 설명용 경로다. 실제 호출 추적이 아니다.</div></div>
<div class="card" id="detail"><h2>선택한 상자</h2><div class="lay">왼쪽 그림에서 상자를 누르세요.</div></div></aside></div>
<details><summary>목록으로 보기 (계층·상태·파일)</summary><div class="card tbl"><table id="list"></table></div></details>
</div>
<script>
const DEF=__DEF__, FACTS=__FACTS__;
const ST=DEF.status, NS="http://www.w3.org/2000/svg", A=100, Bv=40, H=28, F=0.44, COLS=6, LX=-14;
const el=(t,a={},p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e};
const h=(t,a={},html)=>{const e=document.createElement(t);for(const k in a)e.setAttribute(k,a[k]);if(html!=null)e.innerHTML=html;return e};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const byLayer=Object.fromEntries(DEF.layers.map(l=>[l.id,DEF.blocks.filter(b=>b.layer===l.id)]));
const RP=1.5, rows=l=>Math.ceil(byLayer[l.id].length/COLS), depth=l=>(rows(l)-1)*RP+1;
const RMAX=Math.max(...DEF.layers.map(depth)), OX=RMAX*A+330;
let y0=64, last=0; const pos={}, origin={};
for(const l of DEF.layers){origin[l.id]=y0; last=y0+(COLS+depth(l))*Bv; y0+=2*depth(l)*Bv+120;}
const W=OX+COLS*A+90, HT=last+60;
const svg=el("svg",{viewBox:`0 0 ${W} ${HT}`,role:"img","aria-label":"계층 구조도"});
const defs=el("defs",{},svg);
const pat=el("pattern",{id:"hatch",width:6,height:6,patternUnits:"userSpaceOnUse",patternTransform:"rotate(45)"},defs);
el("rect",{width:2.4,height:6,fill:"var(--hatch)"},pat);
const P=(l,x,y)=>[OX+(x-y)*A, origin[l]+(x+y)*Bv];
const pts=a=>a.map(p=>p.map(v=>v.toFixed(1)).join(",")).join(" ");
function shade(hex,k){const n=parseInt(hex.slice(1),16),c=[n>>16,n>>8&255,n&255].map(v=>Math.round(k<0?v*(1+k):v+(255-v)*k));return `rgb(${c.join(",")})`}
const gPlanes=el("g",{},svg), gPath=el("g",{},svg), gBlocks=el("g",{},svg);
for(const l of DEF.layers){
  const R=depth(l), c=[P(l.id,0,0),P(l.id,COLS,0),P(l.id,COLS,R),P(l.id,0,R)];
  el("polygon",{points:pts(c),fill:l.color,"fill-opacity":.08,stroke:l.color,"stroke-opacity":.7,"stroke-width":1.4},gPlanes);
  for(let i=1;i<COLS;i++) el("line",{x1:P(l.id,i,0)[0],y1:P(l.id,i,0)[1],x2:P(l.id,i,R)[0],y2:P(l.id,i,R)[1],stroke:l.color,"stroke-opacity":.13},gPlanes);
  const tx=20, ty=origin[l.id]+R*Bv-6;
  const t=el("text",{x:tx,y:ty,class:"lt",fill:l.color},gPlanes); t.textContent=l.name;
  const s=el("text",{x:tx,y:ty+18,class:"ls"},gPlanes); s.textContent=l.sub;
  byLayer[l.id].forEach((b,k)=>{
    const r=Math.floor(k/COLS), i=k%COLS, cx=i+.5, cy=r*RP+.5, f=F/2;
    const L=P(l.id,cx-f,cy+f), Fr=P(l.id,cx+f,cy+f), Rt=P(l.id,cx+f,cy-f), Bk=P(l.id,cx-f,cy-f), up=p=>[p[0],p[1]-H];
    const g=el("g",{class:"blk",tabindex:0,role:"button","data-id":b.id,"aria-label":`${b.name} — ${ST[b.status]}`},gBlocks);
    const ghost=b.status==="planned", fill=k=>ghost?"none":shade(l.color,k), so=ghost?{"stroke-dasharray":"5 4"}:{};
    const face=(p,k)=>el("polygon",Object.assign({points:pts(p),fill:fill(k),"fill-opacity":.95,stroke:ghost?l.color:shade(l.color,-.45),"stroke-width":ghost?1.5:1},so),g);
    face([L,Fr,up(Fr),up(L)],-.28); face([Fr,Rt,up(Rt),up(Fr)],-.48); face([up(L),up(Fr),up(Rt),up(Bk)],.14);
    if(b.status==="manual") for(const p of [[L,Fr,up(Fr),up(L)],[Fr,Rt,up(Rt),up(Fr)],[up(L),up(Fr),up(Rt),up(Bk)]]) el("polygon",{points:pts(p),fill:"url(#hatch)"},g);
    if(b.status==="watch") el("polygon",{points:pts([up(L),up(Fr),up(Rt),up(Bk)]),fill:"none",stroke:"var(--watch)","stroke-width":2.6},g);
    el("polygon",{class:"ring",points:pts([up(L),up(Fr),up(Rt),up(Bk)].map((p,i)=>[p[0]+[-5,0,5,0][i],p[1]+[0,4,0,-4][i]]))},g);
    const top=[(L[0]+Rt[0])/2,(up(L)[1]+up(Rt)[1])/2]; pos[b.id]=top;
    if(b.llm){el("circle",{cx:top[0],cy:top[1],r:9,fill:"var(--llm)",stroke:"#fff","stroke-width":1.2},g);const t=el("text",{x:top[0],y:top[1]+3.4,"text-anchor":"middle","font-size":7.5,"font-weight":700,fill:"#fff"},g);t.textContent="LLM";}
    else if(b.step){const t=el("text",{x:top[0],y:top[1]+4,"text-anchor":"middle","font-size":12,"font-weight":700,fill:"#fff"},g);t.textContent=b.step;}
    const n=el("text",{x:Fr[0]+LX,y:Fr[1]+17,"text-anchor":"middle",class:"bn"},g); n.textContent=b.name;
    if(b.files[0]){const ft=el("text",{x:Fr[0]+LX,y:Fr[1]+31,"text-anchor":"middle",class:"bs"},g); ft.textContent=b.files[0].split("/").slice(-1)[0]+(b.files.length>1?" +"+(b.files.length-1):"");}
    g.addEventListener("click",()=>select(b.id)); g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select(b.id)}});
  });
}
document.getElementById("stage").appendChild(svg);
const blocks=Object.fromEntries(DEF.blocks.map(b=>[b.id,b])), layerOf=Object.fromEntries(DEF.layers.map(l=>[l.id,l]));
let filter=null, scn=0, step=null, query="";
const hay=b=>[b.name,b.does,b.takes,b.gives,b.note,b.step,...b.files,...b.funcs].join(" ").toLowerCase();
function apply(){
  const S=DEF.scenarios[scn], inStep=b=>step==null||S.steps[step].blocks.includes(b.id);
  const on=b=>inStep(b)&&(filter==null||(filter==="llm"?b.llm:b.status===filter))&&(!query||hay(b).includes(query));
  document.querySelectorAll(".blk").forEach(g=>g.classList.toggle("dim",!on(blocks[g.dataset.id])));
  gPath.innerHTML=""; if(step!=null){const p=S.steps[step].blocks.map(id=>pos[id]); if(p.length>1) el("polyline",{class:"path",points:pts(p)},gPath);}
  document.querySelectorAll("#bar .chip[data-f]").forEach(c=>c.setAttribute("aria-pressed",String(c.dataset.f===String(filter))));
  document.querySelectorAll(".scn").forEach((c,i)=>c.setAttribute("aria-pressed",String(i===scn)));
  const fl=document.getElementById("flow"); fl.innerHTML="";
  S.steps.forEach((s,i)=>{const b=h("button",{class:"step",type:"button","aria-pressed":String(i===step)},`<span class="no">${String(i+1).padStart(2,"0")}</span><span>${esc(s.name)}</span>`);b.addEventListener("click",()=>{step=step===i?null:i;apply()});fl.appendChild(b)});
  document.getElementById("steptext").textContent=step==null?"단계를 누르면 그 단계가 지나가는 상자가 켜집니다.":S.steps[step].text;
}
function ghLink(f,line){const fi=FACTS.files[f]||{};if(!FACTS.github||!FACTS.commit_full||!fi.in_head)return "";
  return `${FACTS.github}/blob/${FACTS.commit_full}/${f}${line&&!fi.changed?`#L${line}`:""}`}
function select(id){
  const b=blocks[id], l=layerOf[b.layer];
  document.querySelectorAll(".blk").forEach(g=>g.classList.toggle("sel",g.dataset.id===id));
  const files=b.files.map(f=>{const fi=FACTS.files[f]||{}, gh=ghLink(f), vs=FACTS.open_prefix?`${FACTS.open_prefix}${f}`:"";
    const fns=b.funcs.filter(r=>r.startsWith(f+":")).map(r=>{const ln=FACTS.funcs[r], nm=r.split(":")[1];return `<div class="fn">${ln&&gh&&!fi.changed?`<a href="${esc(ghLink(f,ln))}" target="_blank" rel="noopener">${esc(nm)}()</a>`:esc(nm)+"()"} <span class="m">${ln?ln+"줄":""}</span></div>`}).join("");
    const tests=(fi.tests||[]); const tl=tests.length?`관련 시험 ${tests.length}개: ${tests.slice(0,4).map(esc).join(", ")}${tests.length>4?" …":""}`:"이 파일을 직접 import 하는 시험 없음";
    return `<div class="file"><div class="p">${gh?`<a href="${esc(gh)}" target="_blank" rel="noopener">${esc(f)}</a>`:esc(f)} ${vs?`<a class="m" href="${esc(vs)}">VS Code</a>`:""}</div>
      <div class="m">${fi.lines??"?"}줄 · ${tl}${fi.in_head===false?` · <span class="chg">아직 커밋 전 — GitHub 링크 없음</span>`:fi.changed?` · <span class="chg">작업본이 커밋본과 다름 — 링크는 커밋본, 줄 번호는 작업본(해시 ${esc(fi.sha)})</span>`:""}</div>${fns}</div>`}).join("");
  document.getElementById("detail").innerHTML=`<h2>선택한 상자</h2><h3>${b.step?esc(b.step)+" ":""}${esc(b.name)}</h3><div class="lay" style="color:${l.color}">${esc(l.name)} — ${esc(l.sub)}</div>
   <span class="badge"><i class="dot ${b.status}"></i>${ST[b.status]}</span><span class="badge">${b.llm?'<i class="dot llm"></i>LLM 이 들어가는 자리':"결정적 코드 (LLM 없음)"}</span>
   <dl><dt>하는 일</dt><dd>${esc(b.does)}</dd><dt>받는 것</dt><dd>${esc(b.takes)}</dd><dt>내보내는 것</dt><dd>${esc(b.gives)}</dd></dl>
   ${b.note?`<div class="note${b.status==="watch"?" w":""}">${esc(b.note)}</div>`:""}${files?`<div class="files">${files}</div>`:""}`;
}
const bar=document.getElementById("bar"), count=f=>DEF.blocks.filter(b=>f==="llm"?b.llm:b.status===f).length;
for(const [f,label] of [...Object.entries(ST),["llm","LLM 이 들어가는 자리"]]){
  const c=h("button",{class:"chip","data-f":f,"aria-pressed":"false",type:"button"},`<i class="dot ${f}"></i>${esc(label)} <span class="n">${count(f)}</span>`);
  c.addEventListener("click",()=>{filter=filter===f?null:f;apply()}); bar.appendChild(c);
}
const reset=h("button",{class:"chip",type:"button"},"전체 보기"); reset.addEventListener("click",()=>{filter=null;step=null;query="";document.getElementById("q").value="";apply()}); bar.appendChild(reset);
document.getElementById("q").addEventListener("input",e=>{query=e.target.value.trim().toLowerCase();apply()});
const scns=document.getElementById("scns");
DEF.scenarios.forEach((s,i)=>{const b=h("button",{class:"scn",type:"button","aria-pressed":"false"},esc(s.name));b.addEventListener("click",()=>{scn=i;step=null;apply()});scns.appendChild(b)});
const O=FACTS.ops||{}, op=(k,v,w)=>`<div class="op${w?" warn":""}"><b>${esc(k)}</b><span>${esc(v)}</span></div>`;
const runs=O.daily_start||"기록 없음";
const ex=!O.daily_ended?"끝 기록 없음(실행 중이거나 중단)":O.daily_exit===0?"정상 종료":O.daily_exit===2?"저하(소스 일부 실패)":`실패(exit ${O.daily_exit??"?"})`;
const PN={team_robot:"로봇",team_agent:"에이전트",team_vision:"비전",team_ai_advance:"우리팀"}, pn=id=>PN[id]||id;
document.getElementById("ops").innerHTML=[
  op("마지막 아침 실행",runs), op("결과",ex+(O.daily_degraded?.length?` · 소스 실패: ${O.daily_degraded.map(pn).join(", ")}`:"")+(O.dns_error_lines?` · 네트워크(DNS) 오류 줄 ${O.dns_error_lines}`:""),O.daily_ended&&O.daily_exit!==0),
  op("arXiv 미룸 재시도",`${O.deferred??0}건`), op("참고 자료 마지막 수집",O.assets_run?.day?`${O.assets_run.day} · ${O.assets_run.status}`:"기록 없음",O.assets_run&&O.assets_run.status!=="ok"),
  op("안 보낸 참고 자료",O.assets_unsent?Object.entries(O.assets_unsent).map(([k,v])=>`${pn(k)} ${v}`).join(" · ")||"—":"—"),
  op("과업 · 프로필",`과업 ${O.assets_tasks??"?"}개 · 일일 프로필 ${O.profiles??"?"}개`), op("저장 논문",O.papers!=null?`${O.papers.toLocaleString()}편`:"—")
].join("");
document.getElementById("meta").innerHTML=`코드 ${esc(FACTS.commit||"?")} (${esc(FACTS.commit_date||"?")})${FACTS.dirty.length?` + 커밋 안 된 파일 ${FACTS.dirty.length}개`:""} · 만든 때 ${esc(FACTS.generated)}<br>상자 ${DEF.blocks.length}개 · 운영 모듈 ${FACTS.modules}개 · 시험 파일 ${FACTS.test_files}개(시험 함수 ${FACTS.test_functions}개) · LLM 호출 모듈 ${FACTS.llm_modules.length}개`;
document.getElementById("list").innerHTML="<tr><th>계층</th><th>상자</th><th>상태</th><th>하는 일</th><th>파일</th></tr>"+DEF.blocks.map(b=>`<tr><td style="color:${layerOf[b.layer].color}">${esc(layerOf[b.layer].name)}</td><td>${b.step?esc(b.step)+" ":""}${esc(b.name)}${b.llm?' <i class="dot llm"></i>':""}</td><td class="st"><i class="dot ${b.status}"></i> ${esc(ST[b.status])}</td><td>${esc(b.does)}</td><td style="font-family:var(--f-mono);font-size:12px">${b.files.map(esc).join("<br>")}</td></tr>`).join("");
apply();
</script>
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">')


def _body(with_facts: dict) -> str:
    # </script> 가 정의 문자열 안에 들어가도 스크립트가 끊기지 않게 한다.
    dump = lambda x: json.dumps(x, ensure_ascii=False).replace("</", "<\\/")
    return BODY.replace("__DEF__", dump(definition())).replace("__FACTS__", dump(with_facts))


def render(with_facts: dict | None = None) -> str:
    """파일 하나로 열리는 문서 — 외부 자원 없음(글꼴은 시스템 글꼴로 떨어진다)."""
    f = with_facts if with_facts is not None else facts()
    return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>연구동향 모니터링 구조도</title>\n<style>{STYLE}</style></head><body>{_body(f)}</body></html>\n')


def render_artifact(with_facts: dict | None = None) -> str:
    """게시용 조각 — 게시할 때 문서 틀이 씌워지므로 틀 없이 쓰고, 로컬 VS Code 링크는 뺀다(GitHub 링크는 남는다)."""
    f = dict(with_facts if with_facts is not None else facts())
    f["open_prefix"] = ""
    return (f'<title>연구동향 모니터링 구조도</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n{FONTS}\n'
            f'<style>{STYLE}</style>\n{_body(f)}')


if __name__ == "__main__":
    data = facts()
    OUT.write_text(render(data), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} — 상자 {len(BLOCKS)}개 · 흐름 {len(SCENARIOS)}개")
    if "--artifact" in sys.argv:
        dest = Path(sys.argv[sys.argv.index("--artifact") + 1])
        dest.write_text(render_artifact(data), encoding="utf-8")
        print(f"{dest} — 게시용 조각")
