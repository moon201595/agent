# 논문 검색·분석 에이전트 — 진행 상황

작성일: 2026-07-30 · 프로젝트 경로: `~/paper-harness` (WSL2 Ubuntu)
범위: 에이전트와 실행 환경만. 발표 자료·대본은 이 문서에 포함하지 않는다.
표기: **[확인됨]** 은 도구 실행으로 확인한 것, **[미확인]** 은 아직 근거가 없는 것.

## 한 줄 요약

결정적 도구 10종을 MCP 서버로 노출하고 에이전트 루프는 Claude Code 에 맡기는 구조가 동작한다. **MCP 연결까지 검증됐고, 논문 1편을 실제로 끝까지 태워 저장까지 확인했다(2026-07-30).** **④ 는 `batch_summarize.py` 온디맨드 배치 스크립트로, ⑥ 은 `review_app.py` (Streamlit) 로 둘 다 구현 완료됐다(2026-07-31)** — 검색→선별→파싱→요약→검증→저장까지 채팅 승인 없이 돌고, 저장된 요약은 브라우저 화면에서 승인/반려/재생성한다. 요약 엔진은 로컬 파인튜닝이 아니라 **무료 API(Gemini 우선, Groq 대체)** 로 방향이 바뀌었다 — §8-2 참고. 요약 템플릿은 v2로 교체됐다 — §5 참고. **⑦ 코드 재현이 완료됐다(2026-08-03)** — 저장소 후보 탐색(`code_finder.py`, HuggingFace 모델카드 경유 GitHub 링크 추적 포함) + Docker 격리 실행(`docker_runner.py`, 자율 루프 3회)까지 도구 8종 이후 마지막 단계까지 구현·실측 검증됨(SWE-agent 완전 성공, TSPulse 는 거대 ML 모노레포 특성상 설치 예산 초과로 정직하게 실패 — 거짓 성공 없음. §5 참고). 8단계 파이프라인 자체는 전부 구현됐다. 평가셋 구축을 시작하며 ③이 arXiv 전용이라 실제 문헌 목록의 절반 가까이를 못 읽는 문제가 새로 드러나, 수동 PDF 업로드 + Unpaywall 오픈액세스 자동 수집을 추가했다(2026-08-04). **평가셋 39편 구축과 `eval.py` 첫 기준선(숫자 1103개 중 1064개 일치, 통과율 0.965)까지 완료됐다(2026-08-05, §5 참고)** — §8 우선순위 목록의 마지막 미해결 항목이었다. 평가셋으로 실제 통과율 저하 원인을 진단해 검증기 위치참조 오탐 수정·서베이 전용 템플릿·Gemini/Groq 양쪽 청킹(전문 안 잘림)까지 세 가지를 고쳤고(2026-08-06), 외부 발전 설계 문서가 제안한 항목 중 **인용 그래프(Citation Search, `s2_get_references`/`s2_get_citations`)** 를 PaSa Crawler/Selector 분리 원칙대로 Crawler만 구현했다(2026-08-06, §5 참고) — 도구 8종에서 **10종**으로 늘었다.

---

## 1. 아키텍처 — 왜 오케스트레이션 코드를 쓰지 않는가

역할이 둘로 나뉜다.

| | 정체 | 하는 일 |
| --- | --- | --- |
| MCP 서버 | `~/paper-harness` (Python) | 도구 10종 제공. **판단하지 않는다** |
| MCP 클라이언트 | Claude Code | 도구를 언제·어떤 순서로 부를지 판단 |

`for` 루프로 파이프라인을 돌리는 코드가 없다. 검색 → 선별 → 파싱 → 요약 → 검증의 순서는 클라이언트가 판단해 도구를 부르는 것으로 이뤄진다.

**외부망 전제로 확정됐다 (2026-07-30).** 내부망 이식(vLLM + LiteLLM + 자체 도구 루프)은 계획에서 빠졌다. 그 결과 자체 에이전트 루프를 구현할 이유가 사라졌고, MCP + 상용 LLM 클라이언트가 임시가 아니라 **최종 형태**다. 이전 문서의 "Phase 0 / Phase 1" 구분과 내부망 2존 구조는 더 이상 유효하지 않다.

### 이 구조의 한계 (열려 있는 결정)

1. ~~**사람이 Claude Code 를 띄워야 돌아간다.**~~ — ④⑥ 모두 Claude Code 밖에서 해결됨(2026-07-31). ④ 요약은 `batch_summarize.py` 로 검색부터 저장까지 무인 실행되고, ⑥ 사람 판단은 `review_app.py`(Streamlit, 브라우저 UI)로 옮겨져 Claude Code 채팅이 전혀 필요 없다. ⑦(코드 재현)·⑧(축적) 만 미착수라, 전체 파이프라인이 완전 무인은 아니다.
2. **팀 배포가 안 된다.** 쓰는 사람마다 자기 `.env`(API 키)가 있어야 한다 — 다만 무료 API 키 발급은 상용 API 키보다 진입장벽이 낮다.

무인 실행 자체는 ④ 한정으로 이미 됨. 남은 질문은 ⑥⑦⑧ 까지 포함한 전체 파이프라인을 어디까지 자동화할 것인가다.

---

## 2. 실행 환경

코드는 Windows 가 아니라 WSL 안에 있다. ⑦ 재현이 Docker 소켓을 직접 잡아야 하고, `/mnt/c` 경로를 컨테이너에 마운트하면 느리고 권한이 꼬인다.

| 항목 | 버전 | 비고 |
| --- | --- | --- |
| OS | Ubuntu 26.04 LTS | WSL2, systemd 가 PID 1 |
| Python | 3.14.4 | `~/paper-harness/.venv` |
| Claude Code CLI | 2.1.220 | `~/.local/bin/claude` (WSL 안에 설치) |
| Docker | Engine CE 29.6.1 | **Docker Desktop 아님** |

### Docker — 회사 정책 문제 없음

- 설치된 것은 Docker 공식 apt 저장소의 **Docker Engine(CE)**, Apache 2.0 이다. 유료 구독 조항(직원 250명 / 매출 $10M 초과)은 Desktop GUI 제품에만 적용된다.
- `docker.service` 가 `active` + `enabled` — 부팅 시 자동 시작. 사용자가 `docker` 그룹이라 `sudo` 불필요.
- **[확인됨]** `docker run --rm hello-world` 성공. 사내망에서 Docker Hub pull 도 뚫린다.
- ⑦ 단계에서 추가 설치할 것이 없다.

### 의존성

`mcp 2.0.0` · `httpx 0.28.1` · `pypdf 6.14.2` · `beautifulsoup4 4.15.0` · `pydantic 2.13.4` · `pytest 9.1.1`

라이선스: mcp(MIT), httpx(BSD-3), pypdf(BSD-3), beautifulsoup4(MIT). **PyMuPDF 는 AGPL 이라 의도적으로 배제했다** — 과제 계획서의 기술 실시·제3자 실시권 조항 때문이다. 의존성 추가 시 라이선스 확인이 선행 조건이다.

---

## 3. 도구 10종과 구현 현황

