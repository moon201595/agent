# 미커밋 변경 독립 검토 — 2026-09-21

범위: 현재 main의 지정된 코드 3개·테스트 2개 diff. CLAUDE.md·AGENTS.md와 PROGRESS §8-184·185, BAND_ANCHOR_OPTIONS를 먼저 읽었다. 기존 다른 변경은 검토 범위 밖이며 보존한다. 보고서만 작성한다. 네트워크·실제 PDF 수집·.env 열람·운영 DB 쓰기·커밋·push·전체 pytest는 하지 않는다. 명시적인 단일 파일 쓰기 제한 때문에 pytest의 임시 DB·캐시 생성도 피하고, 필요한 재현은 `python -B`의 메모리 안에서 수행한다. 전체/파일 pytest 통과를 주장하지 않는다. `rg` 미설치로 grep과 Python으로 대체했다.

## A. 설계 판단

### A1. 출처별 앵커와 지지 규칙의 해결 범위
판정: **의문** — 작은 변경으로 최대값의 1~2개 극단 관측 영향을 줄이는 선택은 합리적이나, (a)와 동등한 해결책은 아니다.

근거: `profile_scoring.py:302-307`은 날짜 내림차순의 세 번째 값을 고른다. 출처별 독립 앵커가 아니다. `selection.py:34-39,60-66`은 source와 채택 날짜를 함께 바꾸지 않으므로 (a)의 provenance 비용 판단은 맞다. PROGRESS §8-185(:6672-6676)의 비용 판단에는 동의한다. 그러나 “실제 고장”을 하나로 확정할 증거는 부족하다. 최신일에 1~2편이라는 빈도만으로 잘못된 선정 빈도를 알 수 없다.

구체적 합성 시나리오(날짜 의미는 가정): S2 9/21 세 편과 arXiv 9/17 여러 편이 있으면 새 앵커도 9/21이며 arXiv는 band 1이다. (a)는 arXiv 자체 최신 9/17을 band 0으로 두어 이 현상을 막는다. 반대로 한 출처 안의 이상치 1편은 지지 규칙이 완화하지만 (a)의 그룹 최대값은 여전히 흔들린다. 서로 다른 문제를 푸는 대안이다.

### A2. min_support=3의 근거
판정: **의문** — 1~2편을 무시하려는 목적에서 3은 최소 정수지만, 최적값이라는 근거는 없다.

근거: `profile_scoring.py:283,302-307`, PROGRESS §8-185(:6648-6653). 세 번째 순서통계량이지 독립 출처 세 곳의 지지나 같은 날 세 편의 일치가 아니다. 9/21·9/11·9/01 각 한 편이어도 9/01을 택한다. 3편짜리 오염/날짜 의미 차이는 막지 못하고, 진짜 신규 두 편도 꼬리로 처리한다. 최신 신호가 드문 프로필은 1 또는 2가 최신성을 더 지킬 수 있고, 일괄 오류가 세 편 이상이면 더 큰 값이 오염에 강할 수 있다(조건부 설계 추론, 성능 미실측). 값별 민감도·사용자 판단 자료 없이 3이 낫다고 확정하지 않는다.

### A3. 오래된 논문의 band 0 승격·부분 실패
판정: **의문** — 출처 그룹별 승격은 없지만 전체 후보 표본을 기준으로 같은 종류의 승격이 생긴다.

근거: `rank_key`의 `max(0, anchor-day)//3` (`profile_scoring.py:341-345`) 때문에 앵커보다 최신인 모든 날짜와 앵커보다 이틀 전까지가 band 0이다. 따라서 band 0의 실제 폭은 최신일−세 번째 최신일+2일까지 늘어난다. 날짜 세 개뿐이면 모두 band 0이며 날짜 간격 상한이 없다. 후보가 적거나 검색 일부가 실패해 최신 지지 후보가 빠지면 앵커가 과거로 이동한다. 검색 창은 위험을 제한하지만 표본 의존 역전을 없애지 않는다. 같은 계층에서는 오래된 2개념이 최신 1개념을 이길 수 있다. 검색 누락 자체를 복구하지도 않는다. 이는 (a)의 오래된 그룹 최신 문제를 완전히 피한 설계가 아니다.

