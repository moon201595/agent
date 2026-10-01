# Codex 최종 독립 검토 — 2026-09-30 19시, 커밋 f65f0cf

잡 task-munxpjrg-2r5t2a (읽기 전용). 원문 그대로. 반영 내역은 docs/PROGRESS.md 199절. 규칙상 마지막 재검토 회차.

**f65f0cf를 최종 승인하기에는 결함이 남아 있습니다.** P0는 확인하지 못했고, P1 3건·P2 9건을 확인했습니다. HEAD가 검토 대상 커밋과 일치하며 작업 트리는 깨끗했습니다.

아래 ‘직접 재현’은 운영 함수를 메모리 SQLite·가짜 응답·가상 시계로 실행했다는 뜻입니다. 실제 네트워크·운영 DB에서의 실측은 아닙니다.

**확인된 결함 — 외부 정찰 우선**

1. **P2 · 직전 스캔보다 오래된 관측이 놓침 단계를 결정합니다. — 직접 재현**  
   근거: [external_scout.py:248](/home/mjh/paper-harness/external_scout.py:248), [external_scout.py:259](/home/mjh/paper-harness/external_scout.py:259).  
   입력: 같은 논문이 `s1=reserve`, 발견 직전 `s2=dropped/exclude_hit`. 실제 결과는 **`('ranked_out', 's2')`**였습니다. `s2`를 판정 근거로 기록하면서 실제로는 과거 `reserve`를 우선합니다. 제외된 논문이 외부 키워드 추가용 ‘놓친 논문’으로 계산됩니다.  
   기대: 직전 스캔의 `excluded`. 관측을 해당 스캔에 묶고, 과거 검색·배달 이력은 별도 사실로 관리해야 합니다.

2. **P2 · 초기 선정만으로 ‘메일까지 간 것’을 셉니다. — 직접 재현＋호출 경로 확인**  
   근거: [external_scout.py:272](/home/mjh/paper-harness/external_scout.py:272), [external_scout.py:375](/home/mjh/paper-harness/external_scout.py:375).  
   입력: `content` 관측만 있고 발송 기록은 없는 논문. 실제로 `already_captured`, `delivered=1`이 됩니다. 그러나 관측은 [scan_search.py:383](/home/mjh/paper-harness/scan_search.py:383)에서 배달 전에 저장되고, 실제 배달 성공 처리는 [run_profile_scan.py:579](/home/mjh/paper-harness/run_profile_scan.py:579)에 있습니다. 본문 처리 탈락·SMTP 실패도 배달 성공으로 집계할 수 있습니다.  
   기대: 선정과 배달을 구분해야 합니다. 발견 시각 이전의 성공한 발송 기록으로 배달 여부를 판정해야 합니다.

3. **P2 · ‘관심 밖 반응과 충돌 금지’가 브리프 절단 범위에 종속됩니다. — 직접 재현＋코드 확인**  
   근거: [agent_maintenance.py:271](/home/mjh/paper-harness/agent_maintenance.py:271), [agent_maintenance.py:277](/home/mjh/paper-harness/agent_maintenance.py:277), [agent_maintenance.py:473](/home/mjh/paper-harness/agent_maintenance.py:473).  
   입력: 28일보다 오래된 유효 `out` 반응과, 해당 용어를 포함한 놓친 E 근거 두 편. 실제 브리프의 반응은 0건이고, 가중치 0.6 추가가 승인됐습니다. 최근 반응도 상위 40편 밖이거나 용어가 초록 700자 뒤에 있으면 검사에서 빠집니다.  
   기대: 모델 입력 상한 때문에 Python 보호 규칙이 약해지면 안 됩니다. `liked_texts` 보호처럼 유효한 관심 밖 반응의 전체 검증 자료를 별도로 유지해야 합니다.

4. **P2 · 다른 지표의 숫자로 정찰 성능 주장이 검증됩니다. — 직접 재현**  
   근거: [external_scout.py:292](/home/mjh/paper-harness/external_scout.py:292).  
   입력: 자기 행에 `AUROC=80`, `F1=60`; 정찰 주장은 `AUROC=60`. 실제 `performance_verified=True`였습니다. 검증에 `benchmark.metric`을 사용하지 않습니다.  
   기대: 거짓 또는 미확인. 벤치마크·지표·자기 행·단위가 맞는 셀만 대조해야 합니다. 이 플래그는 현재 주간 브리프로 전달됩니다.

