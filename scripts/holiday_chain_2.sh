#!/usr/bin/env bash
# 휴장 사슬 2단(2026-09-24) — 건너뛴 AP 등록 전 점검 → 6차 G1~G7(G8 은 9/28 전 스크립트가 멈춘다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
{
  echo "=== $(date '+%F %T') 2단: AP 점검 → 6차 ==="
  scripts/run_precheck.sh tools/trial_temporal_uncertainty.py logs/precheck-temporal-AP.log
  echo "$(date '+%F %T') AP 점검 끝($(grep -h '^등록 전 점검' logs/precheck-temporal-AP.log | tail -1 | cut -c1-80))"
  scripts/measure_ranker_round6.sh
  echo "$(date '+%F %T') 6차 사슬 끝"
} >> logs/holiday-chain-20260924.log 2>&1
