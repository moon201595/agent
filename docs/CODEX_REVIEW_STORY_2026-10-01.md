# Codex 검토 — story-v2 동향 서술 (2026-10-01)

잡 `task-muov192n-9tsn5y`(읽기 전용, 5분 48초). 대상: trend_report·narrative_engine·digest·run_profile_scan 및 테스트의 미커밋 변경. 반영 내역은 PROGRESS §200.

## 1. 발견사항

**P0/P1은 확인하지 못했습니다. P2 결함 4건을 순수 함수 실행으로 재현했습니다.** 지정된 변경만 검토했으며 파일·DB 변경, 메일 발송, 커밋은 하지 않았습니다.

### P2 — 길이 제한이 이미 검증한 근거를 제거합니다

위치: [trend_report.py:421](/home/mjh/paper-harness/trend_report.py:421), [432](/home/mjh/paper-harness/trend_report.py:432), [437](/home/mjh/paper-harness/trend_report.py:437), [446](/home/mjh/paper-harness/trend_report.py:446)

`_keep_tags()`로 인용과 논문 수를 검사한 **뒤에** `_clip()`을 적용합니다.

- 재현: 본문을 `"가"*510 + "[P1:A][P2:A]"`로 주면 흐름의 `papers=[1,2]`는 유지되지만 최종 본문에는 근거 태그가 없습니다.
- headline도 끝의 유일한 태그가 잘린 채 남습니다.
- implication은 태그가 잘려도 이전 `cited` 값으로 판단하여 `(해석)`을 붙이지 않습니다.

따라서 최종 출력이 “실제로 인용한 논문만 포함”, “근거 없는 headline 삭제” 계약을 위반합니다. 렌더러가 뒤에 붙이는 제목 목록은 본문 인용 손실을 복구하지 않습니다.

**제안:** 태그·문장 경계를 보존하며 자르고, 최종 문자열을 대상으로 인용·최소 편수·카드 포함·해석 표시를 다시 검증하십시오.

### P2 — 제목 없는 카드가 있으면 각주 논문을 카드로 오인합니다

위치: [trend_report.py:814](/home/mjh/paper-harness/trend_report.py:814), [417](/home/mjh/paper-harness/trend_report.py:417)

호출자는 카드 수를 전달하지만([run_profile_scan.py:440](/home/mjh/paper-harness/run_profile_scan.py:440)), corpus는 제목 없는 행을 건너뛰고 번호를 다시 매깁니다([trend_report.py:631](/home/mjh/paper-harness/trend_report.py:631)).

재현 입력:

```text
원본: [제목 없는 카드, 정상 카드, 각주 A, 각주 B]
anchors = 2
corpus: P1=정상 카드, P2=각주 A, P3=각주 B
```

`[P2,P3]`만 인용하는 흐름이 카드 포함 조건을 통과했습니다. 실제로는 카드가 한 편도 없습니다.

**제안:** corpus 생성 시 원본 행과 P번호의 대응을 반환하고, 카드에 해당하는 **P번호 집합**으로 검사하십시오. 제목 누락이 없다면 20편 상한 자체는 앞쪽 카드 순서를 뒤집지 않습니다.

### P2 — 이름·주변 메모의 묶음 태그가 보정을 우회합니다

위치: [trend_report.py:413](/home/mjh/paper-harness/trend_report.py:413), [442](/home/mjh/paper-harness/trend_report.py:442)

본문은 묶음 태그를 풀지만, `name`과 `note`는 단일 태그 정규식으로만 제거합니다.

재현 결과:

```text
name: 이름 [P99:A, P1:T]     → 그대로 유지
note: 주변 [P99:A, P1:T]     → 그대로 유지
```

없는 ID와 제목 전용 태그가 최종 소제목·주변 신호에 출력됩니다. `_keep_tags()`도 `[P1:A, P99:Z]`처럼 지원하지 않는 형식이 섞인 괄호 전체를 그대로 남깁니다. 다른 정상 인용으로 흐름 조건을 충족하면 이런 문자열도 함께 통과합니다.

**제안:** 모든 모델 필드에 공통 태그 정규화를 적용하십시오. 이름·메모에서는 묶음을 포함한 모든 P태그를 제거하고, 본문에서는 파싱하지 못한 P태그 표현을 삭제하거나 해당 출력을 거부해야 합니다.

### P2 — 새 목록 정규식이 모델이 쓴 숫자까지 경고에서 제외합니다

