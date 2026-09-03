# paper-harness 작업 규칙

이 레포에서 코드를 수정하는 모든 세션은 아래 규칙을 따른다.

## 비용 원칙 (최우선)

1. **모든 것은 무료여야 한다.** 유료 API, 유료 티어, 결제수단 등록,
   유료 서비스 도입을 절대 하지 않으며, 해법으로 제안하지도 않는다.
2. 429/한도 초과/크레딧 소진의 해법은 순서대로 (a) 다음 무료 provider로
   폴백 (b) retry-after 준수 지수 백오프 (c) 처리량 축소다.
   "업그레이드"는 해법이 아니다.
3. 무료 한도 수치를 코드에 하드코딩하지 않는다. 한도는 예고 없이
   바뀌므로(2025-12 Gemini 삭감 실례) 429 응답 처리로만 대응한다.
4. Gemini 무료 티어는 프롬프트를 Google 제품 개선에 쓸 수 있다.
   LLM에는 **공개 arXiv 논문 텍스트만** 보낸다. 사내 문서·계획서·
   내부 데이터를 LLM API에 보내는 코드를 절대 작성하지 않는다.

## 설계 원칙

5. **전이 지점 단일 소유.** 단계 간 자동 전이(④⑤ 저장 → ⑦ 재현)는
   docker_runner.launch_background() 하나로만 트리거한다. 현재 호출
   지점은 다섯 곳이 전부다 — 자동 전이 2곳(batch_summarize.
   _process_paper()와 review_core._summarize_target(), 둘 다 요약 저장
   직후)과 review_app.py의 수동 버튼 3곳(재현 재시도·시작·재생성 —
   사람이 직접 누르는 액션). 새 자동 호출 지점을 추가하지 않는다 —
   특히 스캔 경로(scan_and_digest)는 _process_paper를 통해서만 ⑦에
   도달해야 한다. 근거: docs/PROGRESS.md §9(pipeline.py 폐기 사유).
   (2026-09-02: _summarize_target이 review_app.py에서 review_core.py로
   옮겨졌다. 규칙의 목적인 "전이 지점이 흩어지지 않는 것"은 그대로이고,
   위치만 바뀌었다 — 오히려 Streamlit 없이 테스트되는 곳으로 와서
   test_review_core.py가 "전이 지점이 여전히 한 곳"임을 감시한다.)
6. **새 지휘자 계층 금지.** 단계들을 다시 꿰는 별도 오케스트레이션
   스크립트/클래스/프레임워크(pipeline.py 부활, LangGraph, Airflow 등)를
   만들지 않는다. 기존 진입점(_process_paper, scan_and_digest,
   scan_all_profiles)을 재사용한다. cron 스케줄링은 여기 해당하지 않는다.
7. **기계가 위조 불가능하게 판정할 수 있는 것만 자동화한다.** 검증·재현
   결과는 이진 판정 가능한 신호(문자열 대조, Docker exit code, DB 기록)로만
   보고한다. 파이프라인 판정 경로에 LLM 판사를 넣지 않는다.

## 정직성 규칙

8. 수치를 만들어내지 않는다. 실측하지 않은 값은 "미실측"이라고 기록한다.
   실패는 실패로 기록한다(거짓 성공 금지 — TSPulse 사례, docs/PROGRESS.md).
9. **테스트와 검증을 약화시키지 않는다.** 테스트를 통과시키기 위해
   테스트를 삭제·완화하거나, verify.py의 [S번호] grounding 로직·임계값을
   낮추거나, eval 기준선(39편, pass_ratio 0.982)을 임의로 이동하는 것을
   금지한다. 기준선이 변동하면 조작하지 말고 원인을 분석해 PROGRESS.md에
   있는 그대로 기록한다. 기준선 이동은 검증 규칙 변경 시에만 정당하며
   사유·날짜를 함께 기록한다.
10. .env 파일을 읽거나 출력하지 않는다. 시크릿 값이 필요한 로직은
   환경변수 이름만 참조한다.

## 작업 규율

11. 모든 코드 변경은 대응 테스트와 함께 커밋한다. pytest 전체 green이
   완료 조건이다.
12. 지시된 마일스톤 범위 밖의 리팩토링·기능 추가·"개선"을 하지 않는다.
   발견한 문제는 고치지 말고 PROGRESS.md 미해결 목록에 적는다.
13. 민감 모듈(verify.py, docker_runner.py, email_delivery.py) 수정은
   plan mode로 계획을 먼저 제시하고 승인 후 진행한다.
14. 설계 결정·실측 결과는 docs/PROGRESS.md에 날짜와 함께 기록한다.
15. 파일 수정 시 sed 대신 str_replace 또는 python3 << 'PYEOF' heredoc을
   쓴다. 셸 명령은 단일 라인을 선호한다. 문서·주석은 평서체로 쓴다.

## 마일스톤 순서

M1 Deep Layer 연결 → M2 다이제스트 상태 주입 → M3 HTML 다이제스트 →
M4 ⑤ recall 보강 → M5 retraction 체크 → M6 API 확장(무료) →
M7 ⑦ 보안 하드닝 → M8 운영 투입. 순서 변경 금지.
