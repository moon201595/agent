# Codex 영향 조사 — "최종 5편 = 본문 확보 논문만" 안 (2026-10-01)

잡 `task-muouhnb1-miigdl`(읽기 전용, 9분 54초). 사용자 결정 대기 중인 안의 영향 지도다. 요약·판단은 PROGRESS §200.

# 1. 요약

**`abstract_only`를 새 리스트로 옮기는 것만으로는 안전하지 않습니다.** 발송 목록·중복 방지·피드백·서술 입력을 함께 바꿔야 합니다. 또한 이는 단순한 표시 변경이 아니라, **관련도가 높은 초록 논문보다 관련도가 낮은 본문 논문을 먼저 배치하는 정책 변경**입니다. 현재 검색 단계는 관련도 순서를 그대로 자르고, Deep Layer는 `abstract_only`도 콘텐츠에 남깁니다. ([scan_search.py:297](/home/mjh/paper-harness/scan_search.py:297), [run_profile_scan.py:265](/home/mjh/paper-harness/run_profile_scan.py:265))

주요 위험은 다음 다섯 가지입니다.

1. **발송 기록 누락:** ledger가 현재 `papers`만 받으므로 signal 논문은 메일에 표시해도 기록되지 않습니다. 외부 정찰은 이를 `not_delivered` 또는 `ranked_out`으로 판정할 수 있습니다. ([scan_deliver.py:119](/home/mjh/paper-harness/scan_deliver.py:119), [external_scout.py:264](/home/mjh/paper-harness/external_scout.py:264))
2. **다음 실행 재노출:** 전체 실행과 단일 프로필 실행 모두 `papers`만 `profile_shown`에 기록합니다. signal을 빠뜨리면 다음 검색에서 다시 후보가 됩니다. ([run_profile_scan.py:579](/home/mjh/paper-harness/run_profile_scan.py:579), [run_profile_scan.py:689](/home/mjh/paper-harness/run_profile_scan.py:689), [scan_search.py:258](/home/mjh/paper-harness/scan_search.py:258))
3. **반응·학습 누락:** 버튼 토큰과 수신자별 버튼 주입도 `papers`만 대상입니다. signal의 반응 기회를 없애면 주간 관심 조정 입력도 달라집니다. ([scan_deliver.py:97](/home/mjh/paper-harness/scan_deliver.py:97), [agent_maintenance.py:271](/home/mjh/paper-harness/agent_maintenance.py:271))
4. **signal만 있는 날의 빈 메일·동향 누락:** `papers`가 비면 동향 생성 블록이 실행되지 않으며, 렌더러는 signal을 모르는 상태라 “새로 걸린 논문이 없습니다”라고 표시할 수 있습니다. ([run_profile_scan.py:366](/home/mjh/paper-harness/run_profile_scan.py:366), [digest.py:1707](/home/mjh/paper-harness/digest.py:1707), [digest.py:2504](/home/mjh/paper-harness/digest.py:2504))
5. **처리량·관측 의미 변화:** 현재 reserve 처리 깊이 상한은 없고 시간 검사는 편별 처리 시작 전에만 합니다. 더 깊이 처리하면 초기 선정 관측과 최종 본문·signal 목록 사이의 차이도 커집니다. 실제 추가 시간·API 사용량은 **미실측**입니다. ([run_profile_scan.py:190](/home/mjh/paper-harness/run_profile_scan.py:190), [run_profile_scan.py:217](/home/mjh/paper-harness/run_profile_scan.py:217), [scan_search.py:319](/home/mjh/paper-harness/scan_search.py:319))

AGENTS.md 전체와 CLAUDE.md를 읽었습니다. 파일 수정·저장, DB 접근, 메일 발송, commit/push는 하지 않았습니다. 아래는 정적 코드 조사와 마지막 절의 **메모리 내 소스 추출 재현** 결과입니다. pytest 및 실제 운영 성능은 **미실측**입니다.

# 2. 소비 지점별 영향 지도

## `result["papers"]`, `reserve`, `title_only_papers`, `title_only_count`

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [scan_search.py:297](/home/mjh/paper-harness/scan_search.py:297) | 한 번 정렬한 결과를 상위 `profile["max_items"]`와 나머지 reserve로 나눔. `title_only_papers`는 reserve 앞부분 | **검색 순위는 변경 불필요.** signal 분류는 실제 처리 결과를 받은 뒤 수행 | 링크 유무 등을 검색 게이트로 쓰면 현재 검색 순위 계약까지 위반 |
| [run_profile_scan.py:189](/home/mjh/paper-harness/run_profile_scan.py:189) | 목표 편수는 프로필을 다시 읽지 않고 **초기 `len(result["papers"])`**로 정함. 순회 대상은 `papers + reserve` | 본문 콘텐츠·signal·실패·미시도를 분리. reserve 처리 깊이와 종료 사유를 명시 | `TITLE_ONLY_MAX_ITEMS`를 처리 상한으로 오인하거나, signal까지 본문 자리에 셈 |
| [run_profile_scan.py:201](/home/mjh/paper-harness/run_profile_scan.py:201) | 콘텐츠 자리가 차면 나머지는 처리하지 않고 모두 `demoted`에 추가 | 자리 충족 후 미시도와 실제 실패를 분리 | 처리하지 않은 논문을 실패로 집계 |
| [run_profile_scan.py:258](/home/mjh/paper-harness/run_profile_scan.py:258) | `done`은 `deep_status="ok"` 또는 `"skipped: 이미 요약 저장됨"`으로 변환 | 성공 판정은 **outcome의 `status=="done"`** 기준 유지 | `deep_status=="done"`으로 검사하면 성공 논문이 하나도 남지 않음 |
| [run_profile_scan.py:265](/home/mjh/paper-harness/run_profile_scan.py:265) | `abstract_only`도 마지막의 `content.append`로 들어감 | 이 분기에서 signal에 넣고 다음 후보로 진행 | 옮긴 뒤 공통 append를 그대로 두면 두 목록에 중복 |
| [run_profile_scan.py:285](/home/mjh/paper-harness/run_profile_scan.py:285) | `demoted + 기존 title_only`에서 demoted/content 중복을 제외하고 8편으로 자름 | **signal도 제외**, 논문 키 기준으로 중복 제거. deferred·깊이 상한 미시도의 취급도 명시 | reserve에서 처리한 signal이 기존 title-only 목록에도 남아 서술 입력·표시에서 중복 |
| [scan_search.py:88](/home/mjh/paper-harness/scan_search.py:88), [run_profile_scan.py:288](/home/mjh/paper-harness/run_profile_scan.py:288) | title-only 상한은 8. `title_only_count`는 최종 잘린 리스트 길이 | signal 편수와 분리. 검색한 실행 코드에서 `title_only_count`는 생성·재설정만 확인했고 렌더러는 리스트 길이를 직접 셈 | 이 값만 고쳐도 메일 KPI는 바뀌지 않음 |
| [run_profile_scan.py:294](/home/mjh/paper-harness/run_profile_scan.py:294) | 최종 `papers`에서 `failed`를 찾아 S2 TLDR 보강 | 현재 실패는 앞에서 demoted되므로 이 경로는 일반 흐름에서 빈 대상. signal 보강으로 재사용하려면 별도 변경 필요 | 기존 코드가 signal을 보강해 줄 것이라고 잘못 가정 |

