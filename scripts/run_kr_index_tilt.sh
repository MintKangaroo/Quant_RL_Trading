#!/usr/bin/env bash
# 시행 AZ(docs/protocols/kr-index-tilt-2026-09.md) — 점검 → 측정. 판정이 이미 있으면 건너뛴다. 무거운 작업이 돌면 다음 회차.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG=logs/trial-kr-index-tilt-AZ.log
PRE=logs/precheck-kr-index-tilt-AZ.log
grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic|collect_index_members)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
if ! grep -q "등록 전 점검" "${PRE}" 2>/dev/null; then
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_kr_index_tilt.py --precheck > "${PRE}" 2>&1
    echo "rc=$?" >> "${PRE}"
fi
grep -q "측정 진행" "${PRE}" || { echo "$(date '+%F %T') 점검 — 측정하지 않는다(또는 점검 실패, ${PRE})" >> "${LOG}"; exit 0; }
{
    echo "=== $(date '+%F %T') 시행 AZ ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_kr_index_tilt.py --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
