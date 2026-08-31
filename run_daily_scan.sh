#!/bin/bash
# run_daily_scan.sh — cron이 매일 부르는 진입점.
#
# run_profile_scan.py --all 을 실행해 등록된 프로필 전체를 스캔하고
# 다이제스트를 DB에 저장한다(research_profile.save_digest — review_app.py
# "리서치 프로필" 탭이 그 값을 그대로 읽어 보여준다).
#
# --send 로 프로필마다 그 프로필의 수신자에게 다이제스트를 보낸다(M8,
# 2026-08-28). 논문이 0편인 날도 보낸다 — 매일 오는 메일 자체가 "파이프라인이
# 살아 있다"는 증거라서, 외부 dead-man's switch 를 안 붙인 지금 그 역할을
# 대신한다. 메일이 안 온 날은 "새 논문이 없었다"가 아니라 "무언가 고장났다"로
# 읽어야 한다(docs/TRIAL_CHECKLIST.md 의 (a) 항목이 이 전제 위에 서 있다).
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
    # --max-pages 를 기본 10(=후보 500편 상한)에서 30 으로 올린다. 상한에
    # 닿으면 run_status 가 'partial' 로 남고, next_since 가 'done' 일 때만
    # 커서를 전진시키므로 **창이 영원히 안 넘어간다**. 2026-08-31 핵심
    # 키워드를 12→26 개로 넓히면서 후보 수가 상한을 넘길 여지가 생겨
    # 여유를 뒀다(후보가 적으면 페이지를 다 안 받으므로 비용은 그대로다).
    .venv/bin/python run_profile_scan.py --all --send --max-pages 30
    status=$?
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 종료 (exit $status) ==="
} >> logs/daily_scan.log 2>&1