### A4. 3편 경계와 폴백
판정: **결함 (P2, 설계 경계)** — 날짜 유효 후보 2→3편에서 무관한 하위 계층 후보 하나가 기존 상위 두 편의 순서를 크게 뒤집는다.

실행: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'`에서 실제 `score_and_rank`, `band_anchor`, `rank_key` 호출. 프로필 core=`target term`, `trend term`(각 1.0), `low term`(0.1). 입력 new=9/21 target term, old=9/11 target term+trend term. 결과 `anchor=9/21; new (tier0,band0), old (tier0,band3)`. low=9/10 low term을 추가하면 `anchor=9/10; old (tier0,band0), new (tier0,band0), low (tier1,band0)`로 바뀐다. low의 published를 연도 `2026`으로 바꾸면 다시 new→old다.

근거: `profile_scoring.py:305-307,345,347-350`. 폴백 설명은 “적격 후보 수”지만 실제 경계는 날짜 유효 후보 수다. 적격 세 편 중 하나의 날짜 보강만으로도 역전한다. 정확히 세 편에서는 최솟값을 앵커로 써 전 후보의 날짜 띠를 없애는 효과가 난다. 소표본에서 규칙을 끄는 의도는 이해하지만, 이 불연속과 최대 시간 간격 무제한까지 승인한 근거는 §8-185에 없다. 수정 정책은 사용자 선택이지만 이 경계를 회귀 테스트와 계약에 반드시 드러내야 한다.

### A5. 본문 문지기의 우회 부활인가
판정: **문제없음** — 현재 코드에 대해 “본문 문지기를 되살렸다”는 반박은 성립하지 않는다.

근거: PROGRESS §8-86(:4438-4454), `profile_scoring.py:591-608,347-352`, `scan_search.py:297-304`. 제외/핵심 적중으로 자격을 정한 뒤 날짜·계층·개념으로 한 번 정렬하고 내용/reserve를 자른다. 새 앵커는 source·PDF 링크·본문 확보 결과를 읽지 않으며 계층도 뒤집지 않는다. OA 수집은 그 이후다. 본문 회수율을 보며 3을 선택했다면 정책 동기는 의심할 수 있지만 그것은 추측이며, 지금 코드에서 출처나 수급 가능성을 대리 변수로 쓰는 문지기를 발견한 것은 아니다. A4는 별도의 최신성·표본 안정성 결함이다.

## B. 구현 경로

### B1. 날짜 결측·연도 정밀도
판정: **문제없음** — published가 결측/year/invalid이면 앵커에서 빠진다.

근거: `publication_day` (`profile_scoring.py:228-244`)는 유효 일자만 ordinal로 만들고 `band_anchor:302`는 None을 제외한다. 위 메모리 실행에서 `[None,"2026","bad"]`의 앵커는 None이었다. 결측 논문은 `rank_key:342-343`에서 큰 띠로 간다. 별도 date_precision 필드가 year인데 published에 인위적 일자가 있는 경우까지 신뢰도를 판정하지는 않는다. 그 필드는 입력 판단에 사용하지 않는 기존 계약이다. band_anchor 자체는 적격 판정·중복 제거를 하지 않는다. 현재 호출부는 적격 필터 뒤이고 일일 경로는 dedupe 뒤이므로 새 운영 결함은 아니다.

### B2. 다른 앵커 소비자·top_k·reserve
판정: **문제없음** — 운영 정렬 앵커 계산은 score_and_rank 한 곳이다. 다만 A4의 표본 의존성은 공통으로 전파된다.

근거: 루트 Python 전체에서 `newest_day|band_anchor|rank_key(` 검색. rank_key 호출은 `profile_scoring.py:608`, 앵커 계산은 :607. top_k는 :609-610에서 정렬 후 적용한다. `scan_search.py:297-304`는 자르기 전 전체 적격 후보를 정렬하여 내용·목록·reserve로 분리한다. reserve만 따로 앵커를 계산하지 않는다. `run_profile_scan.py:257-277`은 처리 결과에 따라 실패분을 내린다. profile_impact/evaluation 등도 score_and_rank를 통해 새 정책을 사용한다. top_k를 작게 준다는 이유로 지지 표본이 줄지는 않는다.

### B3. OA 수집 분기·중복·예외
판정: **결함 (P3, 진단 문구)** — 동일한 실패 URL을 반환한 경우를 “오픈액세스 사본 없음”으로 잘못 단정한다. 수집 분기 누락은 발견하지 않았다.

