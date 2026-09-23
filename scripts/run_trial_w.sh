#!/usr/bin/env bash
# 시행 W — 10/11 13:00 부터 매일 한 번 부른다. 판정이 이미 있으면 건너뛰고, 도구가 스스로 날짜·6차 완료를 확인한다(rc=2 는 기다림).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG=logs/trial-raw-feature-ranker-W.log
grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
# **파이썬 프로세스만 찾는다** — 이 스크립트는 인자로 tools/trial_*.py 를 받아 그냥 "tools/trial_" 로 찾으면 자기 자신이 잡힌다
# (2026-09-23 20:07 AN 점검이 "다른 무거운 작업" 으로 건너뜀 — memory background-job-hygiene 의 pgrep 자기매칭).
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 5000 ] && { echo "$(date '+%F %T') 가용 ${AVAIL}MB — 건너뜀" >> "${LOG}"; exit 0; }
{
    echo "=== $(date '+%F %T') 시행 W ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_raw_feature_ranker.py --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
