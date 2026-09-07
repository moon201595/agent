# paper-harness — AGENTS.md

이 파일은 AGENTS.md 를 읽는 도구(Codex, Cursor, Copilot 등)가 세션 시작 시 자동으로
읽는다. Claude Code 는 AGENTS.md 를 직접 읽지 않으므로 `./CLAUDE.md` 맨 위에서
`@AGENTS.md` 로 import 한다. 두 도구가 같은 사실을 본다.

여기에는 **이 프로젝트가 무엇이고 어떻게 다뤄야 하는지**만 쓴다. Claude ↔ Codex 역할
분담은 `~/.claude/CLAUDE.md` 몫이다. 이 저장소의 판단 규칙(비용 원칙, 설계 원칙,
정직성 규칙, 작업 규율, 마일스톤)은 `./CLAUDE.md` 에 있고 **그쪽이 우선한다** —
충돌하면 CLAUDE.md 를 따르고 이 파일을 고친다.

## 프로젝트 개요

관심 분야의 최신 논문을 매일 스스로 찾고 읽고 검증해서, 사람이 아침에 메일 하나로
"이 분야가 어디로 가고 있나"를 알게 하는 하네스다.

- 주요 스택: Python 3.14 · MCP 서버(stdio) · Streamlit(사람 판단 UI) · SQLite(`data/papers.db`) · Docker(⑦ 코드 재현 격리 실행)
- 요약 LLM 은 무료 API 를 쓴다 — Gemini 우선, Groq 대체. Claude/Codex 자신이 요약을 쓰지 않는다.
- 모노레포가 아니다. 루트 평면 배치이고 `src/` 레이아웃이 아니다. 하위 AGENTS.md 도 없다.
- 오케스트레이션 계층이 없는 것이 설계다. 판단은 MCP 클라이언트가, 이 저장소는 결정적 도구만 맡는다.

파이프라인 단계는 문서·주석·모듈 docstring 전반에서 ①~⑨ 원문자로 부른다.
① 검색 ② 중복 제거·선별 ③ 본문 확보 ④ 요약 ⑤ 수치 검증 ⑥ 사람 판단 ⑦ 코드 재현
⑧ 축적 ⑨ 배달. 새 코드도 이 표기를 따른다.

## 환경 설정

- 가상환경: 저장소 루트의 `.venv` (이미 생성돼 있다). `uv` · Poetry 를 쓰지 않는다.
- 의존성 설치: `.venv/bin/pip install -r requirements.txt`
- 환경 변수: `.env` 에 있다. **읽거나 출력하지 않는다**(CLAUDE.md 규칙 10). 필요한 로직은
  이름만 참조한다 — `GOOGLE_API_KEY` · `GROQ_API_KEY` · `S2_API_KEY` · `UNPAYWALL_EMAIL`.
  발급처와 용도는 `docs/API_KEYS.md`.
- MCP 서버: `.venv/bin/python server.py` (stdio). 클라이언트가 띄운다.
- 사람 판단 UI: `.venv/bin/streamlit run review_app.py`
- 일일 스캔(cron 진입점): `./run_daily_scan.sh` — 매일 05:00 KST, 로그는 `logs/daily_scan.log`.

## 테스트 · 검증 (정확한 명령어 그대로)

- 전체 테스트: `.venv/bin/python -m pytest` — 인자 없이 그냥 돌려도 안전하다.
  `pytest.ini` 의 `norecursedirs = data .venv .git` 이 ⑦ 재현이 clone 해 온 남의
  저장소를 수집 대상에서 뺀다. 이 줄을 지우면 없는 의존성 import 로 100건 넘게 깨진다.
- 단일 테스트: `.venv/bin/python -m pytest test_verify_units.py::test_이름`
- 커버리지: `.venv/bin/coverage run -m pytest && .venv/bin/coverage report`
  (`pytest-cov` 는 설치돼 있지 않다 — `--cov` 옵션은 없다. `coverage` 7.x 를 직접 쓴다)
- 회귀 기준선: `.venv/bin/python eval.py` — 저장된 전체 요약의 통과율을 잰다.
  기준선은 39편 · pass_ratio 0.982 다. **임의로 옮기지 않는다**(CLAUDE.md 규칙 9).
  값이 흔들리면 조작하지 말고 원인을 분석해 `docs/PROGRESS.md` 에 있는 그대로 적는다.