근거: `batch_summarize.py:173-204,224-234`, `server.py:1208-1227`. 실제 함수 `_fetch_open_access` AST를 메모리에서 그대로 compile하고 server를 SimpleNamespace 가짜로 대체하여 호출했다. 실제 server import·실제 fetch_pdf_from_url 호출은 없다. 명령은 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'`.

|입력/가짜 결과|호출 순서|결과|
|---|---|---|
|원 URL 성공|fetch(u)|본문 dict, 이유 빈 문자열|
|원 링크 없음, DOI, 설정, 대체 v|resolve(d) → fetch(v)|본문 dict|
|원 u 실패, 대체 v|fetch(u) → resolve(d) → fetch(v)|본문 dict|
|원 u 실패, 대체도 u|fetch(u) → resolve(d)|재시도 없음, “사본 없음”|
|DOI 없음|없음|원 이유 유지|
|설정 없음|없음|UNPAYWALL_EMAIL 미설정|
|조회 예외|resolve(d)|조회 실패(ValueError)|
|대체 수집 예외|resolve(d) → fetch(v)|사본도 수집 실패(ValueError)|
|대체 없음|resolve(d)|사본 없음|

동일 URL 문자열은 :195의 tried 검사로 두 번 받지 않는다. URL 별칭·다른 쿼리·리다이렉트의 같은 목적지까지 중복을 막는 것은 아니다(새로 도입된 회귀는 아님, 실제 중복 전송 미실측). :195-196은 “사본 URL이 있지만 이미 실패함”과 “URL 없음”을 합친다. :202의 실패 표기처럼 가용성 미확인과 부재를 구별해야 로그를 사실 근거로 쓸 수 있다. resolver는 best_oa_location.url_for_pdf 하나만 보는 기존 구현이라 모든 OA 사본의 부재를 증명하지도 않는다.

helper의 catch는 예외 종류를 why에 남겨 조용히 버리지 않는다. `_abstract_only_outcome:83-87`이 로그/detail로 전달한다. 단 :74의 OpenAlex 호출·:77의 요약이 예상 밖 예외를 내면 이후 로그에 못 도달하는 기존 경로는 남는다. server의 OpenAlex resolver는 :1191-1193에서 예외를 빈 문자열로 낮춘다(기존 동작). 이번 변경으로 외부 장애가 모두 구체적으로 설명된다고까지 말할 수 없다.

### B4. detail 소비자
판정: **문제없음** — 지정 소비자에서 바뀐 문장을 계약으로 파싱하는 경로는 없다.

근거: `grep -nE 'detail|abstract_only|fetch_failed' digest.py run_profile_scan.py ops_dashboard.py scan_deliver.py` 후 해당 분기 확인. `run_profile_scan.py:258-274`는 status로 분기하며 실패 detail은 failed: 접두에 붙이고 200자로 자른다. abstract_only의 detail은 paper에 보존하지 않고 brief만 보존하므로 이유는 로그에서 확인해야 한다. `digest.py:785,1797,1857-1865`는 deep_status/brief 또는 failed 접두로 표시한다. digest의 fail_detail 매핑(:516-518)은 Docker 재현 결과용으로 본 문자열과 다르다. `ops_dashboard.py:383-423`의 로그 집계는 실행 경계·경고·API·최종 JSON을 읽고, :597-605는 일반 오류 축약이다. “본문 비공개/초록도 없음” 정확 문자열에 의존하는 파서는 발견하지 않았다. 이유가 붙어도 렌더링 경로가 바뀌지 않는다.

### B5. 정책 버전·재생·서명·관측
판정: **결함 (P2)** — 과거 replay가 anchor3 결과에 과거 정책 버전을 붙인다. 기존 잠재 결함이 이번 정책 전환에도 그대로 드러난다.

근거: `evaluation.py:158-167`은 과거 profile_snapshot/policy_version을 읽지만 현재 `profile_scoring.score_and_rank`를 호출한 뒤 과거 row[1]을 반환한다. 정책별 정렬 분기/거부가 없다. 현재 재채점용 rescore_current(:172-180)와 구분한다는 계약을 지키지 못한다.