| 단계 | 도구 | 상태 |
| --- | --- | --- |
| ① | `arxiv_search_papers` | ✅ 키 불필요, 호출 간 3초 강제 |
| ① | `s2_search_papers` | ✅ 인용수 제공. `S2_API_KEY` 등록됨(2026-08-01) + 초당 1회 스로틀 적용 |
| ① | `s2_get_references` / `s2_get_citations` | ✅ **완료(2026-08-06)** — 인용 그래프(Citation Search). PaSa의 Crawler/Selector 분리에서 Crawler만 구현(depth=1 고정, `limit`으로 후보 수 강제 상한). `s2_search_papers`와 스로틀 상태(`_s2_lock`/`_last_s2_call`) 공유. §5 참고 |
| ② | `dedupe_and_rank_papers` | ✅ 결정적 규칙, 네트워크 미사용 |
| ③ | `fetch_paper` | ✅ HTML 우선 → PDF 폴백, 멱등. **arXiv 전용** |
| ③ | `get_paper_text` | ✅ 분할 열람 |
| ③ | `ingest_local_pdf` / `fetch_pdf_from_url` / `resolve_unpaywall_pdf` | ✅ **완료(2026-08-04)** — arXiv 밖 논문(수동 업로드 + Unpaywall 오픈액세스 자동). MCP 도구 아님, `review_app.py`가 직접 import. §5 참고 |
| ④ | `batch_summarize.py` / `review_app.py` (Claude Code 밖에서 독립 실행) | ✅ Gemini(`gemini-flash-latest`) 우선, Groq(`llama-3.3-70b-versatile`) 대체. **Claude Code(나 자신)를 요약 엔진으로 쓰는 방안은 폐기됨(2026-07-31)** — 무인 실행·비용 문제로 무료 API 로 전환. §5 "④ 요약 엔진 선정" 참고 |
| ⑤ | `verify_summary_numbers` | ✅ LLM 미사용 |
| — | `save_summary` | ✅ 저장 직전 자동 검증, 불일치도 저장은 함 |
| — | `list_stored_papers` | ✅ |
| ⑥ | `review_app.py` (Streamlit UI) | ✅ 승인·반려(사유 입력)·재생성. 원문 이미지 갤러리 포함 |
| ⑦ | `code_finder.py` + `docker_runner.py` | ✅ **완료(2026-08-03)** — 저장소 후보 탐색 + Docker 격리 실행(자율 루프 3회). `reproduce(arxiv_id)` |
| ⑧ | 축적·피드백 | 🔸 SQLite 에 검증 결과 + ⑥ 검토 상태(review_status)가 쌓임. 규칙 보강·RAG 되먹임 없음 |

**자율 루프는 아직 존재하지 않는다.**

### 파일

```
server.py              MCP 서버 (stdio). 도구 10종 + ⑥ review_status 저장·이미지 추출 헬퍼
batch_summarize.py     ④ 온디맨드 배치 요약 (Claude Code 밖에서 독립 실행, server.py 함수 직접 import)
review_app.py          ⑥ 사람 판단 UI (Streamlit) — 검색·요약 생성 탭 + 요약 검토 탭
summarize_engine.py    ④ 요약 엔진 호출부 (Gemini/Groq). batch_summarize.py·review_app.py 공유
code_finder.py         ⑦ 코드 저장소 후보 탐색 (본문 링크 스캔 + GitHub 검색 + HF 모델카드 경유 GitHub 추적)
docker_runner.py       ⑦ Docker 격리 실행. reproduce(arxiv_id) 가 유일한 자율 재시도 루프(3회)
selection.py           ② 중복 제거·선별 규칙
verify.py              ⑤ 수치 검증기 (LLM 미사용)
eval.py                통과율 일괄 측정 (회귀 기준선)
prompts/
  summary_template.md  ④ 요약 템플릿 v2 (프롬프트 자산, 버전 관리 대상)
test_smoke.py          실동작 7종 (네트워크 필요)
test_verify_units.py   ⑤ 경계 규칙 14종
test_select.py         ② 규칙 8종
data/                  PDF·텍스트·요약·이미지·SQLite·⑦ clone 작업공간 (자동 생성, 커밋 제외)
.env                   GOOGLE_API_KEY · GROQ_API_KEY · S2_API_KEY · UNPAYWALL_EMAIL(선택) (커밋 제외, 각자 발급)
```

`batch_summarize.py` 와 `review_app.py` 는 `server.py` 의 MCP 도구 함수를 `import server` 로 그대로 불러와 직접 호출한다 — `@mcp.tool` 데코레이터가 있어도 일반 함수처럼 호출 가능함을 실측으로 확인했다. 로직 중복 없이 ①②③⑤와 저장 경로를 그대로 재사용한다. 요약 엔진 호출부(Gemini/Groq)는 `summarize_engine.py` 로 따로 빼서 두 스크립트가 중복 없이 공유한다.

`selection.py` 는 `select.py` 로 두면 표준 라이브러리 `select` 를 가려 asyncio 가 깨지므로 이 이름이다.

---

## 4. 설계 원칙 — 반드시 유지할 것

### 자율성 3계층

| 구간 | 반복 | 종료 조건 |
| --- | --- | --- |
| ①~③ 검색·파싱 | 제한 재시도 2회 | 코드가 정해진 횟수만 재시도. **루프가 아니라 예외 처리** |
| ④⑤ 요약·검증 | **없음** | 단일 패스 + 검증 1회. 자동 되돌림 없음 |
| ⑦ 코드 재현 | 자율 루프 3회 | 실행 성공 또는 시도 3회 — `docker_runner.reproduce()` 로 구현됨(2026-08-03) |

허용 기준은 하나 — **종료 조건을 기계가 자동으로, 위조 불가능하게 판정할 수 있는가.**

### 검증기를 루프 판정자로 쓰지 않는다 (Goodhart)

요약을 검증 통과까지 반복시키면 최단 통과 경로가 **"숫자를 아예 쓰지 않는 것"** 이 된다. 통과율은 오르고 요약 가치는 사라져 검증기가 무력화된다. 이 논거가 설계의 중심이다.

숫자가 없는 요약은 통과율 1.0 이 나온다. **이 지표를 품질로 읽으면 안 된다.**

### 검증은 flag-and-pass, 저장 차단이 아니다

불일치 원인 4가지: LLM 환각 / PDF 표 깨짐 / 단위 환산 / 원문 밖 출처(과제 KPI 목표치). 뒤의 셋은 정상인데 기계는 넷을 구분하지 못한다 → 표시만 하고 판단은 사람.

### 되돌림 지점은 ④ 한 곳

①(재검색) 이 아니다. 숫자 오류는 새 논문을 찾아 고칠 수 있는 문제가 아니고, ① 로 되돌리면 무한 루프가 된다.

### 경량화율 ≥45% 는 이 도구와 무관

P0031698 의 「인공지능 비전 모델 경량화율(%)」은 비전 모델 대상이다. 이 논문 도구의 문서·슬라이드에 등장시키면 안 된다.

---

## 5. 검증 결과

### [확인됨] MCP 연결

```
$ claude mcp add paper-harness -- ~/paper-harness/.venv/bin/python ~/paper-harness/server.py
$ claude mcp list
paper-harness: ... - ✔ Connected
```

지난 세션부터 미확인이던 항목이다. 기본은 **local 스코프**라 `~/paper-harness` 에서 `claude` 를 띄울 때만 붙는다. 어디서나 쓰려면 `-s user` 로 등록한다.

### [확인됨] 스모크 7/7 (실 네트워크)

```
[1] arxiv_search: count=3
[2] dedupe_and_rank: 4건 → 중복제거 3건 → 선별 2건, 1위 인용수=99999
[3] fetch_paper(1706.03762): chars=41129 method=html
[4] fetch_paper(2405.15793): chars=291061 method=pdf
[5] verify: total=2 matched=1 unmatched=['99.87']
[6] save_summary: pass_ratio=0.5
[7] list_stored_papers: count=2
```

### [확인됨] 논문 1편 실제 왕복 (2026-07-30)

스모크 테스트는 도구 호출만 확인했을 뿐 ④(LLM 요약)를 거치지 않았다. 처음으로 검색 대상 선정→`fetch_paper`→④ 요약 작성→⑤ 검증→`save_summary` 전 과정을 끝까지 태웠다.

- 대상: `2505.13033` (TSPulse, IBM Research, ICLR 2026 게재)
- `fetch_paper`: HTML 없음 → PDF 폴백, 152,447자
- `prompts/summary_template.md` 형식으로 요약 작성 후 `verify_summary_numbers` 사전 점검(21/22 통과, 나머지 1건은 요약 작성자가 임의로 덧붙인 날짜라 삭제)
- `save_summary` 최종 결과: **20/20 수치 일치, pass_ratio 1.0**
- 결과물: `data/summaries/2505.13033.md` (data/ 는 `.gitignore` 대상이라 이 저장소엔 없음)

PDF 경로 리스크가 실측으로도 나타났다 — 본문 막대그래프(Figure 4~7) 안 수치가 텍스트 추출 과정에서 라벨과 뒤섞여(`0.52\n1M\n0.48\n1M\n8%...` 식) 나왔다. 요약에는 그래프 내부 수치 대신 본문 서술 문장에 명시된 수치만 인용해 우회했다 — ⑤ 검증기가 "숫자가 원문에 있는가"만 볼 뿐 "그 숫자가 맥락에 맞게 인용됐는가"는 보지 못한다는 한계를 그대로 보여주는 사례다.

