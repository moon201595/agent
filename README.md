# paper-harness

논문 수집·정리 하네스의 결정적(deterministic) 도구 계층이다. 판단(선별 기준 조정, 사람 승인)은 MCP 클라이언트(Claude Code)가 담당하고, 이 저장소는 검색·선별·파싱·저장·수치 검증 도구만 제공한다. 이 분리가 설계의 핵심이다.

**④ 요약은 Claude Code(LLM 자체)가 아니라 무료 API(Gemini 우선, Groq 대체)가 쓴다(2026-07-31 확정)** — `batch_summarize.py`(무인 배치) 또는 `review_app.py`(Streamlit, 사람 판단 UI)를 통해서만 생성된다. 비용·무인 실행 문제로 로컬 LLM 파인튜닝과 "Claude Code가 채팅에서 직접 작성" 방식 둘 다 폐기했다.

**외부망 전제로 확정됐다 (2026-07-30).** 내부망 이식(vLLM + 자체 도구 루프)은 계획에서 빠졌다. 그 결과 오케스트레이션 코드를 직접 쓸 이유가 없어졌고, MCP + 상용 LLM 클라이언트 구조가 임시가 아니라 최종 형태다. 이전 문서의 "Phase 0 / Phase 1" 구분은 더 이상 유효하지 않다.

## 구성

- `server.py` — MCP 서버 (stdio). 도구 8종 + ⑥ 검토 상태 저장·이미지 추출 헬퍼. `.env` 자동 로드
- `batch_summarize.py` — ④ 온디맨드 배치 요약 (Claude Code 밖 독립 실행, `server.py` 함수 직접 import)
- `review_app.py` — ⑥ 사람 판단 UI (Streamlit). `streamlit run review_app.py`
- `summarize_engine.py` — ④ 요약 엔진 호출부 (Gemini 우선/Groq 대체). 위 둘이 공유
- `code_finder.py` — ⑦ 코드 저장소 후보 탐색 (본문 링크 스캔 + GitHub 검색 + HuggingFace 모델카드 경유 GitHub 링크 추적)
- `docker_runner.py` — ⑦ Docker 격리 실행. `reproduce(arxiv_id)` 가 유일한 자율 재시도 루프(최대 3회)
- `selection.py` — ② 중복 제거·선별 규칙 (네트워크·LLM 미사용)
- `verify.py` — ⑤ 수치 검증기. 요약문의 숫자를 원문과 문자열 대조 (LLM 미사용)
- `prompts/summary_template.md` — 요약 템플릿 v2 와 작성 규칙 (프롬프트 자산, 버전 관리 대상)
- `eval.py` — 저장된 전체 요약의 통과율 일괄 측정 (회귀 기준선)
- `test_smoke.py` — 실동작 스모크 7종 (네트워크 필요)
- `test_verify_units.py` / `test_select.py` — 단위 테스트 22종 (네트워크 불필요)
- `data/` — PDF·추출 텍스트·요약·이미지·SQLite 인덱스 (자동 생성, 커밋 제외)
- `.env` — `GOOGLE_API_KEY` · `GROQ_API_KEY` · `S2_API_KEY` (커밋 제외, 각자 발급)

`selection.py` 는 `select.py` 로 두면 표준 라이브러리 `select` 를 가려 asyncio 가 깨지므로 이 이름이다.

## 도구 8종

| 도구 | 단계 | 비고 |
| --- | --- | --- |
| `arxiv_search_papers` | ① | 키 불필요, 호출 간 3초 강제 |
| `s2_search_papers` | ① | 인용수 제공. `S2_API_KEY` 없으면 공용 한도 |
| `dedupe_and_rank_papers` | ② | 결정적 규칙. 네트워크 미사용 |
| `fetch_paper` | ③ | HTML 우선 → PDF 폴백. 멱등 |
| `get_paper_text` | ③ | 저장 텍스트 분할 열람 |
| `verify_summary_numbers` | ⑤ | 읽기 전용 |
| `save_summary` | — | 저장 직전 자동 검증, 불일치도 저장은 함 |
| `list_stored_papers` | — | 저장소 목록 |

④ 요약, ⑥ 사람 판단, ⑦ 코드 재현은 이 서버의 일이 아니다.

## 자율성 경계

①~③ 은 실패 시 **상한 2회**까지 코드가 재시도한다 (`MAX_RETRIES`). 재시도할지, 무엇을 다시 부를지 LLM 이 정하지 않는다 — 루프가 아니라 예외 처리다. 4xx 는 다시 불러도 같은 답이므로 재시도하지 않는다 (`RETRYABLE_STATUS`).

④⑤ 는 루프를 돌지 않는다. 단일 패스로 쓰고 검증 1회를 붙인 뒤 그대로 사람에게 넘긴다. **검증기를 루프 판정자로 쓰지 않는다** — 요약을 검증 통과까지 반복시키면 최단 통과 경로가 "숫자를 아예 쓰지 않는 것"이 되고, 통과율은 오르면서 요약 가치가 사라진다.

## 설치