실행: replay 함수 AST를 수정 없이 추출해 메모리 fake 연결·fake snapshot으로 호출했다. 입력은 target term 9/21 new, target term 9/18 body, target term+trend term 9/18 wide. snapshot은 이 세 편, 과거 버전은 `rank-tuple-v2+match-v2+band0.1+dateband3`. 출력은 `status=replayed, policy_version=…dateband3, topk=[wide,new,body]`. 같은 입력을 실제 rank_key에 기존 최대 날짜 앵커로 정렬하면 `[new,wide,body]`. DB/network 없는 격리 재현이다. 과거 정책을 지원하거나 정책 불일치를 거부/현재 재채점이라고 표시해야 한다.

다른 소비자는 정적 확인상 정상이다. `research_profile.py:1010-1013,1078`은 스캔·관측에 새 상수를 저장한다. `profile_impact.py:521-527`의 입력 해시에 정책이 포함되어 옛 분석 캐시를 재사용하지 않고, :457의 shadow 정책 비교도 불일치를 거부한다. `shadow_search.py:219,229`는 현재 버전을 기록한다. `profile_health.py:378-381`는 정책 혼합 창을 MIXED_MODES로 돌려준다(전환 직후 비교가 제한되는 것은 의도된 보호). 새 접미사를 파싱해 깨지는 소비자는 발견하지 않았다. `rank_key` docstring(:336)은 아직 “가장 최신 공개일”이라 문서 정합성 수정이 필요하다.

## C. 테스트가 지키는 것

방법: 테스트 파일 전체 import는 run_profile_scan/server/config와 임시 저장소를 열 수 있으므로 하지 않았다. `python -B`에서 해당 테스트 함수와 필요한 helper/PROFILE의 AST를 그대로 compile하여 실제 profile_scoring 또는 B3의 실제 helper AST+가짜 server에 연결했다. 새 네 함수의 assert는 모두 통과했다. pytest/fixture 통합 실행이 아니며 파일 단위 suite 통과 수치가 아니다. 사용자 제공 두 변이 실측(최대 날짜 회귀, except 내부로 폴백 회귀)은 재측정하지 않았다.

### C1. 외딴 한 편 앵커 테스트
판정: **문제없음 (한정된 감시)** — 실제 score_and_rank의 결과를 검사한다.

근거: `test_rank_contract.py:248-265`, `_order:29-30`. 최신 1개념 한 편과 3일 전 1개념 네 편·2개념 한 편을 넣어 double→tip을 요구한다. 앵커를 최대값으로 되돌리면 실패한다(사용자 제공 실측). 날짜를 개념 폭보다 먼저 비교하거나 폭을 무시해도 double 1위가 깨진다(정적 분석). 그러나 오래된 다섯 편이 모두 같은 날짜라 “셋째”를 정확히 고른다는 것은 못 지킨다.

### C2. 소표본 폴백 테스트
판정: **문제없음 (경계 감시 부족)**.

근거: `test_rank_contract.py:268-276`. 두 편이면 최신 1개념이 3일 전 2개념보다 앞이고, 빈 입력 앵커는 None이어야 한다. 무조건 세 번째 인덱스를 읽거나, 둘의 최소값을 쓰거나, 빈 입력에서 IndexError를 내면 잡는다. 하나·정확히 셋·날짜 유효 수와 전체 후보 수가 다른 경우는 없다.

추가 메모리 변이 실측: band_anchor를 `original(xs,2)`로 대체하면 C1 PASS/C2 AssertionError였다. 즉 최소 지지를 2로 단순 변경하는 것은 둘을 합쳐 감시한다. 반면 `len(xs)>=3이면 최소 날짜, 아니면 기존 함수`로 바꿔도 **두 테스트 모두 PASS**였다. 따라서 “세 번째 순서통계량”을 버리고 모든 과거 후보를 band 0으로 끌어올리는 잘못된 구현이 이 두 테스트를 통과한다. 소스 파일에는 어떤 변이도 쓰지 않았다.

### C3. 링크 없음 Unpaywall 테스트
판정: **문제없음 (helper 단위만)**.

