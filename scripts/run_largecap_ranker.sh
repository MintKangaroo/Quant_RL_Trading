#!/usr/bin/env bash
# 시행 BC(docs/protocols/largecap-ranker-2026-10.md). 판정이 있으면 건너뛴다. 무거운 작업이 돌면 다음 회차.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG=logs/trial-largecap-ranker-BC.log
grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 4500 ] && { echo "$(date '+%F %T') 가용 ${AVAIL}MB — 건너뜀" >> "${LOG}"; exit 0; }
{
    echo "=== $(date '+%F %T') 시행 BC ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_largecap_ranker.py --market both --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
