# paper-harness

관심 분야의 최신 논문을 매일 스스로 찾고 읽고 검증해서, 사람이 아침에 메일 한 통으로 "이 분야가 어디로 가고 있나"를 알게 하는 하네스다. 매일 05:00(KST) cron이 `./run_daily_scan.sh`를 부르고, 프로필별 검색·요약·다이제스트·메일 배달까지 무인으로 돈다. MCP 도구 12종은 이 안의 검색·수집·검증·저장 계층이며, Claude Code에서 개별 도구를 부르는 것은 탐색·디버깅 경로다.

**④ 요약은 무료 API가 쓴다 — Gemini 우선, Groq 대체.** Claude Code/Codex 자신이 채팅에서 요약을 작성하는 방식과 로컬 LLM 파인튜닝은 비용·무인 실행 문제로 폐기했다. 전부 무료여야 하며 유료 API·유료 티어·결제수단 등록을 도입하거나 해법으로 제안하지 않는다. 한도에 걸리면 무료 provider 폴백, Retry-After를 따르는 백오프, 처리량 축소 순으로 대응한다.

외부망 운영을 전제로 한다(2026-07-30 확정). 내부망 이식(vLLM + 자체 도구 루프)과 옛 "Phase 0 / Phase 1" 구분은 현재 계획이 아니다. 자동 운행은 기존 진입점을 재사용하며, 단계를 다시 꿰는 별도 지휘자 계층을 추가하지 않는다.

## 매일 도는 운영 경로 (2026-09-09)

```text
매일 05:00 KST cron
  → ./run_daily_scan.sh
  → .venv/bin/python run_profile_scan.py --all --send --max-pages 30
  → 등록된 프로필별 델타 검색·채점·본문 확보·요약·검증·저장
  → 동향 서술을 붙인 다이제스트 → 프로필 수신자에게 메일
```

**실제 자동 흐름은 ①→②→③→④→⑤→⑧→⑨이고, ⑦은 저장 직후 비동기로 갈라져 나간다.** ① 검색 · ② 중복 제거·선별 · ③ 본문 확보 · ④ 요약 · ⑤ 수치 검증 · ⑥ 사람 판단 · ⑦ 코드 재현 · ⑧ 축적 · ⑨ 배달이라는 번호는 역사적 이름이지 실행 순서가 아니다. ⑥ 사람 판단 게이트는 2026-08-24 자동 흐름에서 빠졌다. 사람의 승인을 기다리지 않고 처리하며, `review_app.py`는 결과 열람·수동 조작 UI로 남아 있다. ⑦은 `batch_summarize._process_paper()`가 요약을 저장한 뒤 `docker_runner.launch_background()`로 시작한다. 메일은 ⑦의 완료를 기다리지 않고 그때의 재현 상태를 담는다.

`run_daily_scan.sh`는 `flock`으로 중복 실행을 막고 `logs/daily_scan.log`에 시작·종료와 실행 출력을 남긴다. 실행 뒤 `scripts/morning_report.py`가 운영 보고서를 만든다. 보고서 생성 실패가 스캔 종료코드를 덮지 않는다. `--all`은 등록된 프로필 전체를 순회하고, `--send`는 각 프로필의 활성 수신자에게 발송한다. 한 프로필의 실패가 다른 프로필을 막지 않으며, 스캔 예외·발송 실패·빈 프로필 목록은 종료코드 1로 보고한다. 프로필 수신자가 없는 경우는 발송하지 않으며 종료코드만으로 메일 도착을 증명할 수 없다.

### 프로필·검색 씨앗·채점 가중치는 서로 다른 설정이다

프로필은 무엇을 관찰할지와 어떤 결과를 먼저 읽을지를 저장한 연구 관심 설정이다. 핵심 키워드, 대상 도메인, 제외어, 선호 venue, 다이제스트 편수와 수신자를 묶는다. 2026-09-09 제공된 운영 현황은 `team_ai_advance` 하나이며 **core 키워드 38개 · S2 검색 씨앗 3개 · 도메인 21개 · 제외 7개**다.

| 설정 | 실제 적용 | 구분하는 이유 |
| --- | --- | --- |
| `core_topics` (`profile_keywords.kind='core'`) | arXiv는 **core 전부를 OR**로 검색한다. 두 소스에서 모은 후보의 제목·초록도 core로 채점한다 | 무엇에 관심 있는지를 정한다. 가중치가 낮아도 arXiv 검색에서 빠지지 않는다 |
| `s2_seeds` (`kind='s2_seed'`) | S2는 **명시된 씨앗만 각각 질의**한다 | 어떤 검색어로 후보를 데려올지를 정한다. 씨앗 자체가 채점 키워드로 자동 추가되지는 않는다 |
| `core_weights` (`kind='core'`의 `weight`) | core 적중의 중요도를 점수에 반영한다 | 씨앗이 있는 현재 프로필에서 가중치는 순위를 정하며, S2 질의 여부를 정하지 않는다 |
| `target_domain` · `venues` · `exclude` | 도메인·venue는 가점, 제외어 적중은 후보 제외 | 관심 주제의 적용 분야를 반영하고 동음이의어 등 불필요한 후보를 줄인다 |

