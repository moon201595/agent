# 도서관 문의 — 본문 텍스트마이닝(TDM) 접근

작성 2026-09-05 · paper-harness (AI ADVANCE TEAM 논문 모니터링)

## 왜 묻는가

매일 자동으로 관심 분야 논문을 찾아 요약하는 내부 도구를 돌리고 있습니다.
**검색·선별은 잘 되는데 본문을 못 받습니다.** 무료 공개 경로를 다섯 개
전부 실측했고 결과는 아래와 같습니다.

| 무료 경로 | 회수율 |
|---|---|
| Semantic Scholar `openAccessPdf` | 0/5 (링크는 있으나 HTML 반환) |
| Unpaywall | 0/5 |
| OpenAlex 오픈액세스 위치 | 1/14 (해당 1편도 출판사 봇 차단) |
| arXiv preprint 역검색 | 0/14 |
| Europe PMC | 0/12 (1편 색인되나 `isOpenAccess: N`) |

즉 **공개 경로로는 사실상 0%** 입니다. 산업공학 저널은 preprint·green OA
문화가 얕은 것이 원인으로 보입니다.

## 막힌 논문의 출판사 분포 (최근 실행 12편 기준)

| 출판사 | 편수 |
|---|---|
| **Elsevier** (10.1016) | **5** |
| **IEEE** (10.1109) | **4** |
| Nature/Springer (10.1038) | 1 |
| ASCE (10.1061) | 1 |
| 기타 | 1 |

**Elsevier + IEEE 가 9/12 (75%)** 입니다.

확인된 대상 저널: Measurement · Solar Energy · Applied Soft Computing ·
Engineering Applications of Artificial Intelligence · Advanced Engineering
Informatics · Displays · Journal of Water Process Engineering ·
IEEE Electron Device Letters · IEEE Instrumentation & Measurement Magazine

## 여쭙고 싶은 것 (세 가지)

1. **KETI 가 ScienceDirect(Elsevier) 와 IEEE Xplore(IEL) 를 구독 중인가요?**
   KESLI(KISTI 주관 국가 컨소시엄) 참가 현황도 함께 알고 싶습니다.

2. 구독 중이라면 **위 저널들이 구독 패키지에 포함**되나요?

3. 구독 라이선스에 **TDM(text and data mining) 조항**이 있나요?
   없으면 무상 추가 요청이 가능한지 궁금합니다.
   가능하다면 **분석 서버의 IP 를 기관 등록 IP 대역에 포함**시키거나
   InstToken 발급이 필요합니다.

## 확인되면 우리가 지킬 것

- **비상업 연구 목적으로만** 사용합니다(내부 동향 파악).
- 출판사가 지정한 **공식 TDM API 로만** 접근합니다.
  웹페이지·PDF 직접 스크래핑은 하지 않습니다.
- Elsevier 규정에 따라 **프로젝트 종료 시 원문을 삭제**하고,
  외부에 내보내는 것은 분석 산출물과 200자 이내 스니펫(DOI 링크 병기)으로
  한정합니다.
- 호출 상한(Elsevier 주 50,000건·초당 10건)을 코드에 강제합니다.

## 참고 — 안 되면 어떻게 할 것인가

본문을 못 받아도 시스템은 돕니다. 초록 기반 요약·키워드 추이·인용망 분석은
그대로 동작하고, 학술문헌상 **동향 파악 목적에는 초록이 본문과 사실상
상호대체 가능**하다는 근거도 있습니다. 다만 **수치 검증과 코드 재현은
본문이 있어야만** 가능해서, 그 두 기능이 저널 논문에서는 비어 있습니다.
