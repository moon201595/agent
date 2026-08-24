#!/bin/bash
# run_daily_scan.sh — cron이 매일 부르는 진입점.
#
# run_profile_scan.py --all 을 실행해 등록된 프로필 전체를 스캔하고
# 다이제스트를 DB에 저장한다(research_profile.save_digest — review_app.py
# "리서치 프로필" 탭이 그 값을 그대로 읽어 보여준다). 실제 메일 발송은
# 아직 없다 — .env에 SMTP 관련 키가 없어 email_delivery.py가 의도적으로
# NotImplementedError를 낸다(내부 SMTP 서버 확인 전까지는 그대로 둘 것).
#
# 로그는 logs/daily_scan.log에 계속 이어붙인다 — crontab 자체 로그(보통
# 안 보이게 묻힘)와 별개로, 사람이 tail -f로 바로 확인할 수 있게. 프로필
# 하나가 실패해도 scan_all_profiles()가 나머지를 계속 처리하므로(2026-08-24,
# run_profile_scan.py 참고) 이 스크립트가 중간에 죽을 일은 거의 없다.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
mkdir -p logs

{
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 시작 ==="
    .venv/bin/python run_profile_scan.py --all
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 종료 ==="
} >> logs/daily_scan.log 2>&1
