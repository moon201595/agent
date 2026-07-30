# 개발 환경 사용법

작성일: 2026-07-30 · 대상: `~/paper-harness` (WSL2 Ubuntu)

## 요약

**매번 할 일은 사실상 없다.** VS Code 열고 채팅 열면 끝이다. 아래 "처음 한 번만" 항목은 전부 완료된 상태다.

---

## 1. 매번 (VS Code 켤 때)

| # | 할 일 |
| --- | --- |
| 1 | VS Code 실행 — 이전 창(`paper-harness [WSL: Ubuntu]`)이 자동으로 열린다 |
| 2 | `Ctrl+Alt+I` 로 Claude Code 채팅 열기 |
| 3 | 작업 |

창이 자동으로 안 열리면 **File → Open Recent → `paper-harness [WSL: Ubuntu]`** 를 고른다.

터미널을 열 필요도, 가상환경을 활성화할 필요도, `claude mcp list` 를 칠 필요도 없다.

### 확인 방법

| 봐야 할 곳 | 정상 상태 |
| --- | --- |
| 창 제목 | `paper-harness [WSL: Ubuntu]` |
| 좌하단 상태바 | `WSL: Ubuntu` |
| 좌하단 | `Restricted Mode` 표시가 **없어야** 한다 |
| 확장 아이콘 | ⚠ 배지가 **없어야** 한다 |

---

## 2. 처음 한 번만 (전부 완료됨)

다시 할 필요 없다. 새 PC 나 새 WSL 에서 세팅할 때만 참고한다.

```bash
# WSL 진입 후
cd ~/paper-harness && code .        # WSL 원격 창 열기 (VS Code Server 자동 설치)
```

그 다음 VS Code 에서:

1. **폴더 신뢰** — 파란 배너 `Manage` → `Trust`.
   Claude Code 확장은 `untrustedWorkspaces: supported: false` 라서 **신뢰하지 않으면 아예 동작하지 않는다.** 채팅 패널이 껍데기만 뜬다.
2. 확장의 `linux-x64` 빌드 설치 제안이 나오면 허용.

이미 끝난 나머지 세팅:

| 항목 | 내용 |
| --- | --- |
| Claude Code CLI | `~/.local/bin/claude` (2.1.220) |
| PATH | `~/.bashrc` 119행에 `export PATH="$HOME/.local/bin:$PATH"` |
| MCP 서버 등록 | `/home/mjh/paper-harness` 프로젝트 스코프 |
| git 신원 | 전역: `moon201595 <answnsgur030@naver.com>` |
| venv | `~/paper-harness/.venv` (Python 3.14.4) |

MCP 등록 명령 (재등록이 필요할 때만):

```bash
claude mcp add paper-harness -- ~/paper-harness/.venv/bin/python ~/paper-harness/server.py
```

`.venv` 의 python 을 **절대경로로** 지정해야 한다. 시스템 python 으로 등록하면 의존성을 못 찾는다. 기본은 project 스코프라 `~/paper-harness` 에서 띄울 때만 붙는다. 어디서나 쓰려면 `-s user` 를 붙인다.

---

## 3. 필요할 때만

### 채팅창 안에서 (슬래시 명령)

| 명령 | 용도 |
| --- | --- |
| `/mcp` | paper-harness 가 붙었는지, 도구 8종이 보이는지 |
| `/usage` | 5시간·주간 한도 확인 |

**슬래시 명령은 채팅창 전용이다.** 터미널에 치면 안 된다.

### 터미널에서

가상환경 활성화가 필요한 것은 **직접 파이썬을 돌릴 때뿐**이다.

```bash
cd ~/paper-harness
source .venv/bin/activate

pytest test_verify_units.py test_select.py -q   # 단위 22개, 네트워크 불필요
python test_smoke.py                            # 실동작 7개, 네트워크 필요
python eval.py                                  # 저장된 요약 통과율
```

진단용:

```bash
claude mcp list     # 서버가 뜨는지 (가상환경 활성화 불필요)
git status          # 변경 확인
```

---

## 4. 내 변경 실시간으로 보기

`Ctrl+Shift+G` (Source Control) 에서 diff 로 보인다. 기준선 커밋(`d797a0e`)이 있어서 이후 모든 변경이 diff 로 잡힌다.

채팅 패널은 도구 호출과 결과를 그때그때 보여준다. 접혀 있으면 펼쳐서 볼 수 있다.

---

## 5. 함정 (실제로 겪은 것들)

