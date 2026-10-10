#!/usr/bin/env bash
# 기술적 합성 신호(시행 TG 하한 기준) — 밤 수집 뒤, 마지막 국장 종가까지로 한 번. 다음 세션 결정에만 보인다(tools/ta_signal.py). 약 1.5분 · 3.3GB.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/ta-signal-$(date +%Y%m).log"
{
    echo "=== $(date '+%F %T') ta 신호 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=900MB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 nice -n 10 .venv/bin/python tools/ta_signal.py
    RC=$?
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
# rc 3(이미 적재)은 정상이다.
[ "${RC:-1}" -eq 3 ] && exit 0
exit "${RC:-1}"