### [확인됨] ④ 요약 엔진 선정 — 로컬 파인튜닝 폐기, 무료 API 채택 (2026-07-31)

애초 계획은 회사 GPU 서버에서 오픈소스 LLM(Qwen2.5-7B 급)을 LoRA 파인튜닝하는 것이었다. 실제 파인튜닝에 들어가기 전 "파인튜닝 없이 프롬프트만으로 되는지" 먼저 확인했고, 그 결과로 계획 자체가 바뀌었다.

**1단계 — 로컬 소형 모델 프롬프팅 (실패):** GPU 없는 WSL 환경(RAM 7.3GB, 여유 1~2GB)에서 llama.cpp + GGUF 양자화 모델로 테스트.

- Qwen2.5-3B: 메모리 부족으로 스왑 스래싱, 사실상 실행 불가.
- Qwen2.5-0.5B: 간단한 "한국어로 답해" 지시조차 무시하고 영어로 답함. 논문 전체+템플릿 프롬프트는 빈 출력.
- Qwen2.5-1.5B: 한국어를 시도는 하나 품질 불량 — "anomaly detection"을 "이상 탐지"가 아니라 "애러노메이드 DETECTION"으로 오역하는 등 실사용 불가 수준.
- **결론: 이 크기의 모델은 메모리 문제를 다 해결해도 품질 자체가 안 나온다.**

**2단계 — 무료 호스팅 API 비교 (Gemini 채택):** TSPulse 논문으로 Groq(`llama-3.3-70b-versatile`)와 Google Gemini(`gemini-flash-latest`)를 `verify.py`로 직접 비교.

| 엔진 | 검증된 숫자 개수 | pass_ratio | 비고 |
| --- | --- | --- | --- |
| Groq | 1개 | 1.0 | 숫자를 거의 안 써서 통과 — **Goodhart 함정의 실제 사례**. 무료 TPM 한도(12,000/분)도 낮아 원문을 1/3만 넣어야 했음 |
| Gemini | 11개 | 1.0 | 실제로 근거 있는 수치를 다수 인용, 전부 원문과 일치 |
| (비교 기준) Claude | 20개 | 1.0 | 기존 §5 "논문 1편 실제 왕복" 결과 |

pass_ratio 숫자만 보면 셋 다 1.0으로 동일해 보이지만, **검증된 숫자 개수가 진짜 품질 신호**라는 게 실측으로 드러났다 — §4 Goodhart 원칙이 이론이 아니라 실제로 재현된 사례다. Gemini를 ④ 의 기본 엔진으로, Groq 는 장애·한도 초과 시 대체 엔진으로 채택했다.

**주의:** Gemini API 키를 URL 쿼리 파라미터로 보내면 `httpx` 요청 로그에 평문으로 찍힌다 — `batch_summarize.py` 는 `x-goog-api-key` 헤더로 보내고 `httpx`/`httpcore` 로거를 WARNING 레벨로 낮춰 이 경로를 막아뒀다.

### [확인됨] `batch_summarize.py` 첫 실행 (2026-07-31)

`--ids 1706.03762` 로 fetch→get_paper_text→Gemini 요약→save_summary(자동 검증) 전 과정을 Claude Code 세션 밖에서 (`python batch_summarize.py` 로 터미널에서 직접) 실행 — **8/8 수치 일치, pass_ratio 1.0**. `server.py` 의 MCP 도구 함수를 `import server` 로 직접 호출하는 방식이 실제로 동작함을 확인했다.

`--keyword` 모드(검색→선별까지 포함한 전체 경로)는 이후 `review_app.py` 의 검색 탭으로 실제 라이브 테스트까지 완료했다 — 아래 참고.

### [확인됨] ⑥ 사람 판단 UI + 원문 이미지 갤러리 (2026-07-31)

`review_app.py` (Streamlit, `streamlit run review_app.py`)로 구현. 두 개 탭:

- **검색·요약 생성**: 키워드/ID/제목 세 입력 모드. `st.status()` 로 ①검색→②선별→③파싱→④요약→⑤검증 단계를 실시간으로 보여준다. 키워드 모드로 실제 라이브 검색(예: `2601.12538`, `2503.23037`, `2605.05287` 획득)까지 확인됐다 — 위 "`--keyword` 모드 라이브 테스트" 항목 해소.
- **요약 검토**: 저장된 요약을 pending/approved/rejected 로 필터링해 목록으로 보여준다. 검증 불일치 숫자는 원문 문맥과 함께 표시해 원문 대조를 돕는다. 승인·반려(사유 입력)·재생성 버튼.

`summaries` 테이블에 `review_status`/`review_note`/`reviewed_at` 컬럼을 추가하면서, 기존 `save_summary` 의 `INSERT OR REPLACE INTO summaries VALUES (...)` 가 컬럼명 없는 위치 기반이라 컬럼 추가만으로 깨지는 버그를 발견해 즉시 수정했다(컬럼명 명시 + 재저장 시 review_status 를 'pending' 으로 자동 리셋 — 새 버전은 다시 검토받아야 하므로).

**원문 이미지 갤러리** (같은 화면의 토글): 논문에서 실제 이미지를 추출해 보여준다.

- HTML 출처: `<figure>` 태그 안의 `<img>` 만 추출 — figcaption 이 있으면 그대로 라벨로 쓴다. 실측(1706.03762)에서 "Figure 1: The Transformer - model architecture." 같은 실제 캡션이 정확히 붙었다.
- PDF 출처: `pypdf` 의 `page.images` 로 추출, 라벨은 "그림 N"(추출 순서일 뿐 논문 Figure 번호와 무관) — PyMuPDF(AGPL)를 배제한 채로는 이미지-캡션 매칭이 안 된다.
- **실측으로 두 번 걸러냄이 필요했다**: (1) 첫 시도에서 저자 소속기관 로고(Illinois·Amazon·Google DeepMind 등)가 Figure 로 오인돼 뽑힘 → HTML 은 `<figure>` 부모 필수로 제한, PDF 는 표지(1페이지) 제외로 대응. (2) 그래도 한 논문(2601.12538)에서 92개가 뽑혀 확인해보니 대부분 3~12KB 로고·아이콘 조각 → PDF 쪽 최소 크기를 3KB→50KB 로 올려 15개로 정리. HTML 은 이미 `<figure>` 필터가 있어 5KB 로 낮게 유지(안 그러면 26KB짜리 진짜 Figure 까지 잘림) — 두 경로가 서로 다른 임계값을 쓴다.

### [확인됨] `prompts/summary_template.md` v2 교체 (2026-07-31)

기존 템플릿을 판단 근거 중심의 v2로 전면 교체(작성자: 사용자, 핵심 규칙 R1~R6 — 수치는 결과 절에만/모든 수치에 조건·비교대상·지표·출처/★ 신뢰도 등급/도구 한계와 논문 평가 분리/지어내지 않기/평서체). 교체 전 `eval.py` 로 기준선을 재고 교체 후 재확인했다:

- 교체 전: 5편, 75/75 숫자 일치 (통과율 1.000)
- TSPulse(2505.13033)를 v2 프롬프트로 재생성 후: 5편, **94/94** (통과율 1.000 유지) — TSPulse 하나만 봐도 검증된 숫자가 20개→39개로 늘었는데도 전부 일치. 형식 요건(R1~R4)이 실제 산출물에서도 지켜짐을 육안으로 확인했다.

### [확인됨] ⑦ 코드 재현 — 저장소 후보 탐색 `code_finder.py` (2026-08-01, 진행 중)

⑦ 의 범위를 먼저 정했다: **논문을 읽고 코드를 새로 짜는 게 아니라, 저자(또는 커뮤니티)가 이미 공개한 코드를 찾아서 돌려보는 것.** 코드가 없는 논문을 다른 논문 코드로 대체하지 않는다 — "코드 없음"도 유용한 정보이므로 그대로 표시한다. 비공식 커뮤니티 재구현체는 허용하되 반드시 "저자 공식 아님"으로 라벨링한다. 성공 기준은 "설치+실행이 에러 없이 돌아가는가"이지 논문 수치 재현이 아니다 — 대부분 GPU·대규모 데이터셋이 필요해 후자는 비현실적이다.

