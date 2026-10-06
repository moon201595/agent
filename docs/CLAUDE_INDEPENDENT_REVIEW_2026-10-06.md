# Claude 독립 검토 — 2026-10-06 (읽기 전용)

## §0. 이 문서의 성격과 한계

- 이 세션은 **읽기 전용 독립 검토**다. 코드·테스트·DB·기존 보고서·`docs/PROGRESS.md` 를 고치지 않았다.
  쓰기는 이 파일 하나뿐이다.
- **절차 위반 하나를 먼저 적는다.** 절별 저장을 하려다 `docs/CLAUDE_INDEPENDENT_REVIEW_2026-10-06.md.part3`
  라는 임시 파일을 **잘못 만들었다**. 읽기 전용 도구(Read/Grep/Glob/Write)로는 삭제할 수 없다 —
  주 도구가 `rm docs/CLAUDE_INDEPENDENT_REVIEW_2026-10-06.md.part3` 로 지워야 한다. 내용은 한 줄 메모뿐이고
  코드·테스트·DB 는 건드리지 않았다.
- **오늘 재측정한 값이 없다.** 아래에 인용하는 수치(10/2 pytest 1,482 passed 등)는
  `docs/CODEX_REPORT_2026-10-02.md` · `docs/CODEX_MAIL_*_2026-10-02.md` · `docs/HANDOFF_CLAUDE_2026-10-06.md`
  에 적힌 **10/2 기록**이고, 이 세션이 다시 돌려 확인한 것이 아니다.
- 테스트 실행·브라우저 측정·메일 발송·네트워크 수집·DB 접속을 하지 않았다. 따라서 "통과한다"가 아니라
  **"테스트 코드가 무엇을 주장하고, 그 주장이 생산 코드의 실제 호출을 거치는가"** 만 판정한다.
- 근거 유형 표기: **정적 확인** = 파일:줄을 직접 읽어 확인. **직접 재현** = 이 세션에서 실행해 확인(이번엔 없음).
  **미확인** = 읽지 않았거나 실행이 필요해 판정 불가.
- 지시에 따라 `.env` · `docs/patent/` · `docs/paper/` 는 읽거나 검색하지 않았다.
- 다른 Claude 세션이 같은 시각 `digest.py` · `saved_digest.py` · `test_weekly_profile_changes.py` ·
  `test_mail_canvas.py` 를 수정했고(인계 문서 §3), 그 쓰기는 끝난 상태로 전달받았다. 이 문서는 **그 수정 후의
  작업 트리 상태**를 읽은 결과다. 그 세션의 보고서(`docs/CLAUDE_REVIEW_2026-10-06.md`)에는 쓰지 않았다.

## §1. 검토 범위

지시받은 우선순위 그대로, 핵심 호출과 대응 테스트만 직접 읽는다(전 파일 광범위 스캔 아님).

1. 운영 화면·발송 기록: `ops_dashboard.py` · `review_app.py` · `trend_report.py` · `test_review_handoff.py`
   — 프로필 저장·URL 복귀, 읽기 전용 DB, 발송 회차/깊이/제목 연결, 요약 절 보존.
2. 메일 카드·상세: `mail_document.py` · `mail_layout.py` · `saved_digest.py` · `digest.py`
   — 기본 닫힌 토글, 표 항목명 중앙 정렬, 전문 섹션명, 작은 근거 footer.
   비교 조건/출처 없는 성능 비교 생성 경로는 `paper_observations.py` · `test_paper_sections.py` 로 확인.
3. 과거 흐름: `trend_history.py` · `test_trend_history.py` — 강제 연결·수치/검증 결과 생성 여부.
4. 이번 두 표시 변경: 하단 여백(패딩)과 꼬리 문구 삭제의 실제 생산 함수와 대응 테스트.

## §2. 우선순위 1 — 운영 화면·발송 기록

### 2.1 프로필 저장 뒤 선택 복귀 (확인됨)

- 저장 경로는 `review_app.py:333` `create_profile` → `review_app.py:343` `st.session_state["_pending_profile"] = pid`
  → `st.rerun()` 이다. 위젯 키(`_research_selected_profile`)를 **직접 대입하지 않는다** —
  사이드바 selectbox 가 `review_app.py:1360` 에서 이미 생성된 뒤라 그 키에 쓰면 Streamlit 예외다.
