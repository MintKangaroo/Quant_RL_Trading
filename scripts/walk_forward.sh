#!/usr/bin/env bash
# 워크포워드 백테스트 — 과거 시점 IC 측정 → 그 가중치로 백테스트.
#
#   scripts/walk_forward.sh 2026-01-02 2026-03-31
#
# 두 단계를 한 스크립트로 묶는 이유는 순서가 곧 정직성이기 때문이다. 평가
# 구간을 본 IC 로 그 구간을 채점하면 성적은 전략의 것이 아니다 (backtest.md §7).
#
# 가중치는 **샌드박스에만** 쓴다. 실전 창고에 과거 observed_at 을 심으면
# "그때 알았던 것" 이라는 거짓 기록이 남는다.
set -euo pipefail

cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

START="${1:?평가 시작일 (YYYY-MM-DD)}"
END="${2:?평가 종료일 (YYYY-MM-DD)}"
SANDBOX="${SANDBOX:-data/_backtest}"
SESSIONS="${SESSIONS:-300}"

# 창고가 수십만 개 Parquet 이라 상한을 안 주면 DuckDB 가 머신을 통째로 멈춘다.
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-3GB}"
export QUANT_RL_DUCKDB_THREADS="${QUANT_RL_DUCKDB_THREADS:-2}"

echo "=== [1/3] 오버레이 준비 ($SANDBOX)"
uv run python tools/run_backtest.py --build-only --sandbox "$SANDBOX"

echo "=== [2/3] IC 측정 — ${START} 이전만 보고 잰다"
uv run python tools/measure_ic.py \
    --analyst chart event flow_kr fundamental regime risk \
    --market KR \
    --sessions "$SESSIONS" \
    --as-of "${START}T15:40:00+09:00" \
    --data-root "$SANDBOX" \
    --save || echo "  (일부 Analyst 미통과 — 가중치 0 으로 적재됐다면 정상이다)"

echo "=== [3/3] 백테스트 ${START} ~ ${END}"
uv run python tools/run_backtest.py \
    --start "$START" --end "$END" --sandbox "$SANDBOX"
