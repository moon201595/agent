# Codex 독립 검토 — 2026-09-30 링크 감사·SOTA 근거·메일 형식

잡 task-munhb9z7-j031xs (5분 30초, 읽기 전용). 원문 그대로. 반영 내역은 docs/PROGRESS.md 194절.

1. **확인된 결함**

- **P1 · [email_delivery.py:94](/home/mjh/paper-harness/email_delivery.py:94) — 차단 로그가 메일 발송을 중단시킨다.**  
  입력 `https://[oops`를 넣어 `send_digest_email()`을 가짜 SMTP로 실행했다. 감사는 URL을 차단하지만, 로그용 `urlparse(u)`에서 `ValueError: Invalid IPv6 URL`이 발생해 **SMTP 호출이 0회**였다. 기대 결과는 링크만 제거하고 발송하는 것이다. 로그의 URL 파싱도 예외를 처리하고, 로그 실패가 발송으로 전파되지 않게 해야 한다. [직접 재현]

- **P1 · [link_policy.py:240](/home/mjh/paper-harness/link_policy.py:240), [link_policy.py:258](/home/mjh/paper-harness/link_policy.py:258) — HTML 감사에 여러 우회가 있다.**  
  `<a data-href="https://arxiv.org/abs/1" href="https://evil.example/x">X</a>`가 **차단 0건으로 그대로 반환**됐다. 실제 `href` 대신 `data-href` 안의 문자열을 검사한다. 허용 앵커 안의 중첩 악성 앵커·`img src`도 그대로 남았고, 차단된 앵커의 내부 이미지도 살아남았다. `<meta http-equiv="refresh" content="0;url=…">`와 CSS `url()` 역시 검사되지 않았다. 기대 결과는 모든 URL 위치의 검사·제거다. 정규식으로 앵커 전체를 보관·복원하지 말고, 파싱한 태그와 실제 속성을 기준으로 전체 트리를 정리해야 한다. [직접 재현]

- **P1 · [link_policy.py:177](/home/mjh/paper-harness/link_policy.py:177), [link_policy.py:204](/home/mjh/paper-harness/link_policy.py:204) — 감사 시간 예산이 전체 감사를 제한하지 않는다.**  
  제어한 시계와 가짜 DNS로 예산을 1초, DNS 호출마다 경과 시간을 10초씩 증가시켰다. 서로 다른 일반 허용 호스트 두 개가 **가상 경과 20초에도 모두 통과**했다. DNS에는 시간 제한이 없고, 일반 호스트는 예산 검사 전에 반환한다. 기대 결과는 예산 소진 시 남은 링크를 제거하고 발송하는 것이다. DNS를 포함한 전체 작업에 실제 마감 시간을 적용해야 한다. 실제 DNS 지연 시간은 미실측이다. [직접 재현]

- **P2 · [link_policy.py:181](/home/mjh/paper-harness/link_policy.py:181), [link_policy.py:192](/home/mjh/paper-harness/link_policy.py:192) — 일시적인 DNS 실패가 이후 정상 반응 버튼까지 차단한다.**  
  첫 Pages 조회에서 `OSError`를 발생시키고 다음 메일부터 공인 IP를 반환하도록 바꿨다. `start()` 후 토큰이 다른 정상 버튼도 차단됐으며 DNS 호출은 총 1회였다. 호스트 실패가 프로세스 전체에 캐시되기 때문이다. 일시적 조회 실패는 다음 메일에서 재시도하거나 짧은 만료 시간을 적용해야 한다. [직접 재현]

- **P2 · [link_policy.py:223](/home/mjh/paper-harness/link_policy.py:223), [link_policy.py:277](/home/mjh/paper-harness/link_policy.py:277) — 평문 검사 누락과 차단 문구의 URL 재노출이 있다.**  
  `www.evil.example/x`, `원문https://evil.example/x`가 검사 없이 남았다. 또한 차단한 `https://doi.org/https://evil.example/x`는 `DOI https://evil.example/x [링크 차단됨]`으로 반환돼 비허용 URL을 다시 노출했다. 기대 결과는 자동 링크가 될 주소를 비활성화하는 것이다. URL 탐지 범위를 보완하고, 차단 표시로 남기는 DOI 문자열도 주소로 활성화되지 않게 처리해야 한다. 메일 앱별 자동 링크 동작은 미실측이다. [직접 재현]

- **P1 · [sota_evidence.py:88](/home/mjh/paper-harness/sota_evidence.py:88), [sota_evidence.py:266](/home/mjh/paper-harness/sota_evidence.py:266) — 성능과 무관한 숫자로 ‘성능 수치 원문 확인’이 된다.**  
  SOTA 주장 뒤에 `We report AUROC on MVTec AD using 10.5% of the training samples.`를 넣으면 성능 근거로 채택한다. `Previous methods obtain 99.1% AUROC on MVTec AD.`만 있어도 채택한다. 전자는 훈련 데이터 비율이고 후자는 다른 방법의 결과다. 기대 결과는 해당 논문의 성능 수치가 확인되지 않았으므로 미검증 유지다. 벤치마크·지표·숫자의 단순 동시 출현과 실제 성능 근거를 구분하고, 관계를 확인하지 못하면 승격하지 않아야 한다. [직접 재현]

- **P2 · [sota_evidence.py:170](/home/mjh/paper-harness/sota_evidence.py:170), [sota_evidence.py:326](/home/mjh/paper-harness/sota_evidence.py:326) — HF 조회 실패·미관측 값을 ‘없음’·0으로 바꾼다.**  
  논문 API가 연결 모델 4개를 반환하고 모델 API가 실패하면 `공식 코드·HF 연결 없음`으로 표시한다. 모델의 `downloads=None`, `likes=None`은 `30일 다운로드 0 · 좋아요 0`으로 표시한다. 기대 결과는 각각 조회 실패·미관측이다. 모델 조회 오류를 별도로 보존하고, 집계와 저장에서도 `None`을 0으로 바꾸지 않아야 한다. [직접 재현]