두 경로로 저장소 후보를 찾는다: (1) 원문 텍스트에서 "code is available at" 류 문구 근처 URL 스캔(`in_text`, 저자가 직접 언급 → 신뢰도 높음), (2) GitHub 검색 API로 논문 제목 검색, 별점 내림차순(`github_search`, 저자 공식 여부 미확인 → 반드시 라벨링).

실측으로 걸린 함정 3개, 전부 수정 후 재검증:

1. **PDF 추출 시 URL이 줄바꿈으로 끊김** — TSPulse 의 HuggingFace 링크가 `huggingface.\nco/...` 로 쪼개져 있어 처음엔 못 찾았다. 검색 전 줄바꿈을 제거하는 것으로 해결.
2. **GitHub 검색어에 공백이 있으면 URL 인코딩 없이 보내져 30초 타임아웃** — 한 단어 검색어("SWE-agent")만 성공하고 나머지는 전부 실패했다. `urllib.parse.quote()` 로 인코딩해 해결. 이 버그 하나로 6편 중 5편의 검색 결과가 비어 있었다.
3. **cue 단어(“available”, “source” 등) 근처에 있다는 이유만으로 arxiv.org·semanticscholar.org·gnu.org 링크까지 코드 저장소 후보로 잘못 잡힘** — 알려진 비-코드 도메인 차단 목록(`_DENY_HOSTS`)을 추가해 해결.

세 버그를 다 고친 뒤 저장된 논문 6편 전체로 재검증 — **6편 모두 최소 1개의 신뢰할 만한 후보를 찾았다**:

| 논문 | 결과 |
| --- | --- |
| Attention Is All You Need | 본문 링크(tensor2tensor) + GitHub 검색 1위(비공식 PyTorch 구현체, 9,777★) |
| SWE-agent | 본문엔 URL 형식이 아닌 도메인만 있어(`swe-agent.com`) 못 잡았지만 GitHub 검색이 공식 저장소를 1위(19,984★)로 정확히 찾음 — 두 경로를 같이 쓰는 이유 |
| Agentic LLM survey | 코드 없음 — 서베이 논문이라 자체 코드가 없는 게 맞는 결과 |
| TSPulse | 본문에서 HuggingFace 링크 정확히 발견 |
| Agentic Reasoning (2601.12538) | 본문 링크는 페이지 번호가 섞여 살짝 깨졌으나(`...Reasoning1`) GitHub 검색이 정확한 버전(1,327★)으로 교차 확인 |
| Securing the Agent | 본문에서 저장소 2개(ogx, ogx-k8s-operator) 정확히 발견 |

**추가(2026-08-03)**: HuggingFace 모델 링크만으로는 설치 대상이 없어 ⑦ 스모크 테스트가 공허해지는 문제를 TSPulse로 실측한 뒤, `find_repo_candidates()` 에 HF 모델카드 README 를 한 번 더 읽어 진짜 GitHub 코드 저장소를 찾는 홉을 추가했다(`_resolve_hf_repo_link`) — TSPulse 는 이제 `ibm-granite/granite-tsfm` 을 최우선 후보로 정확히 찾는다. Docker 격리 실행은 §5 아래 섹션에 있다.

### [확인됨] ⑦ Docker 격리 실행 — 수동 시행착오 2건 (2026-08-03)

자동화 코드(`docker_runner.py`)를 짜기 전에 후보 저장소 2개(구조가 서로 다른 것으로 골라)를 손으로 먼저 돌려봤다. 둘 다 최종적으로는 `python:3.11-slim` 베이스 + `--network none` + 메모리·CPU 상한으로 격리 실행에 성공했지만, 가는 길에 일반화 가능한 함정 3개를 만났다.

**1) SWE-agent (`pip install -e .`, GitHub clone 방식)** — `python:3.11-slim` 에 git 바이너리가 없어서 `sweagent --help` 실행 시 즉시 크래시:

```text
ImportError: Bad git executable.
```

원인은 GitPython(파이썬 패키지)이 import 시점에 실제 `git` CLI를 찾는데, pip 의존성 목록엔 GitPython만 있고 시스템 git 바이너리는 없다는 것. `apt-get install -y git` 을 Dockerfile에 추가하니 해결 — **pip 의존성만 보고 베이스 이미지를 고르면 안 되고, git처럼 흔한 시스템 바이너리는 기본으로 깔아야 한다.** 이후 `--network none` + 메모리 1g + cpu 1 완전 격리 상태에서 `sweagent --help` 가 exit 0으로 정상 실행됨을 확인.

**2) TSPulse (HuggingFace 모델 저장소, `from_pretrained` 방식)** — 코드 저장소가 아니라 HuggingFace 모델 카드였다. README 를 읽어보니 실제 코드는 한 단계 더 들어가야 나왔다: 논문 본문의 "code available at" 링크는 HuggingFace 모델 페이지를 가리키고, **그 모델 카드의 "Repository:" 필드가 진짜 GitHub 저장소(`ibm-granite/granite-tsfm`)를 가리키고 있었다** — 심지어 저자가 "Paper Reproducibility Scripts" 라고 명시적으로 라벨링한 노트북 링크까지 있었다. `code_finder.py` 는 이 2차 링크를 못 찾는다 — HuggingFace 링크를 발견하면 그 페이지의 README 도 한 번 더 스캔해야 한다는 게 개선 과제로 남았다.

Docker 실행 자체에서는 SWE-agent와 정반대의 함정이 나왔다:

- **설치가 무거워서 순진한 타임아웃이 부족했다** — torch·transformers 설치가 6분 넘게 걸려 최초 시도(400초 타임아웃)가 끊겼다. 설치 단계 타임아웃은 저장소 크기가 아니라 의존성 무게(특히 torch 류)를 기준으로 넉넉히 잡아야 한다.
- **`--network none` 이 "빠른 실패"를 보장하지 않는다** — 코드 저장소(SWE-agent)는 설치 시점에만 네트워크가 필요하지만, 사전학습 가중치를 받는 모델 저장소(TSPulse)는 **실행 시점에도** 네트워크가 필요하다. `--network none` 으로 실행하면 즉시 에러가 나는 게 아니라 `huggingface_hub` 라이브러리 내부 재시도(지수 백오프, 총 대기 약 90초)를 다 거친 뒤에야 깨끗한 `OSError` 로 죽는다. 실행 타임아웃을 이 재시도 시간보다 짧게 잡으면 "실패"가 아니라 "타임아웃"으로 잘못 분류된다.
- **`docker run` 클라이언트를 죽여도 컨테이너는 안 죽는다** — 바깥에서 `timeout 30 docker run ...` 으로 감싸 클라이언트 프로세스에 SIGTERM 을 보내도, `docker run` 이 포그라운드로 붙어있는 컨테이너 자체는 계속 실행됐다(`docker ps` 로 실측 확인). 컨테이너 ID를 반드시 추적해서 `docker stop`/`docker kill` 을 컨테이너에 직접 걸어야 타임아웃이 실제로 지켜진다 — 셸 `timeout` 으로 감싸는 방식은 신뢰할 수 없다.

**자동화 설계에 반영할 결론 4가지**:

1. 베이스 이미지에 `git` 을 기본 포함한다.
2. 설치 타임아웃과 실행 타임아웃을 분리하고, 설치는 넉넉하게(10분+), 실행은 저장소 종류(코드 vs 모델)에 따라 다르게 잡는다.
3. `--network none` 은 "설치 후 네트워크가 필요 없는" 저장소에만 쓴다. 실행 스크립트가 `from_pretrained`/API 호출처럼 런타임 네트워크가 필요해 보이면 실행 시엔 네트워크를 열어둔다 — 그래도 무한정 기다리지 않도록 타임아웃은 별도로 강제한다.
4. 타임아웃 강제는 셸 `timeout` 이 아니라 컨테이너 ID를 직접 추적해 `docker stop` 하는 방식으로 구현한다(Docker SDK 또는 `docker run -d` + 폴링).