- 다음 실행이 `review_app.py:1337-1339` 에서 pending 을 pop 해 `_qp["profile"]` 로 옮기고,
  `review_app.py:1345-1346` 이 그 URL 값을 세션 선택으로 적용한다. 순서가 맞다(pending 적용 → 위젯 생성).
- `test_review_handoff.py:13-33` 이 `AppTest.from_file('review_app.py')` 로 실제 앱을 돌려
  `at.selectbox(key='_research_selected_profile').value == pid` 와 `at.query_params['profile'] == [pid]` 를 함께 본다.
  pending 적용(1337-1339)을 빼면 선택이 옛 프로필로 되돌아가 실패하고, 위젯 키 직접 대입으로 되돌리면
  `assert not at.exception` 에서 실패한다. **생산 코드를 실제로 부르는 회귀다.**
- 근거 유형: 정적 확인.

### 2.2 메뉴 이동·URL 복귀 (확인됨)

- `_go()`(`review_app.py:1319-1325`)가 `page` 와 현재 프로필을 `query_params.from_dict` 로 **통째 다시 쓴다** —
  그래서 메뉴를 누르면 `paper` 파라미터가 사라지고 상세에서 목록으로 돌아온다.
- `_select_profile()`(`review_app.py:1328-1330`)이 위젯 콜백에서 URL 을 맞춘다. 이게 없으면 다음 실행의
  `review_app.py:1345-1346` 이 옛 URL 값으로 선택을 되돌린다.
- `test_review_handoff.py:36-64` 가 ① URL 프로필 적용 ② selectbox 변경이 URL 에 반영 ③ 메뉴 이동이 프로필 유지
  ④ URL 로 다시 들어오면 그 프로필로 복귀 ⑤ 파라미터를 비우면 overview ⑥ `paper` 가 메뉴 이동에서 지워짐을
  순서대로 본다. 모두 실제 앱 실행이다.
- 근거 유형: 정적 확인.

### 2.3 운영 개요의 읽기 전용 연결 (확인됨)

`ops_dashboard.research_overview` 가 여는 **모든** 하위 연결을 따라가 보면 전부 `uri=True` + `mode=ro` 다.

| 호출 | 파일:줄 | 모드 |
|---|---|---|
| 개요 본체 | `ops_dashboard.py:692` | `as_uri() + "?mode=ro"`, `uri=True` |
| 주간 프로필 변화 | `weekly_profile_changes.py:48` (`_rows`, `collect` 가 여러 번 부른다) | `?mode=ro`, `uri=True` |
| 창 이동(이번·직전 2회) | `trend_report.py:1050` (`observed_rows`) | `?mode=ro`, `uri=True` |
| 시스템 상태 | `ops_dashboard.py:104` (`system_status`) | `?mode=ro`, `uri=True` |

- `test_review_handoff.py:67-80` 이 `sqlite3.connect` 를 **모듈 속성으로** 가로채므로 위 네 모듈 모두 걸린다
  (전부 `import sqlite3` 후 `sqlite3.connect(...)` 형태다 — `from sqlite3 import connect` 가 없다).
  패치 함수 안에서 `assert kwargs.get('uri') and 'mode=ro' in str(database)` 를 하므로 한 곳만 쓰기 연결을 열어도
  그 자리에서 터진다. `len(calls) >= 4` 도 성립한다(`research_profile.py:164` 가 `search_candidates`,
  `storage.py:60·68` 이 `papers`·`summaries` 를 만들어 `ops_dashboard.py:829` 의 표 조건을 통과시키므로
  `observed_rows` 가 2회 더 열린다).
- 다만 **`profile_overview`(`ops_dashboard.py:159`)·`weight_history`(183)·`keyword_table`(205)·
  `issues_with_reactions`(246)·`reaction_log`(262)·`revision_history`(298)·`agent_history`(325)·
  `collection_rows`(`trend_report.py:1067`)는 여전히 쓰기 가능 연결(`sqlite3.connect(db)`)이다.**
  이번 변경의 대상은 "운영 개요 자료 하위 연결"이었고 그 범위는 지켜졌다. 화면의 다른 자리는 읽기 전용이
  아니다 — 10/2 보고서의 "운영 개요 자료 하위 SQLite 연결을 mode=ro로 바꿨다"를 "운영 화면 전체가
  읽기 전용"으로 읽으면 과장이다. §6 에 P3 으로 적는다.