검색 씨앗과 채점 중요도는 2026-09-09 분리했다([PROGRESS §8-79 관련 기록](docs/PROGRESS.md)). **하위 호환 예외**로 씨앗이 없는 옛 프로필은 `s2_delta.keywords_for_s2()`가 가중치 1.0 이상인 core를 질의어로 고른다. 따라서 모든 프로필에서 가중치와 검색이 무조건 독립이라고 읽으면 안 된다. 현재의 명시적 씨앗 설정에서는 core 가중치를 바꿔도 S2 검색어가 바뀌지 않는다. 씨앗을 빈 목록으로 지우는 것도 S2 비활성화가 아니라 이 하위 호환 경로로 돌아가는 동작이다.

채점은 `profile_scoring.py`의 결정적 계산이다. core에 맞지 않거나 제외어에 걸린 후보를 빼고, 가장 중요한 core 적중과 복수 적중, 도메인·venue·최신성을 반영한다. 검색 결과의 관련도를 LLM 판사에게 맡기지 않는다. 최종 내용 목록은 이 순위에 본문 확보 경로와 키워드별 자리 분산을 적용하며, 링크가 있어도 실제 처리가 실패로 끝나면 다음 후보로 채운다. 본문 수집 실패 뒤 초록 정리에 성공한 경우는 본문 요약과 구분해 남긴다.

### 델타 검색 창과 커서

어제 못 본 논문을 잃지 않으면서 매일 전체를 다시 검색하지 않기 위해 `search_runs`에 프로필·소스별 검색 창과 상태를 남긴다. `research_profile.next_since()`는 이력이 없으면 최근 7일을 보고, 직전 `done`이면 창 끝, `partial`·`failed`이면 창 시작을 다음 커서로 삼는다. 어느 경우든 색인 지연과 이월 후보를 위해 **최소 최근 5일을 다시 본다**. 완료됐다고 항상 검색 시작일이 앞으로 움직이는 것은 아니다.

arXiv와 실제 질의하는 S2의 커서 중 더 이른 시각을 공통 시작으로 쓴다. 검색어 지문은 arXiv는 core 목록, S2는 실제 씨앗 목록으로 따로 계산한다. 기존 지문과 달라지면 최근 7일을 다시 보며, 질의하지 않는 S2는 창 계산에서 뺀다.

arXiv는 `find_new_papers.py`가 제출일 범위와 최신순 정렬로 요청하고, `delta_search.py`가 페이지와 날짜 경계를 처리한다. 운영의 `--max-pages 30`은 이 arXiv 페이지 상한이며, 상한에 닿으면 `partial`이다. S2는 `publicationDateOrYear`로 날짜 범위를 검색한다. S2의 별도 씨앗당 페이지 상한까지 정상 수집한 경우는 의도한 범위의 `done`으로 기록하지만, 시간 예산·페이지 실패·offset 상한 때문에 못 본 경우는 `partial`로 남긴다. **`done`은 모든 관련 논문을 빠짐없이 찾았다는 뜻이 아니다.**

### 다이제스트·동향 서술·메일

상위 후보는 기존 `_process_paper()`로 본문 확보·요약·수치 검증·저장을 수행한다. 무료 API가 느려지면 Deep Layer 시간 예산으로 다음 논문 처리를 미루며, ⑤를 통과할 때까지 요약을 반복하지 않는다. 본문이 없는 논문도 초록 정리나 서지정보로 구분해 관찰 대상에 남긴다. 본문 확보 링크의 존재를 본문 검증 완료로 표시하지 않는다.

`digest.py`는 요약과 검증·재현·철회 상태 등을 평문·HTML 다이제스트로 렌더링한다. `trend_report.py`는 매일 내용 목록과 별도 서지 후보의 제목·초록, 내용 목록의 저장 요약 결과 절을 바탕으로 동향 서술을 작성한다. 수치의 자료 내 존재 여부를 점검하고 근거를 못 찾은 수치는 표시한다. 이는 서술의 의미나 인과관계가 검증됐다는 뜻은 아니다. 서술에 쓸 자료가 3편 미만이면 서술을 생략한다. 월요일에는 시스템 현지 요일 기준으로 주간 비교와 인용망을 포함한 주간 리뷰를 붙인다. 서술·주간 리뷰가 실패해도 다이제스트 배달은 계속한다.

다이제스트 평문은 DB에 저장해 UI에서도 읽는다. 메일은 `email_delivery.py`가 평문·HTML을 함께 보내며, 후보가 0편인 날도 발송한다. 매일 오는 메일 자체가 운행 확인 신호이기 때문이다. **실제로 수신자에게 발송한 뒤에만 내용 목록을 `profile_shown`에 배달된 것으로 기록한다.** 발송 실패나 수신자 없음, `--send` 없는 수동 스캔은 소비 처리하지 않아 다음 검색 창에서 다시 배달될 기회를 남긴다.

### 주간 프로필 개선 루프 (2026-09-11, docs/ASTRA_PLAN_2026-09-10.md)

