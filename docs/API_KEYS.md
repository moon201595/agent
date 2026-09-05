# API 키 — 어디서 받고, 무엇에 쓰는가

작성 2026-09-05 · paper-harness

## 먼저: 지금 막힌 곳은 "검색"이 아니라 "본문 확보"다

| 단계 | 상태 |
|---|---|
| ③ 검색 | **정상.** 후보 435건에서 PhyHGNet·2-D Ambipolar 같은 팀 표적이 실제로 걸린다 |
| **본문 확보** | **막힘.** 저널 논문 회수율 0% (무료 경로 다섯 개 전부) |
| ④ 요약 | 본문 있으면 전체 요약, 없으면 초록 정리로 대체 |
| ⑤ 수치 검증 | **본문 있는 논문만** 가능 — 최근 메일에서 2편만 `[검증 23/23]` |
| ⑦ 코드 재현 | 본문·저장소 있는 것만 판정 |

검색어를 더 늘리거나 소스를 더 붙여도 이 문제는 안 풀린다.
**찾은 논문의 본문을 못 받는 것**이 병목이다.

---

## 1. 이미 쓰고 있는 키 (전부 무료)

| 용도 | 환경변수 | 발급처 | 비고 |
|---|---|---|---|
| ④ 요약 생성 (주력) | `GOOGLE_API_KEY` | https://aistudio.google.com/apikey | Gemini 무료 티어. 키 여러 개면 `GOOGLE_API_KEY2`, `_3` … 로 추가하면 429 때 자동 회전 |
| ④ 요약 폴백 | `GROQ_API_KEY` | https://console.groq.com/keys | Gemini 가 죽은 날 대체 |
| ③ 검색 (저널) | `S2_API_KEY` | https://www.semanticscholar.org/product/api#api-key-form | Semantic Scholar. 없어도 동작하나 한도가 빡빡함 |
| 초록 보강·철회 확인 | `OPENALEX_API_KEY` | https://openalex.org (무키 polite pool 가능) | 사실상 메일 주소만 있으면 됨 |
| 오픈액세스 PDF 조회 | `UNPAYWALL_EMAIL` | 키 불필요 — 메일 주소만 | https://unpaywall.org/products/api |
| 메일 발송 | `SMTP_USER` / `SMTP_PASSWORD` | 사내 SMTP 또는 Gmail 앱 비밀번호 | |

---

## 2. 본문 확보를 위해 **새로 필요한** 키

### 2-1. Elsevier — 최우선 (막힌 논문의 5/12)

- **발급:** https://dev.elsevier.com/ → "I want an API Key" → 자가등록(수 분)
- **약관:** https://dev.elsevier.com/api_service_agreement.html
- **쿼터 표:** https://dev.elsevier.com/api_key_settings.html
- **용도:** ScienceDirect Article (Full-Text) Retrieval API —
  `https://api.elsevier.com/content/article/doi/{DOI}` 로 본문 XML 수신
- **대상 저널:** Measurement · Solar Energy · Applied Soft Computing ·
  Engineering Applications of AI · Advanced Engineering Informatics ·
  Displays · Journal of Water Process Engineering
- **전제:** **기관(KETI) ScienceDirect 구독 + 기관 IP 에서 호출.**
  키만으로는 본문이 안 온다. 원격이면 InstToken 이 필요하다.

### 2-2. IEEE — 차순위 (막힌 논문의 4/12)

- **발급:** https://developer.ieee.org/
- **용도:** IEEE Xplore API — 구독(IEL) 있으면 본문, 없으면 메타데이터+초록만
- **대상:** IEEE Electron Device Letters · IEEE Instrumentation & Measurement
  Magazine · ICIP 등
- **전제:** **기관 IEL 구독.**

### 2-3. CORE — 무료, 기대값은 낮음

- **발급:** https://core.ac.uk/services/api (계정 등록 후 키 발급, 무료)
- **용도:** 저자 자가보관(green OA) 원문 애그리게이터
- **기대:** 응용공학은 green OA 자체가 희박해 추가 회수는 소폭 예상.
  **무료라 붙여볼 가치는 있다.**

---

## 3. 판단이 필요한 지점 (CLAUDE.md 규칙 1)

규칙 1 은 "유료 API·유료 티어·유료 서비스 도입을 절대 하지 않으며,
**해법으로 제안하지도 않는다**" 이다.

Elsevier·IEEE TDM API 는 **API 자체는 무료**지만 **기관 구독(유료)을 전제**한다.
새 지출도 결제수단 등록도 없고 KETI 가 이미 내고 있는 구독을 쓰는 것이지만,
규칙 문언과는 부딪힌다. **사람이 정할 일이다.**

- 규칙을 그대로 두면 → 저널 본문은 포기하고 초록 기반으로 간다(지금 상태).
- 규칙을 다듬으면 → "새 지출 없이 기관이 이미 보유한 구독을 쓰는 것은 허용"
  같은 문구로 갈라낼 수 있다. 규칙 4 를 고칠 때와 같은 종류의 결정이다.

## 4. 순서

1. **사서에게 구독 여부 확인** — `docs/LIBRARIAN_REQUEST.md` 를 그대로 보내면 된다.
2. 구독이 있으면 → 규칙 1 판단 → Elsevier 키 발급 → 막힌 5편으로 회수율 실측.
3. 병행으로 CORE 키 발급 후 14편 재조회(무료라 규칙 1 무관).
4. 그래도 안 뚫리는 논문은 본문을 포기하고 초록 + 인용 신호로 간다.