두 저장소 다 `--network none` 유무와 무관하게 **"설치+실행이 에러 없이 돈다"** 기준으로는 최종 성공(SWE-agent: 완전 격리 성공, TSPulse: 네트워크 열어둔 상태로 성공) — 성공 판정 기준 자체는 그대로 유효하다는 것도 같이 확인됐다.

### [확인됨] ⑦ `docker_runner.py` 구현 및 실측 검증 (2026-08-03)

위 4개 결론을 그대로 코드로 옮겼다. `detect_install_plan()` 이 저장소 구조(pyproject.toml/setup.py/requirements.txt, `[project.scripts]` 엔트리포인트)를 보고 설치·실행 명령을 결정론적으로 추정하고, `run_repo_in_docker()` 가 빌드+실행(네트워크 에러 감지 시 1회 재시도)을, `reproduce(arxiv_id)` 가 후보 저장소를 신뢰도 순으로 최대 3회 시도하는 유일한 자율 루프를 맡는다. 실행 결과는 `server.save_repro_result()` 로 SQLite `repro_results` 테이블에 축적된다(⑧).

실제 논문 2편으로 끝까지 돌려보며 **네 가지를 더 실측으로 찾아 고쳤다** — 전부 "일단 되는 것처럼 보이지만 사실 아무것도 검증 안 함" 또는 "겉보기엔 사소한데 실행 자체가 안 됨" 부류라 검증기의 Goodhart 가드와 같은 원칙으로 다뤘다:

1. **공허한 성공(hollow success)** — HuggingFace 가중치 저장소처럼 설치할 것도 임포트할 패키지도 없으면 placeholder `print` 문만 돌고 exit 0 이 나온다. 이걸 "성공"으로 세면 검증기의 넘버리스 요약과 같은 거짓 통과다. **고침**: 설치·실행 대상이 전혀 없으면 아예 시도하지 않고 `stage="no_target"`, `success=False` 로 명시적으로 남긴다.
2. **배포 이름 ≠ import 이름** — `granite-tsfm` 패키지의 실제 임포트 이름은 `tsfm_public` 이다(scikit-learn→sklearn 과 같은 흔한 패턴). pyproject.toml 의 `name` 을 그대로 믿고 언더스코어만 바꾸면 `ModuleNotFoundError`. **고침**: 선언된 이름의 디렉터리가 실제 존재하는지 확인하고, 없으면 `__init__.py` 를 가진 최상위 디렉터리를 직접 찾는다(flat·`src/` 레이아웃 둘 다).
3. **알파벳순 추정의 함정** — 위 방식으로 최상위 디렉터리를 찾을 때 `services`가 `tsfm_public`보다 알파벳순으로 먼저 걸려 엉뚱한 걸 골랐다. **고침**: `services`·`common`·`utils`·`core`·`shared` 같은 범용 이름을 후보에서 제외하는 목록에 추가.
4. **clone 마다 Docker 빌드 캐시가 매번 깨짐** — 같은 저장소를 다시 clone 해도 `.git` 내부 팩파일이 미세하게 달라져 COPY 레이어 해시가 매번 바뀌고, 이미 한 번 받은 무거운 pip 설치까지 매번 처음부터 다시 했다. **고침**: `.dockerignore` 로 `.git` 을 빌드 컨텍스트에서 제외.

**최종 실측 결과**:

| 논문 | 후보 | 결과 |
| --- | --- | --- |
| SWE-agent (2405.15793) | GitHub 검색 1위(공식, 19,986★) | **성공** — 완전 격리(`--network none`, 1g/1cpu) 상태에서 `sweagent --help` exit 0 |
| TSPulse (2505.13033) | HF 모델카드 경유 GitHub(`granite-tsfm`) → HF 원링크 → GitHub 검색 결과 | **3개 후보 전부 정직하게 실패** — `granite-tsfm` 은 거대 ML 모노레포라 from-source(`-e .`) 설치가 15분 예산을 넘겨 타임아웃, HF 원링크는 설치 대상 없음(`no_target`), GitHub 검색 결과(0★, 커뮤니티)도 설치 대상 없음. **거짓 성공 없이 3회 모두 정확히 실패로 기록됐다** — 이게 이 세션에서 확인하려던 것(Goodhart 가드가 ⑦에서도 지켜지는가)의 핵심 결과다. |

TSPulse 가 "실패"로 끝난 것 자체는 문제가 아니다 — 성공 기준이 "설치+실행이 에러 없이 도는가"인 이상, 실제로 거대한 의존성(torch·transformers 풀스택)을 소스에서 빌드하는 저장소가 정해진 시간 예산 안에 못 끝나는 것은 정직한 결과다. 여기서 지켜야 했던 것은 "안 되는데 되는 척(거짓 성공)"을 안 하는 것이었고, 그건 성립했다.

**알려진 한계(고치지 않고 남겨둠)**: 거대 ML 모노레포의 from-source 설치는 15분을 넘길 수 있다 — 무한정 타임아웃을 늘리는 것보다, 이런 저장소는 "설치 자체가 무겁다"는 별도 신호로 다루거나(예: `pip install <패키지명>` 처럼 PyPI 사전빌드 wheel 을 우선 시도) 애초에 예산 안에 못 끝나는 것을 정직한 결과로 받아들이는 쪽이 낫다고 판단했다.

### [확인됨] ③ arXiv 밖 논문 수집 — 수동 업로드 + Unpaywall 오픈액세스 (2026-08-04)

평가셋을 구축하려고 사용자의 실제 문헌 목록(Vision AI·Agentic AI·Onsensor AI·온디바이스 경량화·자율제조·배터리·AMMR·하드웨어 8개 카테고리, arXiv 스크린샷 확인)을 받아봤더니 상당수가 Nature·ScienceDirect·IEEE·ACM 등 arXiv 밖 저널·컨퍼런스였다. `fetch_paper` 는 arXiv 전용이라 이 목록의 절반 가까이를 아예 못 읽는 문제가 드러났다 — 평가셋만의 문제가 아니라 하네스 실사용 범위 자체의 문제였다.

두 경로를 추가했다(둘 다 채택 — 사용자 확인):

1. **수동 PDF 업로드** (`ingest_local_pdf`) — 이미 기관 구독 등으로 합법적으로 접근 가능한 PDF를 사용자가 직접 올린다. 페이월 우회가 아니다. 어떤 저널이든 무조건 된다.
2. **오픈액세스 자동 수집** (`fetch_pdf_from_url` + `resolve_unpaywall_pdf`) — Unpaywall API(무료, 키 불필요, `email` 파라미터만 요구)로 DOI에서 합법적 오픈액세스 PDF 위치를 찾아 자동으로 받는다.

둘 다 `_text_from_pdf`(기존 pypdf 추출기)를 그대로 재사용한다 — 새 추출 로직 없음. arXiv ID가 없는 논문은 `pdf-<내용 해시 10자리>` 합성 ID를 쓰고(같은 파일 재업로드 시 같은 ID — 멱등), `papers.arxiv_id` 컬럼명은 그대로 두되 새 `papers.source` 컬럼으로 출처를 구분한다.

**실측으로 걸린 함정 1개**: Unpaywall이 더미 이메일(`example.com`)을 실제로 거부했다(422 "Please use your own email address"). 기본값을 사용자 실제 이메일로 바꿔 해결.

**검증**: `resolve_unpaywall_pdf` → `fetch_pdf_from_url` → `ingest_local_pdf` 전체 파이프라인을 실제 arXiv PDF(TSPulse, 152,447자)로 왕복 테스트, 동일 파일 재업로드 시 멱등 확인(같은 `pdf-11a125e40d` ID, 재처리 생략), `_clean_arxiv_id`·`get_paper_text` 등 다운스트림이 합성 ID를 그대로 통과시키는 것 확인. `review_app.py`에 "PDF 업로드"·"DOI/URL(오픈액세스)" 입력 모드 2개 추가, 실제 화면 캡쳐로 확인.

**여전히 안 되는 것**: ScienceDirect·IEEE·ACM 같은 페이월+비-오픈액세스 논문은 자동 수집이 안 된다 — 수동 업로드로만 가능하다. 의도된 제약(라이선스·ToS 준수).

### [확인됨] 평가셋 39편 구축 + `eval.py` 첫 기준선 (2026-08-05)

