#!/bin/bash
# run_daily_scan.sh — cron이 매일 부르는 진입점.
#
# run_profile_scan.py --all 을 실행해 등록된 프로필 전체를 스캔하고
# 다이제스트를 DB에 저장한다(research_profile.save_digest — review_app.py
# "리서치 프로필" 탭이 그 값을 그대로 읽어 보여준다).
#
# 메일 발송은 아직 이 경로에 없다 — run_profile_scan.py 의 --send 는 프로필
# 하나를 지정했을 때만 동작하고 --all 경로에는 안 붙어 있다(M8 착수 시
# 결정할 항목). SMTP 자격증명 자체는 이미 동작이 확인됐다(M3 실발송).
#
# 로그는 logs/daily_scan.log 에 계속 이어붙인다 — crontab 자체 로그(보통
# 안 보이게 묻힘)와 별개로, 사람이 tail -f 로 바로 확인할 수 있게. 프로필
# 하나가 실패해도 scan_all_profiles()가 나머지를 계속 처리하므로(2026-08-24,
# run_profile_scan.py 참고) 이 스크립트가 중간에 죽을 일은 거의 없다.
#
# 중복 실행 방지(M8, 2026-08-28): Gemini 가 막힌 날은 프로필 하나가 몇 시간씩
# 걸릴 수 있어서(§8 미해결 14번) 다음 주기와 겹칠 수 있다. 겹치면 같은 논문에
# LLM 을 두 번 태우고 무료 한도를 낭비하며, ⑦ 재현이 같은 arxiv_id 로 동시에
# 도는 상황도 생긴다. **중복 실행이 아니라 스킵이 안전하다** — 어차피 다음
# 날 새 주기가 온다.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
mkdir -p logs

LOCKFILE="logs/daily_scan.lock"

{
    # flock 은 파일 디스크립터에 락을 건다. 스크립트가 어떻게 끝나든(정상 종료,
    # kill, 크래시) 프로세스가 사라지면 커널이 fd 를 닫으며 락도 자동으로
    # 풀린다 — 락 파일을 지우는 정리 코드가 따로 필요 없고, 죽은 프로세스가
    # 락을 영구히 물고 있는 사고도 안 난다.
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 스킵 — 이전 실행이 아직 진행 중 ==="
        exit 0
    fi

    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 시작 (pid $$) ==="
    .venv/bin/python run_profile_scan.py --all
    status=$?
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 종료 (exit $status) ==="
} >> logs/daily_scan.log 2>&1