일일 전달과 별개로, 매주 한 번 관측 이력에서 검색 프로필 변경 후보를 제안하고 적용 전 영향을 분석한다.
**모든 프로필의 일일 처리·전달이 끝난 뒤** 돈다(`scan_all_profiles` 끝). 제안기 장애·예산 소진·관측
없음은 전부 정상 종료이고 메일에 영향을 주지 않는다.

```text
스캔별 관측 (B) → 제안 (D: LLM / E1: 규칙) → 검증 → 영향 분석·게이트 (C) → 기록
                                                          └ eligible_for_apply 이고 모드가 auto_apply 일 때만 적용
```

지금 상태: 게이트 임계값이 **미설정**이라 어떤 제안도 `eligible_for_apply` 가 되지 않고, 운영 모드는
`proposal_only` 다. 즉 실제 적용은 0건이고 제안·분석·기록만 쌓인다. 임계값은 기준선 2주 뒤 평가 시작
전에 고정한다(§11.7). 선별 순서 계약(`rank-tuple-v1`)은 PROGRESS §8-86, 각 단계는 §8-87~91.

## 구성

일일 운영을 맡는 모듈과 공통 기반은 다음과 같다. 역할은 2026-09-09 작업 트리의 실행 코드를 기준으로 적었다.

| 파일 | 역할 |
| --- | --- |
| `run_daily_scan.sh` · `run_profile_scan.py` | cron 진입점과 프로필별 검색·선별·기존 처리 함수 호출·다이제스트 저장·발송 |
| `research_profile.py` | 프로필·키워드·씨앗·수신자, 검색 이력·후보·배달 기록·최근 다이제스트의 SQLite 저장과 검색 커서 계산 |
| `profile_scoring.py` | 제목·초록의 키워드 적중으로 관련도와 순위를 계산하며 점수 근거를 반환 |
| `find_new_papers.py` · `delta_search.py` | arXiv 날짜 범위 요청과 순수 페이지 수집·날짜 경계 계산을 분리 |
| `s2_delta.py` | S2 씨앗별 날짜 범위 검색, 페이지·시간 예산과 부분 수집 상태 처리 |
| `digest.py` | 저장된 요약·검증·재현 상태와 스캔 결과를 평문·HTML로 렌더링 |
| `trend_report.py` | 매일 동향 서술, 주간 키워드·저자·출처 비교와 인용망 리뷰. 주간 모집단은 발표일이 아니라 요약 저장일 기준 |
| `email_delivery.py` | Gmail SMTP STARTTLS로 평문·HTML 메일 발송. `SMTP_USER` · `SMTP_PASSWORD`를 참조 |
| `retraction.py` | arXiv ID에서 DOI를 만들어 OpenAlex 철회 신호를 조회하고 필요하면 Crossref 갱신 유형으로 확인. 미조회·의심·철회·비철회를 구분 |
| `review_core.py` | Streamlit에서 분리한 요약·업로드·검증 상세·재현 상태 조회 로직. 수동 요약도 저장 직후 ⑦을 시작 |
| `storage.py` | 저장 경로, 기본 DB 스키마·WAL 초기화, SQLite 연결과 arXiv ID 정규화 |
| `http_client.py` · `pacing.py` | arXiv·S2 HTTP 요청·재시도와 공통 호출 간격 제어. S2 429가 반복되면 간격을 늘림 |
| `api_usage.py` | provider·응답 결과별 프로세스 내 호출 계수와 실행·논문별 구간 집계. 별도 프로세스인 ⑦은 이 합계 밖 |
| `injection_scan.py` | 논문 텍스트의 지시문·비정상 유니코드 패턴을 탐지해 의심 이유를 반환. 안전을 보증하는 판정기는 아님 |
| `scripts/morning_report.py` | 최근 일일 로그와 DB의 운영 상태를 모아 `logs/morning_report.txt`에 보고 |
| `observation_signals.py` | 스캔별 관측(`candidate_observations`·`scan_runs`)에서 씨앗 수율·출처 기여·탈락 사유를 센다. 관측 없는 기간은 0 이 아니라 미측정 (B, 2026-09-11) |
| `profile_impact.py` | 프로필 변경 하나의 적용 전 영향 분석 — 고정 스냅샷에서 전후 재채점, 게이트 상태 7종. 임계값 미설정이면 `eligible_for_apply` 가 나오지 않는다 (C) |
| `profile_advisor.py` · `prompts/profile_advisor_v1.md` | 주간 LLM 프로필 제안기. 밖에 나가는 것은 관심사와 논문 제목·초록·키뿐. HTTP 요청 2회 상한을 영속 장부로 지킨다. 운영 모드 기본 `proposal_only` — 적용은 닫혀 있다 (D) |
| `rule_advisor.py` | 규칙 기반 제안기 R — LLM 제안기와 같은 입력·같은 계약·같은 게이트. F/R/A 비교의 R 팔 (E1) |
| `evaluation.py` | 지연·비용·제안 효율·core 적중 비율, 독립 라벨이 있을 때만 의미상 지표, 시점 누수 없는 재생, 불변 실험 manifest (E1) |
| `profile_health.py` | 프로필 건강 지표 — 스캔별 당시 스냅샷 재채점으로 anchor 적중·계층·최신성·제외어 충돌을 센다. "적용 후 악화"의 정의(규칙은 미설정으로 시작) |
| `term_discovery.py` | 탐색 차선 — 키워드에 안 걸려 탈락한 논문에서 n-gram 후보 용어를 로컬로 찾고 용어당 증거 논문만 제안기에 넘긴다(LLM 은 검토자) |
| `schema_guard.py` · `migrate.py` | DDL 은 `PAPER_HARNESS_APPLY_DDL=1` 일 때만 실행, 아니면 대조만 하고 뒤처지면 멈춘다. 모든 스키마 변경(새 설치 포함)은 `migrate.py --apply [--scope operational\|evaluation\|all]` 하나로 — WAL 을 포함한 일관 백업 → 적용 → 재대조 |
| `shadow_search.py` | ⑦ shadow 검색 — 검색 집합을 바꾸는 변경안(씨앗·질의)을 두 팔로 격리 실측. 운영 커서·후보·배달 기록 불변, 결과는 shadow_runs 한 표. 게이트의 needs_shadow_search 를 푼다 |
| (research_profile) `profile_keyword_events` · (profile_impact) `gate_decisions` | 키워드 세대 이력(전후 논리 diff, actor/provenance 분리, rollback 은 세대 복원) · 게이트 판정마다 실제 규칙을 남기는 감사 표. 적용기는 판정 기록을 대조한다 |

