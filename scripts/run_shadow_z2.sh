#!/usr/bin/env bash
# Z2 트랙 shadow — 국장, 모의 체결(docs/design/portfolio-construction.md "Z2 트랙"). 설정은 data/_z2_shadow/config-overrides.yaml.
# 비교 상대는 같은 체결 방식의 KR shadow(data/_shadow, 23:05) — 차이는 K200 필터와 유동시총 가중 둘뿐이다.
# KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-z2-$(date +%Y%m).log"
for _ in $(seq 1 40); do
    pgrep -f "tools/run_session.py --market KR$" > /dev/null || pgrep -f "tools/run_session.py --market KR --capital" > /dev/null || break
    sleep 30
done
{
    echo "=== $(date '+%F %T') Z2 shadow (KR) ==="
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market KR --sandbox data/_z2_shadow
    echo "rc=$?"
} >> "${LOG}" 2>&1