**주의:** `reserve`는 순회 후 “남은 미처리 후보”로 다시 계산되지 않습니다. 초기 reserve에 들어 있던 객체가 콘텐츠로 승격되거나 signal이 되어도 원래 리스트는 남습니다. 따라서 발송 목록을 `papers + signal_papers + reserve`로 만들면 안 됩니다. ([run_profile_scan.py:190](/home/mjh/paper-harness/run_profile_scan.py:190), [run_profile_scan.py:284](/home/mjh/paper-harness/run_profile_scan.py:284))

## `deep_status`, `abstract_brief`, `repro_outcome="not_attempted"`

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [batch_summarize.py:72](/home/mjh/paper-harness/batch_summarize.py:72) | 초록 확보·보강 후 브리프가 있으면 `abstract_only`, 없으면 `fetch_failed` 반환 | **분류 변경만이면 변경 불필요.** 반환된 브리프·원인을 보존 | signal을 만들면서 브리프를 버리거나 초록 정리 실패까지 같은 성공 상태로 취급 |
| [run_profile_scan.py:270](/home/mjh/paper-harness/run_profile_scan.py:270) | 초록 상태, 브리프, 재현 미실행 사유를 paper에 붙임 | signal 이동 후에도 세 필드를 유지 | 재현 실패·미확인과 재현 미시행이 섞임 |
| [digest.py:705](/home/mjh/paper-harness/digest.py:705), [digest.py:729](/home/mjh/paper-harness/digest.py:729) | 브리프를 한 줄 요지로 사용하며 `not_attempted`는 reason을 그대로 표시 | signal 렌더러에서 사용할 정보 범위를 정하고 필요한 표시를 유지 | 제목만 렌더링하면 현재 제공하던 초록 요지와 미실행 설명이 사라짐 |
| [research_frontier.py:374](/home/mjh/paper-harness/research_frontier.py:374) | 초록 상태에 “결과 수치는 확인하지 않음”이라는 읽을 포인트를 생성 | 함수를 계속 부르면 변경 불필요 | signal에서 이 경로를 제거하면 확인 범위 설명도 사라짐 |

본문 확보에 실패해도 브리프 생성은 수행됩니다. 따라서 signal을 제목·링크로만 표시하는 설계라도, 현재 처리 경로를 유지하면 초록 LLM 호출 비용은 계속 발생합니다. 추가 비용은 **미실측**입니다. ([batch_summarize.py:77](/home/mjh/paper-harness/batch_summarize.py:77))

## `mail_ledger`: `position`, `paper_count`

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [scan_deliver.py:119](/home/mjh/paper-harness/scan_deliver.py:119) | `record_issue`에 `papers`만 전달 | 실제 표시한 **본문+signal**을 중복 없이 전달 | signal이 발송 이력·정찰의 배달 판정에서 사라짐 |
| [mail_ledger.py:73](/home/mjh/paper-harness/mail_ledger.py:73) | `paper_count=len(papers)` | 총 표시 논문 수로 유지할지 본문 수와 분리할지 명시 | “발송 논문 수”가 실제 메일과 다름 |
| [mail_ledger.py:75](/home/mjh/paper-harness/mail_ledger.py:75) | 입력 순서대로 `position=1…N` 기록 | 메일 표시 순서와 일치하는 단일 목록 사용 | 버튼 번호·메일 위치·ledger 위치가 어긋남 |
| [mail_ledger.py:37](/home/mjh/paper-harness/mail_ledger.py:37) | 항목에는 섹션·깊이 컬럼이 없음 | 단순 포함은 스키마 변경 불필요. 본문/signal별 집계가 필요하면 구분 정보 필요 | 기존 `paper_count`로 본문 확보율을 계산하면 signal도 본문으로 셈 |
| [mail_ledger.py:71](/home/mjh/paper-harness/mail_ledger.py:71) | 같은 issue 기록 시 회차를 대체하고 기존 항목을 삭제 | **본문과 signal을 같은 issue로 따로 두 번 기록하면 안 됨** | 두 번째 기록이 첫 번째 목록을 지움 |
| [mail_ledger.py:140](/home/mjh/paper-harness/mail_ledger.py:140), [mail_ledger.py:154](/home/mjh/paper-harness/mail_ledger.py:154) | position 순으로 조회하고, 실패가 아닌 회차의 paper_count 합산 | 총 발송 논문 집계라면 소비 함수 자체는 변경 불필요 | 입력 누락이 화면·통계까지 그대로 전파 |

## `feedback_links`: 어떤 논문이 버튼을 받는가

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [scan_deliver.py:97](/home/mjh/paper-harness/scan_deliver.py:97) | `papers`만 토큰 발급 대상 | signal에 버튼을 유지한다면 본문+signal 전달 | signal에는 반응을 남길 수 없음 |
| [scan_deliver.py:102](/home/mjh/paper-harness/scan_deliver.py:102) | 수신자별 `_feedback_links`를 `view["papers"]`에만 주입 | `view["signal_papers"]`도 수신자별 복사·주입 | 토큰은 만들었지만 실제 버튼은 안 나타남 |
| [feedback_links.py:184](/home/mjh/paper-harness/feedback_links.py:184) | 입력 위치로 `item_no=P번호`, `position` 생성 | ledger·메일과 동일한 전체 순서를 쓰거나 별도 번호 정책을 명시 | 섹션마다 따로 호출하면 P1 등의 위치가 중복될 수 있음 |
| [digest.py:1927](/home/mjh/paper-harness/digest.py:1927), [digest.py:1939](/home/mjh/paper-harness/digest.py:1939) | `_feedback_links`가 있어야 HTML·평문 버튼 출력 | 새 signal 렌더러에도 해당 경로 연결 | 한 형식에만 버튼이 나타나는 불일치 |
| [scan_deliver.py:108](/home/mjh/paper-harness/scan_deliver.py:108), [feedback_links.py:204](/home/mjh/paper-harness/feedback_links.py:204) | 발송 후 issue·수신자 단위로 delivered_at 기록 | 같은 issue로 발급하면 이 부분은 변경 불필요 | 별도 issue로 만들고 후처리를 빠뜨리면 토큰 배달 시각 누락 |
| [feedback_weights.py:101](/home/mjh/paper-harness/feedback_weights.py:101) | 반응 논문 키로 당시 관측의 core_hits를 찾음 | 키·관측을 보존하면 학습 계산 변경 불필요 | signal 이동 중 키가 달라지면 반응의 키워드 근거를 못 찾음 |

