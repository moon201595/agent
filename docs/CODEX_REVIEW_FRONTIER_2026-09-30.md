# Codex 독립 검토 — 2026-09-30 성능 동향(frontier) 기능

잡 task-munmga9d-s83rkq (읽기 전용). 원문 그대로. 반영 내역은 docs/PROGRESS.md 198절.

**1. 확인된 결함 목록**

P0는 확인하지 못했습니다. 아래 ‘직접 재현’은 `/tmp`에서 파일 생성 없이 운영 함수를 호출하고, 메모리 SQLite·가짜 페이지·가짜 시계로 확인한 결과입니다. 운영 데이터나 실제 네트워크에서 재현했다는 뜻은 아닙니다.

1. **P1 · 조건 미확인 관측값으로 우열을 계산하고 메일에 표시함 — 직접 재현**
   
   근거: [frontier_store.py:63](/home/mjh/paper-harness/frontier_store.py:63), [research_frontier.py:271](/home/mjh/paper-harness/research_frontier.py:271).
   
   입력: 벤치마크·지표 이름은 같지만 학습 방식·분할이 다른 두 논문. 실제로는 조건 필드 자체를 저장하지 않고 `max/min`과 대소 비교를 수행하며, 메일에 “최고보다 높음 … 같은 조건인지는 미확인”을 표시합니다. **계약 4**에서는 조건 차이·미확인 상태를 우열 계산에서 제외해야 합니다.
   
   수정 방향: 조건 키와 확인 근거를 저장하고, 불완전하거나 다른 조건은 `incomparable`로 분리해야 합니다.

2. **P1 · 에이전트의 `same_conditions=true`만으로 최고 성능 후보가 됨 — 직접 재현**
   
   근거: [external_evidence.py:201](/home/mjh/paper-harness/external_evidence.py:201), [external_evidence.py:254](/home/mjh/paper-harness/external_evidence.py:254), [research_frontier.py:202](/home/mjh/paper-harness/research_frontier.py:202).
   
   입력: 모델명·DS 수치만 있는 표와 `same_conditions=true, differences=[]`. 버전·분할·학습 조건 근거가 전혀 없어도 `verified`가 되고, 실제 출력은 **“관측 범위 내 최고 성능 후보”**였습니다. 차이 인용은 검사하지만 **조건 일치 근거는 검사하지 않습니다.** **계약 1·4** 위반입니다.
   
   수정 방향: 조건별 근거를 필수화하고, 어느 한쪽이라도 미확인인 조건은 일치로 승인하지 않아야 합니다.

3. **P1 · 다른 논문에 캐시를 적용하면서 상대적인 조건 판정까지 복사함 — 직접 재현**
   
   근거: [frontier_store.py:89](/home/mjh/paper-harness/frontier_store.py:89), [research_frontier.py:154](/home/mjh/paper-harness/research_frontier.py:154).
   
   입력: 논문 A를 대상으로 확인한 경쟁 결과를, 같은 벤치마크·지표지만 학습 조건이 다른 B에 재사용. 실제로 `same_conditions`와 `differences`가 그대로 넘어가며, B도 **“관측 범위 내 최고 성능 후보”**가 됐습니다. **계약 2**의 캐시 재사용이 **계약 1·4**의 조건 확인을 우회합니다.
   
   수정 방향: 출처·셀 관측값만 재사용하고, 대상 논문과의 조건 비교는 다시 수행해야 합니다.

4. **P1 · 다른 모델 행을 요청한 모델의 값으로 승인함 — 직접 재현**
   
   근거: [external_evidence.py:231](/home/mjh/paper-harness/external_evidence.py:231), [external_evidence.py:244](/home/mjh/paper-harness/external_evidence.py:244).
   
   입력: `SimLingo=85.07`, `SimLingo-base=80.00`인 표에서 에이전트가 `model=SimLingo, value=80`을 제안. 부분 문자열로 두 행을 잡은 뒤 숫자로 선택하여, **model은 SimLingo인데 locator는 SimLingo-base인 verified 결과**를 반환했습니다. **계약 3**의 행×열 귀속 검증 위반입니다.
   
   수정 방향: 모델·변형·조건을 먼저 식별하고, 복수 행이 남으면 숫자로 신원을 선택하지 말고 거부해야 합니다.

