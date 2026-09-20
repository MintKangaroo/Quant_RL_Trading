#!/usr/bin/env bash
# 시행 AB(지평 앙상블) 측정 — 밤에 한 번. 30분마다 불러도 안전하다:
#  · 이미 끝났으면(판정 줄이 로그에 있으면) 즉시 종료
#  · 무거운 도구가 돌고 있거나 가용 메모리 4GB 미만이면 즉시 종료
#  · 사전등록 docs/protocols/ranker-ensemble-horizons-2026-09.md · 판정은 한 번뿐(--save 로 예산 1회 소진)
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/trial-ranker-ensemble-AB.log"

grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0

for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/backfill_ranker_signals.py tools/trial_ranker_ensemble.py; do
    pgrep -f "${tool}" > /dev/null && exit 0
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4000 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4000MB — 건너뜀" >> logs/trial-ab-queue.log
    exit 0
fi
echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 AB 측정 시작" >> logs/trial-ab-queue.log
{
    echo "=== $(date '+%F %T') 시행 AB ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python -u tools/trial_ranker_ensemble.py --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