## `profile_shown`과 재노출 방지

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [run_profile_scan.py:579](/home/mjh/paper-harness/run_profile_scan.py:579) | `--all`에서 발송 완료 판정 뒤 `papers`만 소비 | 성공한 메일의 본문+signal을 기록 | signal이 다음 검색에서 재노출 |
| [run_profile_scan.py:689](/home/mjh/paper-harness/run_profile_scan.py:689) | 단일 프로필 CLI도 같은 방식 | **이 호출부도 함께 변경** | 전체 실행만 고치면 수동 단일 실행에서 재발 |
| [research_profile.py:732](/home/mjh/paper-harness/research_profile.py:732) | 받은 목록을 paper_key 기준으로 최초 기록 | 함수 자체 변경 불필요 | title-only 전체까지 무작정 합치면 실제 표시하지 않은 후보까지 소비 |
| [scan_search.py:258](/home/mjh/paper-harness/scan_search.py:258) | already_shown에 있으면 순위 계산 전에 제외 | 조회 로직 변경 불필요 | 누락된 signal을 다시 처리하며 초록 호출도 반복 |
| [evaluation.py:55](/home/mjh/paper-harness/evaluation.py:55), [ops_dashboard.py:529](/home/mjh/paper-harness/ops_dashboard.py:529) | 배달 평가·논문별 프로필 연결도 profile_shown 사용 | signal 기록이 정확하면 변경 불필요 | 실제 전달했는데 미전달로 평가하거나 프로필 연결 누락 |

기존 부분 성공 처리는 별도 주의점입니다. ledger는 `partial`을 기록하지만 `_deliver`는 수신자 실패가 하나라도 있으면 `발송 실패`를 반환하고, `delivery_reached_someone()`은 `발송 완료` 접두사만 인정합니다. 따라서 부분 성공 시 소비하지 않는 현상은 **이미 존재하며**, signal 추가만으로 해결되지 않습니다. ([mail_ledger.py:63](/home/mjh/paper-harness/mail_ledger.py:63), [scan_deliver.py:123](/home/mjh/paper-harness/scan_deliver.py:123), [scan_deliver.py:40](/home/mjh/paper-harness/scan_deliver.py:40))

## `research_frontier.analyze`

- **현재:** `result["papers"]` 전체를 받습니다. `deep_status=="done"` 필터는 없습니다. 비-arXiv 논문은 unsupported 상태와 문장 근거를 만들고, arXiv 논문은 표·외부 신호 분석 경로에 들어갑니다. ([run_profile_scan.py:397](/home/mjh/paper-harness/run_profile_scan.py:397), [research_frontier.py:116](/home/mjh/paper-harness/research_frontier.py:116), [research_frontier.py:161](/home/mjh/paper-harness/research_frontier.py:161))
- **필요 변경:** signal도 현재 수준의 성능 주장·채택 신호를 유지하려면 합친 목록을 전달해야 합니다. 본문만 분석하기로 정하면 호출 자체는 그대로 둘 수 있지만 **분석 범위 축소라는 의도적 변경**입니다.
- **위험:** signal의 `_frontier`, `_signals`가 사라집니다. 또한 외부 조사 우선순위에 입력 순서가 사용되므로, 본문 우선으로 재배열한 목록은 조사 대상 선택에도 영향을 줄 수 있습니다. 실제 대상 변화는 **미실측**입니다. ([research_frontier.py:150](/home/mjh/paper-harness/research_frontier.py:150), [research_frontier.py:153](/home/mjh/paper-harness/research_frontier.py:153), [research_frontier.py:205](/home/mjh/paper-harness/research_frontier.py:205))

## `observation_signals`, 후보 관측, `trend_report`

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [scan_search.py:319](/home/mjh/paper-harness/scan_search.py:319), [scan_search.py:370](/home/mjh/paper-harness/scan_search.py:370) | Deep Layer 전에 초기 content/title_only/reserve outcome과 순위를 저장 | **초기 선정 관측으로는 변경 불필요.** 최종 깊이·표시 여부가 필요하면 별도 기록 | 초기 content를 “본문 확보 성공”으로 잘못 해석 |
| [observation_signals.py:48](/home/mjh/paper-harness/observation_signals.py:48) | `outcome='reserve'`만 reserve_terms 집계 | 초기 순위 밖 후보 집계라면 유지. 실제 미전달 집계로 쓰려면 최종 결과를 반영해야 함 | 나중에 본문·signal로 전달된 reserve도 “자리 밖” 집계에 남음 |
| [observation_signals.py:103](/home/mjh/paper-harness/observation_signals.py:103) | 시드 수율의 content는 초기 `OUTCOME_CONTENT` 여부 | 의미·라벨 유지 또는 별도 본문 성공 지표 추가 | signal로 내려간 초기 상위 논문도 content로 셈 |
| [trend_report.py:709](/home/mjh/paper-harness/trend_report.py:709) | 기간 동향은 search_candidates를 읽고 현재 core_hits로 포함 판정 | **변경 불필요** | result 목록 변경만으로 전체 관측 동향까지 줄었다고 해석하면 안 됨 |
| [run_profile_scan.py:366](/home/mjh/paper-harness/run_profile_scan.py:366) | `papers`가 있어야 서술·window_movement·reserve_terms 계산 | signal만 있어도 필요한 집계·서술이 실행되도록 조건 분리 | 본문 0편인 날 signal이 있어도 동향 블록 전체 누락 |
| [run_profile_scan.py:368](/home/mjh/paper-harness/run_profile_scan.py:368) | 서술 입력은 papers+title_only | signal을 중복 없이 포함. 본문 결과 발췌는 done 논문만 유지 가능 | signal 논문의 제목·초록·SOTA 주장 근거가 서술에서 빠짐 |
| [run_profile_scan.py:435](/home/mjh/paper-harness/run_profile_scan.py:435) | 같은 shown으로 서술·감사·catalog·papers_seen 보관 | 변경된 동일 입력을 끝까지 공유 | 생성 근거와 감사·저장 편수가 서로 달라짐 |
| [trend_report.py:460](/home/mjh/paper-harness/trend_report.py:460), [trend_report.py:481](/home/mjh/paper-harness/trend_report.py:481) | 입력 순서대로 P번호를 매기며 최대 20편 사용 | signal 증가 시 입력 순서·상한 정책 필요 | 본문 우선 합치기로 상위 관련 signal이 프롬프트 상한 밖으로 밀림 |