5. **P2 · 검증 상한이 뒤쪽 프로필을 통째로 배제하면서 완료로 기록합니다. — 직접 재현**  
   근거: [external_scout.py:416](/home/mjh/paper-harness/external_scout.py:416), [external_scout.py:435](/home/mjh/paper-harness/external_scout.py:435), [external_scout.py:438](/home/mjh/paper-harness/external_scout.py:438).  
   입력: 네 프로필에서 신원이 확인된 논문 각각 5편. 실제 검증·브리프 편수는 **5·5·2·0**, 실행 상태는 **`done`**입니다. 뒤쪽 프로필은 내부 순서 때문에 매주 외부 근거를 받지 못할 수 있습니다.  
   기대: 상한 안에서 프로필별로 배분하고, 검증하지 않은 항목이 있으면 부분 완료로 기록해야 합니다.

**확인된 결함 — 성능 동향·링크 감사 재검토**

6. **P1 · 조건 인용 두 개만으로 전체 조건 일치와 최고 성능 후보를 승인합니다. — 직접 재현**  
   근거: [external_evidence.py:316](/home/mjh/paper-harness/external_evidence.py:316), [research_frontier.py:255](/home/mjh/paper-harness/research_frontier.py:255).  
   입력: 양쪽에 존재하는 `We evaluate on Bench2Drive.`를 `match_evidence`에 두 번 넣고 `same_conditions=true`. 실제 **`same_conditions=True` → ‘관측 범위 내 최고 성능 후보’**였습니다. 버전·분할·프로토콜·학습 조건 확인은 필요하지 않습니다.  
   기대: 조건 미확인. 필수 조건별 근거와 중복 검사를 적용하고, 하나라도 미확인이면 우열 판정을 막아야 합니다.

7. **P1 · 요청 모델의 행이 없으면 변형 모델의 값을 대신 승인합니다. — 직접 재현**  
   근거: [external_evidence.py:265](/home/mjh/paper-harness/external_evidence.py:265), [external_evidence.py:291](/home/mjh/paper-harness/external_evidence.py:291).  
   입력: 표에는 `SimLingo-base=85.07`만 있고 요청은 `SimLingo`. 실제 **`verified`, model=`SimLingo`, locator=`SimLingo-base × DS`**였습니다. 정확 일치가 없을 때 부분 일치 한 행을 허용하기 때문입니다.  
   기대: 신원 불일치로 거부. 검증된 별칭 외에는 모델·변형 이름의 정확한 대응을 요구해야 합니다.

8. **P1 · 시간 상한이 작업을 실제로 중단시키지 못합니다. — 가상 시계 직접 재현**  
   근거: [external_evidence.py:135](/home/mjh/paper-harness/external_evidence.py:135), [external_evidence.py:146](/home/mjh/paper-harness/external_evidence.py:146), [research_frontier.py:147](/home/mjh/paper-harness/research_frontier.py:147).  
   입력 A: `_fetch(timeout=15)`에서 각 응답을 가상 14초씩 지연시키는 리다이렉트와 마지막 빈 응답. 실제 **가상 42초에 정상 반환**했습니다. HTTP에는 매번 원래 timeout을 적용하고, 리다이렉트·빈 본문에서는 마감 검사가 빠집니다. 끝난 뒤 `incomplete`로 바꾸는 것은 지연 상한이 아닙니다.  
   입력 B: 조회 예산 60초 후 외부 조사가 가상 100초에 끝남. 실제 **100초에 S2 batch를 새로 호출한 뒤**, 예산 초과라 결과를 논문에 붙이지 않았습니다.  
   기대: 모든 I/O에 남은 시간을 전달하고 호출 전·후 및 응답 수신 전체를 제한해야 합니다. 외부 조사와 별도라는 조회 예산의 계산도 일관되게 정리해야 합니다. 실제 운영 지연은 미실측입니다.

9. **P2 · 유효 결과가 없어지는 재추출에서 옛 관측값이 남습니다. — 직접 재현**  
   근거: [frontier_store.py:41](/home/mjh/paper-harness/frontier_store.py:41), [research_frontier.py:127](/home/mjh/paper-harness/research_frontier.py:127).  
   입력: 관측 1건 저장 후 `record(..., [])`. 실제 이전 1건이 남습니다. 빈 결과를 모두 다운로드 실패로 취급하여 정상 재추출에서 제거된 값도 계속 비교에 사용합니다.  
   기대: 확보 실패와 정상 추출의 빈 결과를 구분하고, 후자는 최신 유효 관측을 비워야 합니다.

