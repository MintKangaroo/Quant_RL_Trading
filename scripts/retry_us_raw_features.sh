#!/usr/bin/env bash
# 미장 원피처 캐시 재시도 — 2026-09-20 첫 시도가 OOM(rc=137)으로 죽었다(다른 작업과 겹쳤다).
# 이미 구운 파일은 diagnose_ic 가 건너뛴다. 30분마다 불러도 안전하다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
A="chart event flow_us fundamental regime risk volume"

missing=0
for a in ${A}; do
    [ -f "data/_diag/features-${a}-US.pkl" ] || missing=1
done
[ "${missing}" -eq 0 ] && exit 0

for tool in tools/trial_ranker_ensemble.py tools/backfill_ic_history.py tools/measure_ic.py \
            tools/train_ranker.py tools/diagnose_ic.py; do
    pgrep -f "${tool}" > /dev/null && exit 0
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 5000 ] && exit 0

LOG="logs/bake-raw-features-retry.log"
{
    echo "=== $(date '+%F %T') 미장 원피처 재시도 (가용 ${AVAIL}MB) ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 10 .venv/bin/python -u tools/diagnose_ic.py \
        cache-extra --market US --analyst ${A}
    echo "rc=$?"
} >> "${LOG}" 2>&1