Python 3.10 이상.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest test_verify_units.py test_select.py -q   # 22 passed (네트워크 불필요)
python test_smoke.py                            # 7/7 통과 (네트워크 필요)
```

`mcp` 는 2.0 을 쓴다. 1.x 의 `FastMCP` 가 `mcp.server.mcpserver.MCPServer` 로 개편됐지만 데코레이터·`annotations`(dict 그대로)·`run()` 형태가 유지되어 이식은 import 와 인스턴스 생성 두 줄이었다. `Tool` 모델의 `inputSchema` 는 `input_schema` 로 바뀌었으나 `server.py` 는 이 필드를 쓰지 않는다.

## Claude Code 등록

```bash
claude mcp add paper-harness -- ~/paper-harness/.venv/bin/python ~/paper-harness/server.py
claude mcp list    # paper-harness: ... - ✔ Connected
```

**venv 의 python 을 절대경로로 지정할 것.** 시스템 python 으로 등록하면 의존성을 못 찾는다.

기본은 **local 스코프**라 `~/paper-harness` 에서 `claude` 를 띄울 때만 붙는다. 어느 디렉터리에서나 쓰려면 `-s user` 를 붙여 등록한다.

등록 문법은 버전에 따라 다르므로 확인할 것: https://code.claude.com/docs/en/mcp

Semantic Scholar 는 공용 한도에서 429 가 잦다 — **2026-08-01 키 발급·등록 완료**, `.env` 에 `S2_API_KEY=키값` 한 줄 추가하면 된다 (server.py 가 기동 시 자동으로 읽는다. `export` 로 셸에 직접 넣을 필요 없음).

**주의 — 키가 있어도 공식 한도(초당 1회, 전체 엔드포인트 합산)는 그대로다.** 서버가 이 간격을 자동으로 지키도록 arXiv 와 동일한 패턴(`_throttled_s2_get`)으로 막아뒀다 — 직접 API 를 호출하는 코드를 새로 짤 때는 이 한도를 다시 확인할 것.

## 사용 흐름

**표준 경로 — 터미널 스크립트** (요약은 무료 API가 씀, Claude Code 불필요):

```bash
python batch_summarize.py --keyword "transformer 경량화" --top-n 3
streamlit run review_app.py
```

**Claude Code 채팅에서는 검색·선별까지만 맡긴다** (④ 요약 작성은 더 이상 시키지 않음):

```
"transformer 경량화 논문을 arxiv_search_papers 와 s2_search_papers 로 각각 찾고,
두 결과를 합쳐 dedupe_and_rank_papers 로 상위 3편을 선별해줘."
```

두 검색 결과를 **함께** `dedupe_and_rank_papers` 에 넣어야 한다. 인용수는 S2 만 주므로 arXiv 결과만 넣으면 정렬이 연도만으로 이뤄진다.

`save_summary` 는 저장 직전에 수치 검증을 자동 수행하지만 불일치가 있어도 **저장을 차단하지 않는다** — 과제 KPI "(목표)" 수치처럼 원문 밖 출처의 숫자는 정당하게 불일치할 수 있다. 불일치는 사람이 원문·출처를 확인한다.

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

경계 규칙은 `test_verify_units.py` 에 잠겨 있다. 한국어 조사(`99.87도` → `99` 로 잘림)와 자릿수 경계(`28.4` 가 `128.45` 안에서 매칭)는 실제로 한 번씩 틀렸던 것이므로 회귀 테스트를 지울 것.

## 평가셋 운용

정리해 둔 논문 약 20편을 `fetch_paper` 로 저장하고 기존 요약을 `save_summary` 로 넣으면 고정 평가셋이 된다. 프롬프트 템플릿을 바꿀 때마다:

```bash
python eval.py                   # 통과율 보고
python eval.py --min-ratio 0.9   # 기준 미달 시 종료코드 1
```

외부망 확정으로 "로컬 모델 전환 시 품질 하락 기준선"이라는 원래 목적은 없어졌지만, **프롬프트·템플릿 변경 회귀 감지용으로는 그대로 유효하다.**

## 미해결

이 목록은 `docs/PROGRESS.md` §8 이 최신이다 — 이 파일은 갱신이 늦을 수 있으니 날짜 있는 최신 상태는 그쪽을 볼 것. 2026-08-03 기준 남은 것만 추리면:

- ~~⑦ 코드 재현~~ — 2026-08-03 완료. `code_finder.py`(후보 탐색) + `docker_runner.py`(Docker 격리 실행, 자율 루프 3회)로 8종 도구 이후 마지막 단계까지 구현됨. `reproduce(arxiv_id)` 하나로 후보 탐색부터 판정까지 끝난다. 실측(SWE-agent 완전 성공, TSPulse 계열은 거대 ML 모노레포 특성상 15분 설치 예산 내 실패 — 상세는 `docs/PROGRESS.md` §5)
- ~~Semantic Scholar 키 발급~~ — 2026-08-01 완료. 등록·스로틀 적용까지 끝남
- 평가셋 미구축 — 논문 약 20편을 투입해야 `eval.py` 기준선이 생긴다
- 처리 시간·요약 정확도 수치 측정 없음 — **발표에서 수치를 만들지 말 것**

(참고: 논문 1편 실제 왕복·④ 실행 형태 결정·⑥ 사람 판단 구현·git 커밋/GitHub 등록은 모두 완료됐다. `batch_summarize.py`·`review_app.py` 참고.)

도구 입력 스키마는 인자를 `params` 객체로 한 겹 감싼 형태다 (단일 pydantic 모델을 받는 구조 때문). 동작에는 문제가 없다.

## 의존성 라이선스

mcp(MIT), httpx(BSD-3), pypdf(BSD-3), beautifulsoup4(MIT) — 작성 시점 기준이며 기관 반입 신청 전에 각 저장소에서 재확인할 것.

PyMuPDF 는 AGPL 이라 의도적으로 배제했다. 과제 계획서의 기술 실시·제3자 실시권 조항 때문이다. **의존성 추가 시 라이선스를 먼저 확인하는 것이 선행 조건이다.**

## 데이터 이용 주의

arXiv 논문 대부분은 기본 라이선스로 제출되어 제3자 재배포 권한이 없다. 내부 연구용 저장·분석은 가능하나 추출 전문을 기관 외부로 재배포하지 말 것.
