#!/usr/bin/env bash
# KRX Open API 전일 시총·지수 아침 보충 (2026-09-08). 06:00 브리핑 전 보충은 매일 0건(empty_unconfirmed)이었고
# 같은 호출이 08:41 엔 5,746행을 줬다 — KRX 가 전일 값을 06:00~08:40 사이에 낸다. 15:55 수집(sessions=3)이
# 하루 늦게 채우던 것을 여기서 당긴다. rc 를 밖으로 낸다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/refresh-$(date +%Y%m).log"
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-800MB}"
FAILED=0
{
    echo "=== $(date '+%F %T') KRX 아침 보충(시총·지수) ==="
    for T in shares indices-krx indices-board; do
        .venv/bin/python tools/backfill.py --market KR --table "$T" --sessions 2
        rc=$?; echo "  KR ${T} rc=${rc}"; [ "$rc" -ne 0 ] && FAILED=$((FAILED + 1))
    done
} >> "$LOG" 2>&1
exit "$FAILED"
