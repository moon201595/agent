# paper-harness — AGENTS.md

이 파일은 AGENTS.md 를 읽는 도구(Codex, Cursor, Copilot 등)가 세션 시작 시 자동으로
읽는다. Claude Code 는 AGENTS.md 를 직접 읽지 않으므로 `./CLAUDE.md` 맨 위에서
`@AGENTS.md` 로 import 한다. 두 도구가 같은 사실을 본다.

여기에는 **이 프로젝트가 무엇이고 어떻게 다뤄야 하는지**만 쓴다. 규칙은 `./CLAUDE.md` 의
최소 규칙 7개(2026-09-14 전면 교체)이고 **그쪽이 우선한다** — 충돌하면 CLAUDE.md 를 따르고 이 파일을 고친다.

**Claude ↔ Codex 역할 분담**(누가 언제 무엇을 맡기는가)은 `~/.claude/CLAUDE.md`
몫이고 여기 쓰지 않는다. 다만 **일을 맡은 도구가 이 저장소 안에서 지킬 것**은
여기 있다(아래 "위임받은 도구가 지킬 것") — 그건 이 프로젝트를 다루는 방식이고,
Codex 는 `~/.claude/CLAUDE.md` 를 읽지 않아서 거기 적으면 못 보기 때문이다.

## 프로젝트 개요

관심 분야의 최신 논문을 매일 스스로 찾고 읽고 정리해 메일로 보내고, **메일 논문에 남긴 사용자 반응을 따라
관심 분야를 스스로 조정**하는 연구 지원 에이전트다(2026-09-14 방향 전환 — 계획 `docs/AGENT_PLAN_2026-09-14.md`).

- 주요 스택: Python 3.14 · MCP 서버(stdio) · Streamlit(사람 판단 UI) · SQLite(`data/papers.db`) · Docker(⑦ 코드 재현 격리 실행)
- 요약·서술 LLM 은 현재 Gemini 우선, Groq 대체다. 에이전트 두뇌로 Claude Code·Codex(구독, 헤드리스 포함)를 붙이는 중이다(계획 v2 §1).
- 모노레포가 아니다. 루트 평면 배치이고 `src/` 레이아웃이 아니다. 하위 AGENTS.md 도 없다.
- 일일 진입점은 `run_daily_scan.sh → run_profile_scan.py` 이고, Python 모듈이 검색·저장·실행을 맡는다. LLM 은 JSON 제안을 돌려주고 Python 이 검증·적용한다.

파이프라인 단계는 문서·주석·모듈 docstring 전반에서 ①~⑨ 원문자로 부른다.
① 검색 ② 중복 제거·선별 ③ 본문 확보 ④ 요약 ⑤ 수치 검증 ⑥ 사람 판단 ⑦ 코드 재현
⑧ 축적 ⑨ 배달. 새 코드도 이 표기를 따른다.

## 환경 설정

- 가상환경: 저장소 루트의 `.venv` (이미 생성돼 있다). `uv` · Poetry 를 쓰지 않는다.
- 의존성 설치: `.venv/bin/pip install -r requirements.txt`
- 환경 변수: `.env` 에 있다. **읽거나 출력하지 않는다**(CLAUDE.md 규칙 5). 필요한 로직은
  이름만 참조한다 — `GOOGLE_API_KEY` · `GROQ_API_KEY` · `S2_API_KEY` · `UNPAYWALL_EMAIL`.
  발급처와 용도는 `docs/API_KEYS.md`.
- MCP 서버: `.venv/bin/python server.py` (stdio). 클라이언트가 띄운다.
- 운영 화면: `.venv/bin/streamlit run review_app.py` → http://localhost:8501 — 운영 현황(기본)·논문 DB·시스템 세 페이지(2026-09-16 개편,
  옛 검색·요약·검토 탭 삭제). `.streamlit/config.toml` 이 127.0.0.1 로만 묶는다 — 되돌리기·가중치 저장 버튼이 있고 로그인이 없다.