위치: [trend_report.py:234](/home/mjh/paper-harness/trend_report.py:234), [689](/home/mjh/paper-harness/trend_report.py:689)

`■`를 일반 목록 접두사에 추가하여 Python이 생성한 흐름 번호뿐 아니라 모델 본문의 `■ 숫자.`도 제거합니다.

숫자 없는 corpus에 본문을 다음처럼 넣어 재현했습니다.

```text
■ 987. 성능이 향상됐다 [P1:A][P2:A]
```

최종 `ungrounded_numbers()` 결과는 `[]`입니다. 변경 전 정규식에서는 `987`이 검출됩니다. 일반 문장 속 `987`은 현재도 검출됩니다.

**제안:** 생성된 소제목의 번호만 제외하거나, 모델 필드의 수치를 렌더링 전에 검사하십시오. 임의의 모델 본문에 동일한 예외를 적용하지 않아야 합니다.

검토 결과 정상으로 확인한 경로는 다음과 같습니다.

- **HTML escaping:** 이름·본문·주변 메모는 일일·주간 렌더러에서 `_esc()`를 거칩니다. `<img>`, `<script>`, `<a>` 입력도 이스케이프되는 것을 실행 확인했습니다. 근거: [digest.py:2288](/home/mjh/paper-harness/digest.py:2288), [2293](/home/mjh/paper-harness/digest.py:2293), [2301](/home/mjh/paper-harness/digest.py:2301), [2344](/home/mjh/paper-harness/digest.py:2344), [2363](/home/mjh/paper-harness/digest.py:2363).
- **실패 시 배달 유지:** 파싱·보정·렌더링은 `generate()`의 예외 처리 안에서 실행됩니다. `ValueError` 외 일반 예외도 다음 엔진으로 넘어가며, 전부 실패하면 `None`입니다. 일일 호출부에도 외곽 예외 처리가 있습니다. 근거: [narrative_engine.py:66](/home/mjh/paper-harness/narrative_engine.py:66), [run_profile_scan.py:459](/home/mjh/paper-harness/run_profile_scan.py:459).
- **소비자 호환:** `title_ko.annotate`, `annotate_numbers`, `mentioned_papers`, 저장·history, 주간 `build → format_report`에서 새 문자열 형식 때문에 발생하는 추가 회귀는 확인하지 못했습니다. 다만 `citation_audit`는 목록을 앞 문장에 합치므로, 위 인용 손실을 엄격하게 검출하는 장치는 아닙니다.
- **실제 재생:** 제공된 네 `.txt` 모두 `engine=codex ungrounded=[]`이며, 모든 새 소제목이 현재 인식 함수를 통과했습니다. [team_vision.txt](/tmp/claude-1000/-home-mjh-paper-harness/caead8e4-6cdd-4e3c-95c4-f1d3bca59675/scratchpad/story/out/team_vision.txt:1)에서는 세 흐름과 별도 주변 신호가 관찰됩니다. 위 네 결함은 이 실물에서 발생했다고 주장하는 것이 아니라, 별도 입력으로 재현한 경계 결함입니다.
- **실패 편수 로그:** 현재 `demoted` 유입 경로와 대조하면 실패와 자리 부족을 구분하는 변경은 맞습니다. 시간 예산 초과는 별도 `deferred`로 갑니다.

## 2. 테스트 공백

변경된 테스트 각각의 방어 범위입니다. **직접 실행**은 저장소 함수를 AST로 추출하여 메모리에서 호출한 결과이며, pytest 세션 결과는 아닙니다.

