#!/usr/bin/env bash
# 주 1회 IC 측정 — 랭커 ModelOps ①(docs/design/modelops-ranker.md). 토 12:00, 학습·백필이 안 도는 시간.
#   crontab: 0 12 * * 6 .../scripts/measure_ic_weekly.sh
# 전 Analyst 를 두 시장에서 재고 --save 로 analyst_weights 에 적재한다(가중치는 한계기여 규칙이 정한다).
# rc 를 밖으로 낸다 — 조용한 실패 금지(memory silent-failure-needs-nonzero-rc).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
LOG="logs/ic-weekly-$(date +%Y%m).log"
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-2GB}"
FAILED=0
{
    echo "=== $(date '+%F %T') 주간 IC 측정 ==="
    for M in KR US; do
        nice -n 5 .venv/bin/python -u tools/measure_ic.py --market "$M" --sessions 300 --save
        rc=$?
        echo "  $M rc=$rc"
        [ "$rc" -ne 0 ] && FAILED=$((FAILED + 1))
    done
    echo "완료 — 실패 ${FAILED}건"
} >> "$LOG" 2>&1
exit "$FAILED"
