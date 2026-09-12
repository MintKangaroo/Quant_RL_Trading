#!/usr/bin/env bash
# 주 1회 IC 측정 — 랭커 ModelOps ①(docs/design/modelops-ranker.md). **토 14:00** 이다.
#   crontab: 0 14 * * 6 .../scripts/measure_ic_weekly.sh
#
# 12:00 이 아니다 — 토요일 12:00 에는 미장 일일 점수(run_daily.py --market US, RSS 2.7GB)가 돈다.
# 2026-09-12 첫 자동 실행이 그 둘이 겹쳐 가용 1.2GB 까지 떨어졌다(측정은 1GB, 메모리 가드는 600MB 아래에서
# measure_ic 를 죽인다 — 2~3시간짜리가 통째로 날아간다). 미장 점수는 12:20 shadow 의 입력이라 못 미룬다.
# 전 Analyst 를 두 시장에서 재고 --save 로 analyst_weights 에 적재한다(가중치는 한계기여 규칙이 정한다).
# rc 를 밖으로 낸다 — 조용한 실패 금지(memory silent-failure-needs-nonzero-rc).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
LOG="logs/ic-weekly-$(date +%Y%m).log"
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-2GB}"
FAILED=0
{
    echo "=== $(date '+%F %T') 주간 IC 측정 ==="
    for M in KR US; do
        # --exit-zero: 합격 여부는 숫자로 읽는다. rc 는 "측정을 못 했다" 일 때만 0 이 아니다.
        nice -n 5 .venv/bin/python -u tools/measure_ic.py \
            --market "$M" --sessions 300 --save --exit-zero
        rc=$?
        echo "  $M rc=$rc"
        [ "$rc" -ne 0 ] && FAILED=$((FAILED + 1))
    done
    echo "완료 — 실패 ${FAILED}건"
} >> "$LOG" 2>&1
exit "$FAILED"