5. **P1 · 단위 추측과 방향 제거로 잘못된 비교가 가능함 — 직접 재현**
   
   근거: [research_frontier.py:190](/home/mjh/paper-harness/research_frontier.py:190), [external_evidence.py:235](/home/mjh/paper-harness/external_evidence.py:235), [performance_results.py:156](/home/mjh/paper-harness/performance_results.py:156).
   
   입력 A: 같은 단위의 RMSE `0.8`과 `2.0`. `_better`가 `0.8`을 `80`으로 바꾸어 낮을수록 좋은 지표의 우열을 뒤집습니다. 입력 B: 대상 `DS ↑`, 외부 표 `DS ↓`. 방향 표식을 지운 키만 비교하여 외부 셀을 승인했습니다. **계약 1·3·4** 위반입니다.
   
   수정 방향: 명시적 단위 근거가 있을 때만 척도를 변환하고, 외부 셀의 방향도 비교 키에 포함해야 합니다. 내부 관측 비교의 원시 값 비교도 함께 정리해야 합니다.

6. **P1 · 임의 GitHub 저장소를 공식 출처처럼 수치 근거로 받음 — 직접 재현**
   
   근거: [external_evidence.py:202](/home/mjh/paper-harness/external_evidence.py:202), [external_evidence.py:211](/home/mjh/paper-harness/external_evidence.py:211), [external_evidence.py:223](/home/mjh/paper-harness/external_evidence.py:223).
   
   입력: `https://github.com/unrelated/Bench2Drive-copy`와 적절한 README 표. 공식성 검증 없이 `verified`를 반환했습니다. 저장소 이름에 벤치마크가 포함되면 벤치마크 귀속까지 인정합니다. **계약 2**의 “공식 GitHub만”은 프롬프트에만 있고 Python에서는 강제되지 않습니다.
   
   수정 방향: 공식 벤치마크·저자와 저장소의 연결 근거를 검증한 뒤 fetch·사용을 허용해야 합니다.

7. **P1 · 180초가 hard deadline으로 구현되지 않음 — 직접 재현 + 실제 지연은 코드 읽고 추정**
   
   근거: [external_evidence.py:124](/home/mjh/paper-harness/external_evidence.py:124), [external_evidence.py:274](/home/mjh/paper-harness/external_evidence.py:274), [external_evidence.py:289](/home/mjh/paper-harness/external_evidence.py:289).
   
   입력: 가짜 시계에서 에이전트 종료 145초, 마지막 검증 fetch 종료 185초. **185초인데 `done`**을 반환했습니다. 실제 `_fetch`도 HTTP 작업별 timeout만 주며, 응답 청크를 받는 동안 전체 마감 시간을 검사하지 않습니다. DNS 검사 후 HTTP에는 남은 시간이 아닌 원래 timeout을 줍니다. **계약 2** 위반입니다.
   
   수정 방향: 절대 마감 시간을 DNS·리다이렉트·본문 수신·파싱에 전달하고, 완료 후에도 마감 초과 여부를 검사해야 합니다. 병렬 작업 전체에도 종료 가능한 상한이 필요합니다.

8. **P1 · HTML·S2·HF·GitHub 조회가 전체 예산 밖에서 논문·프로필 수만큼 누적됨 — 코드 읽고 추정**
   
   근거: [research_frontier.py:97](/home/mjh/paper-harness/research_frontier.py:97), [research_frontier.py:124](/home/mjh/paper-harness/research_frontier.py:124), [adoption_signals.py:158](/home/mjh/paper-harness/adoption_signals.py:158), [adoption_signals.py:183](/home/mjh/paper-harness/adoption_signals.py:183), [run_profile_scan.py:556](/home/mjh/paper-harness/run_profile_scan.py:556).
   
   입력: 프로필 4개에 서로 다른 내용 자리 논문 각 N편, S2 batch 실패. HTML 조회 후 외부 조사 180초를 별도로 시작하고, 이후 S2 개별 폴백·HF 조회를 순차 실행합니다. S2 batch는 당일 캐시 확인 전에도 호출합니다. migrate 전에는 신호 저장 실패 때문에 같은 논문의 조회도 프로필마다 반복됩니다.
   
   **실측이 아닌 설정값 합산 예시**로, HTML 15초·S2 개별 10초·HF 10초만 잡아도 `180 + 4×30 + 4N×35초`입니다. N=5라면 약 16분 40초 규모이며, 리다이렉트·재시도·추가 모델/GitHub 조회는 제외한 값입니다. **계약 2**의 전체 상한을 충족하지 않습니다.
   
   수정 방향: 공통 deadline과 프로필 간 조회 캐시를 적용하고, 캐시 확인을 batch 요청보다 먼저 해야 합니다.

9. **P2 · 동점을 상대 논문의 우세로 표시함 — 직접 재현**
   
   근거: [frontier_store.py:73](/home/mjh/paper-harness/frontier_store.py:73), [research_frontier.py:274](/home/mjh/paper-harness/research_frontier.py:274).
   
   입력: 양쪽 DS가 모두 `80`. 실제 출력은 **“다른 논문 값 80 … 이 더 높음”**입니다. 같음이 `below_observed`에 들어갑니다. **계약 1·4**에 맞는 정확한 비교가 아닙니다.
   
   수정 방향: `equal`을 별도로 처리하고 단위 정규화 이후 비교해야 합니다.

