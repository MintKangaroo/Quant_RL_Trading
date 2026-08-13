#!/usr/bin/env bash
# 미장 백필 → IC 측정. **순차로 돈다.**
#
# 동시에 돌리면 두 번 데였다.
#  1) 합산 메모리가 9.7GB 머신의 8GB 벽을 친다
#  2) 백필이 prices 에 파일을 쌓는 동안 측정이 그 파티션을 열다 죽는다
#
# 진행 상황은 logs/backfill-us.log 와 logs/ic-<analyst>.log 에 남는다.
set -u

cd "$(dirname "$0")/.." || exit 1

echo "=== pipeline 시작 $(date '+%F %T') ==="

echo "--- 1/2 미장 백필 ---"
(
    ulimit -v 8388608
    LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
        .venv/bin/python tools/backfill.py --market US
) >logs/backfill-us.log 2>&1
echo "백필 종료(rc=$?) $(date '+%F %T')"

echo "--- 2/2 IC 측정 ---"
tools/ic_queue.sh flow_kr regime chart risk

echo "=== pipeline 끝 $(date '+%F %T') ==="
