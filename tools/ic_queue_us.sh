#!/usr/bin/env bash
# 미장 IC — **백필이 끝난 뒤에** 잰다.
#
# 백필이 도는 동안 재면 두 가지가 어긋난다.
#  1) 종목이 계속 늘어나는 중이라 횡단면 표본이 매 세션 달라진다
#  2) 그때 잰 IC 는 "그 시점까지 받은 종목들" 의 IC 이고, 재현이 안 된다
#
# 그리고 국장 큐가 아직 돌고 있으면 그것도 기다린다 — 동시에 돌리면 합산
# 메모리가 9.7GB 머신의 8GB 벽을 친다.
#
# **미장에서 잴 수 있는 Analyst 만 돈다.** fundamental 은 미장 재무가 없고,
# event 는 미장 이벤트가 없고, flow_us 는 입력이 0건이다. 없는 데이터로
# 잰 IC 는 "안 먹혔다" 가 아니라 "잰 게 없다" 이므로 아예 돌리지 않는다.
set -u

cd "$(dirname "$0")/.." || exit 1

ANALYSTS=(chart risk regime)

echo "=== 미장 IC 대기 시작 $(date '+%F %T') ==="

# 1) 백필이 끝날 때까지.
while pgrep -f "tools/backfill.py --market US" >/dev/null; do
    sleep 300
done
echo "백필 종료 확인 $(date '+%F %T')"

# 2) 국장 측정이 끝날 때까지.
while pgrep -f "tools/measure_ic.py --analyst" >/dev/null; do
    sleep 60
done
echo "국장 큐 종료 확인 $(date '+%F %T')"

for analyst in "${ANALYSTS[@]}"; do
    echo "=== 미장 ${analyst} 시작 $(date '+%F %T') ==="
    (
        ulimit -v 8388608
        LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
            .venv/bin/python tools/measure_ic.py \
            --analyst "${analyst}" --market US --sessions 300 --save
    ) >"logs/ic-us-${analyst}.log" 2>&1
    echo "=== 미장 ${analyst} 종료(rc=$?) $(date '+%F %T') ==="
    sleep 5
done

echo "=== 미장 IC 전부 끝 $(date '+%F %T') ==="
