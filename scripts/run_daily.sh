#!/usr/bin/env bash
# 일일 실행 — Analyst 점수와 News·SNS 판정을 창고에 남긴다.
#
# **뉴스 수집 뒤에 돌아야 한다.** 게이트가 observed_at <= as_of 로 거르므로,
# 수집보다 앞선 시점을 기준으로 돌면 그날 뉴스가 아예 안 보인다. 버그가
# 아니라 규칙대로인데, 순서를 틀리면 판정이 조용히 0건이 된다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
LOG="logs/daily-$(date +%Y%m).log"
{
    echo "=== $(date '+%F %T') market=${MARKET} ==="
    ulimit -v 8388608
    LATTICE_DUCKDB_MEMORY_LIMIT=1GB LATTICE_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_daily.py --market "${MARKET}"
    echo "rc=$?"
} >>"${LOG}" 2>&1