10. **P2 · 비-arXiv 내용 자리 논문은 지원 불가 상태조차 없이 제외됩니다. — 코드 경로 확인, 직접 재현 안 함**  
    근거: [research_frontier.py:117](/home/mjh/paper-harness/research_frontier.py:117), [research_frontier.py:144](/home/mjh/paper-harness/research_frontier.py:144).  
    입력: `arxiv_id='pdf-…'`인 내용 자리 논문. 표 분석과 외부 신호 수집 모두 건너뜁니다.  
    기대: 모든 내용 자리 논문을 다룬다는 계약에 맞게 지원하거나, 최소한 지원 불가·미확인 상태를 명시해야 합니다. 앞선 검토의 해당 지적은 닫히지 않았습니다.

11. **P2 · 신규 논문에서는 HF 조회 실패가 다시 ‘관측 없음’으로 바뀝니다. — 직접 재현**  
    근거: [adoption_signals.py:239](/home/mjh/paper-harness/adoption_signals.py:239), [adoption_signals.py:246](/home/mjh/paper-harness/adoption_signals.py:246).  
    입력: 공개 10일, 인용 0, HF 연결 모델 4개, 모델 조회 `TimeoutError`. 실제 출력은 **‘신규 논문으로 아직 관측 없음(공개 10일)’**뿐입니다. 앞에서 만든 다운로드 조회 실패 문구를 마지막 분기가 지웁니다.  
    기대: 확인된 연결 모델 수와 조회 실패를 유지해야 합니다. 신규 논문 설명은 실패 표시를 대체하면 안 됩니다.

12. **P2 · CSS escape로 외부 URL이 HTML 감사에 남습니다. — 출력 직접 재현, 클라이언트 동작 미실측**  
    근거: [link_policy.py:304](/home/mjh/paper-harness/link_policy.py:304).  
    입력: `style="background-image:u\72l(https://evil.example/x)"`. 실제 속성이 그대로 반환되고 차단 목록은 비어 있습니다. 일반 `url(`만 찾는 정규식이 CSS escape를 처리하지 않습니다.  
    기대: URL을 포함하는 CSS를 제거해야 합니다. 허용된 스타일 속성·값만 보존하거나 CSS 토큰을 해석해야 합니다. 실제 메일 앱의 외부 요청 발생은 확인하지 않았습니다.

**앞선 지적의 종결 여부**

번호는 각 검토 원문의 번호입니다. ‘닫힘’은 해당 재현 경로 기준이며 기능 전체의 무결함을 뜻하지 않습니다.

| 첫 검토 10건 | 판정 |
|---|---|
| 1. 잘못된 URL의 차단 로그가 SMTP 중단 | 닫힘 — 가짜 SMTP 재현 통과 |
| 2. HTML 속성·중첩 태그·CSS 우회 | 부분 — 기존 입력은 차단, CSS escape는 12번 |
| 3. DNS가 감사 시간 예산 밖 | 기존 재현 닫힘 — 제한된 대기와 완료 후 검사 추가 |
| 4. 일시적 DNS 실패 영구 캐시 | 닫힘 — 실패 후 재조회 |
| 5. 붙어 있는 URL·www·DOI 재노출 | 기존 재현 닫힘 |
| 6. 데이터 비율·다른 방법의 수치 승인 | 기존 재현 닫힘 — 문장 경로는 주장 표시로 제한 |
| 7. HF 실패·미관측을 없음·0으로 표시 | 부분 — 저장·합계 개선, 신규 논문 분기는 11번 |
| 8. 당일 캐시가 변경된 원문 무시 | 닫힘 — 원문 분석과 외부 신호 캐시 분리 |
| 9. 카드 인라인 색상·배경 짝 누락 | 지적된 번호·제목·원문 링크는 코드상 닫힘 |
| 10. 상세 내용이 카드 밖이어도 테스트 통과 | 기존 돌연변이를 막는 구조 검사 추가 |

