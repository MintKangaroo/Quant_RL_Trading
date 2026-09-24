#!/usr/bin/env bash
# 휴장 사슬 1단 재시도(2026-09-24 14:3x) — AM 판정만(학습 캐시는 이미 있다) → AO.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
AMLOG=logs/trial-portfolio-variance-AM.log
{
  echo "=== $(date '+%F %T') AM 판정 재시도(기간 나눠 읽기) ==="
  export MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB
  nice -n 5 .venv/bin/python -u tools/trial_portfolio_variance.py --stage judge --save >> "${AMLOG}" 2>&1
  echo "rc=$?" >> "${AMLOG}"
  echo "$(date '+%F %T') AM 끝($(grep -h '^판정:' ${AMLOG} | tail -1)) — AO"
  scripts/run_trial_once.sh tools/trial_rebalance_cadence.py logs/trial-rebalance-cadence-AO.log
  echo "$(date '+%F %T') AO 끝($(grep -h '^판정:' logs/trial-rebalance-cadence-AO.log | tail -1))"
} >> logs/holiday-chain-20260924.log 2>&1