`_narrative_corpus`는 `abstract_brief`가 아니라 원래 `abstract`를 읽습니다. signal을 서술 입력에 합치는 것과 브리프 내용을 서술에 넣는 것은 별개입니다. ([trend_report.py:455](/home/mjh/paper-harness/trend_report.py:455))

## `external_scout.gap_stage`

- **현재 배달 판정:** `mail_issue_items JOIN mail_issues`에서 발견 시각 이전의 `sent/partial` 기록을 찾으면 `already_captured`입니다. `profile_shown`을 배달 대체 근거로 조회하지 않습니다. ([external_scout.py:264](/home/mjh/paper-harness/external_scout.py:264), [external_scout.py:279](/home/mjh/paper-harness/external_scout.py:279))
- **필요 변경:** signal을 ledger에 같은 `paper_key`로 기록하면 **gap_stage 함수 자체는 변경 불필요**입니다.
- **누락 시 구체적 결과:** 초기 `content/title_only`였으면 `not_delivered`; 초기 `reserve`였으면 `ranked_out`입니다. 다음 관측에서 `filtered/already_shown`이어도 ledger가 없으면 `not_delivered`입니다. ([external_scout.py:283](/home/mjh/paper-harness/external_scout.py:283))
- **학습 영향의 차이:** `not_delivered`는 외부 놓침 근거에 포함되지 않지만 `ranked_out`은 포함됩니다. 따라서 reserve 출신 signal 누락은 실제 전달한 논문을 외부 놓침 근거로 사용할 위험까지 있습니다. ([external_scout.py:36](/home/mjh/paper-harness/external_scout.py:36), [agent_maintenance.py:448](/home/mjh/paper-harness/agent_maintenance.py:448))

## `agent_maintenance.build_brief`

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [agent_maintenance.py:255](/home/mjh/paper-harness/agent_maintenance.py:255) | 관측 DB에서 키워드 적중·논문·스캔 수 산출 | 관측을 유지하면 변경 불필요 | 본문 콘텐츠 수로 오해하면 안 됨 |
| [agent_maintenance.py:271](/home/mjh/paper-harness/agent_maintenance.py:271) | 유효 반응을 받아 최신 관측의 제목·초록으로 R근거 생성 | signal 토큰·키를 보존하면 변경 불필요 | 버튼을 제거하면 signal에 대한 사용자 관심이 R근거로 들어올 수 없음 |
| [agent_maintenance.py:310](/home/mjh/paper-harness/agent_maintenance.py:310), [term_discovery.py:65](/home/mjh/paper-harness/term_discovery.py:65) | 내부 놓침 탐색은 최신 `no_core_hit` 관측 대상 | 변경 불필요 | signal 이동만으로 이 탐색 대상이 되는 것은 아님 |
| [agent_maintenance.py:324](/home/mjh/paper-harness/agent_maintenance.py:324), [agent_trend_evidence.py:29](/home/mjh/paper-harness/agent_trend_evidence.py:29) | 기간 동향·reserve 용어·저장된 서술을 T근거로 수집 | 집계는 유지. 일일 signal-only 서술 누락 경로는 고쳐야 함 | 정량 관측은 남지만 서술 이력은 비는 불균형 |
| [agent_maintenance.py:330](/home/mjh/paper-harness/agent_maintenance.py:330) | 외부 정찰의 gap_stage를 E근거로 전달 | ledger 정확성을 보장하면 직접 변경 불필요 | 잘못된 ranked_out 판정이 그대로 브리프·검증에 전달 |

## `ops_dashboard`, `scan_health`, 아침 점검

- **메일 회차·반응 화면:** ledger를 읽고 `(issue_id, paper_key)`로 반응을 붙입니다. signal을 ledger에 포함하면 총 발송 목록·반응 집계는 따라옵니다. 본문/signal을 구분해 보여주려면 별도 정보가 필요합니다. ([ops_dashboard.py:158](/home/mjh/paper-harness/ops_dashboard.py:158), [ops_dashboard.py:243](/home/mjh/paper-harness/ops_dashboard.py:243))
- **DB 논문 목록:** `papers` 테이블이 출발점입니다. ledger에 signal을 넣어도 저장 논문 행이 없는 초록 논문까지 이 화면에 자동 생성되지는 않습니다. 이는 현재 구조의 제약입니다. ([ops_dashboard.py:524](/home/mjh/paper-harness/ops_dashboard.py:524))
- **`observation_signals.scan_health`:** 관측 저장 성공·실패·미완을 셉니다. 콘텐츠 자리 충족률은 검사하지 않으므로 **변경 불필요**입니다. ([observation_signals.py:186](/home/mjh/paper-harness/observation_signals.py:186))
- **`scan_health` 테이블 / `profile_health`:** 초기 `rank_pos <= max_items`를 top-K로 얼리고 비교합니다. 최종 done 목록으로 바꾸지 않아도 되지만, 이 지표로 게이트 이후 메일의 관련도 보존을 검증할 수는 없습니다. ([profile_health.py:88](/home/mjh/paper-harness/profile_health.py:88), [profile_health.py:209](/home/mjh/paper-harness/profile_health.py:209), [scan_search.py:400](/home/mjh/paper-harness/scan_search.py:400))
- **실행 로그 화면:** 시작·종료·exit·경고·API 호출·마지막 JSON을 파싱합니다. 확인한 경로에는 고정된 `5/5` 콘텐츠 성공 검사가 없습니다. 현재 최종 JSON도 후보 수·scored_count 중심이므로 본문/signal/미처리 수를 점검하려면 새 지표가 필요합니다. ([ops_dashboard.py:372](/home/mjh/paper-harness/ops_dashboard.py:372), [run_profile_scan.py:568](/home/mjh/paper-harness/run_profile_scan.py:568))
- **조사 범위 밖:** 외부 예약 프롬프트나 저장소 밖 아침 점검의 `5/5` 기대 여부는 **미실측**입니다. 저장소의 `test_title_ko.py`에 있는 `5/5`는 제목 번호 표기 테스트입니다. ([test_title_ko.py:99](/home/mjh/paper-harness/test_title_ko.py:99))