기존 도구·요약·재현 계층은 그대로 재사용한다.

- `server.py` — MCP 서버 (stdio). 도구 12종 + ⑥ 검토 상태 저장·이미지 추출 헬퍼 + ③ arXiv 밖 논문 수동/오픈액세스 수집. `.env` 자동 로드
- `batch_summarize.py` — ④ 온디맨드 배치 요약 (Claude Code 밖 독립 실행, `server.py` 함수 직접 import)
- `review_app.py` — 결과 열람·프로필 설정·수동 수집·재처리 UI (Streamlit). 자동 흐름의 승인 관문은 아니다. `.streamlit/config.toml`로 테마를 적용한다
- `summarize_engine.py` — ④ 요약 엔진(Gemini 우선/Groq 대체). 긴 원문을 청크로 나누고 엔진별 처리 상한과 대기를 적용한다. 제목으로 서베이/실증 연구 템플릿을 고르며 실제 처리 커버리지를 반환한다. 배치와 수동 요약 경로가 공유한다
- `code_finder.py` — ⑦ 코드 저장소 후보 탐색 (본문 링크 스캔 + GitHub 검색 + HuggingFace 모델카드 경유 GitHub 링크 추적)
- `docker_runner.py` — ⑦ Docker 격리 실행. `reproduce(arxiv_id)` 가 유일한 자율 재시도 루프(최대 3회)
- `selection.py` — ② 중복 제거·선별 규칙 (네트워크·LLM 미사용)
- `sentence_grounding.py` — ④⑤ 공유: 원문을 문장 단위로 잘라 `[S번호]` 태그를 붙인다. ④가 태그 붙은 원문을 LLM에 보내고, ⑤가 같은 함수로 원문을 다시 나눠 인용된 문장 안에 숫자가 실제로 있는지 대조한다
- `hybrid_search.py` — ① 로컬 저장 논문 검색: BM25 + 임베딩 코사인 유사도를 Reciprocal Rank Fusion으로 합치는 순수 계산 모듈 (네트워크는 임베딩 호출 하나뿐, DB 접근 없음 — 캐싱은 `server.py` 쪽 책임)
- `summary_parser.py` — 저장된 요약을 구조화 JSON으로 변환. `### 절 제목` 구조를 살려 불릿 목록으로 뽑고 `verify.py`로 각 수치를 재검증해 `found`/`grounded`/`sentence_id`를 붙인다
- `verify.py` — ⑤ 수치 검증기. `[S번호]` 태그가 있으면 그 문장(±1) 안에서만, 없으면(구형 요약) 원문 전체에서 문자열 대조 (LLM 미사용)
- `prompts/summary_template.md` — 요약 템플릿 v2 와 작성 규칙 (프롬프트 자산, 버전 관리 대상)
- `prompts/summary_template_survey.md` — 서베이/리뷰 논문 전용 변형 (분류체계·하위주제 비교 구조, 절대 규칙 R1~R6은 동일)
- `eval.py` — 저장된 전체 요약의 통과율 일괄 측정 (회귀 기준선)
- `test_*.py` — 테스트 파일 35개, 전체 pytest **785개 통과**(2026-09-09 제공된 실측). `test_smoke.py` 등 네트워크가 필요한 테스트도 포함한다
- `data/` — PDF·추출 텍스트·요약·이미지·SQLite 인덱스 (자동 생성, 커밋 제외)
- `.env` — `GOOGLE_API_KEY` · `GROQ_API_KEY` · `S2_API_KEY` · `UNPAYWALL_EMAIL` · `OPENALEX_API_KEY` · `SMTP_USER` · `SMTP_PASSWORD` 등의 설정 (커밋 제외, 시크릿을 읽거나 출력하지 않는다)