- 근거 유형: 정적 확인.

### 2.4 발송 회차·깊이·제목 연결 (확인됨)

- **회차 깊이**: `ops_dashboard.py:768-802`. DB `summaries.coverage_ratio` 로 먼저 라벨을 잡고(770-774),
  그 뒤 **같은 회차 카드의 실제 라벨**이 있으면 덮어쓴다(789-802). `digest.DEPTH_FULL/PARTIAL/ABSTRACT`
  (`digest.py:854-856`)를 직접 import 해 쓰므로 메일 라벨 문구가 바뀌면 같이 움직인다 — 문자열을 베껴 두지 않았다.
  `test_review_handoff.py:96-111` 이 `digest._paper_entry` 로 **실제 평문 카드**를 만들어 넣고,
  발송 뒤 생긴 `summaries(coverage_ratio=1)` 가 있어도 깊이가 `초록 기반` 으로 남는지 본다.
- **미발송 본문 차단**: `ops_dashboard.py:741-746` 의 `same_issue` 가 (ⅰ) 생성 ≤ 발송 (ⅱ) 같은 KST 일자
  (ⅲ) 직전 회차보다 뒤 세 조건을 모두 요구한다. `test_review_handoff.py:114-120` 이 발송 1시간 **뒤** 본문을
  넣어 `card == []` 를 본다.
- **제목 연결**: `ops_dashboard.paper_id_for_title`(`914-924`)이 정확 일치 → 16자 이상 앞부분 일치가
  **유일할 때만** 연결한다. `test_review_handoff.py:123-126` 이 짧은 후보(`'a'`)가 긴 제목에 붙지 않는 것과
  정확 일치는 되는 것을 함께 본다. `by_norm`(`ops_dashboard.py:747`)이 동명 제목을 빈 문자열로 접어 버리므로
  `test_review_handoff.py:129-143` 의 "동명 2편 + 더 긴 제목 1편"에서는 연결이 안 된다.
- 근거 유형: 정적 확인.

### 2.5 요약 절 보존 (확인됨, 단 P3 하나)

- `ops_dashboard.summary_sections`(`895-911`)가 `summary_parser._HEADING_RE` · `_canonical` 을 **그대로 재사용**한다
  — 별칭표(`summary_parser.py:29-33`)와 번호·굵은 글씨 제거(36-39)가 메일과 한 곳이다.
  최상위 깊이보다 깊은 제목(`#### …`)은 상위 절 **본문으로** 들어가므로(908-910) 하위 제목 아래 내용이
  고정 절 목록에서 사라지지 않는다.
- `test_review_handoff.py:202-222` 가 실제 앱을 돌려 `### **1. 개요**`(굵게+번호) · `### 방법` + `#### 학습 과정`
  (하위 제목) · `### 주요 결과` · `### 한계` 네 경우의 **값**이 화면 markdown 에 남는지 본다.
  별칭 정규화를 빼면 `_DETAIL_SECTIONS`(`review_app.py:1035-1036`)의 키와 안 맞아 카드가 비고, 하위 제목 처리를
  빼면 `관측으로 학습` 이 사라진다.
- 근거 유형: 정적 확인.

### 2.6 운영 화면 개요의 용어 계산 생략 (확인됨)

`trend_report.window_movement`(`trend_report.py:130-184`)의 `top_terms > 0` 조건이 175-176 에 있고,
`ops_dashboard.py:830` 이 `top_terms=0` 으로 부른다. `test_review_handoff.py:191-199` 가
`emerging_terms` 를 `pytest.fail` 로 덮어 **불리면 실패**하게 만든다 — 호출 유무를 직접 본다.

## §3. 우선순위 2 — 메일 카드·상세

### 3.1 같은 카드 안, 기본 닫힌 토글 (확인됨)

- **새 메일**: `digest.py:2132` `open_attr = ""` 가 상수다. 조건 분기가 없으므로 검증 flag·재현 실패와 무관하게
  늘 닫힌다. 토글은 카드 `div` 안의 `<details>` 하나(`digest.py:2180-2187`)이고 반응 버튼·원문 링크는
  그 **밖**(2188 `foot`)이다.