| 테스트 | 무엇을 깨뜨리면 실패하는가 / 한계 |
|---|---|
| `test_prompt_carries_topics_but_never_our_measurements` — `test_trend_report.py:323` | 정적 확인: 관심 분야 누락·검사 대상 내부 값 노출을 잡습니다. `anchors=""` 추가는 새 카드 규칙을 검증하지 않습니다. |
| `test_prompt_demands_the_shape_digest_renders` — `:745` | 직접 통과. 생성 소제목·인식 조건 변경을 잡습니다. HTML 선두 상자의 개폐는 검사하지 않습니다. |
| `test_story_repair_keeps_threads_to_their_own_evidence` — `:802` | 직접 통과. 다른 논문 태그 허용·미인용 논문 유지·중복 배정·최소 편수 완화를 각각 메모리에서 변경하자 실패했습니다. |
| `test_story_repair_drops_unknown_and_title_only_tags` — `:815` | 직접 통과. 단일 unknown/T 태그, 무근거 headline, implication 해석 표시, 초록 없는 주변 신호를 검사합니다. 묶음 태그·잘림은 검사하지 않습니다. |
| `test_story_needs_a_card_paper_in_every_thread` — `:825` | 직접 통과. 카드 조건 제거 시 실패했습니다. 실제 corpus 생성·제목 누락·번호 대응은 우회합니다. |
| `test_story_render_lists_titles_from_the_corpus` — `:837` | 직접 통과. 제목 목록·번호·R 우선 선택·단일 흐름의 관계문 제거를 검사합니다. headline 없는 다중 흐름 경로는 없습니다. |
| `test_parse_story_rejects_broken_shapes` — `:851` | 직접 통과. 코드펜스 처리·필수 키 검사 제거를 잡습니다. 필드 타입·추가 속성·중첩 구조 위반 사례는 없습니다. |
| `test_narrative_falls_through_engines_until_a_valid_story` — `:861` | 정적 확인: 산문 수용·폴백 누락·전체 실패 시 반환·anchor 프롬프트 누락을 잡습니다. Codex를 `None`으로 대체하므로 `schema` 전달을 제거해도 잡지 못합니다. |
| `test_scan_carries_grounded_material_through_both_mail_formats` — `test_briefing.py:177` | 정적 확인: 원문 근거 전달·두 메일 형식의 S태그를 검사합니다. JSON을 그대로 실어도 일부 핵심 assertion은 충족하므로 story 조립 자체의 보증은 약합니다. |
| `test_monday_prompt_carries_all_three_inputs_without_leaking_counts` — `test_monday_chain.py:91` | 정적 확인: 직접 조립한 프롬프트의 입력 누락·창 집계 수치 노출을 잡습니다. 실제 월요일 호출 연결이나 카드 조건은 검사하지 않습니다. |

추가로 보호되지 않는 규칙:

- **메모리 변이에도 기존 순수 테스트가 통과:** 빈 흐름 이름 거부, 흐름 수 상한, 필드 길이 제한, 무근거 relation의 `(해석)` 표시, 주변 신호 중복·흐름 논문 재사용 금지.
- implication·주변 신호 개수 상한, 한 흐름 안의 중복 논문 ID, 이름·메모의 태그 제거, 묶음 태그 정규화.
- 길이 제한 **이후** 인용·카드 조건 재검사, corpus 제목 누락·20편 경계.
- Codex에 실제 `--output-schema`와 올바른 JSON 파일을 전달하는 경로.
- 새 숫자 마커 예외, 새 lead box, 변경된 실패 편수 로그.

## 3. 미실측

- **pytest 전체·통합 테스트 미실측.** [conftest.py:12](/home/mjh/paper-harness/conftest.py:12)는 임시 데이터 디렉터리를 만들지만, 이번 요청은 모든 파일 쓰기를 금지합니다. 임시 파일과 DB를 만드는 pytest 실행은 생략했습니다.
- 대신 **순수 테스트 6개 직접 통과**, 위 결함 재현, 조건 변경에 대한 테스트 민감도, 일일·주간 HTML 줄 이스케이프를 메모리에서 확인했습니다. `.env`를 읽는 저장소 초기화는 실행하지 않았습니다.
- **현재 Codex/API의 스키마 수용 재호출은 미실측.** 코드상 모든 객체는 필수 키와 `additionalProperties: false`를 갖추며, 제공된 네 재생 결과는 Codex 성공의 증거입니다. 현재 스키마로 새 요청을 보내 재확인하지는 않았습니다.
- **실제 메일 클라이언트 표시·SMTP·운영 DB 저장 왕복은 미실측.**




---

## 2차 검토 (중단)

잡 `task-muovuj4y-tzrftq`, 2분 42초 만에 Codex 사용량 한도로 실패(재개 15:18). 멈추기 전 남긴 출력 전문:

> 재현 결과, 제목 없는 카드가 전부 corpus에서 빠지면 카드 조건 자체가 해제되어 각주끼리의 흐름이 통과합니다. 중첩 태그도 이름·메모에
> 잔여 표기가 남습니다. 새 테스트 중에는 해당 방어 코드를 제거해도 다른 조건 때문에 통과할 수 있는 사례가 있어, 메모리에서 변이 검사를
> 진행하겠습니다.

두 결함은 반영했다(PROGRESS §200). 깊이 라벨·서술 발언권 규칙·copernicus 허용은 Codex 가 검토하지 못했다.