## `digest.py`: 초록 카드·새 signal 섹션·번호

| 위치 | 현재 동작 | 필요한 변경 | 잊었을 때 위험 |
|---|---|---|---|
| [digest.py:822](/home/mjh/paper-harness/digest.py:822), [digest.py:1890](/home/mjh/paper-harness/digest.py:1890), [digest.py:1953](/home/mjh/paper-harness/digest.py:1953) | 초록 전용 평문·HTML 본문·상태 칩 분기 | signal 전용 렌더러를 만들거나 기존 카드 재사용. 초록 기반·미검증 구분 유지 | 초록을 본문 요약처럼 표시하거나 제목·링크·상태 설명 유실 |
| [digest.py:2008](/home/mjh/paper-harness/digest.py:2008) | HTML 초록 카드는 저장 요약 대신 브리프로 요지 생성 | 상태 필드를 보존하면 재사용 가능 | 이전 저장 요약과 오늘 초록 상태가 섞이는 표시 |
| [digest.py:1707](/home/mjh/paper-harness/digest.py:1707), [digest.py:2504](/home/mjh/paper-harness/digest.py:2504), [digest.py:2532](/home/mjh/paper-harness/digest.py:2532) | 빈 메일 여부는 papers와 title_only만 검사 | 양 형식에서 signal 포함 | signal만 있어도 “새 논문 없음” 또는 관련 절 누락 |
| [digest.py:1740](/home/mjh/paper-harness/digest.py:1740), [digest.py:2517](/home/mjh/paper-harness/digest.py:2517) | 카드 목록은 papers만 순회 | 본문 아래 signal 섹션 추가 | 리스트 이동만 하면 메일에서 완전히 사라짐 |
| [digest.py:2475](/home/mjh/paper-harness/digest.py:2475) | “신규 논문”은 len(papers), title-only는 별도 리스트 길이 | 본문/초록/전체 표시 수를 구분 | 실제 전달 편수를 축소 표시 |
| [digest.py:1688](/home/mjh/paper-harness/digest.py:1688) | arXiv 초록 대체 안내도 papers만 검사 | signal 포함 | 장애로 초록을 보냈는데 해당 안내 누락 |
| [digest.py:2151](/home/mjh/paper-harness/digest.py:2151), [digest.py:1114](/home/mjh/paper-harness/digest.py:1114), [digest.py:2547](/home/mjh/paper-harness/digest.py:2547) | 본문 목록으로 번호를 만들고 len(papers)를 분모로 사용 | signal 번호 체계를 정하고 서술 번호와 일치시킴 | signal에 본문 번호를 잘못 붙이거나 토큰·ledger 번호와 혼동 |
| [digest.py:1081](/home/mjh/paper-harness/digest.py:1081) | 서술에서 부른 title-only 중 papers에 없는 것을 별도 링크 목록으로 제공 | signal이 별도 섹션에 있으면 “이미 표시된 키”에도 포함 | 같은 signal이 본문 아래와 서술 참조 목록에 중복 |
| [digest.py:1116](/home/mjh/paper-harness/digest.py:1116), [digest.py:2549](/home/mjh/paper-harness/digest.py:2549) | 서술 제목 한국어 병기는 papers+title_only 사용 | signal 포함 | 카드에는 번역이 있어도 서술에는 누락 |

**`title_only_papers`에 넣는 것은 signal 섹션 구현의 대체가 아닙니다.** 현재 title-only 전체 목록은 렌더링하지 않으며, 서술에서 이름을 부른 항목만 별도 링크로 나타납니다. 기존 테스트도 무조건적인 제목 나열이 없음을 검사합니다. ([digest.py:1083](/home/mjh/paper-harness/digest.py:1083), [digest.py:2578](/home/mjh/paper-harness/digest.py:2578), [test_digest.py:708](/home/mjh/paper-harness/test_digest.py:708))

## 함께 연결해야 할 부가 경로

| 위치 | 현재 동작 | 필요한 변경·위험 |
|---|---|---|
| [run_profile_scan.py:335](/home/mjh/paper-harness/run_profile_scan.py:335) | papers+title_only에 관측 날짜 부착 | signal 포함. 빠뜨리면 first_seen·summarized_at 보강 누락 |
| [run_profile_scan.py:480](/home/mjh/paper-harness/run_profile_scan.py:480) | 같은 두 목록의 제목 번역 | signal 포함. 빠뜨리면 새 섹션만 한국어 제목 누락 |
| [evidence_state.py:232](/home/mjh/paper-harness/evidence_state.py:232), [evidence_state.py:247](/home/mjh/paper-harness/evidence_state.py:247) | papers만 철회 재점검·상태 capture·`_delivered_state` 부착 | 현재 signal의 상태 관측을 유지하려면 포함 |
| [scan_deliver.py:78](/home/mjh/paper-harness/scan_deliver.py:78), [scan_deliver.py:110](/home/mjh/paper-harness/scan_deliver.py:110) | papers의 키만 상태 통지 acknowledge | prepare와 같은 표시 대상 사용. 한쪽만 바꾸면 관측하고도 배달 확인하지 않음 |
| [run_profile_scan.py:471](/home/mjh/paper-harness/run_profile_scan.py:471) | 상태 관측 실패 때 papers의 임시 상태만 제거 | signal까지 상태를 붙였다면 실패 정리도 확장 |
| [email_delivery.py:91](/home/mjh/paper-harness/email_delivery.py:91) | 최종 평문·HTML을 SMTP 전 링크 감사 | 동일 발송 경로를 쓰면 변경 불필요 |

# 3. 기존 테스트와 순위 계약 판정

아래 **수정**은 새 정책을 채택할 때 기대값 변경이 필요한 테스트, **확장**은 기존 계약을 유지하면서 signal 사례를 추가할 테스트입니다. 이번 조사에서는 실행하지 않았습니다.

## 직접 기대값을 바꿔야 하는 테스트