사용자 문헌 목록 50편을 실제로 돌렸다. **arXiv 28/28 전부 성공.** DOI 12편 중 Unpaywall 오픈액세스 자동 수집은 첫 시도 2/12 → 버그 2개를 잡은 뒤 6/12로 개선(아래). 나머지 10편(ACM·IEEE·MDPI·ScienceDirect·PMC·NeurIPS proceedings 등)은 페이월이거나 자동 다운로드가 막혀 있어 수동 업로드가 필요하다. 목록 중 3편(Riaz, Du, Zhou et al.)은 서로 다른 논문인데 URL이 중복 재사용돼 있어 사용자에게 정정을 요청했고, 사용자가 재확인/재전달한 링크로도 시도했으나 전부 실패(ISO 유료 표준, IEEE 오픈액세스 없음, MDPI 자동 다운로드 차단) — 이 3편도 수동 업로드 대상.

**실측으로 잡은 버그 2개**:
1. `_clean_arxiv_id` 가 `arxiv.org/html/` 링크를 못 걷어냄(abs·pdf만 처리) — ACBench 링크가 이 형식이라 걸림. `html` 도 처리하도록 수정.
2. `fetch_pdf_from_url` 이 "URL이 `.pdf`로 끝나면 content-type 검사를 건너뜀" 처리를 해뒀었는데, nature.com이 실제로는 HTML 로그인/에러 페이지를 `*.pdf` URL로 200 OK 응답한 경우(4건: EHG-ZCR, Noninvasive EMI, Edge SoC, Wire-rope)를 그대로 통과시켜 pypdf 단계에서야 "invalid pdf header"로 깨졌다. **파일 시그니처(`%PDF-` 매직바이트)로 검증하도록 수정** — 재시도 결과 4건 전부 정상 복구. ACM·Cloudflare 류 봇 차단 대응용 User-Agent 헤더도 같이 추가(Groq 대응과 동일 패턴).

**39편 전부 요약까지 마쳐 첫 회귀 기준선 확보**: `python eval.py` → **숫자 1103개 중 1064개 일치, 통과율 0.965**.

**요약 단계에서 실측으로 잡은 구조적 문제 하나 더**: 34편을 연속 호출로 돌렸더니 22편째부터 Gemini·Groq 무료 티어 둘 다 429(분당 한도 초과)를 맞았다 — 호출 사이에 페이싱이 전혀 없었기 때문이다(①~③은 이미 `_throttled_*_get` 이 있는데 ④ 요약 호출부엔 없었다). 수동으로 25초 간격을 두고 재시도하니 12/12 전부 성공 — 그래서 이 페이싱을 스크립트마다 따로 두지 않고 `summarize_engine.py` 안에 429 전용 상한 재시도(2회, 20초·40초 백오프)로 고정했다. 429 가 아닌 실패(키 없음 등)는 그대로 즉시 다음 엔진으로 넘어간다 — 이것도 무한 재시도가 아니라 상한 있는 예외 처리(①~③과 같은 원칙).

**주의 — 검증기의 Goodhart 함정이 실제로 나왔다**: 2편(π*0.6 `2511.14759`, LLM-Assisted Roll-to-Roll `2511.22975`)이 `pass_ratio=1.0 (0/0)` — 숫자를 아예 안 써서 통과한 경우다. 둘 다 Gemini 가 연속 429 로 막혀 있던 시점에 Groq 로 생성됐다 — §5 "④ 요약 엔진 선정"에서 이미 문서화한 Groq 의 취약점(무료 티어에서 숫자를 거의 안 씀)이 그대로 재현됐다. **자동으로 재생성하지 않았다** — 검증기를 루프 판정자로 쓰지 않는다는 원칙대로, `review_app.py` 에서 사람이 보고 반려·재생성 여부를 정할 대상으로만 남겨뒀다.

### [확인됨] 통과율 저하 3건 원인 진단 + 수정 3개 (2026-08-06)

39편 기준선에서 통과율이 낮은 3편을 실제로 까봤다 — "논문 분야가 달라서 프롬프트가 안 맞다"는 가설을 세웠지만 실측 결과는 셋 다 **다른 원인**이었다(의료·서베이 등 완전히 다른 분야의 다른 논문들은 이미 0.93~1.00으로 잘 나오고 있었다 — 분야 자체는 통과율 저하 원인이 아니었다).

| 논문 | 통과율 | 원인 |
|---|---|---|
| VegaEdge `2311.07880` | 0.84 | 검증기가 "본문 6.1절" 류 **출처 위치 표기**를 데이터 수치로 오인 |
| Kimi Linear `2510.26692` | 0.75 | MMLU/BBH 벤치마크 비교표 — **PDF 표 깨짐**(기존에 문서화된 한계) |
| Feelbert `2504.19965` | 0.71 | 원문 236,983자가 **60,000자 상한에서 잘려** 결과 절 자체를 못 봄 |

이 중 검증기 오탐(VegaEdge)과 truncation(Feelbert)은 수정했고, PDF 표 깨짐(Kimi Linear)은 기존에 이미 알려진 한계라 이번엔 안 건드렸다(§6 참고). 사용자가 지적한 것처럼 "일부만 읽고 요약하면 검증 자체가 무의미하다"는 지적을 받아들여 truncation은 경고만 붙이지 않고 **근본적으로 없앴다**(아래).

**1. `verify.py` 출처 위치 오탐 수정** — 숫자 바로 앞/뒤 12자 이내에 절·장·Table·Figure·Appendix·Eq. 같은 위치 마커가 있으면 검증 대상에서 제외한다. 회귀 테스트 3개 추가. 39편 재검증: **통과율 0.965 → 0.982** (VegaEdge 0.84→1.00 포함, 여러 편 개선). Kimi Linear는 여전히 0.74로 정확히 남아 — 이 수정이 진짜 문제(PDF 표 깨짐)를 안 가린다는 것도 확인.

**2. 서베이/리뷰 논문 전용 템플릿 추가** — `summary_template.md`(v2)는 "방법 상세→실험 설정→결과" 구조라 실증 연구에 맞다. 서베이 논문(단일 실험·단일 결과가 없음)에 그대로 쓰면 "④ 결과"가 억지로 비게 된다 — 실측: 서베이 2편이 통과율 1.0인데 검증된 숫자가 9개·12개뿐이었다(정확하지만 빈약함 — 검증기가 못 잡는 완전성 문제, §6에 이미 문서화된 한계와 같은 종류).

`prompts/summary_template_survey.md` 신설 — 절대 규칙(R1~R6)은 동일하되 출력 형식만 "분류체계 → 하위 주제별 정리 → 서베이가 짚은 공백" 구조로 바꿨다. 어떤 템플릿을 쓸지는 **결정적 키워드 규칙**(`summarize_engine.is_survey_paper`, 제목의 "survey"/"review of"/"overview of" 류 표현)으로 판정한다 — LLM 판단이 아니다. 서버가 판단하지 않는다는 원칙을 요약 엔진 쪽에서도 지킨 것. 오판정 시 `select_template(force="survey"|"default")`로 사람이 덮어쓸 수 있다.

`batch_summarize.py`·`review_app.py`(검색 3종 경로 + "다시 생성" 버튼) 전부 논문마다 저장된 제목으로 템플릿을 다시 고르도록 배선했다 — 기존엔 스크립트당 템플릿을 한 번만 읽어 모든 논문에 똑같이 썼다.

**검증은 미완**: 실제 서베이 2편을 새 템플릿으로 재생성해봤으나 이 세션에서 Gemini·Groq 무료 티어를 하루 종일 두드린 탓에 **둘 다 일일 한도가 소진**돼 Groq로만 생성됐고 Groq 특유의 숫자-희박 현상(0/0, 1/1)이 나와 템플릿 품질 비교가 안 됐다. 분류기 판정 자체(`is_survey_paper`)는 실제 두 논문 제목으로 정확히 True 확인됨. **한도가 풀리면 재생성해서 완전성이 실제로 개선되는지 확인할 것.**