`selection.py` 는 `select.py` 로 두면 표준 라이브러리 `select` 를 가려 asyncio 가 깨지므로 이 이름이다.

## 도구 12종

| 도구 | 단계 | 비고 |
| --- | --- | --- |
| `arxiv_search_papers` | ① | 키 불필요, 호출 간 3초 강제 |
| `s2_search_papers` | ① | 인용수 제공. `S2_API_KEY` 없으면 공용 한도 |
| `s2_get_references` | ① | 인용망 backward — 이 논문이 인용한 것 |
| `s2_get_citations` | ① | 인용망 forward — 이 논문을 인용한 것 |
| `hybrid_search_local_papers` | ① | 로컬 저장 논문 대상 BM25+임베딩 하이브리드 검색 |
| `dedupe_and_rank_papers` | ② | 결정적 규칙. 네트워크 미사용 |
| `fetch_paper` | ③ | HTML 우선 → PDF 폴백. 멱등 |
| `get_paper_text` | ③ | 저장 텍스트 분할 열람 |
| `verify_summary_numbers` | ⑤ | 읽기 전용 |
| `save_summary` | — | 저장 직전 자동 검증, 불일치도 저장은 함 |
| `get_summary_json` | — | 저장된 요약을 구조화 JSON으로 변환. 읽기 전용 |
| `list_stored_papers` | — | 저장소 목록 |

`s2_get_references`/`s2_get_citations`는 Crawler/Selector 패턴(PaSa)에서 **Crawler만** 구현한다 — depth는 항상 1, 후보 수는 `limit`으로 코드가 상한을 강제하는 결정적 조회다. 어떤 후보가 관련 있는지 판정(Selector)은 이 서버의 일이 아니다 — 반환된 제목·초록을 사람이나 Claude Code가 보고 판단한다.

`hybrid_search_local_papers`는 `arxiv_search_papers`/`s2_search_papers`(외부 API 자체를 검색)와 다르다 — **이미 `fetch_paper`로 저장해 둔 논문들 안에서** 다시 찾는 도구다. BM25(어휘 일치)와 임베딩 코사인 유사도(`gemini-embedding-001`, 의미 일치)를 Reciprocal Rank Fusion으로 합친다. 논문 임베딩은 `paper_embeddings` 테이블에 캐시돼 재검색이 빠르다. `GOOGLE_API_KEY`가 없으면 BM25 단독으로 계속 동작한다(하이브리드가 안 되면 조용히 실패하는 대신 성긴 검색으로 저하만 시킴).

`get_summary_json`은 저장된 요약 마크다운을 `### 절 제목` 구조 그대로 살려 절마다 불릿 목록으로 뽑고, `verify.py`로 각 수치 주장을 다시 검증해 `found`/`grounded`/`sentence_id`를 함께 붙인다. 값의 조건·비교대상·지표 같은 자연어 세부 필드는 정규식으로 억지로 쪼개지 않는다 — 애매한 문장을 필드로 분류하는 것 자체가 판단이라 이 서버의 일이 아니다.

④ 요약, ⑥ 사람 판단, ⑦ 코드 재현은 이 서버의 일이 아니다.

도구 입력 스키마는 인자를 `params` 객체로 한 겹 감싼 형태다(단일 pydantic 모델을 받는 구조 때문). 동작에는 문제가 없다.

## ③ arXiv 밖 논문 — 수동 업로드 · 오픈액세스 자동 수집 (2026-08-04)

`fetch_paper`는 arXiv 전용이다. 그런데 실제 문헌 조사는 Nature·ScienceDirect·IEEE·ACM 처럼 arXiv에 없는 저널·컨퍼런스 논문이 더 많다 — 특히 산업/하드웨어 계열은 거의 그렇다. 이걸 못 넣으면 평가셋은 물론 하네스 자체의 실사용 범위가 arXiv로 좁혀진다. 그래서 두 경로를 추가했다:

| 함수 | 위치 | 용도 |
| --- | --- | --- |
| `server.ingest_local_pdf(pdf_bytes, title, source_note)` | `server.py` | 이미 합법적으로 접근 가능한 PDF(기관 구독 등)를 직접 업로드. 페이월 우회 아님 |
| `server.fetch_pdf_from_url(pdf_url, title, source_note)` | `server.py` | 오픈액세스 PDF를 URL로 직접 수집 |
| `server.resolve_unpaywall_pdf(doi)` | `server.py` | DOI → 합법적 오픈액세스 PDF 위치 조회 (Unpaywall API, 무료·키 불필요, `email` 파라미터만 요구) |

전부 **MCP 도구가 아니다** — `ingest_local_pdf`는 바이너리(PDF bytes)를 받는데 이걸 JSON 파라미터로 감싸는 건 MCP 관례에 안 맞아서, `save_repro_result`/`set_review_status`와 같은 "plain 함수, `server.py`에서 직접 import" 패턴을 그대로 따른다. `review_app.py`의 "PDF 업로드"·"DOI/URL(오픈액세스)" 입력 모드가 이걸 쓴다.