10. **P2 · 관측 저장 키가 충돌하고 재추출에서 사라진 값이 남음 — 직접 재현**
    
    근거: [arxiv_tables.py:251](/home/mjh/paper-harness/arxiv_tables.py:251), [performance_results.py:248](/home/mjh/paper-harness/performance_results.py:248), [frontier_store.py:21](/home/mjh/paper-harness/frontier_store.py:21), [frontier_store.py:38](/home/mjh/paper-harness/frontier_store.py:38).
    
    입력: 같은 figure 안에 DS 표와 SR 표가 있고 각각 셀이 `r1c1`. 파서 API는 두 표에 같은 figure ID를 반환하므로 둘 다 `T1:r1c1`이 되고, 저장 후 **SR만 남았습니다.** 이후 빈 재추출 결과를 기록해도 이전 SR은 남습니다. **계약 1·3**의 관측 축적과 근거 보존을 훼손합니다.
    
    수정 방향: 독립 table 식별자·원문 버전을 키에 넣고, 재추출 시 최신 유효 관측과 과거 기록을 구분해야 합니다.

11. **P2 · 구조가 있는 결과도 추출에서 누락하고 비-arXiv 논문은 통째로 제외함 — 직접 재현 / 일부 코드 읽고 추정**
    
    근거: [performance_results.py:228](/home/mjh/paper-harness/performance_results.py:228), [research_frontier.py:98](/home/mjh/paper-harness/research_frontier.py:98).
    
    입력: 캡션 `AUROC results on Real3D-AD`, 열 `Method / Airplane / Mean`, 행 `Ours / 0.8 / 0.9`. **빈 결과를 반환**했습니다. 외부 검증에는 캡션 지표 보완이 있지만 자체 결과 추출에는 없습니다. 또한 `pdf-…` ID는 추출 시도 전에 건너뜁니다. **계약 1**의 모든 내용 자리 논문 수집에 공백이 있습니다.
    
    수정 방향: 캡션 지표와 구조적 평균 열의 결합을 검증하고, 비-arXiv 입력도 처리하거나 지원 불가 상태를 명시해야 합니다.

12. **P2 · 검증 fetch 실패를 조사 완료로 기록함 — 직접 재현**
    
    근거: [external_evidence.py:217](/home/mjh/paper-harness/external_evidence.py:217), [external_evidence.py:294](/home/mjh/paper-harness/external_evidence.py:294), [research_frontier.py:221](/home/mjh/paper-harness/research_frontier.py:221).
    
    입력: 경쟁 출처 fetch가 `TimeoutError`. 경쟁 항목은 `fetch_failed`인데 전체 상태는 **`done`**, 메일은 “외부 경쟁 결과를 원문 표에서 확인하지 못함”이 됩니다. 이 실패 결과는 7일 `done` 캐시 대상도 됩니다. **계약 2**의 “외부 비교 미완료”와 다릅니다.
    
    수정 방향: 접근 실패·예산 초과와 정상 조사 후 미발견을 구분하고, 실패 결과로 장기간 재조사를 막지 않아야 합니다.

13. **P2 · 검증한 arXiv 버전과 표시하는 출처 버전이 다를 수 있음 — 직접 재현**
    
    근거: [external_evidence.py:152](/home/mjh/paper-harness/external_evidence.py:152), [external_evidence.py:207](/home/mjh/paper-harness/external_evidence.py:207).
    
    입력: `https://arxiv.org/html/2609.12345v1`. 실제 fetch URL은 **버전을 제거한 `/html/2609.12345`**, 결과의 출처 URL은 여전히 `v1`입니다. 최신 버전의 수치를 과거 버전 링크에 붙일 수 있어 **계약 3**의 근거 귀속이 깨집니다.
    
    수정 방향: 지정 버전을 유지하고 실제 조회 URL·버전·내용 해시를 저장해야 합니다.

**2. 결함 아님으로 판단해 제외한 지점과 테스트의 한계**

