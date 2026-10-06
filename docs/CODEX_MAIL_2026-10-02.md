# 메일 가독성과 선택적 과거 연결 — 2026-10-02

## 결정과 범위

사용자 요청: UI처럼 읽기 쉬운 메일, 짧은 흐름의 표 기반 두 칸 또는 구역 상자, 기존 논문 상세 토글 보존. 지난 흐름은 오늘 연구 문제와 연결하는 것이 도움이 될 때만 표시한다. 일일 7일 증감 표는 추가하지 않는다.

오늘 AI 에이전트 메일의 저장 내용을 형식만 바꿔 원래 수신자인 사용자에게 한 통 재발송한다. 다른 프로필·수신자로 확대하지 않는다. 커밋·push 없음.

검증된 구조는 기존 audit_json에 저장하여 스키마 변경 없이 재사용한다. 새 과거 연결은 존재하는 H번호와 오늘 흐름의 P근거가 있어야 하고 해석으로 표시한다. null은 표시하지 않는다. 오늘 재발송에는 새로 생성한 과거 해석을 넣지 않는다.

## 구현된 배치와 시간 연결

- `mail_layout`의 720px 바깥 표·680px 본문과 340px 칸을 쓴다. 짧은 두 흐름/보조 절만 두 칸, 긴 절과 논문 상세는 전체 폭이다. 좁은 화면은 inline-table의 줄바꿈과 미디어 쿼리로 한 열이다. Outlook용 조건부 표도 넣었다.
- 오늘의 한 줄·흐름·우리 연구에서 볼 것·주변 신호는 서로 다른 박스다. 제목 목록에 원문 링크를 붙이고 중복 링크 목록을 줄였다. P근거는 내용 그대로 작고 흐린 글자로 남겼다. 상세 분석 토글·반응 버튼은 보존한다.
- story-v3의 흐름별 `history`는 null 또는 `{ref,relation,note}`다. 허용 관계는 이어짐·확장·갈라짐이다. 없는 H번호, 오늘 해당 흐름 밖 P근거, 근거 없는 노트, 정량 시간 표현을 제거한다. 연결이 없다고 새 흐름으로 판정하지 않는다. 의미 연결 자체는 모델의 해석이며 사실 확인을 대신하지 않는다.
- 검증된 구조를 기존 `profile_narratives.audit_json.story`에 저장한다. 다음 입력은 같은 프로필의 최근 6일 관측 최대 12개 흐름이다. 옛 story-v1/v2는 기존 표시 파서로 복원한다. H번호는 이번 프롬프트의 주소이며 원본 날짜·narrative_id·thread_index를 같이 저장한다. 기존 네 항목 narrative 반환 계약은 유지한다.
- 저장 평문 전용 렌더러는 수치·요약을 재생성하지 않는다. 원 회차와 카드 수·순번·제목을 대조하며 원 수신자의 기존 반응 토큰을 그대로 쓴다. 원 DB는 URI mode=ro·query_only로 읽는다. 오늘 제목만 8편이라는 상단 값은 사용자가 붙인 원래 메일에서 확인했다(평문에는 없어 명시 인자로 복원).