**3. 긴 논문 truncation 제거 — 청크로 전문을 읽는다** — 기존엔 60,000자에서 그냥 잘랐다(Gemini flash 의 실제 컨텍스트 창은 훨씬 크다 — 60,000자는 애초에 보수적으로 잡은 값이었다). `MAX_PAPER_CHARS`(=`CHUNK_SIZE`)를 300,000자로 올리고, 그래도 넘치면 남은 원문을 300,000자씩 최대 `MAX_CHUNKS=4`개까지 이어붙여(총 1,200,000자까지) 전부 읽는다. 지금까지 실측된 최대 논문(462,289자, Agentic Reasoning)도 청크 2개로 끝난다 — 상한 없는 루프가 아니라 여기도 상한 있는 예외 처리.

청크 1은 기존과 동일하게 템플릿 전체를 채운다. 청크 2 이상은 "이 발췌에만 있는 새 결과·한계점만 뽑아라, 없으면 없다고만 써라" 형식의 보충 프롬프트로 받아 청크 1의 결과 뒤에 그대로 이어붙인다 — 마크다운을 파싱해 특정 절에 끼워 넣는 위험한 수술은 안 한다. 새 내용이 없는 청크는 조용히 건너뛴다.

**여기서 진짜 버그 하나를 더 발견했다**: `get_paper_text`(MCP 도구)가 채팅 컨텍스트 절약용으로 `max_chars` 상한이 80,000자로 걸려 있어서(`GetTextInput.max_chars: le=80000`), 청킹 로직을 엔진에 다 만들어도 **호출부가 애초에 80,000자 넘게 못 넘겨주는** 배선 문제가 있었다. `server.read_full_text(arxiv_id)`(MCP 도구 아님, 원문을 상한 없이 그대로 읽음)를 새로 추가해 `batch_summarize.py`·`review_app.py`(3개 경로)가 전부 이걸 쓰도록 바꿨다.

**4. Groq도 청킹하게 확장 (2026-08-06, 후속)** — 처음엔 Gemini만 청킹하고 Groq 는 그대로 15,000자에서 잘랐다("무료 TPM 한도가 빠듯해서 청크를 늘리면 오히려 더 자주 막힌다"는 이유였다). 사용자가 "둘 다 할 수 있게 하는 게 낫다"고 지적해 Groq도 청킹하도록 확장했다. `_summarize_chunked()`로 청킹 로직 자체를 엔진 공용으로 리팩터하고, `call_groq_addendum()`을 신설해 Gemini와 같은 방식(보충 프롬프트, 마크다운 수술 없이 이어붙이기)을 쓴다.

Groq는 청크 크기(15,000자, `GROQ_FALLBACK_CHARS` 그대로)는 유지하되 **청크 사이 간격을 60초**로 크게 뒀다 — 한 번 호출이 12,000 TPM 예산 대부분을 이미 쓰기 때문에(발췌+템플릿+응답 토큰 합산), 그보다 짧게 두면 바로 429가 난다. 상한은 `GROQ_MAX_CHUNKS=32`(15,000×32=480,000자, 지금까지 실측된 최대 논문을 커버) — 최악의 경우 논문 1편에 30분 안팎 걸린다. 느리지만 Gemini가 완전히 막혔을 때만 타는 경로라 감수할 만하다고 판단했다.

**검증**: Gemini 실패 시 Groq 청킹으로 정확히 넘어가는 것, Groq 청크가 순서대로 처리되고 빈 청크는 건너뛰는 것을 모킹으로 확인(청크 사이 실제 60초 지연이 그대로 걸려 테스트 자체도 실측만큼 오래 걸렸다 — 지연 로직이 진짜 작동한다는 방증이기도 하다). `call_groq_addendum()`은 실제 Groq API로도 소규모 발췌 1건을 왕복해 정상 동작 확인.

**검증**: 청킹 제어 흐름은 Gemini 응답을 모킹해서 확인했다(3청크 논문 → 호출 3회, 청크별로 올바른 구간 전달, "추가 내용 없음" 청크는 건너뜀, 중간 청크 실패 시 그때까지 결과 보존하고 중단 — 전부 통과). **실제 API로 Feelbert(236,983자)를 재검증하려 했으나 이 시점엔 Gemini·Groq 둘 다 일일 한도 소진 — 한도 풀리면 재확인할 것.**

### [확인됨] 인용 그래프(Citation Search) — `s2_get_references` / `s2_get_citations` (2026-08-06)

외부에서 받은 "심층 조사와 발전 설계" 문서(§0.3, 우선순위 2번 항목)가 제안한 Citation Search를 구현했다. PaSa 논문의 Crawler/Selector 분리를 그대로 적용해 **Crawler(결정적 조회)만** 서버에 넣고 Selector(관련성 판단)는 넣지 않았다 — ④⑥⑦과 같은 이유로, "서버는 판단하지 않는다" 원칙을 새 기능에도 그대로 지켰다.

Semantic Scholar Graph API의 `/paper/ARXIV:{id}/references`, `/paper/ARXIV:{id}/citations` 두 엔드포인트를 감싼 함수다. depth는 항상 1(1-hop)로 고정, 후보 수는 `limit`(기본 20, 최대 100)으로 코드가 상한을 강제한다 — 재귀 확장이나 반복 호출이 없으니 무한 크롤링 위험이 없다. `_throttled_s2_get`을 URL 매개변수를 받도록 일반화해 `s2_search_papers`와 **같은 스로틀 상태**(`_s2_lock`/`_last_s2_call`, 초당 1회)를 공유한다 — S2 레이트리밋이 "엔드포인트 전체 누적"이라 별도 스로틀을 두면 실제로는 의미가 없기 때문이다.

**검증**: 문법 검사 통과 → 회귀 테스트(`test_verify_units.py` + `test_select.py`) 25개 전부 통과 → 저장소에 이미 있는 실제 논문(TSPulse, `2505.13033`)으로 실제 S2 API 왕복. `s2_get_references`는 TSPulse가 실제로 인용한 VQShape·TimeMixer 등을 정확히 반환했고, `s2_get_citations`는 TSPulse를 인용하는 2026년 시계열 이상탐지 논문 2편("PAI", "VACE")을 정확히 반환했다 — 둘 다 실제로 관련 있는 결과라 동작이 맞다고 판단.

**아직 안 한 것**: Selector 쪽(반환된 후보 중 뭐가 진짜 관련 있는지 자동 판단)은 이 세션에서 의도적으로 손대지 않았다 — server.py 도구가 아니라 사람이나 Claude Code가 결과를 보고 판단하는 몫으로 남겨뒀다. Hybrid Search(BM25+임베딩), 구조화 JSON 출력 등 같은 문서의 다른 제안 항목은 아직 미착수.

### [확인됨] 단위 테스트 25개 (네트워크 불필요)

`verify.py` 경계 규칙 17종(2026-08-06 인용 위치 참조 제외 규칙 3종 추가) + `selection.py` 규칙 8종. 전부 "한 번 틀렸거나 틀릴 뻔한" 케이스다.

| 잠근 것 | 왜 |
| --- | --- |
| `99.87도` → `99` 로 잘림 방지 | 한국어 조사가 붙으면 뒤경계 `\w` 가 숫자를 자른다 |
| `정확도92.4%` 매칭 | 앞경계를 `\w` 로 막으면 한글(유니코드 `\w`)이 매칭을 막는다 |
| `총12,345개` → `345` 방지 | 틀린 숫자를 만들어 거짓 플래그를 보낸다 |
| `28.4` 가 `128.45` 안에서 매칭 방지 | **거짓 통과.** 검증기의 존재 이유가 무너진다 |
| `3e-4` 지수 표기 추출 | 학습률이 검증에서 빠진다 |

### ③ HTML 우선 — 실측으로 가정이 뒤집힌 부분

pypdf 는 2단 조판과 표를 자주 뭉개고 그게 ⑤ 의 거짓 불일치로 직결된다. arXiv HTML 은 그 원인을 구조적으로 없앤다.

**HTML 제공 여부는 투고 시점으로 예측할 수 없다.** [확인됨] 2026-07:

| 논문 | 연도 | 경로 |
| --- | --- | --- |
| `1706.03762` | 2017 | **html** (41,129자) |
| `2405.15793` | 2024 | **pdf** (291,061자) |

