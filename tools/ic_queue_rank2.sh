#!/usr/bin/env bash
# 순위 정규화 2차 — 남은 4개 Analyst 재측정 (KR).
#
# chart·risk 는 7e246de 에서 이미 바꿨다. 이 큐는 아직 z 를 쓰던 나머지
# event·fundamental·regime·flow_kr 을 같은 방식으로 재는 것이다.
#
# 비교 기준(2026-08-13, KR 300세션):
#   event  +0.0332   fundamental +0.0618   regime -0.0057   flow_kr -0.0011
#
# --save 를 건다. 나빠지면 코드를 되돌리고 다시 재서 새 행으로 정정한다
# (append-only 니까 마지막 행이 유효하다). 통과한 둘을 먼저 돌리는 이유는,
# 손해라면 빨리 알고 되돌리기 위해서다.
#
# 순차로만 돈다 — 미장 백필이 도는 중이라 동시에 재면 8GB 벽을 친다.
set -u

cd "$(dirname "$0")/.." || exit 1

ANALYSTS=(fundamental event regime flow_kr)

echo "=== 순위 2차 큐 시작 $(date '+%F %T') ==="

for analyst in "${ANALYSTS[@]}"; do
    echo "=== ${analyst} 시작 $(date '+%F %T') ==="
    (
        ulimit -v 8388608
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/measure_ic.py \
            --analyst "${analyst}" --market KR --sessions 300 --save
    ) >"logs/ic-rank2-${analyst}.log" 2>&1
    echo "=== ${analyst} 종료(rc=$?) $(date '+%F %T') ==="
    sleep 5
done

echo "=== 순위 2차 큐 전부 끝 $(date '+%F %T') ==="
