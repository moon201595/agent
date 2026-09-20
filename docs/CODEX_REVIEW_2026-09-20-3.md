# 최종 확인 검토 — 2026-09-20

## 1. 셸·메일 달력 통일 — 닫힘

근거: `run_profile_scan.py:88-95`가 KST 날짜를 `work_calendar.is_weekly_day`에 전달하고 예외 때 월요일(`67`)로 돌아간다. 셸 CLI도 같은 함수로 weekly를 판정한다(`work_calendar.py:104-111`, `run_daily_scan.sh:54-55`). 메일 수집 호출은 `run_profile_scan.py:343-349`에 연결돼 있다.

실행: `PYTHONDONTWRITEBYTECODE=1 /home/mjh/paper-harness/.venv/bin/python - <<'PY'` 형태의 stdin 스크립트에서 실제 함수를 호출했다. `.env` 읽기를 막기 위해 config 최초 import 동안 해당 경로의 `Path.exists`만 False로 처리했다. 재현 핵심 호출은 다음과 같다.

```python
m = datetime.fromisoformat("2026-10-05T20:00:00+00:00")
print(wc.day_kind(wc.today(m)), rps.is_weekly_review_day(m))
# weekly True
```

| 입력 UTC → KST 05:00 날짜 | day_kind | 메일 주간 판정 |
| --- | --- | --- |
| 9/20 20:00 → 9/21 | weekly | True |
| 10/4 20:00 → 10/5 | skip | False |
| 10/5 20:00 → 10/6 | weekly | True |
| 10/6 20:00 → 10/7 | run | False |

`patch.object(wc, 'is_weekly_day', side_effect=RuntimeError('calendar unavailable'))`로 같은 입력을 다시 호출하면 메일 판정은 순서대로 `True, True, False, False`였다. 월요일 폴백이 맞다. `_holidays`만 빈 집합으로 바꾼 경우에도 양쪽은 10/5에 weekly/True, 10/6에 run/False로 일치했다(`work_calendar.py:28-34`의 공휴일 자료 부재 경로). 정상 달력에서 10/6·10/7 연속 주간 판정은 발생하지 않는다. 이는 날짜 판정 검증이며 같은 날 재실행의 발송 중복 방지를 검증한 것은 아니다.

## 2. 이전 주간 보고일 탐색 — 닫힘

근거: `weekly_profile_changes.py:155-161`이 첫 근무일을 판정하고 예외 때 전달받은 요일로 돌아간다. `_previous_cycle`은 `191-207`에서 최신순으로 읽고, 현재 날짜는 제외한 뒤 이 판정으로 이전 스캔을 고른다.

실행: `sqlite3.connect(':memory:')`에 `scan_runs(profile_id, started_at, profile_snapshot)`을 만들고, 9/28 월 05:00 KST(X 가중치 1.0)와 9/29 화 05:00 KST(X 가중치 1.2)를 UTC ISO로 넣었다. `patch.object(wpc.sqlite3, 'connect', return_value=con)`으로 연결만 메모리 DB에 돌렸다. 운영 `_rows`의 SQL 실행과 `_previous_cycle`은 그대로 호출했다(`weekly_profile_changes.py:46-50`). 디스크·운영 DB 쓰기는 없다.

```python
out = wpc._previous_cycle(Path('/unused-review-memory'), 'p',
                          datetime(2026, 10, 6, 5, tzinfo=KST), 7)
print(datetime.fromisoformat(out[0]).astimezone(KST).date(), out[1])
# 2026-09-28 {('X', 'core'): 1.0}
```

같은 DB에서 `is_weekly_day`에 RuntimeError를 주면 10/6 기준 선택은 `2026-09-29`였다. 요청한 **옛 같은 요일 폴백**이다. 10/12 월요일 기준은 정상/예외 양쪽 모두 `2026-09-28`이었다(이 DB에는 그 뒤 스캔이 없다).

추가 합성 DB 확인: 9/21 월·9/22 화를 넣고 9/28에 조회하면 `2026-09-21`, 9/28 월·10/6 화·10/7 수를 넣고 10/12에 조회하면 `2026-10-06`이었다. 일반 주와 휴일 다음 주 연결도 맞다. `patch.dict(sys.modules, {'work_calendar': None})`로 import 자체를 막아도, 메일은 10/5 True·10/6 False, `_is_report_day(day, 1)`은 10/5 False·10/6 True로 각자의 명시된 옛 규칙을 따랐다.

## 3. 번역 부분 성공 보존 — 닫힘