근거: `test_process_paper.py:291-313`. 실제 helper를 부르고 DOI 조회가 정확히 한 번, 대체 URL의 fake 수집 결과가 반환되고 이유는 빈 문자열임을 요구한다. 조회 제거/DOI 잘못 전달/빈 결과 반환/엉뚱한 URL 수집을 잡는다. 폴백을 except 안으로 옮기면 실패한다는 것은 사용자 제공 실측이다. 그러나 `_process_paper:224-231`에 다시 “링크 없으면 helper 호출 전 조기 반환”을 넣어도 이 테스트는 직접 helper만 부르므로 통과할 수 있다. 통합 배선의 회귀 감시가 빠졌다.

### C4. 미설정 사유 테스트
판정: **문제없음 (반환값만)**.

근거: `test_process_paper.py:316-333`. 설정이 빈 문자열이면 fetched=None, why에 UNPAYWALL_EMAIL, resolver 0회다. 가드 제거·미설정 사유 누락·잘못된 성공 반환을 잡는다. 그러나 `_abstract_only_outcome`에서 why를 다시 버리거나 print를 지워도 이 테스트에는 영향이 없다. “왜 초록만인지 실제 로그/detail에 남긴다”는 이번 변경은 새 테스트들이 직접 감시하지 않는다.

### C5. 빠진 감시
판정: **의문** — 정책 핵심 경계와 실제 호출 연결을 추가로 고정해야 한다.

최소 보강 대상은 (1) 날짜가 서로 다른 4편 이상으로 정확히 세 번째를 선택하고 더 오래된 후보 추가에 앵커가 흔들리지 않는지, (2) A4의 2→3 경계·year/결측·낮은 계층 지지 정책, (3) `_process_paper`를 통해 링크 없음→Unpaywall 성공→후속 처리, (4) 원 URL 실패→대체 성공, 동일 URL 중복 방지, 조회/수집 예외 이유, (5) abstract_only 성공의 detail와 로그에 원 why 보존, (6) 과거 정책 replay 오표기를 감시하는 것이다. 기존 `test_process_paper.py:185-237`은 대체 resolver가 None인 초록 보강 경로이지 대체 성공/동일 URL 회귀 테스트가 아니다. B3에서 현재 분기 동작은 직접 확인했지만 그것이 저장소의 지속적 회귀 감시를 대신하지는 않는다.

### C6. 기존 두 테스트 수정의 정당성
판정: **문제없음** — 실패를 숨기려 기대 순위를 바꾼 경우와 다르다. 다만 부수 주장에 약한 assert가 있다.

`test_no_link_no_abstract_anywhere_is_still_an_honest_failure:276-288`은 fetch_failed를 유지하며 변경된 진단 계약(링크 없음+Unpaywall)에 맞춰 문구 assert를 바꿨다. B4에서 옛 문구 파서도 없음을 확인했다. “초록도 없음”이라는 정보가 detail에서 사라진 것은 진단 세밀함 감소지만 실패를 성공으로 바꾸거나 없는 brief를 허용하도록 바꾼 것은 아니다. `"Unpaywall"` 포함만으로는 미설정과 조회 실패를 구별하지 못하므로 가드 진단은 C4와 함께 봐야 한다.

`test_paper_without_pdf_link_still_reaches_the_abstract_brief:248-273`의 UNPAYWALL_EMAIL=""는 초록 경로를 환경과 무관하게 고정하는 타당한 fixture 변경이다. 설정이 켜지면 새 정책상 대체 PDF 시도는 정상이다. 그러나 boom의 AssertionError는 helper의 `except Exception`(:180,201)에 잡힌다. 이 fake만으로 “수집 호출 0회”를 증명하지는 못한다. fake 호출 횟수를 별도로 assert해야 그 주장이 성립한다(기존부터 있던 감시 약점). 이번 수정 자체를 AGENTS의 “테스트를 고쳐서 통과시킨 것”으로 판정할 근거는 없다.

## D. 기록 수치 검증

DB 접근은 매번 `sqlite3.connect('file:/home/mjh/paper-harness/data/papers.db?mode=ro', uri=True)`와 `PRAGMA query_only=ON`으로 제한했다. 집계는 읽기 트랜잭션에서 수행했다. 프로그램은 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'`로 실행하고 저장소 DB helper는 호출하지 않았다. 적격 집합은 당시 `candidate_observations.rank_pos IS NOT NULL`이다(`scan_search.py:373-381`). S2는 검색 출처이고 저널 확정 분류와는 다르며, DOI 키 여부도 함께 확인한다.