- **문장 수치를 관측 DB에 직접 넣는 경로는 발견하지 못했습니다.** `record`에는 `table_results` 결과가 들어가고, 문장 근거는 별도로 렌더링합니다. [research_frontier.py:106](/home/mjh/paper-harness/research_frontier.py:106)
- **제3자 저장소 별 수집은 운영 경로에서 차단됩니다.** `_repo_of`는 `official/author`만 반환합니다. 성능 조사에서 임의 GitHub를 받는 6번 결함과는 별개입니다. [adoption_signals.py:146](/home/mjh/paper-harness/adoption_signals.py:146)
- **읽을 포인트를 가중치 학습에 직접 넣는 경로는 발견하지 못했습니다.** 가중치 계산은 유효 사용자 반응을 읽습니다. 다만 읽을 포인트에 조건 미확인 우열 문구가 새는 문제는 1번에 포함했습니다. [feedback_weights.py:261](/home/mjh/paper-harness/feedback_weights.py:261)
- **새 DDL의 schema_guard 우회는 발견하지 못했습니다.** migrate 소유자 등록도 있습니다. 표가 없을 때 저장 실패를 잡아 발송을 계속하는 경로는 존재합니다. [migrate.py:40](/home/mjh/paper-harness/migrate.py:40)
- **같은 프로세스에서는 migrate 전에도 외부 에이전트 재호출을 막습니다.** `_CALLED_ON`과 `_RESULTS`가 있습니다. 다만 프로세스 재시작까지 지속되는 보장은 없고, 신호 조회 캐시는 별개입니다. [research_frontier.py:145](/home/mjh/paper-harness/research_frontier.py:145)
- **새 카드의 색상·배경 짝과 escape 처리는 확인했습니다.** SMTP 직전 링크 감사도 그대로 통과합니다. 실제 메일 클라이언트 다크모드 품질까지 확인한 것은 아닙니다. [digest.py:1946](/home/mjh/paper-harness/digest.py:1946), [email_delivery.py:91](/home/mjh/paper-harness/email_delivery.py:91)
- **범주 열이 남는다는 사실만으로 오귀속이라 단정하지 않았습니다.** 재현한 `AUROC / Airplane`, `AUROC / Car`는 서로 다른 키로 보존됐습니다.
- **테스트는 운영 함수를 실제로 호출하지만, 일부는 잘못된 계약을 고정합니다.** [test_frontier.py:70](/home/mjh/paper-harness/test_frontier.py:70)은 SimLingo 요청에 SimLingo-base 값을 승인하는 것을 기대합니다. [test_frontier.py:184](/home/mjh/paper-harness/test_frontier.py:184)은 조건 미확인 우열 표시를 기대합니다. 예산 테스트는 fetch 시작 전 소진만 검사하고 진행 중 초과는 검사하지 않습니다. 조건 일치 테스트도 `same=True`를 직접 넣어 근거 검증을 생략합니다.

**3. 검토하지 못한 범위와 미확정 의심 지점**

- 실제 외부 페이지·운영 DB·SMTP·헤드리스 Codex는 호출하지 않았습니다. 운영 추가 지연, 실제 캐시 내용, 실제 발송 성공은 **미실측**입니다.
- 에이전트 인자에는 임시 cwd, read-only, 사용자 설정·규칙 무시, shell/apps/MCP 등의 비활성화가 있습니다. 그러나 설치된 CLI가 모든 인자를 실제로 적용하는지, 파일 읽기·다른 도구까지 완전히 차단되는지는 실행하지 않아 **미확정**입니다. [external_evidence.py:92](/home/mjh/paper-harness/external_evidence.py:92)
- `_fetch`는 HTTPS·허용 호스트·공인 DNS·리다이렉트별 검사를 합니다. 다만 검사한 IP를 HTTP 연결에 고정하지 않아 DNS 검사와 실제 연결 사이에 틈이 있습니다. 악용 가능한 호스트·환경은 확인하지 못했으므로 **SSRF 성공으로 단정하지 않습니다.**
- 프로세스 HTML 캐시는 날짜·버전별 만료가 없습니다. 장기 실행 프로세스의 stale 가능성은 있으나 실제 운영 프로세스 수명은 검증하지 않았습니다.
- `arxiv_tables.py`는 요청대로 API 사용 방식만 검토했습니다.

**4. 지정 pytest 실행 결과**

지정 명령을 그대로 실행했으나 **테스트 수집 전에 종료 코드 1로 실패**했습니다.

- 실행된 테스트: **0개**
- 통과: **0개**
- 테스트 assertion 실패: **0개** — 성공을 뜻하지 않습니다.
- 실패 테스트 이름: 없음
- 시작 실패 원인: `FileNotFoundError: No usable temporary directory found`
- `/tmp`, `/var/tmp` 등을 사용할 수 없는 읽기 전용 샌드박스 제약입니다.

파일 수정·생성, 커밋, 운영 DB·네트워크·헤드리스 호출은 하지 않았습니다.


Codex session ID: 01a0f0a1-9176-7560-a226-375fa773f802
Resume in Codex: codex resume 01a0f0a1-9176-7560-a226-375fa773f802
