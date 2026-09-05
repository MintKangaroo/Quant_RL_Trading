#!/usr/bin/env bash
# 기업행위 보정 뒤 IC 전면 재측정 (KR).
#
# 2026-08-16 에 adj_factor 백필이 들어갔다 — 729종목·1,123건. 그 전의 IC 는
# **전부 원주가 위에서 잰 것**이다. 계수가 전부 None 이었고(adj-factor 결함),
# 액면분할이 그대로 -98% 수익률로 라벨에 들어가 있었다. 모멘텀 창이 250일이면
# 사건 하나가 그 뒤 250세션을 오염시킨다.
#
# 표본 확인 (read_prices 원주가 → 보정가, 일간 최대 절대수익률):
#   KR:192400  80.4% → 13.0%     KR:053080  56.4% → 29.9%
#   KR:293780  53.0% → 29.8%
#
# 비교 기준 (2026-08-13~15, KR 300세션, 원주가):
#   fundamental +0.0618   event +0.0332   regime -0.0057   flow_kr -0.0011
#   chart·risk 는 순위 정규화 1차에서 따로 쟀다
#
# --save 를 건다. analyst_weights 는 append-only 라 마지막 행이 유효하고,
# 나빠지면 되돌려 다시 재서 새 행으로 정정한다 (ic_queue_rank2.sh 와 같은 규약).
#
# 순차로만 돈다. 동시에 재면 DuckDB 한도가 프로세스마다 따로 걸려 합이 머신
# RAM(9.7GB)을 넘고, used 8GB 에서 서버가 통째로 멈춘다 (ic_queue.sh).
set -u

cd "$(dirname "$0")/.." || exit 1

ANALYSTS=(fundamental risk chart event regime flow_kr)

echo "=== 보정 IC 큐 시작 $(date '+%F %T') ==="

for analyst in "${ANALYSTS[@]}"; do
    echo "=== ${analyst} 시작 $(date '+%F %T') ==="
    (
        ulimit -v 8388608
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/measure_ic.py \
            --analyst "${analyst}" --market KR --sessions 300 --save
    ) >"logs/ic-adj-${analyst}.log" 2>&1
    echo "=== ${analyst} 종료(rc=$?) $(date '+%F %T') ==="
    sleep 5
done

echo "=== 보정 IC 큐 끝 $(date '+%F %T') ==="