arXiv ID가 없는 논문은 `pdf-<내용 해시 10자리>` 형태의 합성 ID를 쓴다 — 같은 파일을 다시 올려도 같은 ID가 나와 `fetch_paper`처럼 멱등하다. `papers.arxiv_id` 컬럼 이름은 그대로 두고(스키마 변경 최소화), 새로 추가한 `papers.source` 컬럼으로 출처(`arxiv` / `manual-pdf: ...` / `open-access: ...`)를 구분한다.

**주의**: Unpaywall은 더미 이메일(`example.com` 등)을 422로 거부한다(실측 확인). `UNPAYWALL_EMAIL`을 `.env`에 설정하지 않으면 기본값(사용자 실제 이메일)을 쓴다.

**여전히 안 되는 것**: ScienceDirect·IEEE·ACM처럼 페이월이면서 오픈액세스도 아닌 논문은 자동 수집이 안 된다 — PDF 업로드 경로로 사용자가 직접 넣어야 한다. 이건 의도된 제약이다(라이선스·ToS 준수).

## 자율성 경계

arXiv·S2 공통 HTTP 경로는 일반 재시도 가능 오류에 최초 요청 뒤 **상한 2회** 재시도한다(`http_client.MAX_RETRIES`). 429는 별도 재시도 예산과 Retry-After·백오프를 적용하며, 400·404 같은 재시도 불가 응답은 즉시 실패한다. 재시도 여부를 LLM이 정하지 않는다. S2 시간 예산은 요청 전체를 강제로 끊는 엄격한 마감은 아니다(§미해결 참고).

④⑤ 는 루프를 돌지 않는다. 요약 생성 결과에 검증 1회를 붙인 뒤 저장·배달한다. API 오류 재시도와 긴 원문의 청킹은 검증 통과를 위한 재생성 루프와 다르다. **검증기를 루프 판정자로 쓰지 않는다** — 요약을 검증 통과까지 반복시키면 최단 통과 경로가 "숫자를 아예 쓰지 않는 것"이 되고, 통과율은 오르면서 요약 가치가 사라진다.

## 설치

현재 운영 환경은 Python 3.14이며, 저장소 루트에 `.venv`가 이미 있다. 기존 환경에서는 다시 만들지 않고 아래 설치·검증 명령을 쓴다.

```bash
python3 -m venv .venv                         # 새 환경에서만 생성
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest                    # 785개 통과 (2026-09-09 실측)
.venv/bin/coverage run -m pytest && .venv/bin/coverage report   # 커버리지
```

`pytest` 는 **인자 없이 그냥 돌려도 안전하다** — `pytest.ini` 의
`norecursedirs = data .venv .git` 이 ⑦ 재현이 clone 해 온 남의 저장소를 수집
대상에서 뺀다. 이 줄을 지우면 없는 의존성 import 로 100건 넘게 깨진다
(2026-08-18 실측).

`pytest-cov` 는 설치돼 있지 않아 `--cov` 옵션이 없다. `coverage` 7.x 를 직접 쓴다.
린터·포매터·타입체커를 두지 않고 추가하지도 않는다 — Ruff·Black·isort·mypy
설정이 없고 `pyproject.toml` 도 없다. **완료 조건은 pytest 전체 green 하나다.**

네트워크가 필요한 테스트가 섞여 있다(`test_smoke.py` 등). 오프라인에서 그 파일이
실패하는 것은 코드 결함이 아니므로, 판단하기 전에 실패 사유를 먼저 확인한다.

`mcp` 는 2.0 을 쓴다. 1.x 의 `FastMCP` 가 `mcp.server.mcpserver.MCPServer` 로 개편됐지만 데코레이터·`annotations`(dict 그대로)·`run()` 형태가 유지되어 이식은 import 와 인스턴스 생성 두 줄이었다. `Tool` 모델의 `inputSchema` 는 `input_schema` 로 바뀌었으나 `server.py` 는 이 필드를 쓰지 않는다.

무료 키의 용도·발급 안내는 [docs/API_KEYS.md](docs/API_KEYS.md), 메일 도착·라벨·로그 점검은 [docs/TRIAL_CHECKLIST.md](docs/TRIAL_CHECKLIST.md)를 따른다. 전체 구조와 결정 이력은 [시스템 개요(2026-09-08)](docs/SYSTEM_OVERVIEW_2026-09-08.md)와 [docs/PROGRESS.md](docs/PROGRESS.md)에 있다. 날짜가 붙은 문서의 수치는 그 시점의 기록이다.

## Claude Code 등록 (수동 탐색용)

```bash
claude mcp add paper-harness -- ~/paper-harness/.venv/bin/python ~/paper-harness/server.py
claude mcp list    # paper-harness: ... - ✔ Connected
```

**venv 의 python 을 절대경로로 지정할 것.** 시스템 python 으로 등록하면 의존성을 못 찾는다.

기본은 **local 스코프**라 `~/paper-harness` 에서 `claude` 를 띄울 때만 붙는다. 어느 디렉터리에서나 쓰려면 `-s user` 를 붙여 등록한다.

등록 문법은 버전에 따라 다르므로 확인할 것: https://code.claude.com/docs/en/mcp