| 테스트 | 판단 |
|---|---|
| `test_run_profile_scan.py::test_arxiv_outage_breaker_sends_the_rest_to_the_abstract_path` — [634행](/home/mjh/paper-harness/test_run_profile_scan.py:634) | **수정:** `result["papers"]`에 abstract_only 두 편이 남는다고 직접 단언. signal 목록 기대값으로 변경 필요 |
| `test_run_profile_scan.py::test_paper_without_link_reaches_process_paper` — [1193행](/home/mjh/paper-harness/test_run_profile_scan.py:1193) | **수정:** 첫 papers 항목의 초록 상태·브리프와 메일 브리프 문구를 단언. 처리 함수 도달 검사는 유지 |
| `test_digest.py::test_abstract_only_paper_stays_inside_its_own_toggle` — [613행](/home/mjh/paper-harness/test_digest.py:613) | **조건부 수정:** 기존 카드 재사용이면 유지. 제목·링크만 표시하는 signal 카드로 바꾸면 토글·내용 기대는 의도적 변경 |
| `test_digest.py::test_abstract_brief_replaces_the_error_dump` — [743행](/home/mjh/paper-harness/test_digest.py:743) | **조건부 수정:** 새 섹션에서 브리프를 제거한다면 사용자에게 보이는 계약 변경. 기존 카드 함수 유지 시 이 단위 테스트 자체는 유지 가능 |
| `test_digest.py::test_abstract_brief_never_claims_verification` — [750행](/home/mjh/paper-harness/test_digest.py:750) | **의미 유지:** 새 signal 렌더러에서도 미검증 구분을 검사해야 함 |
| `test_digest.py::test_abstract_brief_falls_back_when_empty` — [757행](/home/mjh/paper-harness/test_digest.py:757) | **조건부 수정:** signal의 빈 브리프 표시 정책에 맞춰 검사 대상 조정 |

## 유지하면서 signal 사례를 확장해야 할 테스트

각 목록의 링크는 해당 테스트 정의 위치입니다.

| 범위 | 기존 테스트 |
|---|---|
| 실패 격리·상태 | `test_run_profile_scan.py::test_deep_layer_isolates_failure_of_one_paper` ([277](/home/mjh/paper-harness/test_run_profile_scan.py:277)); `test_deep_layer_records_fetch_failed_dict_as_failure` ([358](/home/mjh/paper-harness/test_run_profile_scan.py:358)); `test_deep_layer_skips_already_summarized_paper` ([335](/home/mjh/paper-harness/test_run_profile_scan.py:335)) |
| 예산·보류 | `test_run_profile_scan.py::test_deep_layer_stops_when_budget_is_exceeded` ([801](/home/mjh/paper-harness/test_run_profile_scan.py:801)); `test_budget_is_checked_before_starting_not_mid_paper` ([832](/home/mjh/paper-harness/test_run_profile_scan.py:832)); `test_deferred_papers_are_not_listed_in_the_digest` ([859](/home/mjh/paper-harness/test_run_profile_scan.py:859)) |
| 장애 대체 | `test_run_profile_scan.py::test_arxiv_search_outage_skips_fetch_from_the_first_paper` ([637](/home/mjh/paper-harness/test_run_profile_scan.py:637)) |
| 재노출 방지 | `test_run_profile_scan.py::test_delivered_papers_are_dropped_before_ranking` ([527](/home/mjh/paper-harness/test_run_profile_scan.py:527)); `test_journal_paper_shown_yesterday_is_dropped_today` ([720](/home/mjh/paper-harness/test_run_profile_scan.py:720)); `test_candidate_key_matches_profile_shown` ([1402](/home/mjh/paper-harness/test_run_profile_scan.py:1402)) |
| 발송 후 소비 | `test_run_profile_scan.py::test_successful_delivery_consumes_the_papers_that_were_sent` ([1595](/home/mjh/paper-harness/test_run_profile_scan.py:1595)); `test_successful_delivery_consumes_the_papers` ([1573](/home/mjh/paper-harness/test_run_profile_scan.py:1573)); `test_failed_delivery_leaves_papers_for_tomorrow` ([1551](/home/mjh/paper-harness/test_run_profile_scan.py:1551)); `test_no_recipient_does_not_consume` ([1747](/home/mjh/paper-harness/test_run_profile_scan.py:1747)); `test_scan_without_send_does_not_consume` ([1623](/home/mjh/paper-harness/test_run_profile_scan.py:1623)) |
| 제목 번역 | `test_run_profile_scan.py::test_paper_titles_get_korean_before_the_digest_is_built` ([1992](/home/mjh/paper-harness/test_run_profile_scan.py:1992)); `test_a_failed_translation_does_not_stop_the_digest` ([2028](/home/mjh/paper-harness/test_run_profile_scan.py:2028)); `test_title_ko.py::test_a_paper_named_only_in_the_narrative_gets_korean_in_both_formats` ([136](/home/mjh/paper-harness/test_title_ko.py:136)) |
| 토큰·버튼 | `test_feedback_links.py::test_deliver_gives_each_recipient_own_buttons_and_marks_delivery` ([228](/home/mjh/paper-harness/test_feedback_links.py:228)); `test_links_are_per_recipient_and_google_sees_no_paper_or_email` ([80](/home/mjh/paper-harness/test_feedback_links.py:80)) |
| ledger·화면 | `test_ops_dashboard.py::test_ledger_reconstructs_days_before_it_existed_but_not_days_it_covers` ([45](/home/mjh/paper-harness/test_ops_dashboard.py:45)); `test_overview_counts_mails_reactions_and_schedule` ([70](/home/mjh/paper-harness/test_ops_dashboard.py:70)); `test_issue_reactions_and_reaction_log_show_status` ([113](/home/mjh/paper-harness/test_ops_dashboard.py:113)) |
| 정찰 배달 판정 | `test_external_scout.py::test_latest_observation_and_mail_ledger_decide_the_stage` ([85](/home/mjh/paper-harness/test_external_scout.py:85)); `test_gap_stage_uses_observations_before_discovery` ([77](/home/mjh/paper-harness/test_external_scout.py:77)); `test_gap_stage_has_no_time_leak_and_marks_not_yet_evaluable` ([104](/home/mjh/paper-harness/test_external_scout.py:104)) |
| 근거 상태·서술 | `test_evidence_state.py::test_prepare_observes_only_current_papers_and_handles_abstract_only` ([251](/home/mjh/paper-harness/test_evidence_state.py:251)); `test_briefing.py::test_scan_carries_grounded_material_through_both_mail_formats` ([192](/home/mjh/paper-harness/test_briefing.py:192)) |
| frontier | `test_frontier.py::test_analyze_never_raises_and_mail_still_renders` ([247](/home/mjh/paper-harness/test_frontier.py:247)); `test_reading_point_has_no_praise_and_states_limits` ([200](/home/mjh/paper-harness/test_frontier.py:200)) |
| メ일 빈 상태·장애·번호 | `test_digest.py::test_arxiv_search_failure_is_visible_in_both_mails` ([1617](/home/mjh/paper-harness/test_digest.py:1617)); `test_empty_digest_html_matches_plain_sections` ([1632](/home/mjh/paper-harness/test_digest.py:1632)); `test_narrative_points_back_to_the_numbered_paper` ([1157](/home/mjh/paper-harness/test_digest.py:1157)); `test_paper_already_in_the_body_is_not_repeated` ([1064](/home/mjh/paper-harness/test_digest.py:1064)) |

