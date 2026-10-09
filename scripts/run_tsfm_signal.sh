#!/usr/bin/env bash
# TTM 제로샷 신호(시행 IX-T 하한 기준) — 밤 수집 뒤, 마지막 국장 종가까지로 한 번. 다음 세션 결정에만 보인다(tools/tsfm_signal.py).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/tsfm-signal-$(date +%Y%m).log"
{
    echo "=== $(date '+%F %T') tsfm 신호 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=800MB nice -n 10 .venv/bin/python tools/tsfm_signal.py
    RC=$?
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
# rc 3(이미 적재)은 정상이다.
[ "${RC:-1}" -eq 3 ] && exit 0
exit "${RC:-1}"