### D1. 기록된 38스캔 중 19건
판정: **문제없음 (수치 확인, 해석 한정)**.

실행 SQL: `SELECT COUNT(*),SUM(observations IS NOT NULL),MIN(started_at),MAX(started_at) FROM scan_runs` → **41,38,2026-09-11T04:44:53+00:00,2026-09-20T21:10:09.804496+00:00**. `SELECT * FROM candidate_observations WHERE rank_pos IS NOT NULL`을 scan_id별로 묶어 publication_day의 최대 날짜와 그 날짜 후보 수를 계산했다. 적격 관측이 있는 스캔 **38**, 최대 날짜에 **1~2편인 스캔 19**. §8-185(:6651)의 숫자는 맞다.

이것은 “최신 날짜의 지지 수가 1~2”라는 빈도다. **19회 선정이 잘못됐거나 모두 고쳐졌다는 수치가 아니다.** 셋째 날짜가 하루 전이어도 내용 순위는 안 바뀔 수 있다. 정책 버전도 섞인 과거 관측이며 각 당시 정책에서 같은 피해가 발생했다는 재생 증거는 별도다. :6675-6676의 “절반의 스캔에서 이미 어긋나 있던 것을 바로잡는다”는 해석은 이 집계만으로 뒷받침되지 않는다.

### D2. 9/21 team_ai_advance 날짜 분포
판정: **문제없음**.

`scan_runs.started_at >= '2026-09-20T15:00:00'`(9/21 KST)의 `team_ai_advance`, scan_id=`5190e35581e2`, 적격 **463편**. published의 일자를 Counter로 집계한 최신 구간은 **9/20 1, 9/19 1, 9/18 4, 9/17 68, 9/16 123**. §8-185(:6649)와 정확히 일치한다. 전체 검색 후보 1,287편과 혼동하지 않는다.

### D3. defect detection arXiv 0·저널 11~14
판정: **문제없음 (해당 두 프로필·당시 관측에 한정)**.

실행: 위 날짜 네 스캔의 적격 행에서 `"defect detection" in json.loads(core_hits or '[]')`를 세고 source/DOI 키를 교차 확인했다.

|프로필 / scan_id|arxiv 출처 적중|s2 출처 적중|그중 doi: 키|
|---|---:|---:|---:|
|team_ai_advance / 5190e35581e2|0|11|11|
|team_vision / 15f77124620d|0|14|14|
|team_agent / 586fbcd11121|0|0|0|
|team_robot / 63049db6c9b5|0|0|0|

§8-185(:6664)의 11~14는 ai_advance와 vision에 대해 맞다. S2에서 얻은 DOI 논문을 여기서 저널이라고 부른 것까지는 재현되지만, **arXiv 전체에 이 분야 논문이 없다는 “분야의 사실”(:6666)을 증명하지 않는다.** 검색 쿼리·기간·기배달 제거·프로필 자격을 통과한 이 스캔 후보의 사실이다. 학술 출판 형태는 이 관측 필드만으로 확정하지 않는다.

### D4. 새 앵커 재생
판정: **의문** — “초기 상위 5편의 집합 변경”이라는 뜻이면 robot만이라는 수치가 맞다. “순서도 포함”하거나 “실제 발송 결과”라면 구분이 필요하다.

실행은 당시 scan_runs.profile_snapshot과 해당 scan_id의 rank_pos가 있는 **전체 후보**를 사용했다. abstract_ref가 있으면 같은 profile_id/paper_key와 참조 scan_id의 abstract를 복원했고, SHA256 앞 16자리로 abstract_sha와 대조했다. `_key`에 원 paper_key를 보존하고 doi: 키는 doi로, 나머지는 arxiv_id로 복원했다. 현재 실제 score_and_rank를 호출한 후, 같은 결과를 실제 rank_key에 **최대 날짜 앵커**를 넘겨 정렬한 것을 옛 정책 결과로 비교했다. 네 스캔 모두 초록 해시 불일치 **0**, core_hits 재계산 불일치 **0**, 기존 rank_pos 전체 순서 재현 **True**였다. 손상/현재 프로필 변경을 새 앵커 효과로 오인하지 않았다.