- **저장 메일**: `saved_digest._card`(`saved_digest.py:70-105`)가 카드 줄을 `before`/`analysis`/`after` 로 가른다.
  경고(`[⚠ …`)는 `line.startswith("[")` 이고 `[S…` 가 아니므로 `in_after` 를 켜고(89-90), 분석 시작
  판정(86-88)보다 뒤에 와도 분석이 토글 밖으로 새지 않는다. 반응 버튼은 101-102, 즉 `</details>` 뒤다.
- 테스트:
  - `test_mail_detail.py:85-97` 이 `saved_digest.render_html` 로 5편 카드를 만들고
    `toggles == 5` · `open_toggles == 0` · `[card['toggles'] …] == [1,1,1,1,1]` · 각 카드 토글 안 글자가
    그 카드 것인지(`cards[i]['body']` 비교) · `분석 대상 5` 가 토글 밖에 없는지를 본다.
    `Regions.owner()`(`test_mail_detail.py:65-67`)가 **실제 부모 스택**에서 `ph-card` 를 찾으므로
    "직전 카드 번호"로 때우지 않는다 — 카드 밖으로 새는 분석을 잡는 설계다.
  - `test_mail_detail.py:70-82` 가 경고가 앞에 온 경우에 `html.index('original-token') > html.index('</details>')`
    로 반응 링크가 토글 밖임을 **위치로** 확인한다.
  - `test_mail_ui.py:67` 이 새 메일(`digest.generate_digest_html`)과 저장 메일 **양쪽**에서
    `toggles == 1 and open_toggles == 0` 을 본다.
- 근거 유형: 정적 확인.

### 3.2 실제 표의 항목명 중앙 정렬 (확인됨)

- `mail_document.section`(`mail_document.py:57-67`)이 한 `<tr>` 에 `td.ph-label` + `td.ph-detail-body` 를 낸다.
  라벨 칸은 `align="center" valign="middle"` **속성**과 `text-align:center;vertical-align:middle` **인라인 스타일**을
  둘 다 들고, 안쪽 `div.ph-section-title` 에도 `text-align:center` 가 있다. 본문 칸은 `align="left" valign="top"`.
  `mail_document.document`(70-76)이 모든 행을 `table.ph-document` **하나**로 묶고 `table-layout:fixed` 다.
- 테스트:
  - `test_mail_table.py:11-37` 이 `<style>` 블록을 **지운 뒤** 파싱해서 표가 1개(`ph-document`),
    `ph-section` 행 6개, 각 행의 자식 `td` 가 정확히 2개이고 부모가 같은 `tr` 인지, 속성·인라인 스타일·bgcolor 가
    맞는지, 그리고 `display:inline-table` 이 없는지(중첩 표 배치로 되돌리는 변이)까지 본다.
  - `test_mail_ui.py:71-100` 이 저장/새 메일 양쪽에서 `len(labels) == len(bodies) == 6` 과
    `label['parent'] is body['parent']` 를 본다 — 라벨을 본문 위로 쌓으면 실패한다.
  - `test_mail_ui.py:114-125` 가 본문이 길 때(`'긴 설명 ' * 60`) 세로 중앙을 따로 본다.
  - 초록 2편은 3행(`test_mail_table.py:40-60`), 원문은 6행(`test_mail_detail.py:104-111`).
- 근거 유형: 정적 확인.

### 3.3 전문 섹션명 (확인됨)

- 이름 변환은 `mail_document._LABEL_NAMES`(`mail_document.py:11-27`) **한 곳**이고,
  `heading_name()`(38-40)·`nominal_line()`(43-45)이 그 표만 쓴다. 저장 분석 **키는 바꾸지 않고**
  표시 이름만 바꾼다(docstring).
- 세 소비자가 같은 표를 쓴다: 새 메일 `mail_document.section`(63), 저장 메일 `saved_digest._line` →
  `mail_document.from_lines`(79-90), UI `review_app._summary_line_html`(1043) · `review_app.py:1099`.
- 테스트: `test_paper_sections.py:31-48` 이 저장/새 메일 양쪽에서 `section_titles == NAMES`
  (`연구 개요·방법론·실험 구성·주요 결과·연구 한계·분석 메모`)를, `test_paper_sections.py:114-135` 가
  UI 에서 `rm-ct'>{label}<` 형태로 같은 여섯 이름을 본다. 평문 저장 → 재파싱 왕복은
  `test_paper_sections.py:50-62`.