메일 배치 참고: [Campaign Monitor](https://www.campaignmonitor.com/dev-resources/guides/mobile/), [Gmail 공식 CSS 문서](https://developers.google.com/workspace/gmail/design/css). 이 자료로 클라이언트별 실제 렌더링을 보장하지는 않는다.

## 검증 진행 기록

기존 기대값은 바꾸지 않았다. 새 테스트의 첫 실행에서 잘못 넣은 반응 action(`like`, 운영 계약은 `more/useful/out`)과 파서가 정리한 구두점 공백 기대값 두 개를 바로잡았다. 검사 조건을 삭제하거나 코드 결함을 허용하도록 약화하지 않았다. 1차 관련 표적 테스트는 258 passed(2.77초)다. 최종 검증·변이는 아래에 추가한다.

Chromium은 libnspr4/libnss3/libasound 누락으로 처음 실행에 실패했다. apt 패키지 세 개를 /tmp에만 다운로드·해제하여 시스템 설치 없이 오프라인 캡처했다. 1100px 화면에서 두 흐름 x=206/546, y=451.5, 폭 각340px였고, 390px 화면은 x=18, y=522/1040.75로 세로 배치됐다. 두 폭 모두 가로 넘침 false였다. 이후 상단 지표·근거 글자 크기 변경을 했으므로 최종 캡처 측정도 아래에 갱신한다. Gmail·Naver·Outlook 실제 수신 화면은 미실측이다.

### 최종 코드 검증

`.venv/bin/python -m pytest`를 인자 없이, 환경 보조 코드 없이 실행했다: **1,417 passed, 35 warnings, 104.49초, exit 0**. 기존 `review_app.py` 스타일 문자열의 `\m` SyntaxWarning 35건이다. 새 검사 25개는 mail_layout 8개, trend_history 11개, mail_reformat 5개, scan 실제 저장 경로 1개다.

저장소 소스/테스트를 /tmp로 복사하고 기준선 통과 후 한 변이씩 넣었다. 21개 모두 정상 수집 뒤 테스트 실패(exit 1)로 검출됐다. 운영 파일은 변이하지 않았다. 초기 fixture에서 feedback.issue_links의 위치 인자를 잘못 넣어 생긴 실패는 발송 API의 명명 인자로 바로잡았으며, 이를 제품 회귀 성공으로 세지 않았다. 기존 프롬프트 회귀가 요구한 증가·전환·부상 일반화 금지 문구는 제품에 보존했다. 기존 테스트 기대값은 변경하지 않았다.

| 변이 | 지킨 회귀 |
|---|---|
| unknown_history | test_trend_history.py::test_no_connection_is_invented_for_missing_or_unverified_history[connection1] |
| uncited_history | test_trend_history.py::test_no_connection_is_invented_for_missing_or_unverified_history[connection2] |
| quantitative_history | test_trend_history.py::test_no_connection_is_invented_for_missing_or_unverified_history[connection5] |
| hidden_history | test_trend_history.py::test_verified_connection_has_original_date_and_stays_an_interpretation |
| unchecked_history_numbers | test_trend_history.py::test_verified_connection_has_original_date_and_stays_an_interpretation |
| lost_stored_structure | test_briefing.py::test_scan_stores_verified_story_structure_in_existing_audit_json |
| unsafe_ui_history | test_trend_history.py::test_ui_escapes_history_and_shows_it_separately_from_the_body |
| narrow_long_text | test_mail_layout.py::test_short_threads_pair_and_long_threads_keep_full_width |
| lost_desktop_columns | test_mail_layout.py::test_short_threads_pair_and_long_threads_keep_full_width |
| lost_mobile_css | test_mail_layout.py::test_short_threads_pair_and_long_threads_keep_full_width |
| unsafe_saved_analysis | test_mail_layout.py::test_saved_render_keeps_all_analysis_and_old_feedback_outside_toggle |
| lost_analysis_toggle | test_mail_layout.py::test_saved_render_keeps_all_analysis_and_old_feedback_outside_toggle |
| unchecked_card_count | test_mail_layout.py::test_saved_render_rejects_count_or_position_mismatch[\uc5f0\uad6c \ub3d9\ud5a5 \ube0c\ub9ac\ud551 \xb7 2026-10-02\n\u25a0 \uc624\ub298\uc758 \uc5f0\uad6c \ud750\ub984\n   \u25a0 \uc624\ub298\uc758 \ud55c \uc904\n   \uc624\ub298 \uc694\uc9c0 [P1:A]\n   \u25a0 1. \uccab \ud750\ub984\n   \uc5ed\ud560\uc744 \ube44\uad50\ud55c\ub2e4 [P1:A][P2:A]\n   - One & Paper [P1:A]\n   \u25a0 2. \ub450 \ubc88\uc9f8 \ud750\ub984\n   \ub2e4\ub978 \ubc29\ubc95\uc744 \ubcf8\ub2e4 [P2:A][P3:A]\n   - Other paper [P3:A]\n   \u25b8 \uc704\uc5d0\uc11c \uc774\ub984\uc73c\ub85c \ubd80\ub978 \ub17c\ubb38\n      \xb7 Other paper (\ub2e4\ub978 \ub17c\ubb38) \u2014 https://arxiv.org/abs/2609.00003\n\u25a0 \uc624\ub298\uc758 \ud575\uc2ec \ub17c\ubb38 2\ud3b8 (\uc804\uccb4 \ud6c4\ubcf4 9\uac74 \uc911)\n1. One & Paper (\uccab \ub17c\ubb38)\n   \ud575\uc2ec \ud0a4\uc6cc\ub4dc: agent\n   \uc694\uc9c0 \uc22b\uc790 76.3%\n   \uc77d\uc744 \ud3ec\uc778\ud2b8 \u2014 \uc870\uac74\n   \ubb34\uc5c7\uc744\xb7\uc5b4\ub5bb\uac8c :\n     - \uc800\uc7a5 \ubd84\uc11d <unsafe> & 42\n   \ud575\uc2ec \uacb0\uacfc :\n     - \uc815\ud655\ub3c4 76.3%, \ube44\uad50 3.7% [S0012]\n   [\uc6d0\ubb38 \ubd84\uc11d \uc644\ub8cc] [\uc7ac\ud604 \uc2e4\ud328]\n   https://arxiv.org/abs/2609.00001\n\u25a0 \uc774\ubc88 \ucc3d\uc758 \ud0a4\uc6cc\ub4dc\ubcc4 \uc801\uc911 \ud3b8\uc218 (\ud6c4\ubcf4 9\uac74 \uae30\uc900)\n   agent 3\uac74\n\u25a0 \uc774\ubc88 \uc2e4\ud589\uc5d0\uc11c \uac78\ub7ec\uc9c4 \uac83: \uc774\ubbf8 \ubcf4\ub0b8 \ub17c\ubb38 2\uac74\n] |
| unchecked_issue_titles | test_mail_layout.py::test_saved_render_rejects_wrong_issue_papers - Fa... |
| wrong_plain_content | test_mail_reformat.py::test_snapshot_reuses_recipient_tokens_and_keeps_database_unchanged |
| renewed_feedback_token | test_mail_reformat.py::test_snapshot_reuses_recipient_tokens_and_keeps_database_unchanged |
| duplicate_resend | test_mail_reformat.py::test_preview_sends_nothing_and_send_backs_up_once_for_one_recipient |
| lost_second_title_link | test_mail_layout.py::test_inline_links_cover_multiple_overlapping_titles_without_touching_markup |
| prefix_title_mismatch | test_mail_layout.py::test_saved_titles_must_match_the_whole_original_title |
| writable_snapshot | test_mail_reformat.py::test_snapshot_reuses_recipient_tokens_and_keeps_database_unchanged |
| lost_query_only | test_mail_reformat.py::test_snapshot_reuses_recipient_tokens_and_keeps_database_unchanged |

최종 오프라인 캡처: 1100px에서는 첫 흐름 두 칸의 y=487, x=206/546, 폭340px이다. 390px는 x=18, y=583/1087.75, 폭354px이고, 320px는 x=18, y=608.5/1206.64, 폭284px이다. 모두 가로 넘침 false. style 블록을 지운 320px도 x=24, y=640/1243.75, 폭272px로 세로 배치·넘침 없음이었다. 수신 클라이언트 렌더링을 대신한 실측은 아니다. 캡처/측정은 data/mail_previews에 있다.

Streamlit은 정확한 argv·저장소 cwd로 찾은 PID3474를 종료하고 setsid nohup으로 PID17791을 띄웠다. `/`와 `/_stcore/health` 모두 HTTP200이다. 앞선 인계 보고서에서 환경 제약으로 남았던 실제 재기동·보조 코드 없는 전체 pytest 미확인은 이번 권한 있는 실행에서 해소됐다.

### 오늘 저장 본문 재발송

원 회차 `team_agent:20261001T202023:8e02e7`, 카드5편. 저장 평문 SHA-256은 `9bf695fc863df320ec05e31ae3386f73e7ce0833a2f6844dd6a9499ebb033c7c`다. 미리보기 HTML은52,779바이트이다. 메일 발송 결과는 아래에 추가한다.

SMTP가 **2026-10-02 10:56:05 KST**에 형식 수정본 한 통을 수락했다. 사용자가 붙인 원래 메일의 수신자인 Naver 주소 한 명으로만 발송했다. 제목은 원래 제목 뒤에 `[형식 수정본]`을 붙였다. 새 스캔·LLM 호출·새 과거 연결·새 반응 토큰·새 DB 회차는 없다. 최종 링크 감사 경로(email_delivery.send_digest_email)를 거쳤다. 수신함 도착 자체는 미실측이다.

백업: `data/mail_previews/team_agent_2026-10-02_reformatted.backup.db`, integrity_check=ok. 발송 뒤에도 평문 SHA-256은 같다. 백업/현재 DB 행 수는 feedback_tokens360/360, feedback_events5/5, mail_issues56/56, profile_narratives49/49다. 이 행 수 대조는 전체 DB의 바이트 단위 동일성을 증명하는 것은 아니다. 발송 성공 기록은 같은 디렉터리의 `.sent.json`이며 반복 --send는 중복 발송을 거부한다.

## 지적 목록과 재현

아래 파일:줄은 최종 코드의 관련 위치다. P2는 기능·계약 결함, P3는 요청된 가독성 개선이다. 새로 만든 재배치 경로에서 점검 중 발견한 두 문제도 적용 전에 수정했다.

| 등급 | 관련 위치 | 수정 전 재현/근거 | 수정·검사 |
|---|---|---|---|
| P2 | trend_report.py:306, run_profile_scan.py:444 | 지난 6일 글은 입력인데 결과 구조에 지난 흐름 연결 칸이 없고 과거 비교 근거가 없다는 모순된 지시가 남음 | 선택적 history/H후보·오늘 P검증, 기존 구조 보관; generation/validation/scan 저장 테스트 |
| P3 | digest.py:2384, mail_layout.py:14 | 오늘 원 메일의 흐름·적용점·주변 신호가 연속 div로 이어지고 별도 링크 목록에서 논문 이름 반복 | 표 기반 짧은 두 칸·개별 박스·목록 인라인 링크; production HTML 테스트와 화면 폭 실측 |
| P2 | digest.py:2410 | 새 인라인 링크 구현 첫판에서 한 줄에 원제 두 개를 넣으면 첫 제목만 링크됨 | 텍스트 노드별 긴 제목 우선 일괄 치환; overlapping titles 고정값 테스트/변이 |
| P2 | saved_digest.py:89 | 새 회차 대조 첫판에서 One을 One & Paper의 제목으로 허용(startwith만 확인) | 원제 전체 또는 원제+한국어 괄호만 허용; prefix mismatch 테스트/변이 |

## 남은 쟁점·미실측

- 오늘 재발송은 과거 연결을 새로 생성하지 않았다. 새 기능의 실제 모델 출력·유용성·의미 정확성은 다음 실행에서 확인할 항목이다. H/P의 존재·소속 확인은 문장 의미가 참이라는 보증이 아니다.
- 원래 회차의 HTML 자체는 DB에 저장돼 있지 않다. 저장 평문과 발송 명세를 기준으로 내용/수치를 보존해 재배치했다. 오늘 상단 제목만8편 값은 사용자가 준 원래 메일에서 복원했다.
- 과거 후보는 같은 프로필의 최근6일·최대12흐름이다. 전체 연구 분야의 증감이나 더 긴 기간의 추세를 판정하지 않는다.
- Gmail·Naver·Outlook에서 실제 메일 렌더링/반응 클릭은 미실측이다. Chromium 오프라인 배치와 SMTP 수락만 측정했다.
- 인계 보고서의 서술 총량1,200자 목표 초과 문제는 이번 배치 수정으로 문장을 임의 삭제해 해결하지 않았다. story-v3를 포함한 의미 보존 총량 예산은 별도 쟁점이다.
- 원 DB DDL/대량 삭제/내용 수정은 하지 않았다. 커밋·push 없음. 사용자 기존 변경은 보존했다.

## 산출물

구현: digest.py, mail_layout.py, trend_history.py, trend_report.py, run_profile_scan.py, review_app.py, saved_digest.py, mail_reformat.py. 회귀: test_mail_layout.py, test_trend_history.py, test_mail_reformat.py, test_briefing.py의 새 저장 경로 검사. PROGRESS §207에 기록했다.

미리보기/캡처/백업/SMTP 성공 기록은 `.gitignore` 대상인 `data/mail_previews/`에 있다. 최종 문서 저장 뒤 git diff --check도 통과했다.