- 일일 스캔(cron 진입점): `./run_daily_scan.sh` — 매일 05:00 KST, 로그는 `logs/daily_scan.log`.
- **Windows 작업 스케줄러에도 같은 두 작업이 있다**(`paper-harness\daily-scan` 매일 05:00, `paper-harness\weekly-agent` 금 17:00, XML 은
  `%USERPROFILE%\paper-harness-tasks\`). PC 가 절전이면 WSL cron 은 그 시각을 건너뛴다(2026-09-15 실측) — Windows 작업은 깨어나는 즉시
  실행(StartWhenAvailable)하고 꺼진 WSL 도 켠다. PC 를 깨우지는 않는다. 둘이 겹치면 flock·주차 표지가 한 번만 돌게 한다.
  지우기: `schtasks.exe /Delete /TN "paper-harness\daily-scan" /F`.
- 주간 작업(cron 진입점): `./run_weekly_agent.sh` — 금요일 17:00 KST, `db_retention`(백업 뒤 정리) → `agent_maintenance`(헤드리스 Claude 제안 →
  Codex 판정 → Python 검증·적용). 로그는 `logs/weekly_agent.log`. 모델 없이 브리프만 보려면 `.venv/bin/python agent_maintenance.py --brief-only`.
- **DB 스키마 변경**: 각 모듈의 DDL 은 `schema_guard` 를 통해서만 돈다. 운영 DB(테이블이 있는 파일)에는
  코드 실행만으로 적용되지 않는다 — `.venv/bin/python migrate.py` 로 빠진 것을 보고
  `migrate.py --apply`(WAL 포함 일관 백업 자동)로 적용한다. 새 설치는 `--apply --scope all`. 평가 DB 는
  `--scope evaluation`. 테스트는 conftest 가 플래그를 켜 임시 DB 에 바로 적용한다(2026-09-12).

## 테스트 · 검증 (정확한 명령어 그대로)

- 전체 테스트: `.venv/bin/python -m pytest` — 인자 없이 그냥 돌려도 안전하다.
  `pytest.ini` 의 `norecursedirs = data .venv .git` 이 ⑦ 재현이 clone 해 온 남의
  저장소를 수집 대상에서 뺀다. 이 줄을 지우면 없는 의존성 import 로 100건 넘게 깨진다.
- 단일 테스트: `.venv/bin/python -m pytest test_verify_units.py::test_이름`
- 커버리지: `.venv/bin/coverage run -m pytest && .venv/bin/coverage report`
  (`pytest-cov` 는 설치돼 있지 않다 — `--cov` 옵션은 없다. `coverage` 7.x 를 직접 쓴다)
- 회귀 기준선: `.venv/bin/python eval.py` — 저장된 전체 요약의 통과율을 잰다.
  기록된 기준선은 39편 · pass_ratio 0.982 다(내부 품질 확인용 — 메일에는 검증 수치를 싣지 않는다, 2026-09-14).
  값이 흔들리면 원인을 분석해 `docs/PROGRESS.md` 에 있는 그대로 적는다.
- 린터·포매터·타입체커를 두지 않는다. Ruff · Black · isort · mypy 설정이 없고, 추가하지 않는다.
  `pyproject.toml` 도 없다. **완료 조건은 pytest 전체 green 이다**.
- 네트워크가 필요한 테스트가 섞여 있다(`test_smoke.py` 등). 오프라인에서 그 파일이 실패하는
  것은 코드 결함이 아니다 — 판단 전에 실패 사유를 확인한다.

## 코드 스타일 · 컨벤션

- 타입 힌트를 쓴다(비테스트 함수 257개 중 245개에 반환 타입이 있다). 새 코드도 붙인다.
- 주석과 docstring 은 **한국어 평서체**로 쓰고, 무엇을 하는지가 아니라 **왜 그렇게 했는지**를
  적는다. 실측 결과와 날짜를 함께 남기는 것이 이 저장소의 관행이다. 이 톤을 유지한다.
- 모듈 docstring 첫 줄은 담당 단계로 시작한다 — 예: `"""② 중복 제거·선별 — 결정적 규칙, 네트워크·LLM 미사용."""`
- 파일 수정은 `sed` 대신 str_replace 또는 `python3 << 'PYEOF'` heredoc 을 쓴다(한글·특수문자 치환 사고 방지).
- `selection.py` 를 `select.py` 로 바꾸지 않는다 — 표준 라이브러리 `select` 를 가려 asyncio 가 깨진다.

## 아키텍처 메모

코드만 보고 유추하기 어려운 것만 적는다. 전체 지도는 `README.md`, 결정 이력은 `docs/PROGRESS.md`.

- `server.py` — MCP 도구 12종. ④ 요약, ⑥ 사람 판단, ⑦ 재현은 이 서버의 일이 아니다.
- `summarize_engine.py` — ④ 요약 엔진(Gemini 우선/Groq 대체). 긴 논문은 청크로 나눠 전문을 읽는다.
- `sentence_grounding.py` — ④⑤ 공유. 원문에 `[S번호]` 태그를 붙이고, ⑤ 가 같은 함수로 다시 나눠 대조한다.
- `verify.py` — ⑤ 수치 검증기. 문자열 대조만 하고 LLM 을 쓰지 않는다. `[S번호]` 가 있으면 그 문장(±1) 안에서만 찾는다.
- `docker_runner.py` — ⑦ 격리 실행. `reproduce(arxiv_id)` 가 이 저장소의 유일한 자율 재시도 루프다(최대 3회).
- `digest.py` · `trend_report.py` · `email_delivery.py` — ⑨ 배달.
- `feedback_links.py`(반응 버튼 서명·수집) → `feedback_weights.py`(매일 스캔 직전 가중치 조정, Python 만) → `agent_maintenance.py`(주 1회 키워드·검색어·
  제외어 조정). 셋 다 `create_profile` revision 으로 쓰고 origin 이 `feedback`·`agent` 로 갈린다. 에이전트 변경의 검증 규칙(좋아요 근거·제외어 2편·
  사용자 키워드 삭제 금지)은 프롬프트가 아니라 `agent_maintenance.validate` 가 강제한다 — 두 모델이 동의해도 통과 못 한다.
- `mail_ledger.py`(발송 회차·논문 기록) · `ops_dashboard.py`(운영 화면 자료 — 화면 `review_app.render_research_tab` 은 그리기만) ·
  `code_ladder.py`(⑦ 코드 단계: 공식→저자 연관→제3자→유사 구현→없음, 유사 구현은 표시만) · `sota_claims.py`(논문 자체 SOTA 주장 문장만, 미검증 표시).
- `term_hygiene.py` — ②주간·⑨동향 공용 용어 위생. 낱말·구절·우산어 목록과 `reject_reason` 이 여기 하나뿐이다.
- **⑦ 재현 시작점**: ④⑤ 저장 → ⑦ 재현은 현재 `docker_runner.launch_background()` 로 시작하고, 호출 지점은
  `batch_summarize._process_paper`(새벽 스캔)와 `review_core._summarize_target`(옛 검색 화면의 로직 — 화면에서는 2026-09-16 에 빠져
  지금 부르는 곳이 없다) 둘이다. 화면의 수동 재현 버튼 3개는 개편 때 없앴다.
  시작점이 흩어지면 같은 논문 재현이 겹친다 — 새 호출 지점을 만들 때는 이 목록을 갱신한다.
- 검증(`verify.py`)·재현 결과는 문자열 대조·Docker exit code·DB 기록으로 남는다. LLM 은 그 결과를 해석할 수 있지만
  결과 값 자체를 만들지 않는다(CLAUDE.md 규칙 7).

### 다룰 때 주의할 곳

- `docker_runner.py` · `email_delivery.py` — 격리 실행과 실제 발송을 맡는다. 바꾸면 기존 재현 성공 사례 회귀와 발송 경로 테스트를 같이 돌린다.
- `data/` — 자동 생성물이고 ⑦ 이 clone 해 온 외부 저장소가 들어 있다. 커밋 대상이 아니다. 오래된 데이터는 보존표(계획 v2 §9)대로 백업 뒤 정리한다.
- `.env` — 읽지 않는다.
- `docs/patent/` · `docs/paper/` — 사내 문서, `.gitignore` 대상. 외부 LLM 입력에 넣지 않는다.
- `prompts/*.md` — 프롬프트 자산이고 버전 관리 대상이다.

## 위임받은 도구가 지킬 것 (Codex 등)

이 저장소는 Claude Code 와 Codex 가 함께 만진다. **누가 언제 무엇을 맡길지**는
`~/.claude/CLAUDE.md`(사용자 전역) 몫이고 여기 쓰지 않는다. 여기 있는 것은
**일을 맡은 쪽이 이 저장소 안에서 지킬 것**이다 — Codex 는 `~/.claude/CLAUDE.md`
를 읽지 않으므로 그쪽에 적으면 못 본다.

아래는 관행이 아니라 **실제로 사고가 났던 자리**다. 2026-09-08 하루의 협업에서
나온 것이 대부분이다.

- **기본은 읽기 전용이다.** 검토·조사를 맡았으면 코드도 테스트도 고치지 않고
  발견만 보고한다. 쓰기가 필요하면 위임하는 쪽이 **허용된 파일을 명시**한다.
  명시가 없으면 안 고치는 쪽을 고른다.
- **커밋·push 하지 않는다.** 파일만 만들어 두면 통합은 위임한 쪽이 한다.
- **주석이 아니라 코드를 근거로 삼는다.** 이 저장소는 주석이 코드보다 강한
  주장을 하는 사례가 반복됐다(알려진 함정 참고). 2026-09-08 에는 그 함정을
  적어 둔 쪽이 같은 함정에 빠져 "done 은 래칫을 전진시킨다"는 틀린 설명을
  주석·커밋·PROGRESS 에 세 곳이나 남겼다. **인용하기 전에 실행 경로를 확인한다.**
- **수치는 저장소에서 뽑고, 재보지 않은 값은 "미실측"이라고 쓴다**.
  근거 위치(파일:줄, PROGRESS 절 번호, 커밋 해시)를 함께 남긴다. 추측이면
  추측이라고 명시한다. 없는 것을 "없다"고 말하는 것도 결과다 — 억지로 찾지 않는다.
- **테스트를 고쳐서 통과시키지 않는다.** 통과하는 것과 지키는 것은 다르다 —
  2026-09-08 에 새로 붙인 테스트 3개가 운영 코드의 `min` 을 부르지 않고 테스트
  안에서 다시 계산해, `min` 을 `max` 로 바꿔도 67개가 전부 통과했다.
  **테스트를 낼 때는 "이 테스트가 무엇을 망가뜨리면 실패하는가"를 같이 답한다.**
- **긴 산출물은 절별로 저장하며 나아간다.** 다 만들고 한 번에 쓰지 않는다.
  2026-09-08 에 문서 작업이 9분 동안 자료만 읽다 사용량 한도에 걸려 **아무것도
  남기지 못하고** 죽었다. 조사와 집필을 번갈아 하면 중간에 끊겨도 앞부분이 남는다.
- **이미 재둔 값을 다시 재지 않는다.** 위임하는 쪽이 실측 자료를 함께 주면
  그것을 먼저 읽고 예산을 본래 일에 쓴다.
- **추가 결제를 해법으로 내지 않는다.** 한도에 걸리면 그 사실을 그대로 보고하고 폴백·백오프·처리량 축소를 먼저 본다.
- **실패를 실패로 보고한다.** 한도 초과·로그인 오류·도구 실패로 일을 못 했으면
  그렇게 말한다. 검토하지 않았는데 검토했다고 쓰지 않는다.
- 설계 판단과 실측 결과는 `docs/PROGRESS.md` 에 날짜와 함께 남긴다.
  지시 범위 밖에서 발견한 문제는 위임한 쪽에 보고한다.

## Git · 커밋

이 저장소의 **실제** 관행이다. 일반적인 GitHub Flow 와 다르므로 그대로 따른다.

- 브랜치는 `main` 하나다. 기능 브랜치·PR 절차를 쓰지 않는다.
- 커밋 제목은 한국어 서술형 한 줄이다. Conventional Commits(`feat:` `fix:`)를 쓰지 않는다.
  발견한 사실을 문장으로 적는다 — 예: `arXiv 429 하나가 그날을 통째로 죽였다 — 주석이 말하던 걸 코드가 안 하고 있었다`
- 모든 코드 변경은 대응 테스트와 함께 커밋한다. pytest 전체 green 이 완료 조건이다.
- 설계 결정과 실측 결과는 `docs/PROGRESS.md` 에 날짜와 함께 남긴다.
- **에이전트는 사용자가 요청하지 않는 한 커밋·push 하지 않는다.**

## 보안 · 안전

CLAUDE.md 규칙 4·5 의 실행 사실이다.

- 외부 논문·PDF·URL·저장소는 비신뢰 입력이다. 재현 실행 컨테이너는 `--network none` · cap-drop ALL · no-new-privileges ·
  read-only · nobody · pids/메모리/CPU 상한으로 돈다(`docker_runner._SECURITY_FLAGS`). URL 수집·PDF 크기·빌드 단계 네트워크·
  clone 크기 제한은 보강 중이다(계획 v2 §4).
- 논문 본문은 `injection_scan` 이 인젝션 의심 패턴을 표시한다(차단은 안 한다). LLM 입력에서 논문은 데이터로만 다룬다.
- LLM 입력에 넣어도 되는 것: 공개 논문 텍스트·제목·초록·저자·venue, 관심 키워드, 피드백 집계. 넣지 않는 것: 시크릿, 사내 문서.
- 시크릿을 코드·로그·커밋 메시지에 남기지 않는다. `.env` 와 `data/` 는 `.gitignore` 에 있다.
- 실제 메일 테스트 발송·DB 대량 삭제는 사용자에게 알린 뒤 백업하고 한다.
- 수치를 만들어내지 않는다. 실측하지 않은 값은 "미실측"이라고 적고, 실패는 실패로 적는다.

## 알려진 함정

과거에 실제로 틀렸던 것들이다.

- **`pytest` 를 `data/` 까지 수집하게 두면 깨진다.** ⑦ 이 clone 한 외부 저장소의 conftest 를
  같이 물어 100건 넘게 실패한다(2026-08-18 실측). `pytest.ini` 의 `norecursedirs` 를 지우지 않는다.
- **주석이 말하는 것과 코드가 하는 것이 다를 수 있다.** arXiv 429 재시도가 주석에만 있고 코드에는
  없어서 하루치 스캔이 통째로 죽은 적이 있다. 주석을 근거로 "이미 처리됨"이라고 판단하지 말고 코드를 본다.
- **합성 ID 는 도착할 때의 신원이 아니다.** arXiv 밖 논문은 `pdf-<내용 해시 10자리>` 형태의 ID 를 쓴다.
  `arxiv_id` 컬럼에 들어가지만 arXiv 논문이라는 뜻이 아니다 — 출처는 `papers.source` 컬럼이 가른다.
- **본문 확보 여부는 관련성이 아니다**(2026-09-11 개정). 그전에는 "초록만 있는 논문이 본문 있는
  논문의 자리를 먹지 않게" 링크 유무로 내용 자리를 갈랐다(`_eligible_for_content`, 2026-09-06 실측:
  저널 OA 링크 20편 중 6편만 실제로 열렸다). 그런데 그 문지기가 **상위 관련 논문을 하위 논문 뒤로**
  보내 "관련성 계층 우선" 계약을 깼다. 초록 정리 경로(§8-41)가 생겨 원래 걱정(자리만 먹고 처리 실패)이
  사라졌으므로 문지기를 뺐다(docs/ASTRA_PLAN_2026-09-10.md §5.5, PROGRESS §8-86). 지금도 유효한 절반은
  이것이다 — **본문 확보 실패를 링크 존재로 판단하면 안 된다. 받아본 결과를 믿는다.**
- **키워드 매처는 낱말 사이에 공백·하이픈류·언더스코어·슬래시·괄호만 허용한다**(match-v2, 2026-09-12).
  그전엔 통째 escape 라 "large language model (LLM) agents"·"vision--language models"·"MVTec-AD" 를
  놓쳤다 — 1.0 계층 키워드가 가장 흔한 표기를 못 잡고 있었다. 넓히고 싶어도 `\W+`·`.*` 로 가지
  않는다(사이에 다른 낱말이 끼면 다른 뜻이다). 정책 버전 `rank-tuple-v1+match-v2`.
- **용어 후보 필터는 정확 토큰 일치가 아니다**(2026-09-13, §8-108·109). 후보 생성은 `term_hygiene.ngrams`(문장 경계·
  `reject_reason`·우산어) 하나이고 세 소비자(`trend_report.emerging_terms` · `term_discovery.discover` · `rule_advisor`)가 같이 쓴다(2026-09-14 통일). 판정용 정규화
  (하이픈 분리·단수화)와 표시 문자열을 가르고, **낱말 목록(any-token)과 담화 구절, 우산어(all-token)를 합치지
  않는다** — `state` 를 낱말 목록에 넣으면 `state estimation` 이 죽고(실제로 죽어 있었다), 우산어를 any 로 바꾸면
  `world model` 이 죽는다. 새 금지어는 재생 픽스처에서 실제로 올라온 것만 넣는다(whack-a-mole 금지).
- **순위는 가중합이 아니라 튜플이다**(2026-09-11, v2 2026-09-16). `profile_scoring.rank_key` 가
  `(계층, 3일 날짜 띠, -개념 폭, -날짜, -도메인, 키)` 로 정하고 `priority` 는 설명 필드다. 띠의 기준은 그 스캔의 가장 최신 후보이고(절대 달력 아님),
  폭은 문자열이 아니라 **개념**(robot/robotic·LLM/LLM-based 변형은 하나)으로 센다 — `rank-tuple-v2+match-v2+band0.1+dateband3`. 선정 경로에 합산
  재정렬(MMR·자리 상한·문지기)을 다시 붙이면 계약이 깨진다 — `test_rank_contract.py` 가 감시한다.
- **거짓 성공을 기록하지 않는다.** TSPulse 재현이 실패했는데 성공으로 기록된 사례가 있다(PROGRESS.md).
- **arXiv 는 OR 항이 많은 질의를 못 받는다**(2026-09-16 실측). 키워드 47개 질의 하나는 36초 뒤 503, 24개씩 둘은 10·15초에 200. `run_profile_scan` 이
  `ARXIV_TERMS_PER_QUERY`(20)개씩 갈라 던지고 합친다 — 프로필 키워드를 늘릴 때 이 상한을 없애거나 `_arxiv_query_from_core_topics` 하나로 되돌리지 않는다.
- **테스트가 운영 DB 를 건드릴 수 있다**(2026-09-16 실측). conftest 가 켠 DDL 플래그 아래서 `storage.DB_PATH` 기본값으로 가는 코드는 운영 DB 에
  표를 만들고, 스캔 테스트가 실제 `gh` 검색을 불렀다. 새 모듈은 DB 경로를 인자로 받고, digest 를 부르는 테스트는 격리 픽스처(`isolated_store`)를 쓴다.
  conftest 가 `code_finder.github_search` 를 기본 거부한다 — 검색이 필요한 테스트는 가짜로 덮는다.
