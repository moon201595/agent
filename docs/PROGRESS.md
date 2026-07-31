# 논문 검색·분석 에이전트 — 진행 상황

작성일: 2026-07-30 · 프로젝트 경로: `~/paper-harness` (WSL2 Ubuntu)
범위: 에이전트와 실행 환경만. 발표 자료·대본은 이 문서에 포함하지 않는다.
표기: **[확인됨]** 은 도구 실행으로 확인한 것, **[미확인]** 은 아직 근거가 없는 것.

## 한 줄 요약

결정적 도구 8종을 MCP 서버로 노출하고 에이전트 루프는 Claude Code 에 맡기는 구조가 동작한다. **MCP 연결까지 검증됐고, 논문 1편을 실제로 끝까지 태워 저장까지 확인했다(2026-07-30).** ⑦ 코드 재현만 아직 미착수다. **④ 는 `batch_summarize.py` 온디맨드 배치 스크립트로, ⑥ 은 `review_app.py` (Streamlit) 로 둘 다 구현 완료됐다(2026-07-31)** — 검색→선별→파싱→요약→검증→저장까지 채팅 승인 없이 돌고, 저장된 요약은 브라우저 화면에서 승인/반려/재생성한다. 요약 엔진은 로컬 파인튜닝이 아니라 **무료 API(Gemini 우선, Groq 대체)** 로 방향이 바뀌었다 — §8-2 참고. 요약 템플릿은 v2로 교체됐다 — §5 참고.

---

## 1. 아키텍처 — 왜 오케스트레이션 코드를 쓰지 않는가

역할이 둘로 나뉜다.

| | 정체 | 하는 일 |
| --- | --- | --- |
| MCP 서버 | `~/paper-harness` (Python) | 도구 8종 제공. **판단하지 않는다** |
| MCP 클라이언트 | Claude Code | 도구를 언제·어떤 순서로 부를지 판단. ④ 요약 작성 |

`for` 루프로 파이프라인을 돌리는 코드가 없다. 검색 → 선별 → 파싱 → 요약 → 검증의 순서는 클라이언트가 판단해 도구를 부르는 것으로 이뤄진다.

**외부망 전제로 확정됐다 (2026-07-30).** 내부망 이식(vLLM + LiteLLM + 자체 도구 루프)은 계획에서 빠졌다. 그 결과 자체 에이전트 루프를 구현할 이유가 사라졌고, MCP + 상용 LLM 클라이언트가 임시가 아니라 **최종 형태**다. 이전 문서의 "Phase 0 / Phase 1" 구분과 내부망 2존 구조는 더 이상 유효하지 않다.

### 이 구조의 한계 (열려 있는 결정)

1. ~~**사람이 Claude Code 를 띄워야 돌아간다.**~~ — ④ 요약 단계는 `batch_summarize.py` 로 해결됨(2026-07-31). Claude Code 없이 `python batch_summarize.py --ids ...` 로 검색부터 저장까지 무인 실행된다. 다만 ⑥⑦⑧ 은 여전히 Claude Code 세션 안에서 사람이 직접 수행하는 구간이라, 전체 파이프라인이 완전 무인은 아니다.
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

## 3. 도구 8종과 구현 현황

| 단계 | 도구 | 상태 |
| --- | --- | --- |
| ① | `arxiv_search_papers` | ✅ 키 불필요, 호출 간 3초 강제 |
| ① | `s2_search_papers` | ✅ 인용수 제공. 키는 선택 |
| ② | `dedupe_and_rank_papers` | ✅ 결정적 규칙, 네트워크 미사용 |
| ③ | `fetch_paper` | ✅ HTML 우선 → PDF 폴백, 멱등 |
| ③ | `get_paper_text` | ✅ 분할 열람 |
| ④ | `batch_summarize.py` (Claude Code 세션 밖 별도 스크립트) | ✅ Gemini(`gemini-flash-latest`) 우선, Groq(`llama-3.3-70b-versatile`) 대체. Claude Code 세션 안에서는 여전히 클라이언트가 직접 작성 |
| ⑤ | `verify_summary_numbers` | ✅ LLM 미사용 |
| — | `save_summary` | ✅ 저장 직전 자동 검증, 불일치도 저장은 함 |
| — | `list_stored_papers` | ✅ |
| ⑥ | `review_app.py` (Streamlit UI) | ✅ 승인·반려(사유 입력)·재생성. 원문 이미지 갤러리 포함 |
| ⑦ | 코드 재현 | ❌ 미착수. Docker 코드 한 줄도 없음 |
| ⑧ | 축적·피드백 | 🔸 SQLite 에 검증 결과 + ⑥ 검토 상태(review_status)가 쌓임. 규칙 보강·RAG 되먹임 없음 |

**자율 루프는 아직 존재하지 않는다.**

### 파일