| 성능 동향 검토 13건 | 판정 |
|---|---|
| 1. 조건 미확인 관측 우열 | §198의 사용자 결정에 따라 트리거 표시로 존치 — 동일 지적 반복하지 않음 |
| 2. `same_conditions=true`만으로 승격 | 미종결 — 6번 |
| 3. 다른 논문에 조건 판정 캐시 복사 | 닫힘 — 재사용 시 조건 판정 제거 |
| 4. 모델 행 오귀속 | 부분 — 두 행 충돌은 방지, 단일 변형 행은 7번 |
| 5. 척도 추측·방향 제거 | 부분 — RMSE 오변환·반대 방향은 방지, 비율 지표는 여전히 단위 근거 없이 척도 추정 |
| 6. 임의 GitHub 저장소 승인 | 기존 입력은 닫힘 — 대상 원문에 언급된 저장소만 허용. 언급 자체가 공식성의 완전한 증명은 아님 |
| 7. 180초 hard deadline 부재 | 미종결 — 8번 |
| 8. HTML·외부 신호 조회 예산 누적 | 부분 — 캐시 개선, 조회 마감 문제는 8번 |
| 9. 동점을 상대 우세로 표시 | 닫힘 — `equal` 분리 |
| 10. 관측 키 충돌·사라진 셀 잔존 | 부분 — 저장 키 충돌·비어 있지 않은 재추출은 개선, 빈 재추출은 9번 |
| 11. 캡션 지표 누락·비-arXiv 제외 | 부분 — 캡션 평균 열 개선, 비-arXiv는 10번 |
| 12. fetch 실패를 완료로 캐시 | 닫힘 — `incomplete`, 장기 `done` 캐시 제외 |
| 13. 검증 arXiv 버전과 링크 불일치 | 지정 버전 보존 경로는 닫힘 |

**테스트가 계약을 충분히 지키지 못하는 지점**

- [test_frontier.py:291](/home/mjh/paper-harness/test_frontier.py:291)의 동일 조건 테스트는 **버전·경로 수만** 확인하고 전체 조건 일치를 기대합니다. 필요한 계약보다 약한 성공 조건을 고정합니다.
- [test_frontier.py:371](/home/mjh/paper-harness/test_frontier.py:371)의 조회 예산 테스트는 외부 신호를 즉시 반환하는 가짜로 대체하여, 실제 batch·수집 내부의 마감 초과를 검사하지 않습니다.
- [test_external_scout.py:76](/home/mjh/paper-harness/test_external_scout.py:76)의 단계 테스트는 초기 `content`를 배달 성공으로 기대하고, 여러 과거 스캔의 충돌을 검사하지 않습니다.
- [test_performance_results.py:41](/home/mjh/paper-harness/test_performance_results.py:41)의 `locator.startswith("S3.T1:")`와 다음 줄의 `S3.T2:` 검사는 현재 `S3.T1#0:…` 형식과 맞지 않아 검사 대상을 놓칩니다.

**결함으로 보지 않은 지점과 확인 범위**

- 정찰의 추가 점수·추천 필드는 화이트리스트에서 제거됩니다. E의 검증 텍스트는 공식 제목·초록이고, 정찰의 기여·제조 적용 설명은 전달되지 않습니다.
- E만으로 키워드를 추가할 때 두 근거·가중치 0.7 상한을 검사하며, **E만으로 가중치를 올리는 경로는 거부**합니다. 최종 적용 후보는 기존 Codex 판정과 Python 검증을 거칩니다.
- `--brief-only`, `--no-scout`, 정찰 호출 예외 처리와 일일 메일 측 부가 분석 예외 처리는 존재합니다. 실제 헤드리스 장애·발송은 미실측입니다.
- 새 DDL은 `schema_guard`를 사용하고 `migrate.py`에 등록돼 있습니다.
- 커밋에는 원문 픽스처 `2609.35059.html`과 합성 픽스처만 들어 있습니다. 제외 대상 두 원문은 `.gitignore`에 있고, 합성 Bench2Drive 계약 테스트에는 해당 파일 부재에 따른 skip 조건이 없습니다. CC BY 픽스처에는 제목·저자·출처·라이선스·발췌 표시가 있습니다. 라이선스 원출처를 외부에서 재확인하지는 않았습니다.

**검증 한계**

지정 pytest는 `.env` 로딩·바이트코드·캐시를 차단한 상태로 실행했으나, **`No usable temporary directory`로 수집 전에 실패**했습니다. pytest 실행·통과 테스트는 0개입니다. 별도로 기존 테스트 함수 44개와 가짜 SMTP 회귀 1개를 직접 호출해 assertion 통과를 확인했으며, 위 결함은 추가 메모리 재현으로 확인했습니다.

실제 SMTP, DNS·HTTP, 운영 DB·마이그레이션, 헤드리스 CLI 격리 효과, 메일 앱 렌더링은 검증하지 않았습니다. 파일 생성·수정·커밋, `.env` 읽기는 하지 않았습니다.

