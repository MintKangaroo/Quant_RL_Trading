#!/usr/bin/env bash
# 늦게 온 종가로 회계 스냅샷을 다시 찍는다. **22:40 일봉 수집 뒤에 돈다.**
#
# 국장 일봉은 장이 끝나고 한참 뒤에 나오는데 세션은 16:00 에 돈다. 그래서
# 그 시각 NAV 는 어제 종가로 계산되고, 오늘 행에 어제 값이 박힌다 —
# 화면에는 "오늘 수익률 0.00%" 로 보인다(2026-08-19 실측 -1.13%).
#
# 실전·shadow 둘 다 고친다. 창고가 다르면 화면도 갈린다.
#
# **ulimit -v 를 쓰지 않는다.** DuckDB 는 mmap 때문에 가상주소가 실사용보다
# 훨씬 크고, 그 제한에 걸리면 예외가 아니라 rc=139(세그폴트)로 죽는다.
# 메모리는 QUANT_RL_DUCKDB_MEMORY_LIMIT 로 조인다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
LOG="logs/accounting-$(date +%Y%m).log"

{
    echo "=== $(date '+%F %T') market=${MARKET} ==="
    for ROOT in data data/_shadow; do
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/refresh_accounting.py \
                --market "${MARKET}" --root "${ROOT}"
        echo "  rc=$?"
    done
} >>"${LOG}" 2>&1