**테스트 이름보다 실제 호출 범위를 봐야 하는 사례:**  
`test_run_profile_scan.py::test_scan_and_digest_records_only_the_content_slots`는 이름과 달리 `scan_and_digest`를 호출하지 않습니다. `mark_shown([shown])`만 직접 호출하고 footnote가 기록되지 않았음을 확인합니다. 따라서 signal을 발송 후 소비하는 통합 경로의 누락은 잡지 못합니다. ([test_run_profile_scan.py:749](/home/mjh/paper-harness/test_run_profile_scan.py:749))

## 의미를 유지해야 하는 하위 테스트

다음은 목록 이동만으로 기대값을 바꿀 이유가 없습니다.

- `test_process_paper.py::test_openalex_fills_a_missing_abstract` ([185](/home/mjh/paper-harness/test_process_paper.py:185))
- `test_process_paper.py::test_paper_without_pdf_link_still_reaches_the_abstract_brief` ([248](/home/mjh/paper-harness/test_process_paper.py:248))
- `test_process_paper.py::test_arxiv_paper_with_s2_abstract_falls_back_when_arxiv_is_down` ([427](/home/mjh/paper-harness/test_process_paper.py:427))
- `test_process_paper.py::test_skip_arxiv_fetch_goes_straight_to_the_abstract` ([444](/home/mjh/paper-harness/test_process_paper.py:444))
- `test_process_paper.py::test_missing_paper_is_not_an_arxiv_outage` ([453](/home/mjh/paper-harness/test_process_paper.py:453))
- `test_digest.py::test_out_of_rank_papers_are_no_longer_listed` — title-only와 signal을 구분해야 유지할 수 있음. ([695](/home/mjh/paper-harness/test_digest.py:695))
- `test_run_profile_scan.py::test_candidates_are_recorded_before_selection` — 초기 관측 의미 유지. ([1294](/home/mjh/paper-harness/test_run_profile_scan.py:1294))
- `test_observations.py::test_reserve_terms_filters_outcomes_and_profiles` ([359](/home/mjh/paper-harness/test_observations.py:359))
- `test_observations.py::test_reserve_terms_latest_scan_only` ([343](/home/mjh/paper-harness/test_observations.py:343))
- `test_observations.py::test_reserve_terms_restores_referenced_abstracts_readonly` ([374](/home/mjh/paper-harness/test_observations.py:374))
- `test_observations.py::test_스캔_기록이_없으면_미측정이고_있으면_0편과_미시도를_가른다` ([191](/home/mjh/paper-harness/test_observations.py:191))
- `test_agent_maintenance.py::test_brief_carries_reactions_hits_and_missed_terms` ([100](/home/mjh/paper-harness/test_agent_maintenance.py:100))
- `test_agent_maintenance.py::test_trend_sources_match_existing_python_collectors` ([655](/home/mjh/paper-harness/test_agent_maintenance.py:655))
- `test_external_scout.py::test_brief_carries_verified_external_evidence_with_official_text` ([217](/home/mjh/paper-harness/test_external_scout.py:217))
- `test_profile_health.py::test_H7_반사실은_스캔_시점에_얼려_뒤의_채점_변경에_흔들리지_않는다` ([326](/home/mjh/paper-harness/test_profile_health.py:326))
- `test_profile_health.py::test_얼린_값은_재호출로도_바뀌지_않는다` ([345](/home/mjh/paper-harness/test_profile_health.py:345))

## 순위 계약 테스트와 위반 여부

핵심 직접 검사입니다.

| 테스트 | 실제 검사 범위 | 제안과의 관계 |
|---|---|---|
| `test_rank_contract.py::test_본문_링크_유무가_선정_순서를_바꾸지_않는다` — [134](/home/mjh/paper-harness/test_rank_contract.py:134) | `scan_profile`을 호출하여 링크 없는 상위 논문이 papers, 하위 논문이 reserve임을 단언 | **Deep Layer만 변경하면 그대로 통과 가능** |
| `test_rank_contract.py::test_reserve_도_같은_계약_순서다` — [165](/home/mjh/paper-harness/test_rank_contract.py:165) | `score_and_rank`의 전체 순서 검사 | 큐 순서를 유지하면 통과 가능 |
| `test_run_profile_scan.py::test_scan_profile_ranks_by_relevance_not_text_availability` — [1065](/home/mjh/paper-harness/test_run_profile_scan.py:1065) | 검색 단계 선정 검사 | 검색 게이트를 추가하지 않으면 유지 |
| `test_run_profile_scan.py::test_high_relevance_paper_without_text_outranks_low_relevance_with_text` — [1113](/home/mjh/paper-harness/test_run_profile_scan.py:1113) | 역시 `scan_profile`까지만 호출 | 최종 메일의 본문 우선 재배치를 탐지하지 못함 |

나머지 `test_rank_contract.py`의 순위 불변식도 유지 대상입니다.

