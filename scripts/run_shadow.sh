#!/usr/bin/env bash
# shadow 운용 — 하루치 세션을 돌린다. 돈은 오가지 않는다.
#
# **run_daily.sh 뒤에 돌아야 한다.** Selector 가 읽는 것이 그 스크립트가 남긴
# signals 다. 순서를 바꾸면 후보가 조용히 0건이 된다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
CAPITAL="${2:-0}"
LOG="logs/shadow-$(date +%Y%m).log"
{
    echo "=== $(date '+%F %T') market=${MARKET} ==="
    ulimit -v 8388608
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market "${MARKET}" --capital "${CAPITAL}"
    echo "rc=$?"
} >>"${LOG}" 2>&1