근거: `title_ko.py:76-90`은 첫 호출 예외에는 raise, 두 번째 예외에는 break 후 out 반환이다. 완료한 제목은 pending에서 빠진다(`86-89`). 호출자는 반환된 번역을 논문에 붙이고, 첫 호출 실패는 기존 예외 처리 후 다이제스트 생성으로 이어진다(`run_profile_scan.py:463-476`).

실행: 동일 stdin 실행기에서 `summarize_engine.complete`만 비동기 가짜로 교체하고 실제 `asyncio.run(tk.translate(object(), ['First paper', 'Second paper']))`를 호출했다. 부분 응답은 `1. 첫 번역`, 실패는 `RuntimeError`, 재요청 성공 응답은 `1. 둘째 번역`을 사용했다.

| 공급자 응답 조건 | 실제 반환/예외 | complete 호출 수 |
| --- | --- | --- |
| 첫 응답 일부 성공 → 재요청 실패 | `{'First paper': '첫 번역'}` | 2 |
| 첫 호출부터 실패 | `RuntimeError('first_failure')` 전파 | 1 |
| 첫 응답에서 두 제목 모두 성공 | `{'First paper': '첫 번역', 'Second paper': '둘째 번역'}` | 1 |
| 첫 응답 일부 성공 → 재요청 성공 | `{'First paper': '첫 번역', 'Second paper': '둘째 번역'}` | 2 |

두 번째 프롬프트에서 `First paper in prompt=False`, `Second paper in prompt=True`도 확인했다. 이미 성공한 제목을 다시 요청하지 않으며 새 번호로 받은 응답도 맞는 제목에 붙었다. 첫 호출 오류가 빈 dict로 숨겨지는 회귀는 없다. 실제 공급자·SMTP 발송은 미실측이다.

## 4. 셸 실패 폴백 — 닫힘

근거: `run_daily_scan.sh:54-55`는 달력 CLI 실패 시 `TZ=Asia/Seoul date +%u`가 1이면 weekly, 아니면 run을 출력한다. skip 종료는 정확한 문자열 비교(`56-59`)만 통과한다. weekly는 관리 블록을 실행하고(`75-80`), 그 뒤 일일 스캔이 있다(`82`). 폴백 자체에는 skip이나 종료 명령이 없다.

실행 1: 저장소 루트에서 `bash -n run_daily_scan.sh` 실행 결과 **exit 0, stderr 빈 문자열**이었다.

실행 2: 원본의 DAY_KIND 대입 블록을 읽어 `bash -c`로 실행했다. 운영 셸 전체는 실행하지 않았다. 외부 Python 명령만 실패 스텁으로 바꾸고, date 스텁은 TZ가 Asia/Seoul인지 확인한 뒤 요일을 반환하게 했다. 재현 핵심은 다음과 같다.

```python
sh = Path('run_daily_scan.sh').read_text()
block = sh[sh.index('    DAY_KIND='):
           sh.index('    if [ "$DAY_KIND" = "skip" ]')]
block = block.replace('.venv/bin/python', 'review_python')
pre = ('set -uo pipefail\nreview_python() { return 1; }\n'
       'date() { [ "${TZ:-}" = "Asia/Seoul" ] || return 98; echo 1; }\n')
r = subprocess.run(['bash', '-c', pre + block +
                    'printf "%s\\n" "$DAY_KIND"\n'], capture_output=True, text=True)
# stdout='weekly\n', returncode=0
```

요일 스텁을 1~7로 각각 실행한 결과: **1→weekly, 2~7→run**, 모두 exit 0. date도 실패하도록 `return 1`로 바꾼 경우 **run, exit 0**이었다. 정상 CLI가 skip/weekly/run을 출력하는 경우에는 각각 그 값이 그대로 유지됐다. 확인한 실패 분기에는 ‘실행하지 않음’으로 물러나는 경로가 없고, 주말에도 CLI 실패 시 run으로 진행한다. 전체 스케줄러 기동·운영 스캔은 미실측이다.

요약: `main`, HEAD `408a13b`에서 이전 보고서·PROGRESS §8-178·CLAUDE.md·AGENTS.md를 읽고 **네 항목 모두 닫힘**으로 판정했다. 위 직접 실행 범위에서 새 결함은 발견하지 못했다. Python 재현 스크립트와 셸 검사 모두 exit 0으로 완료했으며, pytest 전체 재실행은 하지 않았다. 지정 보고서 하나만 작성하고 코드·테스트·운영 DB는 수정하지 않았으며 커밋·push하지 않았다. 실제 05:00 기동, 외부 모델, 메일 전달은 미실측이다.
