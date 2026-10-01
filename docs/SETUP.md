# 개발·운영 환경

최초 작성 2026-07-30 · 전면 개정 2026-10-01 · 대상: WSL2 Ubuntu 의 `~/paper-harness`

옛 판(7~8월)은 "채팅으로 논문을 하나씩 요약"하던 시절의 사용법이었다. 지금 시스템은 매일 무인으로 돌고, 사람은 메일을 읽고
반응 버튼을 누른다. 이 문서는 그 운영을 유지·수정하는 데 필요한 것만 남긴다. 명령·모듈의 사실은 [AGENTS.md](../AGENTS.md)가 기준이다.

## 1. 평소

| 하고 싶은 것 | 방법 |
| --- | --- |
| 작업하기 | VS Code 를 WSL 원격 창(`paper-harness [WSL: Ubuntu]`)으로 열고 Claude Code 채팅을 연다 |
| 오늘 운행 확인 | `logs/daily_scan.log` 끝부분, 또는 `logs/morning_report.txt` |
| 운영 화면 | `.venv/bin/streamlit run review_app.py` → http://localhost:8501 (127.0.0.1 전용) |
| 전체 테스트 | `.venv/bin/python -m pytest` — 인자 없이 돌려도 안전하다 |

가상환경을 활성화할 필요는 없다. 모든 명령을 `.venv/bin/...` 로 부른다.

## 2. 자동 운행이 걸려 있는 곳

| 트리거 | 내용 |
| --- | --- |
| WSL cron `0 5 * * *` | `run_daily_scan.sh` — 쉬는 날 판정(`work_calendar`)은 스크립트 안에서 한다 |
| WSL cron `30 6,10 * * *` | `scripts/check_daily_mail.py` — 그날 메일이 안 나갔으면 알린다 |
| Windows 작업 스케줄러 `paper-harness\daily-scan` | 매일 05:00, 깨어나는 즉시 실행·절전 해제 허용. PC 가 절전이면 WSL cron 은 그 시각을 건너뛰기 때문이다 |

동시에 겹치면 `flock` 이 뒤의 것을 건너뛴다. 주간 관리는 같은 주 두 번째 실행을 `agent_runs` 기록이 막는다. 다만 **일일 스캔이
끝난 뒤 다른 트리거가 순차로 다시 부르면 스캔·발송이 한 번 더 돌 수 있다**(주차 표지는 일일 스크립트가 쓰기만 한다).
작업 XML 은 Windows 사용자 폴더의 `paper-harness-tasks\` 에 있다. 등록 상태 확인: cron 2026-10-01, Windows 작업 XML 2026-09-18.
지우기: `schtasks.exe /Delete /TN "paper-harness\daily-scan" /F`.

로그: `logs/daily_scan.log`(매일), `logs/weekly_agent.log`(손으로 돌린 주간 관리), `logs/cron.log`(cron 자체 출력).

종료코드(`run_profile_scan.py --all`): 0 정상, 1 스캔 예외·발송 실패·프로필 없음, **2 발송은 했지만 검색 소스 하나가 실패**.
종료코드만으로 메일 도착을 증명할 수는 없다 — 수신자가 없으면 보내지 않고도 0 이다.

## 3. 처음 한 번 (새 PC·새 WSL)

```bash
cd ~/paper-harness && code .                       # WSL 원격 창 (VS Code Server 자동 설치)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python migrate.py --apply --scope all    # 새 설치의 스키마 (운영 DB 는 --scope 없이, 자동 백업)
```

1. VS Code 파란 배너에서 **폴더 신뢰**(`Manage` → `Trust`). 신뢰하지 않으면 Claude Code 확장 채팅이 껍데기만 뜬다.
2. `.env` 를 만든다. 이름과 발급처는 [API_KEYS.md](API_KEYS.md). 값은 저장소·로그·채팅에 남기지 않는다.
3. 주간 관리·동향 서술이 부르는 `claude`·`codex` CLI 에 로그인해 둔다. cron 의 PATH 에는 `~/.local/bin` 이 없어서 스크립트가 직접 붙인다.
4. MCP 도구가 필요하면(탐색·디버깅용) venv 의 python 을 **절대경로로** 등록한다:
   `claude mcp add paper-harness -- ~/paper-harness/.venv/bin/python ~/paper-harness/server.py`
5. Windows 작업 스케줄러 작업을 다시 만든다(2절).

## 4. 함정 (실제로 겪은 것)

### `/tmp` 는 날아간다
WSL 의 `/tmp` 는 WSL 이 내려가면 지워진다. 발표 자료 생성 스크립트를 거기 두었다가 잃은 적이 있다. 다시 쓸 것은 `~` 아래에 둔다.

### API 키를 URL 에 실으면 로그에 남는다
Gemini 를 `?key=...` 쿼리로 부르면 `httpx` 요청 로그가 URL 째 찍는다. 키는 헤더(`x-goog-api-key`)로 보내고 `httpx`·`httpcore`
로거를 WARNING 으로 낮춰 막았다. 새 API 를 붙일 때마다 확인한다.

### 기본 User-Agent 가 막힌다
Groq 를 파이썬 기본 User-Agent 로 부르면 Cloudflare 가 봇으로 보고 1010 을 낸다. User-Agent 를 명시한다.

### `select.py` 라는 파일명은 금지
표준 라이브러리 `select` 를 가려 asyncio 가 깨진다. 그래서 `selection.py` 다.

### `pytest.ini` 의 `norecursedirs` 를 지우지 않는다
⑦ 재현이 clone 한 남의 저장소(`data/`)까지 수집해 100건 넘게 깨진다(2026-08-18).

### 테스트가 운영 DB 를 건드릴 수 있었다
conftest 가 데이터 디렉터리를 임시 경로로 돌린다(2026-09-16). 새 모듈은 DB 경로를 인자로 받는다.

### PC 절전 중에는 cron 이 안 돈다
2026-09-15 실측. 그래서 Windows 작업 스케줄러(2절)를 같이 건다. 전원 설정의 "절전 해제 타이머 허용"이 켜져 있어야 한다.

### Windows 창으로 열면 diff 가 깨진다
`\\wsl.localhost\Ubuntu\...` 로 폴더를 열면 Claude Code 확장의 diff 뷰가 실패한다. WSL 원격 창으로 연다.

### PowerShell 에는 `&&` 가 없다
Windows PowerShell 5.1 은 `&&` 를 문법 오류로 낸다. 이 저장소 명령은 WSL 터미널에서 친다.

### 슬래시 명령은 채팅창 전용
`/mcp`·`/usage` 를 bash 에 치면 파일 경로로 해석된다.

### 클립보드로 한글 복사
`clip.exe` 는 UTF-8 을 못 읽는다: `iconv -f UTF-8 -t UTF-16LE 파일 | clip.exe`.

## 5. 세션

Claude Code 대화 기록은 폴더 단위로 나뉜다. 새 세션에서 맥락을 이을 때는 `docs/PROGRESS.md` 끝부분과 `AGENTS.md` 를 먼저 읽힌다.
세션 안에서 건 예약(CronCreate)은 그 창이 닫히면 사라진다 — 꼭 돌아야 하는 일은 cron·작업 스케줄러에 둔다.
