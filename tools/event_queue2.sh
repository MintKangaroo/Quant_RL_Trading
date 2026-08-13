#!/usr/bin/env bash
# no_halt 제거 후 event 재측정.
set -u
cd "$(dirname "$0")/.." || exit 1
echo "=== event 재측정 시작 $(date '+%F %T') ==="
( ulimit -v 8388608
  LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
    .venv/bin/python tools/measure_ic.py --analyst event --market KR --sessions 300 --save
) >logs/ic3-event.log 2>&1
echo "=== 종료(rc=$?) $(date '+%F %T') ==="