- `test_계층이_최신성보다_먼저다` ([33](/home/mjh/paper-harness/test_rank_contract.py:33))
- `test_같은_계층이면_최신이_먼저다` ([45](/home/mjh/paper-harness/test_rank_contract.py:45))
- `test_같은_날이면_적중_폭_그다음_도메인` ([56](/home/mjh/paper-harness/test_rank_contract.py:56))
- `test_날짜_결측은_같은_계층_뒤로_가지만_계층을_넘지_않는다` ([66](/home/mjh/paper-harness/test_rank_contract.py:66))
- `test_동률은_입력_순서가_아니라_키로_정한다` ([81](/home/mjh/paper-harness/test_rank_contract.py:81))
- `test_연속_가중치는_0_1_구간으로_묶여_기존_계층은_그대로다` ([89](/home/mjh/paper-harness/test_rank_contract.py:89))
- `test_계층은_적중_키워드와_원_가중치로_구한다` ([103](/home/mjh/paper-harness/test_rank_contract.py:103))
- `test_점수는_설명일_뿐_자격을_정하지_않는다` ([115](/home/mjh/paper-harness/test_rank_contract.py:115))
- `test_병합은_더_이른_공개일을_남긴다` ([175](/home/mjh/paper-harness/test_rank_contract.py:175))
- `test_띠_안에서는_개념_폭이_날짜를_이긴다` ([185](/home/mjh/paper-harness/test_rank_contract.py:185))
- `test_띠를_넘는_오래된_논문은_결합이_많아도_뒤로_간다` ([194](/home/mjh/paper-harness/test_rank_contract.py:194))
- `test_띠의_기준은_절대_달력이_아니라_가장_최신_후보다` ([201](/home/mjh/paper-harness/test_rank_contract.py:201))
- `test_표기_변형_둘은_한_개념이다` ([211](/home/mjh/paper-harness/test_rank_contract.py:211))
- `test_날짜_없음은_띠_뒤에_남고_계층은_넘지_않는다_v2` ([225](/home/mjh/paper-harness/test_rank_contract.py:225))
- `test_띠의_앵커는_무적중_최신_논문이_아니라_적격_후보의_최신이다` ([233](/home/mjh/paper-harness/test_rank_contract.py:233))
- `test_개념_접기는_실제_변형_쌍만_접는다` ([242](/home/mjh/paper-harness/test_rank_contract.py:242))
- `test_앵커는_외따로_떨어진_한_편이_정하지_않는다` ([248](/home/mjh/paper-harness/test_rank_contract.py:248))
- `test_앵커가_물러나는_거리에는_상한이_있다` ([268](/home/mjh/paper-harness/test_rank_contract.py:268))
- `test_앵커_지지_규칙은_표본이_작으면_적용하지_않는다` ([290](/home/mjh/paper-harness/test_rank_contract.py:290))

**판정:** 검색·처리 큐의 순위 계약은 보존할 수 있습니다. 그러나 최종 콘텐츠 자리와 표시 순서에서는 기존 계약을 변경합니다. 상위 A가 abstract_only, 하위 B가 done이면 현재 A가 지키던 콘텐츠 자리를 B가 받고, A는 B 아래로 이동합니다. **각 리스트 내부의 순서 보존은 전체 표시 순서의 관련도 보존과 같지 않습니다.** 현재 `test_rank_contract.py`가 통과하더라도 이 변경의 정당성이나 안전성을 증명하지 않습니다. ([run_profile_scan.py:265](/home/mjh/paper-harness/run_profile_scan.py:265), [test_rank_contract.py:160](/home/mjh/paper-harness/test_rank_contract.py:160), [test_run_profile_scan.py:1194](/home/mjh/paper-harness/test_run_profile_scan.py:1194))

# 4. `[자리]` 로그와 `demoted` 집계의 모순

**모순이 확인됐습니다. N은 본문 수집 실패 건수가 아닙니다.**

`demoted`에는 서로 다른 세 경로가 합쳐집니다.

| 경로 | 실행 코드 | 실제 의미 |
|---|---|---|
| 자리 충족 이후 | [run_profile_scan.py:201](/home/mjh/paper-harness/run_profile_scan.py:201) | `len(content) >= max_items`면 처리 함수 호출 없이 추가 |
| 처리 예외 | [run_profile_scan.py:234](/home/mjh/paper-harness/run_profile_scan.py:234) | 임의의 처리 예외. 본문 수집뿐 아니라 요약 등 다른 단계 실패일 수 있음 |
| 실패 outcome | [run_profile_scan.py:273](/home/mjh/paper-harness/run_profile_scan.py:273) | done·abstract_only 외 결과를 failed로 바꿔 추가 |

그런데 로그는 구분 없이 `len(demoted)`를 다음처럼 출력합니다. ([run_profile_scan.py:281](/home/mjh/paper-harness/run_profile_scan.py:281))

```text
[자리] 본문 수집에 실패한 N편을 각주로 내렸다
```

**메모리 내 재현 결과:** 운영 소스의 189~288행 AST를 추출해 실행했습니다. 프로젝트 모듈은 import하지 않았고, 처리 함수·시계·계측을 대역으로 제공했습니다. 첫 시도는 재현 래퍼의 지역변수 초기화 누락으로 실패했고, 초기화 후 재실행한 결과입니다.

- 입력: 콘텐츠 후보 1편, reserve 10편.
- 대역 처리 결과: 호출된 논문은 모두 `done`.
- 실제 처리 호출: 첫 논문 **1편만**.
- 실제 본문 실패: **0편**.
- 최종 콘텐츠: **1편**.
- 최종 `title_only_papers`: **8편**.
- 출력:

```text
[자리] 본문 수집에 실패한 10편을 각주로 내렸다 — 내용 자리는 1/1편
```

이는 합성 입력에 대한 **소스 추출 실행 결과**이며 운영 로그의 발생 빈도는 **미실측**입니다.

오류는 두 겹입니다.

1. **“본문 수집 실패”가 틀림:** 미시도 후보가 포함됩니다. ([run_profile_scan.py:201](/home/mjh/paper-harness/run_profile_scan.py:201))
2. **“N편을 각주로 내렸다”도 정확하지 않음:** 로그 이후 최종 title-only 목록은 8편으로 잘리며, 그 목록조차 메일에서 전부 나열하지 않습니다. ([run_profile_scan.py:285](/home/mjh/paper-harness/run_profile_scan.py:285), [scan_search.py:88](/home/mjh/paper-harness/scan_search.py:88), [test_digest.py:708](/home/mjh/paper-harness/test_digest.py:708))

따라서 변경 시에는 최소한 **본문 성공 / 초록 signal / 처리 실패 / 자리 충족으로 미시도 / 예산·깊이 상한 보류**를 별도로 집계해야 합니다. 현재 `dropped` 계산값은 이 로그에 사용되지 않아 오류를 보정하지 않습니다. ([run_profile_scan.py:280](/home/mjh/paper-harness/run_profile_scan.py:280))


