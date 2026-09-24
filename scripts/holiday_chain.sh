#!/usr/bin/env bash
# 추석 휴장 당김(2026-09-24, 사용자 지시) 1단 — 등록 전 점검 끝나길 기다렸다 AM(loop→judge) → AO. 여기서 멈춘다:
# AM 결과를 보고 6차 기준 ② 를 정정할지 사람이(세션이) 정한 뒤 6차를 따로 시작한다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG=logs/holiday-chain-20260924.log
{
  echo "=== $(date '+%F %T') 휴장 사슬 1단 시작 ==="
  while pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null \
        || pgrep -f "scripts/run_precheck\.s[h]" > /dev/null; do sleep 60; done
  echo "$(date '+%F %T') 점검 끝 — AM"
  AMLOG=logs/trial-portfolio-variance-AM.log
  if ! grep -q "^판정:" "${AMLOG}" 2>/dev/null; then
    export MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB
    nice -n 5 .venv/bin/python -u tools/trial_portfolio_variance.py --stage loop >> "${AMLOG}" 2>&1 \
      && nice -n 5 .venv/bin/python -u tools/trial_portfolio_variance.py --stage judge --save >> "${AMLOG}" 2>&1
    echo "rc=$?" >> "${AMLOG}"
  fi
  echo "$(date '+%F %T') AM 끝($(grep -h '^판정:' ${AMLOG} | tail -1)) — AO"
  scripts/run_trial_once.sh tools/trial_rebalance_cadence.py logs/trial-rebalance-cadence-AO.log
  echo "$(date '+%F %T') AO 끝($(grep -h '^판정:' logs/trial-rebalance-cadence-AO.log | tail -1)) — 1단 끝. 6차는 AM 판독 뒤 세션이 시작한다"
} >> "${LOG}" 2>&1