Semantic Scholar 는 공용 한도에서 429 가 잦다 — **2026-08-01 키 발급·등록 완료**, `.env` 에 `S2_API_KEY=키값` 한 줄 추가하면 된다 (server.py 가 기동 시 자동으로 읽는다. `export` 로 셸에 직접 넣을 필요 없음).

키가 있어도 429가 사라지는 것은 아니다. `http_client.throttled_s2_get()`이 공통 페이서와 재시도를 적용하고, 429 응답을 받으면 호출 간격을 늘린다. 무료 한도를 고정된 처리 가능량으로 가정하지 않는다.

## 수동 실행·탐색 경로

표준 운영 경로는 앞의 cron → `run_daily_scan.sh`다. `review_app.py`는 결과 열람·수동 조작 UI이며 승인 관문이 아니다. 등록된 프로필을 수동으로 스캔하려면 같은 진입점을 쓴다. `--send`를 빼면 다이제스트를 생성·저장하지만 메일은 보내지 않는다. 검색·요약은 실행되므로 읽기 전용 명령은 아니다.

```bash
.venv/bin/python run_profile_scan.py team_ai_advance --max-pages 30
.venv/bin/streamlit run review_app.py
```

`batch_summarize.py`는 키워드로 특정 논문들을 온디맨드 처리할 때 남아 있는 별도 경로다. 매일의 프로필 델타 검색·메일 배달을 대신하는 표준 경로는 아니다.

```bash
.venv/bin/python batch_summarize.py --keyword "transformer 경량화" --top-n 3
```

MCP 개별 도구 호출은 탐색·디버깅에 쓴다. 수동 검색에서는 arXiv·S2 결과를 **함께** `dedupe_and_rank_papers`에 넣는다. 인용수는 S2만 주므로 arXiv 결과만으로는 같은 순위 근거를 얻지 못한다. 이 수동 선별과 매일의 `profile_scoring.py` 기반 채점은 구분한다.

`save_summary`는 저장 직전에 수치 검증을 자동 수행하지만 불일치가 있어도 **저장을 차단하지 않는다**. 불일치는 사람이 원문·출처를 확인할 신호로 남으며, 재생성이나 사람 승인 대기로 자동 전환하지 않는다.

## ③ 원문 파싱 — HTML 우선인 이유

pypdf 는 2단 조판과 표를 자주 뭉개고, 그게 ⑤ 의 거짓 불일치로 직결된다. arXiv HTML(LaTeXML 판)은 그 원인을 구조적으로 없앤다.

**HTML 제공 여부는 투고 시점으로 예측할 수 없다.** 실측 (2026-07):

| 논문 | 연도 | 경로 | 추출 |
| --- | --- | --- | --- |
| `1706.03762` | 2017 | **html** | 41,129자 |
| `2405.15793` | 2024 | **pdf** | 291,061자 |

옛 논문에 HTML 이 있고 최신 논문에 없다. arXiv 가 구논문 HTML 을 소급 생성했고, LaTeXML 변환이 실패하는 논문도 있다. 그래서 날짜로 분기하지 않고 무조건 HTML 을 먼저 시도한 뒤 404 면 폴백한다.

어느 경로였는지는 DB `papers.extract_method` 에 남는다. ⑤ 불일치를 볼 때 "PDF 표 깨짐"을 의심해야 하는지가 이 값으로 갈린다.

## 수치 검증기의 한계 (알고 쓸 것)

- 한 자리 정수(0~9)는 검증 대상에서 제외한다 (어디에나 존재해 무의미).
- 단위 환산(0.5m ↔ 50cm)은 탐지하지 못한다.
- `%` 는 토큰에서 분리해 대조한다. 따라서 `3.2%` 와 `3.2%p` 를 구분하지 못한다.
- PDF 추출 품질에 따라 원문에 있는 숫자가 누락 판정될 수 있다.
- 숫자가 없는 요약은 통과율 1.0 이 된다. **이 지표를 품질로 오해하면 안 된다.**
- 따라서 unmatched 는 "오류 확정"이 아니라 "사람이 확인" 신호다.

경계 규칙은 `test_verify_units.py` 에 잠겨 있다. 한국어 조사(`99.87도` → `99` 로 잘림)와 자릿수 경계(`28.4` 가 `128.45` 안에서 매칭)는 실제로 한 번씩 틀렸던 것이므로 회귀 테스트를 지우지 말 것.

## 평가셋 운용

`eval.py`는 저장된 전체 요약의 수치 검증 통과율을 보고한다. **회귀 기준선은 39편 · pass_ratio 0.982**이며 임의로 옮기지 않는다. 자동 운행으로 저장 요약이 늘었으므로 현재 전체 DB와 과거 39편 기준선은 같은 모집단이라고 가정하면 안 된다. 프롬프트·템플릿을 바꿀 때는 구성 변화와 검증 결과 변화를 구분한다.

```bash
.venv/bin/python eval.py                  # 저장 요약 전체의 통과율 보고
.venv/bin/python eval.py --min-ratio 0.9  # 지정 문턱 미달 시 종료코드 1
```

`--min-ratio 0.9`는 CLI 사용 예시이며 회귀 기준선 0.982를 바꾸는 지시가 아니다.