```
server.py              MCP 서버 (stdio). 도구 8종 + ⑥ review_status 저장·이미지 추출 헬퍼
batch_summarize.py     ④ 온디맨드 배치 요약 (Claude Code 밖에서 독립 실행, server.py 함수 직접 import)
review_app.py          ⑥ 사람 판단 UI (Streamlit) — 검색·요약 생성 탭 + 요약 검토 탭
summarize_engine.py    ④ 요약 엔진 호출부 (Gemini/Groq). batch_summarize.py·review_app.py 공유
selection.py           ② 중복 제거·선별 규칙
verify.py              ⑤ 수치 검증기 (LLM 미사용)
eval.py                통과율 일괄 측정 (회귀 기준선)
prompts/
  summary_template.md  ④ 요약 템플릿 v2 (프롬프트 자산, 버전 관리 대상)
test_smoke.py          실동작 7종 (네트워크 필요)
test_verify_units.py   ⑤ 경계 규칙 14종
test_select.py         ② 규칙 8종
data/                  PDF·텍스트·요약·이미지·SQLite (자동 생성, 커밋 제외)
.env                   GOOGLE_API_KEY · GROQ_API_KEY (커밋 제외, 각자 발급)
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
| ⑦ 코드 재현 | 자율 루프 3회 | 실행 성공 또는 시도 3회 (미구현) |

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

### [확인됨] 단위 테스트 22개 (네트워크 불필요)

`verify.py` 경계 규칙 14종 + `selection.py` 규칙 8종. 전부 "한 번 틀렸거나 틀릴 뻔한" 케이스다.

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

```bash
cd ~/paper-harness && claude
```

```
"transformer 경량화 논문을 arxiv_search_papers 와 s2_search_papers 로 각각 찾고,
두 결과를 합쳐 dedupe_and_rank_papers 로 상위 3편을 선별해.
그 중 1편을 fetch_paper 로 저장한 뒤
@prompts/summary_template.md 형식으로 정리해서 save_summary 로 저장해.
검증 보고서에 unmatched 가 있으면 템플릿의 처리 절차대로 수정해."
```

두 검색 결과를 **함께** `dedupe_and_rank_papers` 에 넣어야 한다. 인용수는 S2 만 주므로 arXiv 결과만 넣으면 정렬이 연도만으로 이뤄진다.

---

## 8. 미해결 (우선순위)

1. ~~**논문 1편 실제 왕복**~~ — 2026-07-30 완료. §5 참고.
2. ~~**④ 실행 형태 결정**~~ — 2026-07-31 완료. `batch_summarize.py` 로 구현, §5 "④ 요약 엔진 선정" 및 "`batch_summarize.py` 첫 실행" 참고. 로컬 오픈소스 LLM 파인튜닝 계획은 프롬프트만으로는 소형 모델 품질이 안 나온다는 걸 실측으로 확인하고 폐기 — Gemini/Groq 무료 API로 대체했다. 회사 GPU 서버(호스트 `203.254.171.81`, 계정 `black`, 포트 `8281`)는 접속 정보는 받았으나 VS Code Remote-SSH 연결을 아직 안 끝냈다 — 지금은 ④ 목적으로는 불필요해졌고, 다른 용도로 필요해지면 그때 이어서 접속할 것.
2-1. ~~**`--keyword` 모드 라이브 테스트**~~ — 2026-07-31 완료. `review_app.py` 검색 탭에서 실제 검색→선별→저장까지 확인됨.
3. **Semantic Scholar 무료 키 발급** — 공용 한도로는 실사용 불가. 2026-07-30 재검색 중에도 429(요청 한도 초과) 재확인. **[미확인]** 사내 외부 API 키 발급 절차가 별도인지 확인 필요.
4. **평가셋 구축** — 이미 정리해 둔 논문 약 20편을 `fetch_paper` + `save_summary` 로 투입. `eval.py --min-ratio` 로 프롬프트 변경 회귀를 감지한다. 외부망 확정으로 "로컬 모델 전환 기준선"이라는 원래 목적은 없어졌지만 회귀 감지용으로는 유효하다.
5. ~~**⑥ 사람 판단 구현**~~ — 2026-07-31 완료. `review_app.py`, §5 참고. ⑦ 재현 대상 선정 연결은 아직 안 함(⑦ 자체가 미착수라서).
6. **⑦ 코드 재현** — GitHub API 저장소 조회 + Docker 격리 실행(`--network none`, 메모리·CPU 상한, 타임아웃), 시도 상한 3회. clone 대상은 반드시 WSL 네이티브 경로. 남은 항목 중 유일한 미착수 단계.
7. ~~git 커밋 — `user.name` / `user.email` 미설정~~ — 이미 전역 설정 완료(`moon201595 <answnsgur030@naver.com>`, `docs/SETUP.md` 참고). 이 항목은 폐기.
8. 추출식 노트 검토 — ⑤ 를 "숫자 대조"에서 "근거 문장 번호 대조"로 격상하면 수치 환각이 구조적으로 불가능해진다. 프롬프트 난이도가 오르므로 평가셋 확보 후.

### 측정하지 않은 것

처리 시간, 요약 정확도, 처리량. **수치를 만들지 말 것.**

---

## 9. 폐기된 것

`~/agents-retired` — 파이프라인을 직접 오케스트레이션하던 초기 구현. `pipeline.py` 가 ①~⑤ 를 `for` 루프로 돌리는 구조였고, 이는 "오케스트레이션 코드를 쓰지 않는다"는 설계와 정면으로 어긋났다.

검증기도 폐기했다. 실측 비교에서 8개 케이스 중 **3건 오류 (harness 0건)** 였다. 원인은 `source.find(needle)` 단순 부분문자열 대조로, `28.4` 가 `128.45` 안에서 매칭되어 **틀린 숫자를 통과시켰다.**

거기서 이 프로젝트로 옮긴 것은 3개다: ② 중복 제거·선별, ③ arXiv HTML 우선 경로, ①~③ 재시도 상한 2회. 상세는 `~/agents-retired/RETIRED.md`.
