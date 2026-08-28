# 시험 운행 1주 체크리스트

작성: 2026-08-28 (M8)

수신자를 본인 1명으로 두고 1주를 돌린 뒤, 무사고면 팀장 보고 후 팀 확대를 판단한다.
**팀 확대는 이 체크리스트가 1주 통과한 뒤에만 한다** — 팀 전체에 매일 나가는 메일은
한 번 잘못 나가면 되돌릴 수 없다.

## 매일 아침 확인 (5분)

### (a) 메일이 왔는가

- 왔으면 → (b)로.
- **안 왔으면 아래 순서로 좁힌다.** 이 순서가 중요한 이유는, 아래로 갈수록
  "더 앞단에서 죽었다"는 뜻이라 위쪽부터 봐야 원인이 빨리 나온다.

```bash
cd ~/paper-harness

# 1. cron이 스크립트를 아예 못 띄웠나 (경로·권한·해석기 문제)
cat logs/cron.log            # 비어 있으면 정상. 내용이 있으면 그게 원인.

# 2. 스크립트는 떴는데 중간에 죽었나
tail -40 logs/daily_scan.log # "시작" 뒤에 "종료"가 있는지, exit 코드가 0인지

# 3. 아예 안 돌았나 (WSL이 꺼져 있었을 가능성)
grep "$(date -u +%Y-%m-%d)" logs/daily_scan.log || echo "오늘 실행 기록 없음"
systemctl status cron --no-pager | head -3
```

> **WSL 주의**: WSL2는 Windows 쪽에서 잡아두는 프로세스가 없으면 배포판이 종료된다.
> 터미널을 다 닫고 잔 날은 cron도 같이 죽어 있다. "오늘 실행 기록 없음"이 나오는
> 날이 반복되면 그게 원인이고, Windows 작업 스케줄러 등록(M8 미완 항목)이 필요하다.

### (b) 검증·재현 라벨이 정상인가

메일 각 항목에 라벨이 붙어 있어야 한다. 아래 중 하나면 정상:

| 라벨 | 뜻 |
| --- | --- |
| `[검증 n/m 통과]` | ⑤ 수치 대조 결과 |
| `[검증할 수치 없음]` | 요약에 검증 대상 숫자가 없었음 |
| `[검증 데이터 없음]` | ⑤가 안 돌았음 |
| `[재현 ✓/✗/⏳ 실행중/–]` | ⑦ 상태 |
| `[미검증 · 초록 기반]` / `[미검증 · S2 TLDR]` | Deep 처리가 실패한 논문 |

**이상 신호**: 모든 항목이 `[검증 데이터 없음]`이면 ④⑤가 통째로 안 돈 것이다.
`logs/daily_scan.log`에서 `deep_status`를 확인한다.

### (c) T+1 재현 상태가 갱신되는가

어제 `[재현 ⏳ 실행중]`이던 논문이 오늘 `✓` 또는 `✗`로 바뀌어야 한다.
**며칠째 `⏳`에 머물러 있으면** 재현 프로세스가 멈춘 것이다:

```bash
ls -la data/repro/*.running     # 오래된 마커가 남아 있으면 죽은 프로세스의 잔해
ps aux | grep docker_runner | grep -v grep
```

### (d) 로그에 에러가 있는가

```bash
grep -iE "error|exception|traceback|실패" logs/daily_scan.log | tail -20
```

`처리 실패`가 몇 건 있는 것은 정상이다(논문 하나가 실패해도 나머지는 계속 처리된다).
**같은 논문이 며칠 연속 실패**하면 그 논문만 따로 본다.

### (e) API 사용량

```bash
# OpenAlex 잔여 크레딧 (하루 10,000 = $1, 논문당 1)
.venv/bin/python -c "
import httpx, summarize_engine as e
r = httpx.get('https://api.openalex.org/works/doi:10.1016/S0140-6736(97)11096-0',
              params={'api_key': e.ENV['OPENALEX_API_KEY'], 'select': 'id'}, timeout=20)
print('OpenAlex 잔여:', r.headers.get('x-ratelimit-remaining'), '/', r.headers.get('x-ratelimit-limit'))"

# LLM 429 발생 횟수 (Gemini 한도 소진 여부)
grep -c "429" logs/daily_scan.log
```

**Gemini 429가 잦으면** Groq 폴백을 타느라 배치가 몇 시간씩 늘어진다(§8 미해결 14번).
그날 다이제스트가 아침에 안 와 있으면 이게 원인일 가능성이 높다.

## 주 1회 확인

### 재현 성공률과 egress 판단 근거

```bash
.venv/bin/python -c "
import server
with server._db() as con:
    rows = list(con.execute('SELECT success, stage FROM repro_results'))
print(f'재현 시도 {len(rows)}건, 성공 {sum(r[0] for r in rows)}건')
"
```

§8 미해결 16번의 **종결 조건**: 재현 시도가 30건 이상 쌓였을 때 실행 단계
네트워크 오류가 0건이면 egress allowlist는 "필요 없음"으로 종결한다.

### 인젝션 flag 오탐 확인

`[⚠ 본문에 모델 대상 지시로 보이는 패턴]`이 뜬 논문이 있으면 **첫 몇 건은
사람이 직접 원문을 열어** 확인한다(§8 미해결 17번). 지금까지의 추정은
"프롬프트를 인용한 에이전트 논문"이지만 사람이 확인한 적이 없다.

## 1주 통과 기준

- [ ] 7일 중 **5일 이상** 메일이 정상 도착 (WSL 종료로 안 돈 날은 원인이 명확하므로 별도 집계)
- [ ] 검증·재현 라벨이 매번 정상 렌더링
- [ ] `⏳ 실행중`이 T+1에 갱신되는 것을 최소 1회 확인
- [ ] `logs/cron.log`가 계속 비어 있음 (내용이 생겼다면 원인 해결 후 재시작)
- [ ] OpenAlex 크레딧 여유 (하루 사용량이 한도의 10% 미만)
- [ ] 다이제스트 내용이 **읽을 만한가** — 이건 사람만 판단할 수 있다.
      상위 논문이 실제로 볼 만하지 않으면 스코어링 가중치를 조정해야 한다(M-plan Phase 2).

## 미완 항목 (시험 운행 중에도 남아 있음)

1. **Windows 작업 스케줄러 등록** — 없으면 WSL이 꺼진 날은 안 돈다.
2. **`--all` 경로에 메일 발송이 안 붙어 있다** — `run_profile_scan.py --send`는
   프로필을 하나 지정했을 때만 동작한다. cron이 부르는 `--all`은 다이제스트를
   DB에 저장만 하고 메일을 보내지 않는다. 시험 운행을 하려면 이걸 먼저 연결해야 한다.
3. healthchecks.io dead-man's switch (선택, 외부 ping이라 사내 정책 확인 필요)
