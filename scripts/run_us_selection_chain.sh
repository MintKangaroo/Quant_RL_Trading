#!/usr/bin/env bash
# 미장 선정 시행 AU → AV → AW (docs/protocols/us-selection-chain-2026-09.md). 판정이 이미 있으면 그 시행은 건너뛴다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> logs/trial-us-selection-AU.log; exit 0
fi
for T in AU AV AW; do
    LOG="logs/trial-us-selection-${T}.log"
    grep -q "^판정:" "${LOG}" 2>/dev/null && continue
    {
        echo "=== $(date '+%F %T') 시행 ${T} ==="
        MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_us_selection.py --trial "${T}" --save
        echo "rc=$?"
    } >> "${LOG}" 2>&1
    grep -q "^판정:" "${LOG}" || exit 1
done
# 지수 이기기 둘(docs/protocols/us-index-tilt-2026-09.md) — AX 점검(액티브 셰어) → AX → AY.
PRE=logs/precheck-us-index-tilt-AX.log
if [ ! -s "${PRE}" ] || ! grep -q "등록 전 점검" "${PRE}"; then
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_us_index_tilt.py --trial AX --precheck > "${PRE}" 2>&1
    echo "rc=$?" >> "${PRE}"
fi
grep -q "측정 진행" "${PRE}" || { echo "$(date '+%F %T') AX 점검 — 측정하지 않는다(또는 점검 실패)" >> logs/trial-us-index-tilt-AX.log; exit 0; }
for T in AX AY; do
    LOG="logs/trial-us-index-tilt-${T}.log"
    grep -q "^판정:" "${LOG}" 2>/dev/null && continue
    {
        echo "=== $(date '+%F %T') 시행 ${T} ==="
        MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_us_index_tilt.py --trial "${T}" --save
        echo "rc=$?"
    } >> "${LOG}" 2>&1
    grep -q "^판정:" "${LOG}" || exit 1
done