|프로필|적격 수|옛→새 앵커|초기 top5 arxiv 출처|집합 변경|순서 변경|
|---|---:|---|---|---|---|
|team_agent|563|9/19→9/18|0→0|없음|없음|
|team_ai_advance|463|9/20→9/18|0→0|없음|없음|
|team_robot|322|9/19→9/18|4→5|있음|있음|
|team_vision|73|9/20→9/18|0→0|없음|있음|

robot에서 빠진 5위는 `doi:10.1145/3828158.3834805`, 들어온 5위는 `2609.19200`이다. 앞 네 편 `2609.19579,2609.20107,2609.20747,2609.20756`은 그대로다. vision은 옛 `[A,B,C,D,E]`에서 새 `[A,D,B,C,E]`로 바뀐다. A=`doi:10.1088/1361-6501/aea9b8`, B=`doi:10.1007/s11554-026-01983-0`, C=`doi:10.1117/1.jei.35.5.053009`, D=`doi:10.3390/automation7050145`, E=`doi:10.1007/s00170-026-19024-2`.

**초기 선정과 발송은 다르다.** `mail_issues`와 `mail_issue_items`를 issue_id로 join해 9/21 KST 발송 position 순서와 당시 rank_pos 1~5를 대조했다. agent/robot은 동일하지만 ai_advance/vision은 다르다. ai_advance의 실제 5위는 초기 `doi:10.1007/s00202-026-03759-y` 대신 `doi:10.1117/12.3123090`이다. vision의 실제 메일은 `[A,C,D,E,doi:10.1038/s41598-026-70180-7]`이다. 따라서 이 재생은 순위 정책의 반사실 비교이며 새 정책으로 끝까지 처리·발송했을 결과는 **미실측**이다. 원인은 `scan_search.py:358-359`의 설명뿐 아니라 실제 `run_profile_scan.py:257-284`의 실패/내용 분리 경로로 확인된다. 개별 논문 교체 사유까지 이 DB 대조만으로 확정하지 않는다.

재생 핵심 실행식:

```python
profile = json.loads(scan['profile_snapshot'])
rows = con.execute('SELECT * FROM candidate_observations WHERE scan_id=? '
                   'AND rank_pos IS NOT NULL ORDER BY rank_pos', (scan['scan_id'],)).fetchall()
# 각 행의 abstract_ref를 동일 profile_id/paper_key의 참조 행으로 복원한다.
# sha256(abstract.encode()).hexdigest()[:16] == abstract_sha 확인 후 papers 생성.
new = profile_scoring.score_and_rank(papers, profile)['papers']
old_anchor = max(profile_scoring.publication_day(p['published'])[0] for p in new)
old = sorted(new, key=lambda p: profile_scoring.rank_key(p, p['_score'], profile, old_anchor))
assert [r['paper_key'] for r in rows] == [p['_key'] for p in old]
# k=profile['max_items']=5. 집합과 순서, source='arxiv' 편수를 각각 비교.
```

근거: §8-185(:6662-6667), 위 scan_id 네 개의 관측과 snapshot. 새 앵커에 의한 효과가 “메인 원인”이 아니었다는 정정은 이 제한된 재생 결과에 부합한다.

### D5. 일주일 저널 고유 50편 중 본문 가능 3편
판정: **의문 — 50은 확인, 3은 미검증/미실측**.

9/15~9/21 KST(양끝 포함), 실제 recipients_sent>0인 mail_issues에 한정하여 아래 SQL을 실행했다.

```sql
SELECT COUNT(DISTINCT i.paper_key), COUNT(*)
FROM mail_issue_items i JOIN mail_issues m USING(issue_id)
WHERE m.sent_at >= '2026-09-14T15:00:00'
  AND m.sent_at < '2026-09-21T15:00:00'
  AND m.recipients_sent > 0 AND i.paper_key LIKE 'doi:%';
```

출력은 **50,65**(고유 DOI 키 50·메일 항목 65). 9/14 KST부터 잡아도 동일하다. 전체 원장 범위는 `2026-09-15T20:08:23.843762+00:00`~`2026-09-20T21:15:56.970898+00:00`, 28회 전부 status=sent다. 따라서 이 범위 이전 메일을 포함한 완전한 일주일 이력을 주장하지 않는다. 50은 **현재 발송 원장에 보존된 DOI 논문 수**다.

