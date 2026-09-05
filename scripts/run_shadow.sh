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
# **rc 를 밖으로 내보낸다.** 블록의 마지막이 `echo` 면 파이썬이 무엇으로
# 끝나든 스크립트는 0 이다 — 크론이 보는 것은 이 값 하나다.
RC=0
{
    echo "=== $(date '+%F %T') market=${MARKET} ==="
    # 가상 주소공간 16GB. 8GB 는 2026-09-02·03 미장 daily 를 죽였다 — RSS 는 2.2GB 였는데
    # 스레드 아레나·duckdb·OpenMP 가 가상 공간을 먼저 채운다. 실메모리는 memory-guard 가 본다.
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market "${MARKET}" --capital "${CAPITAL}"
    RC=$?
    echo "rc=${RC}"
} >>"${LOG}" 2>&1
exit "${RC}"