- 알 수 없는 라벨은 보존된다: `from_lines`(81-87)가 `_HEADING_RE.fullmatch` 에 안 걸리는 줄을 현재 그룹
  본문으로 넘기고, `test_mail_detail.py:129` 가 `알 수 없는 메모 : 그대로 보존` 을 확인한다.
- 근거 유형: 정적 확인.

### 3.4 작은 근거 footer (확인됨)

- `mail_document.evidence_footer`(`mail_document.py:48-54`)가 `_REF_RE`(`[S숫자]`)만 모아 중복 제거하고
  `font-size:11.5px` 짜리 `div.ph-evidence` 하나를 낸다. 근거가 없으면 `""` — 빈 상자를 안 남긴다.
  **원래 문장의 S번호는 지우지 않는다**(본문은 별도로 그려진다).
- 두 경로 모두 표 **뒤**에 붙는다: 새 메일 `digest.py:2056`
  (`mail_document.document(out) + mail_document.evidence_footer(refs)`), 저장 메일
  `mail_document.py:90`(`preamble + document(rows) + evidence_footer(...)`), UI `review_app.py:1110`.
- 테스트 `test_paper_sections.py:65-79` 가 ① footer 가 1개이고 `div` 이며 11.5px ②
  `html.index('class="ph-evidence"') > html.index('</table>')` 로 위치 ③ `[P1:R]`·`[fake]` 가 근거로
  올라오지 않음 ④ 근거가 없으면 `ph-evidence` 자체가 없음을 본다.
  `test_paper_sections.py:36-43` 이 **저장·새 양쪽 html 모두**에 footer 1개를 요구한다 —
  10/2 보고서가 "새 생성 경로 footer 삭제 변이가 처음 살아남았다"고 적은 그 구멍을 메운 자리로 보인다.
- 근거 유형: 정적 확인.

### 3.5 조건·출처 없는 성능 비교를 새로 만들지 않는가 (확인됨)

표시 게이트는 `paper_observations.comparison_rows`(`paper_observations.py:33-62`) 하나다. 통과 조건 전부:

- 대상(자기) 셀: `benchmark·metric·text·model·locator` 가 모두 있고(38-39) 값이 **유한한 수**이며
  bool 이 아니다(40-42).
- 상대: `status == 'verified'` · `same_conditions is True` · `differences` 없음 ·
  `unverified_differences == 0` · `conditions_not_checked` 없음 · 조건 수 ≥ `REQUIRED_CONDITIONS` 수 ·
  값이 유한수 · `text·model·locator·source_url` 모두 있고 `source_url` 이 `https://` 로 시작 ·
  `external_evidence.comparable_values(...)` 가 `None` 이 아님(50-56).
- `comparable_values`(`external_evidence.py:226-233`)는 비율 지표가 아니면 척도를 **추측하지 않고 거부**한다.
- 통과한 행의 글자는 전부 저장값(`model`·`text`·`locator`·`matched_conditions`)을 그대로 이어 붙인 것이고
  (58-62) **우열 낱말이 없다**. `digest._comparison_block_html`(`digest.py:597-605`)은 그 행을
  `mail_document.section("성능 비교", …)` 로 기존 표 한 행에 넣기만 한다 — 새 수치를 만드는 코드가 없다.
- 저장 관측 읽기(`stored_comparison_rows`, 11-30)는 `mode=ro` + `PRAGMA query_only=ON` 이고,
  같은 날(`checked_on`)과 **유일한 자기 셀**(`len(own) != 1` 이면 포기)만 쓴다.
- 테스트:
  - `test_paper_sections.py:100-111` 이 10가지 변형(조건 불일치·조건 차이·미대조·부분 조건·중복 조건·
    미검증·값 없음·NaN·문자열 조건)마다 `성능 비교` 가 **아예 안 생기는지** 본다.
  - `test_paper_sections.py:203-208` 이 RMSE 척도 차이를 따로 본다.
  - `test_paper_sections.py:82-97` 이 정상 케이스에서 절 **순서**(주요 결과 → 성능 비교 → 연구 한계)와
    `최고`·`우수`·`향상 확인` 이 없음을 본다.
  - `test_paper_sections.py:150-162` 이 실제 DB 를 만들어 `?mode=ro` · `uri=True` ·
    `PRAGMA query_only == 1` · **파일 바이트 불변**을 확인한다.
  - `test_paper_sections.py:177-200` 이 DB → `paper_detail` → `review_app.render_paper_detail` 전 사슬을 돈다.