“실제로 본문 확보 가능한 3편”은 DOI/링크 존재나 저장된 과거 성공만으로 현재 수집 가능성을 검증할 수 없다. 외부 요청·PDF 수집 금지에 따라 실행하지 않았다. 또한 이번에 읽은 §8-185(:6646-6684)에는 **50/3이라는 문장 자체가 없다**. :6682-6683에는 오늘 저널 13편·직링크 2편·그 둘 수집 실패라는 다른 기록이 있다. 요청에 주어진 50/3 주장을 별도로 검토한 것이며 문서 위치를 혼동하지 않는다.

## 고쳐야 할 것

1. **P2 — 3편 경계의 최신성 급변을 해결하거나 명시적으로 계약화해야 한다.** `profile_scoring.py:305-307,345`에서 두 후보의 순위를 하위 계층의 오래된 셋째 후보가 뒤집는다(A4 실측). 정확히 세 편이면 날짜 간격과 무관하게 전부 band 0이다. 날짜 간격/표본 부족을 다루는 정책을 정하고 2→3, 날짜 보강, 서로 다른 4개 이상 날짜의 테스트를 추가해야 한다. 이 검토가 임의의 새 임계값을 정답으로 제안하지는 않는다.
2. **P2 — 과거 정책 replay의 결과 오표기를 막아야 한다.** `evaluation.py:166-167`에서 현재 anchor3 계산에 옛 정책 라벨을 붙인다(B5 재현). 과거 정책별 실행 또는 불일치 거부/현재 재채점 표기를 적용하고 과거 스냅샷 테스트를 둬야 한다. 미변경 파일에 있는 기존 잠재 결함이지만 이번 정책 변경의 소비자 검토에서 확인한 실제 영향이다.
3. **P2 검증 공백 — 정확한 셋째 날짜와 통합 OA 배선을 테스트해야 한다.** 새 순위 테스트 둘은 “3편 이상이면 최솟값” 변이도 통과한다(C2 실측). helper 테스트는 process_paper의 조기 반환 재도입을 못 잡는다(C3 정적 분석). 로그/detail의 why 보존도 실제 성공 반환까지 확인해야 한다.
4. **P3 — OA 부재와 동일 실패 URL을 구분해야 한다.** `batch_summarize.py:195-196`의 “사본 없음”은 반환 URL이 존재하는 경우 거짓이다. “이미 실패한 동일 URL”과 “대체 PDF URL 미확보”를 분리해야 한다. 실제 OA 전체의 부재를 단정하지 않는다.
5. **P3 — 문서 주장 범위를 줄여야 한다.** §8-185의 19/38은 선정 오류 19건이 아니고, defect detection arXiv 0은 해당 검색 후보의 관측이지 분야 전체의 사실이 아니다. robot만 바뀐다는 말은 초기 top5 **집합** 기준으로 한정해야 하며 vision의 순서 변화와 최종 메일 차이를 분리해야 한다. `rank_key:336`의 최대 날짜 설명도 새 앵커 계약과 맞춰야 한다. :6684의 UNPAYWALL_EMAIL 미설정은 사용자 제공 현재 상태와 달라 이미 과거 기록이다(설정 값은 읽지 않았다).

**설계 종합 판단:** 출처별 앵커를 보류하고 작은 변경을 선택한 판단 자체가 틀렸다고 결론 내리지는 않는다. 본문 문지기를 부활시켰다는 주장도 코드 근거가 없다. 다만 “소표본에서도 안전한 최신성 완화”라고 받아들이는 것은 A4 반례 때문에 틀렸다. 현재 지지 규칙은 날짜 의미 혼합을 해결하지 않고, 절반의 스캔을 바로잡았다는 효과 주장도 입증되지 않는다. 이 한계를 인정하고 경계 정책을 다듬는 조건부 선택이다.

검토 종료: A→B→C→D 순으로 절별 저장했다. 실제 네트워크/모델/PDF/발송은 실행하지 않았고 운영 DB에는 mode=ro 연결만 사용했다. AST 격리 실행과 DB 집계 결과만 실측으로 보고했다. pytest 전체·파일 suite는 미실행이며 사용자 제공 1,194 통과를 이번 검토 실측으로 재기재하지 않는다. 코드·테스트·DB·설정·PROGRESS를 수정하지 않았고 결과물은 이 문서 하나다.
