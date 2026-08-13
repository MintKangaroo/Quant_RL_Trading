#!/usr/bin/env bash
# 피처별 IC — chart · event (KR 300세션). 순차로만 돈다.
set -u
cd "$(dirname "$0")/.." || exit 1
echo "=== 피처 큐 시작 $(date '+%F %T') ==="
for analyst in chart event; do
    echo "=== ${analyst} 시작 $(date '+%F %T') ==="
    (
        ulimit -v 8388608
        LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
            .venv/bin/python tools/measure_features.py \
            --analyst "${analyst}" --market KR --sessions 300
    ) >"logs/feat-${analyst}.log" 2>&1
    echo "=== ${analyst} 종료(rc=$?) $(date '+%F %T') ==="
    sleep 5
done
echo "=== 피처 큐 끝 $(date '+%F %T') ==="