- 근거 유형: 정적 확인.

## §4. 우선순위 3 — 선택적 과거 흐름

### 4.1 강제 연결이 없는가 (확인됨)

- `trend_history.catalog`(`trend_history.py:12-43`)는 **후보만** 만든다. 저장된 `audit_json` 의
  `story-v3` 구조를 우선 쓰고 없으면 `trend_report.parse_rendered_story` 로 되읽으며,
  `reader_date` 가 ISO 날짜로 안 읽히는 행은 건너뛴다(17-20). 옛 `[P번호]` 태그는 32줄에서 제거하므로
  과거 P번호가 오늘 근거로 새지 않는다.
- 연결 검증은 `trend_report.repair_story`(`trend_report.py:515-525`) 하나다. `thread["history"]` 는
  다음을 **모두** 만족할 때만 생긴다:
  1. `proposed['ref']` 가 실제 catalog 에 있다(518) — 없는 H번호는 버린다.
  2. `relation` 이 `trend_history.RELATIONS`(`continuing/expanding/branching`) 안이다(520) — `new` 는 거부.
  3. `note` 가 **오늘 그 흐름의 논문**을 인용한다(`_fit(…, set(papers), …)` 의 `cited_now` 비어 있으면 거부, 519-520).
  4. `note` 에 `증가|감소|급증|급부상|쇠퇴|최초` 나 `숫자+%/배/편/건` 이 없다(521) — **새 수치·증감 판정 금지**.
  - 아니면 `repairs.append("history_unverified")` 로 기록만 하고 연결을 **붙이지 않는다**.
- `source` 는 `dict(source)`(523), 즉 저장 기록 사본이다. 표시 문장
  `trend_history.display`(`trend_history.py:56-61`)의 날짜·흐름 이름은 전부 그 사본에서 오고
  끝에 `(해석)` 이 붙는다. `trend_report.model_text`(576-586)에는 **날짜가 들어가지 않는다** —
  숫자 대조기가 과거 날짜를 오늘 원문에서 찾다가 실패하는 일을 막는다.
- 표시는 본문과 분리된다: `render_story`(600-602)가 별도 줄로 내고
  `parse_rendered_story`(1620-1621)가 `history_note` 키로 되읽고, UI `review_app.py:761` 이
  `div.rm-history` 로 본문 위에 `_h()` escape 해 그린다.
- 테스트:
  - `test_trend_history.py:19-28` — 저장 구조 우선·옛 본문 되읽기·날짜 불량 행 제외·`[P` 미유출.
  - `test_trend_history.py:31-40` — 원본 날짜·`(해석)` 표시·`model_text` 에 날짜 없음·본문과 분리.
  - `test_trend_history.py:43-55` — **7가지** 거부 케이스(null·없는 H번호·오늘 흐름 밖 근거·다른 흐름 논문 근거·
    허용 밖 relation·정량 낱말·정량 수치)에서 `"history" not in story["threads"][0]` 이고
    `지난 관측`·`새 흐름` 이 글에 없으며 **본문은 남는지**(`선택과 탐색`)를 본다.
  - `test_trend_history.py:58-77` — 생성 경로가 H후보를 프롬프트에 싣고 저장 구조를 내는지,
    구형 v2 의 빈 칸이 `null` 로만 보완되는지.
  - `test_trend_history.py:80-88` — UI escape·본문과 순서 분리.
- 요약: **매 메일에 억지로 같은 주제를 이어붙이는 경로를 찾지 못했다.** 모델이 연결을 안 내면(null) 그대로
  없고, 내더라도 네 조건을 다 통과해야 표시된다.
- 근거 유형: 정적 확인.

## §5. 이번 두 표시 변경

### 5.1 「최근 N일 흐름」부터 메일 끝까지, 흰 영역 안쪽 여백 (확인됨)

- 여백 값은 한 곳이다 — `digest._BLOCK_PAD = "12px 14px"`(`digest.py:1247`)와 `_block()`(`1250-1258`).
  내용이 비면 `""` 를 돌려 **빈 흰 상자를 남기지 않는다**.