외부망 확정으로 "로컬 모델 전환 시 품질 하락 기준선"이라는 원래 목적은 없어졌지만, **프롬프트·템플릿 변경 회귀 감지용으로는 그대로 유효하다.**

## 운행 실측 (2026-09-09)

2026-08-10에는 신규 논문 4편의 ③수집·④요약·⑤검증 종단간 중앙값 약 70초를 실측했고, 병목은 ④ 요약 생성이었다(`docs/PROGRESS.md` §5). 이 편당 실측과 아래 일일 운행 전체 소요는 범위가 다르다.

아래는 이미 측정된 운영 결과를 제공받아 인용한 값이다. 이번 README 갱신에서는 스캔·pytest·eval을 재실행하거나 DB를 재측정하지 않았다.

| 항목 | 제공된 실측 |
| --- | --- |
| 운행 소요·종료 | 483초 · 종료코드 0 |
| API 호출 | 47회: arxiv 16 · gemini 8(503 1회 포함) · openalex 14 · s2 9(429 6회 포함) |
| 후보 | 신규 447편 |
| 요약·검증 | 6편 생성, 전부 Gemini · 수치 검증 전부 통과 |
| 메일 | 1명 발송 완료 |
| 같은 운행의 arXiv core 적중 | 388편 중 313편(80.7%) |
| 같은 운행의 S2 core 적중 | 59편 중 13편(22.0%) |
| 전체 테스트 | `.venv/bin/python -m pytest` 785개 통과 · 테스트 파일 35개 |
| 유지할 회귀 기준선 | `eval.py`, 39편 · pass_ratio 0.982 |

출처별 적중률은 검색 후보의 core 적중이며, 검색 회수율이나 요약 정확도가 아니다. 수치 검증 통과도 요약 전체의 의미적 정확성을 보증하지 않는다. 현재 요약의 의미적 정확도와 전체 관련 논문 대비 검색 회수율은 **미실측**이다. 이 한 번의 운행을 평균 처리 시간이나 매일의 성능 보장으로 쓰지 않는다. 같은 날짜의 프로필·씨앗 개정 후 성능도 이 운행값만으로 입증되지 않는다.

## 미해결

이 목록은 `docs/PROGRESS.md` §8 이 최신이다 — 이 파일은 갱신이 늦을 수 있으니 날짜 있는 최신 상태는 그쪽을 볼 것. 2026-09-09 기준 남은 항목은 다음 다섯 가지다.

- **cron이 도는 날과 안 도는 날을 기계가 구분하지 못한다.** 절전·종료로 예정된 실행을 놓칠 수 있고, 실행되지 않은 날을 별도로 감지하는 장치가 없다. 사용자는 당분간 그대로 두기로 했다(PROGRESS §8-72).
- **S2 검색 수율이 낮다.** 2026-09-09 운행에서 arXiv는 388편 중 313편(80.7%), S2는 59편 중 13편(22.0%)이 점수를 받았다. 그 전날 표본에서는 S2 517편 중 7편(1.4%)이었다. 서로 다른 표본이므로 개선율로 단정하지 않는다. S2는 relevance 검색이고 구문 검색이 아니어서 질의어가 돌아온 논문에 없을 수 있다(PROGRESS §8-73).
- **`SEARCH_BUDGET_SECONDS=300`은 엄격한 상한이 아니다.** 키워드 사이와 페이지 사이에서만 확인하므로 이미 날아간 요청 하나는 끊지 못한다. 2026-09-09 운행에서는 첫 씨앗이 2쪽에서 대기 상한 287초에 걸려 포기했고, 세 번째 씨앗은 실패했다(PROGRESS §8-75).
- **결합 가점은 두 적중에서 상한에 닿는다.** 서로 다른 core를 세 개 맞혀도 두 개와 동점이어서 여러 연구축이 만나는 논문을 그만큼 더 위로 올리지 못한다. 현재 계산은 연구축이 아니라 적중 문자열 수를 세므로 상한 변경에도 근거가 필요하다(PROGRESS §8-79의 남은 한계).
- **새 S2 씨앗 셋의 수율은 미실측이다.** 2026-09-09 운행에서 `world model` 42편·`vision-language-action` 21편이 잡혔지만 둘 다 전부 arXiv 경유였고 S2 경유는 0편이었다. `photoplethysmography`만 6편을 S2로 데려왔다. 이 한 번의 관측으로 새 씨앗의 수율을 확정할 수 없어 며칠 더 봐야 한다.

## 의존성 라이선스

mcp(MIT), httpx(BSD-3), pypdf(BSD-3), beautifulsoup4(MIT) — 작성 시점 기준이며 기관 반입 신청 전에 각 저장소에서 재확인할 것.

PyMuPDF 는 AGPL 이라 의도적으로 배제했다. 과제 계획서의 기술 실시·제3자 실시권 조항 때문이다. **의존성 추가 시 라이선스를 먼저 확인하는 것이 선행 조건이다.**

## 데이터 이용 주의

arXiv 논문 대부분은 기본 라이선스로 제출되어 제3자 재배포 권한이 없다. 내부 연구용 저장·분석은 가능하나 추출 전문을 기관 외부로 재배포하지 말 것.
