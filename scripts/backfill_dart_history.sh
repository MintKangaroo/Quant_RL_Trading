#!/usr/bin/env bash
# DART 과거 백필 — RL 학습 구간을 2021 년까지 넓히는 데이터 작업.
#
# **왜 하나.** 1회차 학습이 과적합으로 판정됐다(2026-08-25). 학습 구간 362일에
# 에피소드 250일이라 같은 시작점을 714번 반복해서 봤다. 가격은 2021-08 부터
# 이미 있고, 막는 것은 Analyst 입력(fundamentals 2025-05·documents 2025-07)
# 뿐이다. 이 백필로 시작점이 112 → 940, 반복이 714 → 85회가 된다
# (rl-training.md §에피소드 설계).
#
# **밤에 돈다.** DART API 를 오래 두드리고, 정기 파이프라인(22:40~23:05)과
# 겹치면 둘 다 느려진다. 순서대로 하나씩 돌린다 — 병렬이면 DART 쿼터를
# 두 배로 태운다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/backfill-dart-history.log"
RC=0
{
    echo "=== $(date '+%F %T') DART 5년 백필 시작 ==="
    # ulimit -v 를 걸지 않는다 — 2026-08-23 학습, 2026-08-25 이 스크립트 자신이
    # 그것 때문에 조용히 죽었다. DuckDB 메모리 상한이 진짜 안전장치다.
    for TABLE in fundamentals-dart documents-dart; do
        echo "--- ${TABLE} ---"
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python -u tools/backfill.py \
            --table "${TABLE}" --market KR --years 5
        rc=$?
        echo "${TABLE} rc=${rc}"
        # **첫 표가 죽으면 둘째도 안 간다.** 쿼터 소진·키 만료면 계속해 봐야
        # 같은 이유로 죽고, 로그만 두 배가 된다.
        if [ "${rc}" -ne 0 ]; then RC=${rc}; break; fi
    done
    echo "=== $(date '+%F %T') 끝 rc=${RC} ==="
} >>"${LOG}" 2>&1
exit "${RC}"