- 린터·포매터·타입체커를 두지 않는다. Ruff · Black · isort · mypy 설정이 없고, 추가하지 않는다.
  `pyproject.toml` 도 없다. **완료 조건은 pytest 전체 green 하나다**(규칙 11).
- 네트워크가 필요한 테스트가 섞여 있다(`test_smoke.py` 등). 오프라인에서 그 파일이 실패하는
  것은 코드 결함이 아니다 — 판단 전에 실패 사유를 확인한다.

## 코드 스타일 · 컨벤션

- 타입 힌트를 쓴다(비테스트 함수 257개 중 245개에 반환 타입이 있다). 새 코드도 붙인다.
- 주석과 docstring 은 **한국어 평서체**로 쓰고, 무엇을 하는지가 아니라 **왜 그렇게 했는지**를
  적는다. 실측 결과와 날짜를 함께 남기는 것이 이 저장소의 관행이다. 이 톤을 유지한다.
- 모듈 docstring 첫 줄은 담당 단계로 시작한다 — 예: `"""② 중복 제거·선별 — 결정적 규칙, 네트워크·LLM 미사용."""`
- 파일 수정은 `sed` 대신 str_replace 또는 `python3 << 'PYEOF'` heredoc 으로 한다(규칙 15).
- `selection.py` 를 `select.py` 로 바꾸지 않는다 — 표준 라이브러리 `select` 를 가려 asyncio 가 깨진다.

## 아키텍처 메모

코드만 보고 유추하기 어려운 것만 적는다. 전체 지도는 `README.md`, 결정 이력은 `docs/PROGRESS.md`.

- `server.py` — MCP 도구 12종. ④ 요약, ⑥ 사람 판단, ⑦ 재현은 이 서버의 일이 아니다.
- `summarize_engine.py` — ④ 요약 엔진(Gemini 우선/Groq 대체). 긴 논문은 청크로 나눠 전문을 읽는다.
- `sentence_grounding.py` — ④⑤ 공유. 원문에 `[S번호]` 태그를 붙이고, ⑤ 가 같은 함수로 다시 나눠 대조한다.
- `verify.py` — ⑤ 수치 검증기. 문자열 대조만 하고 LLM 을 쓰지 않는다. `[S번호]` 가 있으면 그 문장(±1) 안에서만 찾는다.
- `docker_runner.py` — ⑦ 격리 실행. `reproduce(arxiv_id)` 가 이 저장소의 유일한 자율 재시도 루프다(최대 3회).
- `digest.py` · `trend_report.py` · `email_delivery.py` — ⑨ 배달.
- **전이 지점 단일 소유**: ④⑤ 저장 → ⑦ 재현 자동 전이는 `docker_runner.launch_background()` 하나로만 트리거한다.
  호출 지점은 다섯 곳이 전부다(자동 2 + 수동 버튼 3). 새 자동 호출 지점을 추가하지 않는다(규칙 5).
- **새 지휘자 계층 금지**: 단계를 다시 꿰는 오케스트레이션 스크립트·클래스·프레임워크를 만들지 않는다.
  `pipeline.py` 부활, LangGraph, Airflow 전부 해당한다. 기존 진입점(`_process_paper`,
  `scan_and_digest`, `scan_all_profiles`)을 재사용한다(규칙 6). 폐기 사유는 PROGRESS.md §9.
- **판정 경로에 LLM 판사를 넣지 않는다**: 검증·재현 결과는 이진 판정 가능한 신호(문자열 대조,
  Docker exit code, DB 기록)로만 보고한다(규칙 7).

### 승인 없이 건드리지 않는 곳

- `verify.py` · `docker_runner.py` · `email_delivery.py` — 수정 전에 계획을 먼저 제시하고 승인을 받는다(규칙 13).
- `data/` — 자동 생성물이고 ⑦ 이 clone 해 온 외부 저장소가 들어 있다. 커밋 대상이 아니다.
- `.env` — 읽지 않는다.
- `prompts/*.md` — 프롬프트 자산이고 버전 관리 대상이다. 절대 규칙 R1~R6 을 임의로 완화하지 않는다.

## Git · 커밋

이 저장소의 **실제** 관행이다. 일반적인 GitHub Flow 와 다르므로 그대로 따른다.

