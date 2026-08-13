#!/usr/bin/env bash
# event 재측정 — 피처별로 먼저 보고, 그 다음 종합. chart 도 다시 잰다
# (low_volatility 를 뺐으므로 이전 +0.0197 은 더 이상 이 코드의 성적이 아니다).
set -u
cd "$(dirname "$0")/.." || exit 1
echo "=== 시작 $(date '+%F %T') ==="

echo "=== event 피처별 $(date '+%F %T') ==="
( ulimit -v 8388608
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
    .venv/bin/python tools/measure_features.py --analyst event --market KR --sessions 300
) >logs/feat-event2.log 2>&1
echo "=== event 피처별 종료(rc=$?) $(date '+%F %T') ==="

for analyst in event chart; do
    echo "=== ${analyst} 종합 $(date '+%F %T') ==="
    ( ulimit -v 8388608
      QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/measure_ic.py \
        --analyst "${analyst}" --market KR --sessions 300 --save
    ) >"logs/ic2-${analyst}.log" 2>&1
    echo "=== ${analyst} 종합 종료(rc=$?) $(date '+%F %T') ==="
done
echo "=== 전부 끝 $(date '+%F %T') ==="