- 흰 블록으로 묶이는 자리 전부: `_window_html`(`1276` 창 자료가 없어 자리 밖 후보만 있는 경우 · `1328` 본 절) ·
  `_external_scout_html`(`1519`) · `_profile_changes_html`(`1604`) · 맨 아래 걸러진 줄(`2799-2802`,
  `padding:10px 14px 0` — 위 가는 줄은 바깥 폭 그대로 긋고 글자만 들인다). 그 아래
  `_profile_update_html`(`1670-1671`)은 이번 변경 전부터 `padding:9px 14px` 였다.
- 바깥 폭으로 푼 것이 아니다 — `_block` 은 `mail_layout.frame` 의 폭을 참조하지 않는다.
- 테스트 `test_mail_canvas.py:135-167` 은 `digest.generate_digest_html` 이 **실제로 낸 HTML** 을 파싱한다
  (`Tree`, `54-78`). 절 제목 `<p>` 의 **다음 형제**가 흰 블록인지, `_side_padding`(`92-112`)이 style 에서 읽은
  좌우 값이 12px 이상인지, 본문 글자(`상승`·`에이전트가 놓친 연구`·`가중치 변화`)가 그 블록 **안**인지를
  세 절에서 보고 꼬리 줄(`165-167`)도 같은 조건으로 본다. 여백을 테스트 안에서 다시 계산하지 않고
  나온 style 만 읽는다. `149` 의 `max-width:1056px`·`1040px` 이 "여백 대신 바깥 폭 늘리기" 변이를 잡는다.
- `test_mail_canvas.py:170-173` 이 `_block("") == ""` 로 빈 절의 빈 상자를 막는다.
- 근거 유형: 정적 확인.

### 5.2 꼬리의 앞 문구만 삭제, 반응 건수 유지 (확인됨)

- `_changes_tail`(`digest.py:1607-1620`)에 남은 항목은 `reactions_used` 하나다. `hidden` 인자는 받되 쓰지 않는다 —
  **집계는 그대로 돈다**(`_split_moves` `1411-1418` 이 계속 세고, 가중치·revision 은 DB·운영 화면에 남는다).
- 평문(`1738`)과 HTML(`1600`)이 같은 함수를 부른다 — 두 판이 갈라질 자리가 없다.
- 빈 절 방지가 같은 함수에 묶였다: `_has_auto_change`(`1472-1487`)가 `hidden` 을 참으로 세는 대신
  `_changes_tail(ch, hidden)` 의 결과를 본다. 작게 움직인 것만 있고 반응이 0 이면 `False` 라
  `_profile_changes_html`(`1527`)·`_profile_changes_section`(`1686`)이 절을 통째로 뺀다.
- 저장 메일은 **표시에서만** 지운다: `_HIDDEN_MOVES_RE`(`saved_digest.py:47`) → `_without_hidden_moves`(`50-52`)
  → `_line`(`55-59`). 그 문구뿐이던 줄은 `""` 라 빈 칸도 안 남는다. 저장 평문·DB 를 되쓰는 코드는 없다 —
  재표시 경로 `mail_reformat.load_snapshot` 은 `mode=ro` + `PRAGMA query_only=ON`(`mail_reformat.py:20-21`)이다.
- 테스트 `test_weekly_profile_changes.py:383-399` 는 감춘 수 1·`delta 0.05` 가 **남아 있는지** 먼저 보고,
  평문·HTML 양쪽에 문구가 없고 `반영된 사용자 반응 3건` 이 있는지 본다. 3 은 픽스처 입력
  (`_only_small_moves(..., reactions=3)`)에서 온 값이라 건수를 고정값으로 바꾸는 변이도 걸린다.
  `402-412` 는 반응 0 인 주에 `_has_auto_change is False`·평문 `[]`·HTML `""` 를, `415-424` 는 저장 줄
  재표시에서 앞 문구만 빠지고 `15건` 이 남는 것·그 문구뿐이면 빈 칸도 없는 것을 본다.
- 근거 유형: 정적 확인.

## §6. 지적 정리와 남은 것

**P1 · P2 — 없다.** §5 에서 읽은 범위에서 표시 변경이 생산 경로를 우회하거나 집계·저장을 지운 자리를 찾지 못했다.