- 브랜치는 `main` 하나다. 기능 브랜치·PR 절차를 쓰지 않는다.
- 커밋 제목은 한국어 서술형 한 줄이다. Conventional Commits(`feat:` `fix:`)를 쓰지 않는다.
  발견한 사실을 문장으로 적는다 — 예: `arXiv 429 하나가 그날을 통째로 죽였다 — 주석이 말하던 걸 코드가 안 하고 있었다`
- 모든 코드 변경은 대응 테스트와 함께 커밋한다. pytest 전체 green 이 완료 조건이다(규칙 11).
- 설계 결정과 실측 결과는 `docs/PROGRESS.md` 에 날짜와 함께 남긴다(규칙 14).
- **에이전트는 사용자가 요청하지 않는 한 커밋·push 하지 않는다.**

## 보안 · 안전

- **모든 것이 무료여야 한다.** 유료 API·유료 티어·결제수단 등록을 도입하지 않고, 해법으로 제안하지도 않는다(규칙 1).
- 429·한도 초과의 해법은 순서대로 (a) 다음 무료 provider 폴백 (b) retry-after 준수 지수 백오프 (c) 처리량 축소다.
  "업그레이드"는 해법이 아니다. 무료 한도 수치를 코드에 하드코딩하지 않는다 — 429 응답 처리로만 대응한다(규칙 2·3).
- 외부 LLM(Gemini 무료 티어)에 **보내도 되는 것**: 논문 텍스트, 제목·초록·저자·venue,
  연구 관심 분야 키워드(core_topics, domain_hints).
  **절대 안 보내는 것**: 사내 문서·계획서·회의록·로드맵, 미공개 실측 데이터, 고객·개인 정보, 시크릿.
  가르는 기준은 "우리가 무엇에 관심 있나"는 나가도 되지만 "우리가 무엇을 하고 있나"는 안 된다는 것이다.
  판단이 서지 않으면 보내지 않는다(규칙 4).
- 시크릿을 코드·로그·커밋 메시지에 남기지 않는다. `.env` 와 `data/` 는 `.gitignore` 에 있다.
- 메일 발송, 외부 네트워크 대량 호출, 되돌리기 어려운 삭제·마이그레이션은 사람 승인 없이 실행하지 않는다.
- 수치를 만들어내지 않는다. 실측하지 않은 값은 "미실측"이라고 적고, 실패는 실패로 적는다(규칙 8).
- 테스트를 통과시키려고 테스트를 삭제·완화하거나 `verify.py` 의 grounding 임계값을 낮추지 않는다(규칙 9).

## 알려진 함정

과거에 실제로 틀렸던 것들이다.

- **`pytest` 를 `data/` 까지 수집하게 두면 깨진다.** ⑦ 이 clone 한 외부 저장소의 conftest 를
  같이 물어 100건 넘게 실패한다(2026-08-18 실측). `pytest.ini` 의 `norecursedirs` 를 지우지 않는다.
- **주석이 말하는 것과 코드가 하는 것이 다를 수 있다.** arXiv 429 재시도가 주석에만 있고 코드에는
  없어서 하루치 스캔이 통째로 죽은 적이 있다. 주석을 근거로 "이미 처리됨"이라고 판단하지 말고 코드를 본다.
- **합성 ID 는 도착할 때의 신원이 아니다.** arXiv 밖 논문은 `pdf-<내용 해시 10자리>` 형태의 ID 를 쓴다.
  `arxiv_id` 컬럼에 들어가지만 arXiv 논문이라는 뜻이 아니다 — 출처는 `papers.source` 컬럼이 가른다.
- **초록만 있는 논문이 본문 있는 논문의 자리를 먹지 않게 해야 한다.** 본문 확보 실패를 링크 존재로
  판단하면 안 된다 — 받아본 결과를 믿는다.
- **규칙을 지키느라 목적을 놓친 적이 두 번 있다**(2026-09-03). 규칙 7 을 판정이 아닌 서술에까지 적용해
  동향 글을 통째로 뺐고, 규칙 4 가 관심 분야 키워드를 프롬프트에서 지워 "이 흐름이 우리 분야와 어디서
  만나는가"를 못 쓰게 만들었다. 규칙이 목적을 방해하면 규칙을 고치고 사유와 날짜를 PROGRESS.md 에 남긴다.
- **거짓 성공을 기록하지 않는다.** TSPulse 재현이 실패했는데 성공으로 기록된 사례가 있다(PROGRESS.md).