### PowerShell 에는 `&&` 가 없다

Windows PowerShell 5.1 은 `&&` 를 파서 에러로 뱉는다.

```powershell
cd ~/paper-harness && claude     # 에러
```

애초에 `~/paper-harness` 는 WSL 경로라 PowerShell 에서 `cd` 자체가 안 된다. WSL 터미널에서 해야 한다.

### 슬래시 명령을 셸에 치면 안 된다

`/mcp` 를 bash 에 치면 파일을 찾는다. `mcp` 를 치면 엉뚱하게 `mmv` 패키지 설치를 권한다. 채팅창에서만 쓴다.

### Windows 창으로 열면 경로가 깨진다

`\\wsl.localhost\Ubuntu\...` UNC 경로로 폴더를 열면 확장의 diff 뷰가 실패한다:

```
Unable to read file '_claude_vscode_fs_left:/wsl.localhost/Ubuntu/home/mjh/...'
```

`_claude_vscode_fs_left` 는 확장의 diff 파일시스템이다. **WSL 원격 창으로 열면 해결된다.**

### statusLine 상태바는 VS Code 확장에서 안 된다

모델·컨텍스트·5시간 한도를 하단에 상시 표시하는 `statusLine` 설정을 만들어뒀지만(`~/.claude/settings.json`, `~/.claude/statusline.py`) **확장이 그 명령을 실행조차 하지 않는다.** 실측으로 확인했다 — 렌더 코드가 없고, 호출 로그도 안 남는다.

- 상시 표시를 원하면 통합 터미널에서 `claude` 로 세션을 띄운다 (거기서는 뜬다)
- 확장에서는 `/usage` 로 확인한다. 한도가 높아지면 입력창 위에 배너로도 뜬다

### `select.py` 라는 파일명은 금지

표준 라이브러리 `select` 를 가려서 asyncio 가 깨진다. 그래서 `selection.py` 다.

### WSL 파일을 클립보드로 복사할 때 한글

`clip.exe` 는 UTF-8 을 제대로 못 읽는다. `iconv` 로 변환해야 안 깨진다.

```bash
iconv -f UTF-8 -t UTF-16LE ~/paper-harness/docs/PROGRESS.md | clip.exe
```

### `/tmp` 는 날아간다

WSL 의 `/tmp` 는 tmpfs 라 WSL 이 내려가면 지워진다. 오래 두어야 할 것은 `~` 아래에 둔다.

---

## 6. 세션·인증이 나뉘는 단위

### 대화 기록은 폴더(프로젝트) 단위

`/home/mjh/paper-harness` 창에서 시작한 대화는 다른 폴더의 `/resume` 목록에 안 나온다. **새 프로젝트에서 `SESSIONS` 목록이 비어 있는 것은 로그아웃이 아니라 기록이 없는 것이다.**

새 세션에서 맥락을 잇는 방법:

```
@docs/PROGRESS.md 읽고 이어서 작업하자.
```

### 인증은 홈 디렉터리 단위

Windows 와 WSL 이 각각 별개 파일을 쓴다.

| | 경로 |
| --- | --- |
| Windows | `C:\Users\answn\.claude\.credentials.json` |
| WSL | `/home/mjh/.claude/.credentials.json` |

현재 양쪽 모두 같은 계정(`answnsgur030@naver.com`, Pro)으로 로그인돼 있다. 확인·재로그인 명령:

```bash
claude auth status     # {"loggedIn": true, ...}
claude auth login      # 필요할 때만
```

### 재시작 후 유지되는 것

| 항목 | 재시작 후 |
| --- | --- |
| VS Code 창 | ✅ 자동 복원 (`window.restoreWindows` 기본값) |
| 폴더 Trust | ✅ 유지 |
| MCP 서버 연결 | ✅ 세션 시작 시 자동 |
| 로그인 | ✅ 유지 |
| **Claude 대화** | ❌ 새로 시작. 이전 건 `/resume` |

---

## 7. 토큰 아끼기

논문 전문은 4만~29만 자다. 통째로 읽히면 컨텍스트를 크게 먹는다. `get_paper_text` 에 `offset` / `max_chars` 가 있는 이유가 이것이다.

```
원문은 get_paper_text 로 앞 2만 자만 먼저 읽고, 부족하면 offset 을 옮겨 더 읽어.
```

요약에 필요한 것은 보통 초록·실험·결론이라 처음부터 다 읽을 필요가 없다.