- **P3-1. 다시 그린 메일의 평문 쪽에는 옛 문구가 남는다.** `mail_reformat.py:51` 이 `"text"` 로 저장 원문을
  그대로 돌려주고 `_line` 을 거치는 것은 `"html"`(`50`)뿐이다. 기록을 고치지 않는다는 결정과는 일관되지만,
  평문만 보는 클라이언트에는 그 문구가 간다. 트리거: 옛 날짜 본문을 `mail_reformat` 으로 재발송할 때.
- **P3-2. 문구가 줄 끝에 온 경우 구분자가 남을 수 있다.** `_HIDDEN_MOVES_RE`(`saved_digest.py:47`)는 문구
  **뒤**의 ` · ` 만 먹고 앞의 구분자는 먹지 않는다 — `… 반응 15건 · 그 밖에 …` 순서의 저장 줄이면
  `반응 15건 ·` 로 끝난다. 지금 생성기는 꼬리를 `_changes_tail` 한 곳에서만 만들고 거기엔 그 문구가 이미
  없어, 옛 본문의 실제 순서는 작업 트리에서 확인할 수 없다(미확인 — DB 를 열지 않았다).
- **P3-3(§2.5 요약). 하위 제목 노출.** `ops_dashboard.py:908-910` 이 `####` 줄을 접두사째 상위 절 본문에
  넣는다. 고정 절 목록에서 내용이 사라지지 않게 하려는 선택이고 값 손실은 없다. 화면에 기호가 어떻게
  그려지는지는 실행하지 않아 미실측이다.
- **P3-4(§2.3 요약). 조회 helper 의 읽기 전용 확대 제안.** 운영 개요 하위 연결은 모두 `mode=ro` 인데
  `ops_dashboard.profile_overview`(`159`)·`weight_history`(`183`)·`keyword_table`(`205`)·
  `issues_with_reactions`(`246`)·`reaction_log`(`262`)·`revision_history`(`298`)·`agent_history`(`325`)·
  `trend_report.collection_rows`(`1067`)는 쓰기 가능 연결로 연다. **쓰기가 일어난 것을 재현한 결함이 아니다** —
  조회 전용 helper 를 같은 기준으로 넓히자는 제안이고, 10/2 문장을 "화면 전체가 읽기 전용"으로 읽지 말자는 표시다.

**남은 것 · 미실측**

- 이 세션은 pytest·브라우저·메일·DB·네트워크를 **실행하지 않았다**. 인용 가능한 수치(전체 1,486 passed /
  35 warnings / 112.19초, 허용 목록 사본에서 변이 8개 전부 FAILED, 합성 HTML Chromium 5조건에서 좌우 14px ·
  가로 넘침 0 · 문구 없음 · 반응 15 보존)는 **다른 세션·주 도구의 기록**이다 —
  `data/mail_previews/claude_padding/` 의 세 JSON 과 `docs/CODEX_HANDOFF_EXECUTION_2026-10-06.md`.
- 실제 Gmail·Outlook 수신 화면은 미실측이다(합성 HTML 기준 확인만 있다).
- `test_mail_canvas` 의 여백 판정은 **인라인 style 문자열**만 읽는다. 지금 구현이 인라인이라 맞지만,
  여백을 `<style>` 규칙으로 옮기면 그 테스트는 못 본다.
- 옛 저장 본문에 그 꼬리 문구가 몇 줄 남아 있는지는 세지 않았다(DB 미접속).
- §0 이 적은 `.part3` 임시 파일은 지금 `docs/` 목록에 **없다** — 삭제가 끝났다.


---

저장 경위(주 도구 기록): §0~4는 독립 검토 Claude가 직접 저장했다. §5~6은 이어쓰기 Claude의 실제 Edit 입력 원고를 그대로 저장한 것이다. 이어쓰기 세션은 검토를 마치고 exit0으로 반환했지만, 주 도구가 설정한 로컬 CLI의 dontAsk/허용 도구 조합이 Edit를 거부해 파일 저장은 실패했다. 사용자에게 추가 승인을 요구할 사안이 아니라 위임 도구 설정 문제였으므로, 기존 사용자 승인 범위 안에서 주 도구가 반환된 원고를 저장했다. §0의 한 줄 .part3 임시 파일은 주 도구가 삭제했다.
