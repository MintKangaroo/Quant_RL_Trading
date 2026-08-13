#!/usr/bin/env bash
# 뉴스 수집 — 운영용 래퍼. crontab 이 이걸 부른다.
#
# **하루 요청 한도가 100 이다** (newsapi 무료 티어). 종목 하나에 요청 하나이므로
# 실행당 종목 수 × 실행 횟수가 100 을 넘으면 안 된다. 지금 배치는
# 30종목 × 2회 = 60 으로, 임시 재실행 여유를 남긴 값이다.
#
# 한도를 넘기면 그날 남은 조회가 전부 실패하는데, 뉴스 필터는 **매수를 막는**
# 쪽이라 조용히 비면 위험한 종목이 후보에 남는다. 여유를 두는 이유가 이것이다.
set -u

cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

LIMIT="${1:-30}"
MARKET="${2:-KR}"
LOG="logs/news-$(date +%Y%m).log"

{
    echo "=== $(date '+%F %T') market=${MARKET} limit=${LIMIT} ==="
    # DuckDB 한도를 조인다. 이 머신은 9.7GB 이고 국장·미장 워커가 같이 도는
    # 시간대가 있어, 수집기 하나가 메모리를 크게 잡으면 그쪽이 죽는다.
    ulimit -v 8388608
    QUANT_RL_DUCKDB_MEMORY_LIMIT=512MB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/collect_news.py --market "${MARKET}" --limit "${LIMIT}"
    echo "rc=$?"
} >>"${LOG}" 2>&1
