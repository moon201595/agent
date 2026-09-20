#!/bin/bash
# run_weekly_agent.sh — 손으로 돌릴 때만 쓰는 진입점. 정규 실행은 2026-09-19 부터
# run_daily_scan.sh 의 월요일 블록이다(§8-163) — 일일 스캔 바로 앞에서 돈다.
#
# 1) db_retention — 보존표(계획 v2 §9)대로 오래된 관측·실행 기록·캐시를 **백업 뒤** 정리한다.
# 2) agent_maintenance — 프로필마다 브리프 → Claude 제안 → Codex 판정(의견이 갈리면 Codex) → Python 검증·적용.
#    결과는 각 프로필의 다음 아침 메일에 "이번 주 에이전트가 바꾼 것"으로 한 번 실린다.
#
# 일일 스캔과 같은 락을 쓴다 — 정리가 스캔과 겹치면 계획을 세운 뒤 새로 들어온 행과 rowid 가 엇갈릴 수 있다.
# 스캔이 아직 돌고 있으면 최대 1시간 기다리고, 그래도 안 끝나면 이번 주는 건너뛴다(다음 주가 온다).
#
# .env 를 읽지 않는다 — 두 단계 모두 시크릿이 필요 없고, 에이전트의 CLI 자식에게는 HOME·PATH 만 넘긴다(규칙 5).
# 두 단계는 서로 독립이다. 정리가 실패해도 에이전트는 돈다.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
mkdir -p logs
export PATH="$HOME/.local/bin:$PATH"      # cron PATH 에는 claude·codex·gh 가 없다(2026-09-16 실측)

{
    # 같은 주에 두 번 돌지 않는다(2026-09-16). cron 과 Windows 작업 스케줄러(절전·WSL 중지 대비)가 둘 다 이 스크립트를 부르므로 겹칠 수
    # 있다 — 동시 실행은 자기 락으로, 순차 두 번째는 KST ISO 주 표지로 건너뛴다. 정리 단계를 두 번 돌리면 백업이 하나 더 생겨 회전이
    # 옛 백업을 밀어낸다.
    exec 8>"logs/weekly_agent.lock"
    if ! flock -n 8; then
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 주간 작업 스킵 — 다른 트리거가 이미 돌고 있음 ==="
        exit 0
    fi
    week=$(TZ=Asia/Seoul date +%G-W%V)
    if [ "$(cat logs/weekly_agent.stamp 2>/dev/null)" = "$week" ]; then
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 주간 작업 스킵 — $week 는 이미 실행함 ==="
        exit 0
    fi
    exec 9>"logs/daily_scan.lock"
    if ! flock -w 3600 9; then
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 주간 작업 스킵 — 일일 스캔이 1시간 넘게 락을 잡고 있음 ==="
        exit 0
    fi
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 주간 작업 시작 (pid $$) ==="

    .venv/bin/python db_retention.py --apply > logs/retention_last.json
    retention=$?
    echo "  [보존] exit $retention (결과: logs/retention_last.json)"

    # 바깥 timeout 을 두지 않는다 — 파이썬만 죽이면 새 세션으로 띄운 CLI 자식이 남는다(외부 검토 2026-09-15). 상한은 안쪽에 있다:
    # CLI 한 번 600초(프로세스 그룹째 종료) × 프로필당 2회.
    .venv/bin/python agent_maintenance.py
    agent=$?
    echo "  [에이전트] exit $agent"

    echo "$week" > logs/weekly_agent.stamp
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 주간 작업 종료 ==="
    if [ "$retention" -ne 0 ] || [ "$agent" -ne 0 ]; then
        exit 1
    fi
    exit 0
} >> logs/weekly_agent.log 2>&1