옛 논문에 HTML 이 있고 최신 논문에 없다. arXiv 가 구논문 HTML 을 소급 생성했고 LaTeXML 변환이 실패하는 논문도 있다. 그래서 날짜로 분기하지 않고 무조건 HTML 을 먼저 시도한 뒤 404 면 폴백한다. 어느 경로였는지는 `papers.extract_method` 에 남는다 — ⑤ 불일치를 볼 때 "PDF 표 깨짐"을 의심해야 하는지가 이 값으로 갈린다.

### mcp 2.0 이식

1.x 의 `FastMCP` 가 `mcp.server.mcpserver.MCPServer` 로 개편됐다. 데코레이터·`annotations`(dict 그대로 허용)·`run()` 형태가 유지되어 **이식은 import 와 인스턴스 생성 두 줄**이었다. `Tool` 모델의 `inputSchema` → `input_schema` 변경은 `server.py` 가 그 필드를 쓰지 않아 무관했다.

---

## 6. 검증기의 알려진 한계 (알고 쓸 것)

- 한 자리 정수(0~9)는 검증 대상 제외 — 어디에나 존재해 무의미하다.
- 단위 환산(0.5m ↔ 50cm) 탐지 불가.
- `%` 는 토큰에서 분리해 대조하므로 `3.2%` 와 `3.2%p` 를 구분하지 못한다.
- PDF 추출 품질에 따라 원문에 있는 숫자가 누락 판정될 수 있다.
- 따라서 불일치는 **"오류 확정"이 아니라 "사람이 확인" 신호**다.

---

## 7. 사용 흐름

**④ 요약을 Claude Code(나)가 채팅 안에서 직접 작성하는 흐름은 폐기됐다(2026-07-31).** 지금은 무료 API(Gemini/Groq)가 요약을 쓴다.

**표준 경로는 이 하나뿐이다 — 터미널에서 스크립트 실행, Claude Code 불필요:**

```bash
cd ~/paper-harness
python batch_summarize.py --keyword "transformer 경량화" --top-n 3   # 검색→선별→요약→검증→저장까지 무인
# 또는
streamlit run review_app.py    # 브라우저에서 검색·요약 생성 + 승인/반려
```

①②③(검색·선별·파싱)은 판단이 필요 없는 결정적 코드다 — `arxiv_search_papers`는 API 호출, `dedupe_and_rank_papers`는 인용수·날짜 규칙 정렬일 뿐이라 Claude Code 가 채팅에서 그 도구를 불러도 실행 로직은 동일하다. 그래도 이걸 README에 정식 대안 경로처럼 나란히 적어뒀다가 "그럼 검색·선별을 Claude 가 판단해서 하는 거냐"는 오해를 샀다(2026-08-04) — ④⑥⑦ 처럼 판단이 필요해서 채팅에서 뺀 것과는 다른 이유인데 같은 자리에 나란히 두니 구분이 안 됐다. `batch_summarize.py` 가 이미 전 과정을 무인으로 처리하므로 정상 사용에서는 채팅 개입이 아예 필요 없다 — 개별 도구 채팅 호출은 디버깅·탐색용일 뿐 정식 경로가 아니라는 걸 README에서 삭제하고 이 문단으로만 남겼다.

두 검색 결과를 **함께** `dedupe_and_rank_papers` 에 넣어야 한다. 인용수는 S2 만 주므로 arXiv 결과만 넣으면 정렬이 연도만으로 이뤄진다.

---

## 8. 미해결 (우선순위)

1. ~~**논문 1편 실제 왕복**~~ — 2026-07-30 완료. §5 참고.
2. ~~**④ 실행 형태 결정**~~ — 2026-07-31 완료. `batch_summarize.py` 로 구현, §5 "④ 요약 엔진 선정" 및 "`batch_summarize.py` 첫 실행" 참고. 로컬 오픈소스 LLM 파인튜닝 계획은 프롬프트만으로는 소형 모델 품질이 안 나온다는 걸 실측으로 확인하고 폐기 — Gemini/Groq 무료 API로 대체했다. 회사 GPU 서버(호스트 `203.254.171.81`, 계정 `black`, 포트 `8281`)는 접속 정보는 받았으나 VS Code Remote-SSH 연결을 아직 안 끝냈다 — 지금은 ④ 목적으로는 불필요해졌고, 다른 용도로 필요해지면 그때 이어서 접속할 것.
2-1. ~~**`--keyword` 모드 라이브 테스트**~~ — 2026-07-31 완료. `review_app.py` 검색 탭에서 실제 검색→선별→저장까지 확인됨.
3. ~~**Semantic Scholar 무료 키 발급**~~ — 2026-08-01 완료. `.env` 에 `S2_API_KEY` 등록, 실제 검색 호출로 정상 동작 확인. **키를 받아도 "초당 1회, 전체 엔드포인트 합산" 공식 한도는 그대로 적용된다** — 서버가 이 한도를 안 지키고 있어서 `_throttled_s2_get`(arXiv 와 같은 패턴, `S2_MIN_INTERVAL=1.0`)을 새로 추가했다. 처음 `.env` 에 넣을 때 키 이름을 `S2_API_KEY` 대신 `S2 API Key`(띄어쓰기)로 잘못 써서 한 번 안 먹혔던 것도 함께 발견·수정.
4. ~~**평가셋 구축**~~ — 2026-08-05 완료. 사용자의 실제 문헌 목록(8개 카테고리, 50편)을 arXiv 자동 수집(28) + Unpaywall 오픈액세스 자동 수집(6) + 기존 6편으로 39편을 저장, 전부 요약·저장까지 마쳐 `eval.py` 기준선 확보: **숫자 1103개 중 1064개 일치 — 통과율 0.965**. §5 참고. 남은 13편(수동 PDF 업로드 필요 10편 + 링크 오류 3편)은 `review_app.py` "PDF 업로드" 탭으로 채울 것.
5. ~~**⑥ 사람 판단 구현**~~ — 2026-07-31 완료. `review_app.py`, §5 참고. ⑦ 재현 대상 선정 연결은 아직 안 함(⑦ 자체가 미착수라서).
6. ~~**⑦ 코드 재현**~~ — 2026-08-03 완료. 저장소 후보 탐색(`code_finder.py`, HuggingFace 모델카드 경유 GitHub 링크 추적 포함) + Docker 격리 실행(`docker_runner.py`, `reproduce(arxiv_id)` 자율 루프 3회)까지 §5 참고. SWE-agent 완전 성공, TSPulse 는 정직하게 실패(거짓 성공 없음) 확인됨.
7. ~~git 커밋 — `user.name` / `user.email` 미설정~~ — 이미 전역 설정 완료(`moon201595 <answnsgur030@naver.com>`, `docs/SETUP.md` 참고). 이 항목은 폐기.
8. ~~**인용 그래프(Citation Search)**~~ — 2026-08-06 완료. `s2_get_references`/`s2_get_citations`, PaSa Crawler/Selector 분리에서 Crawler만 구현. §5 참고.
9. 추출식 노트 검토 — ⑤ 를 "숫자 대조"에서 "근거 문장 번호 대조"로 격상하면 수치 환각이 구조적으로 불가능해진다. 프롬프트 난이도가 오르므로 평가셋 확보 후. **평가셋은 이미 확보됨(2026-08-05) — 이제 착수 가능.**
10. Hybrid Search(BM25+임베딩), 구조화 JSON 출력 — 외부 "심층 조사와 발전 설계" 문서가 제안한 나머지 항목, 미착수.

### 측정하지 않은 것

처리 시간, 요약 정확도, 처리량. **수치를 만들지 말 것.**

---

## 9. 폐기된 것

`~/agents-retired` — 파이프라인을 직접 오케스트레이션하던 초기 구현. `pipeline.py` 가 ①~⑤ 를 `for` 루프로 돌리는 구조였고, 이는 "오케스트레이션 코드를 쓰지 않는다"는 설계와 정면으로 어긋났다.

검증기도 폐기했다. 실측 비교에서 8개 케이스 중 **3건 오류 (harness 0건)** 였다. 원인은 `source.find(needle)` 단순 부분문자열 대조로, `28.4` 가 `128.45` 안에서 매칭되어 **틀린 숫자를 통과시켰다.**

거기서 이 프로젝트로 옮긴 것은 3개다: ② 중복 제거·선별, ③ arXiv HTML 우선 경로, ①~③ 재시도 상한 2회. 상세는 `~/agents-retired/RETIRED.md`.
