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
# **rc 를 밖으로 내보낸다.** 예전에는 블록의 마지막이 `echo` 라 파이썬이
# 무엇으로 끝나든 스크립트는 0 이었다. 크론이 보는 것은 이 값 하나다.
RC=0
{
    echo "=== $(date '+%F %T') market=${MARKET} ==="
    # 가상 주소공간 16GB. 8GB 는 2026-09-02·03 미장 daily 를 죽였다 — RSS 는 2.2GB 였는데
    # 스레드 아레나·duckdb·OpenMP 가 가상 공간을 먼저 채운다. 실메모리는 memory-guard 가 본다.
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_daily.py --market "${MARKET}"
    RC=$?
    echo "rc=${RC}"
} >>"${LOG}" 2>&1
exit "${RC}"
