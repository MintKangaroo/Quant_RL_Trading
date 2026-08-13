#!/usr/bin/env bash
# IC 측정을 한 번에 하나씩만 돌린다.
#
# 동시에 돌리면 DuckDB 한도가 프로세스마다 따로 걸려서 합이 머신 RAM(9.7GB)을
# 넘고, used 8GB 에서 서버가 통째로 멈춘다. 큐가 느린 것이 서버가 죽는 것보다
# 낫다.
#
# ulimit -v 는 가상주소 기준이라 실제 RSS(약 2.6GB)보다 넉넉히 잡는다. 넘치면
# 프로세스만 죽고 로그가 남는다 — 머신이 죽으면 로그도 안 남는다.
set -u

cd "$(dirname "$0")/.." || exit 1

wait_for_running_measures() {
    # 앞서 띄운 측정이 끝날 때까지 기다린다. 자기 자신은 스크립트 이름으로
    # 돌기 때문에 아래 패턴에 걸리지 않는다.
    while pgrep -f "tools/measure_ic.py --analyst" >/dev/null; do
        sleep 30
    done
}

run_one() {
    local analyst="$1"
    echo "=== queue: ${analyst} 시작 $(date '+%F %T') ==="
    (
        ulimit -v 8388608
        LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
            .venv/bin/python tools/measure_ic.py \
            --analyst "${analyst}" --sessions 300 --save
    ) >"logs/ic-${analyst}.log" 2>&1
    echo "=== queue: ${analyst} 종료(rc=$?) $(date '+%F %T') ==="
}

wait_for_running_measures
for analyst in "$@"; do
    run_one "${analyst}"
    sleep 5
done
echo "=== queue: 전부 끝 $(date '+%F %T') ==="
