# API 키 — 무엇을 받아야 하는가

작성 2026-09-05 · paper-harness · **실측 근거 포함**

## 결론 먼저

**"최신 논문 찾기 → 요약 → 동향 보고"까지는 지금 있는 키로 이미 된다.**
추가로 받을 키는 목적에 따라 갈린다.

| 원하는 것 | 필요한 키 | 상태 |
|---|---|---|
| 최신 논문 찾기 | 없음(arXiv 무키) + `S2_API_KEY` | **이미 있음** |
| 초록 기반 요약 | `GOOGLE_API_KEY` | **이미 있음** |
| 동향 보고·리뷰 | `GOOGLE_API_KEY` | **이미 있음** |
| 메일 발송 | `SMTP_USER`/`SMTP_PASSWORD` | **이미 있음** |
| 하루 요약 편수 늘리기 | `GOOGLE_API_KEY2`, `_3` … | **추가 발급 권장** |
| **저널 본문**(수치 검증·코드 재현용) | Elsevier·IEEE | **기관 구독 필요** |

---

## 1. 지금 바로 받으면 좋은 것 (무료, 5분)

### 1-1. Gemini 키 추가 — 가장 효과 큼

- **링크:** https://aistudio.google.com/apikey
- **왜:** 요약이 전부 이 키로 돈다. 무료 한도에 걸리면(429) 그날 요약이 멈춘다.
  **키 이름을 `GOOGLE_API_KEY2`, `GOOGLE_API_KEY3` 으로 `.env` 에 넣기만 하면
  코드가 자동으로 회전한다** — 다른 설정 필요 없다.
- **주의:** 429(키별 한도)에는 회전이 듣지만 503(모델 혼잡)에는 안 듣는다.
  503 은 이미 `gemini-flash-lite` 로 모델을 바꿔 대응하고 있다.

### 1-2. CORE — 무료, 기대값은 낮음

- **링크:** https://core.ac.uk/services/api → 계정 등록 후 키 발급
- **왜:** 저자가 자기 홈페이지·기관 저장소에 올린 원문(green OA) 애그리게이터.
- **기대:** 응용공학은 green OA 가 희박해 소폭. **무료라 붙여볼 값은 있다.**

---

## 2. 이미 쓰고 있는 키

| 용도 | 환경변수 | 발급처 |
|---|---|---|
| 요약 생성(주력) | `GOOGLE_API_KEY` | https://aistudio.google.com/apikey |
| 요약 폴백 | `GROQ_API_KEY` | https://console.groq.com/keys |
| 저널 검색 | `S2_API_KEY` | https://www.semanticscholar.org/product/api |
| 초록 보강·철회 확인 | `OPENALEX_API_KEY` (무키도 동작) | https://openalex.org |
| 오픈액세스 PDF 조회 | `UNPAYWALL_EMAIL` (키 불필요) | https://unpaywall.org/products/api |
| 메일 발송 | `SMTP_USER` / `SMTP_PASSWORD` | 사내 SMTP 또는 Gmail 앱 비밀번호 |

키가 필요 없는 것: arXiv API, Crossref, Europe PMC.

---

## 3. 저널 본문 — 기관 구독이 있어야 한다 (실측으로 확인)

### 무엇을 재봤나

무료 경로 **여섯 개**를 전부 실측했다. 전멸이다.

| 경로 | 회수율 |
|---|---|
| S2 `openAccessPdf` | 0/5 (링크는 있으나 HTML 반환) |
| Unpaywall | 0/5 |
| OpenAlex 오픈액세스 위치 | 1/14 (그마저 출판사 봇 차단) |
| arXiv preprint 역검색 | 0/14 |
| Europe PMC | 0/12 |
| **Elsevier API 키 없이 호출** | **0/7** — 아래 참고 |

### Elsevier 를 직접 찔러본 결과

Crossref 에 출판사가 등록해둔 TDM 링크가 **8/12편**에 있었다(무료·무키로 조회 가능).
그 링크를 키 없이 호출했다:

```
파라미터 없이   → HTTP 200 · 1,866B   (제목·저널·DOI 만. 초록도 본문도 없음)
view=FULL      → HTTP 401 AUTHENTICATION_ERROR: Invalid API Key
```

그리고 대상 Elsevier 논문 **7편 전부 `openaccess=0`**(구독 전용)이었다.
→ **키 + 기관 구독 없이는 본문이 안 온다는 것이 확인됐다.**

### 받으려면

| | 링크 | 전제 |
|---|---|---|
| **Elsevier** (막힌 논문 5/12) | https://dev.elsevier.com/ | **KETI ScienceDirect 구독 + 기관 IP** |
| **IEEE** (4/12) | https://developer.ieee.org/ | **KETI IEEE IEL 구독** |

대상 저널: Measurement · Solar Energy · Applied Soft Computing ·
Engineering Applications of AI · Advanced Engineering Informatics · Displays ·
Journal of Water Process Engineering · IEEE Electron Device Letters ·
IEEE Instrumentation & Measurement Magazine

**먼저 할 일:** `docs/LIBRARIAN_REQUEST.md` 를 사서에게 보내 구독 여부를 확인.
그 답이 나오기 전에는 키를 받아도 소용이 없다(키만으로는 401 이다).

### 규칙 1 판단이 필요하다

CLAUDE.md 규칙 1 은 "유료 API·유료 티어를 도입하지 않으며 해법으로 제안하지도
않는다" 이다. Elsevier·IEEE 는 **API 자체는 무료**지만 **기관 구독(유료)을 전제**한다.
새 지출도 결제수단 등록도 없지만 규칙 문언과는 부딪힌다 — **사람이 정할 일이다.**

---

## 4. 본문 없이도 되는 것 / 안 되는 것

| 기능 | 본문 필요? |
|---|---|
| 논문 찾기·랭킹 | 아니오 |
| 무슨 논문인지 한 줄 요약 | 아니오(초록으로 충분) |
| 동향 보고·리뷰 | 아니오 |
| **수치 검증**(`[검증 27/27 통과]`) | **예** |
| **코드 재현**(`[재현 …]`) | **예** |

즉 **구독이 없어도 메일은 정상 동작한다.** 저널 논문에서 검증·재현 라벨이
비는 것뿐이다.