- **P2 · [sota_evidence.py:243](/home/mjh/paper-harness/sota_evidence.py:243) — 당일 캐시가 변경된 원문·주장도 무시한다.**  
  메모리 SQLite에서 성능 근거 없는 초록을 수집한 뒤, 같은 날 초록에 `Our method achieves 99.1% AUROC on MVTec AD.`를 추가했다. 새 입력으로 직접 계산하면 근거가 나오지만 `collect()`는 기존 `claim`을 반환했다. 캐시가 현재 주장·출처·내용을 확인하기 전에 반환된다. 외부 통계의 일일 캐시와 원문 성능 판정을 분리하거나 입력 해시로 무효화해야 한다. [직접 재현]

- **P2 · [digest.py:1969](/home/mjh/paper-harness/digest.py:1969), [digest.py:1975](/home/mjh/paper-harness/digest.py:1975), [digest.py:1989](/home/mjh/paper-harness/digest.py:1989) — 새 카드에도 글자색·배경색 짝 규칙이 빠져 있다.**  
  초록 기반 카드 하나를 생성해 속성을 검사하면 제목 링크·원문 링크·번호에 `color`만 있고 배경색은 없다. 기대한 “글자색에는 배경색을 짝지운다” 규칙을 충족하지 않는다. 해당 요소에 카드 배경색을 함께 지정하고 검사 대상에 인라인 요소도 포함해야 한다. 실제 다크모드에서의 가독성 저하는 미실측이다. [직접 재현]

- **P2 · [test_digest.py:629](/home/mjh/paper-harness/test_digest.py:629) — 카드 밖으로 상세 내용이 빠져도 회귀 테스트가 통과한다.**  
  렌더러 반환값의 첫 `<details>` 앞에 `</div><div>`를 삽입해 상세 내용을 별도 형제 상자로 옮겼다. 해당 테스트 함수의 **전체 assertion이 그대로 통과**했다. 시작·끝 문자열과 태그 개수는 부모·자식 관계를 검증하지 못한다. HTML을 파싱해 제목·상세 내용·버튼이 같은 카드 아래 있는지 확인해야 한다. pytest 실행 결과가 아니라, 원래 테스트 함수를 메모리에서 직접 호출한 결과다. [직접 재현]

2. **결함 아님으로 판단한 의심 지점**

- 대문자·탭·줄바꿈을 섞은 `javascript:`, HTML 엔티티 스킴, 따옴표 없는 단순 악성 `href`는 직접 재현에서 차단됐다.
- `https:\\evil`, 시험한 IDN·퓨니코드 위장, 잘못된 포트와 길이 초과 URL은 차단됐다. 유효한 비표준 포트 허용은 현재 명시된 정책만으로 결함이라 단정하지 않았다.
- 입력의 `\x00`을 먼저 제거하므로 시험한 플레이스홀더 충돌은 발생하지 않았다.
- 앵커 바깥의 단순 `<area href>`, `<form action>`, `<base href>`는 주소 속성이 제거됐다.
- 정상 Pages·Apps Script 버튼은 정적 정책상 허용된다. 위 DNS 실패 캐시는 별도 결함이다.
- S번호는 기존 공용 문장 분할기를 사용하고, 초록 근거에는 번호를 붙이지 않는다.
- 인기 지표로 성능 등급을 올리는 경로는 없으며, 세 축을 합산하는 점수도 없다.
- 스냅숏 표가 없을 때 저장 실패를 잡고 근거를 반환하는 경로는 동작했다. 실제 운영 DB에는 접근하지 않았다.
- 카드의 키워드와 SOTA 정보는 `<details>` 앞, 반응 버튼과 원문 링크는 밖에 있다. 접힘을 지원하지 않아도 본문을 숨기는 CSS는 없다.
- 테스트가 기대 S번호를 공용 분할기로 계산하는 것은 운영 검증기와의 번호 일치를 확인하는 용도로 타당하다. 다만 독립적인 문장 분할 정확도 검증은 아니다.

3. **검토하지 못한 범위**

- 지정한 5개 파일의 pytest 실행은 **수집 전 실패**했다. `.env` 접근·바이트코드·pytest 캐시를 막고 실행했으나, 샌드박스가 임시 파일 생성을 거부해 `FileNotFoundError: No usable temporary directory`가 발생했다. 테스트 통과 건수는 보고할 수 없다.
- 실제 SMTP 발송, DNS·DOI 리다이렉트, S2/GitHub/HF 응답과 429 재시도 시간은 미실측이다. 네트워크·SMTP 대역과 메모리 SQLite로 재현했다.
- Gmail·Outlook·Apple Mail의 실제 렌더링, 자동 링크 생성, 다크모드, 모바일 폭·클리핑은 확인하지 못했다.
- 운영 DB 상태와 실제 마이그레이션은 확인하지 않았다. 별도로 발견된 미추적 `docs/WAKE_FALLBACK_2026-09-23.md`는 지정 범위 밖이라 검토하지 않았다.
- 저장소 파일 생성·수정·삭제와 커밋은 하지 않았고, `.env`도 읽지 않았다.


Codex session ID: 01a0f01d-d7ac-7193-bd2a-d782db03e847
Resume in Codex: codex resume 01a0f01d-d7ac-7193-bd2a-d782db03e847
