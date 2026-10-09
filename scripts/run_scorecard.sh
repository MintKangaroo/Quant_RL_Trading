#!/usr/bin/env bash
# 자기개선 ① 채점지 — 라벨이 닫힌 결정 세션을 signal_scorecard 에 적는다(tools/scorecard.py, START 2026-10-15).
# 크론: 50 7 * * 2-6 — 전날 장 마감 종가가 들어온 뒤, 08:40 세션 전. 가볍다(분 단위).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/scorecard-$(date +%Y%m).log"
{
    echo "=== $(date '+%F %T') 채점지 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=800MB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 .venv/bin/python tools/scorecard.py
    RC=$?
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
exit "${RC:-1}"
