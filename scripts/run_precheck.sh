#!/usr/bin/env bash
# 등록 전 점검(예산 없음) 한 번 — 수익은 계산하지 않는다. 이미 결과가 있으면 건너뛴다.
#     scripts/run_precheck.sh <도구.py> <로그>
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
TOOL="$1"; LOG="$2"
grep -q "^등록 전 점검:" "${LOG}" 2>/dev/null && exit 0
# **파이썬 프로세스만 찾는다** — 이 스크립트는 인자로 tools/trial_*.py 를 받아 그냥 "tools/trial_" 로 찾으면 자기 자신이 잡힌다
# (2026-09-23 20:07 AN 점검이 "다른 무거운 작업" 으로 건너뜀 — memory background-job-hygiene 의 pgrep 자기매칭).
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 4500 ] && { echo "$(date '+%F %T') 가용 ${AVAIL}MB — 건너뜀" >> "${LOG}"; exit 0; }
{
    echo "=== $(date '+%F %T') ${TOOL} --precheck ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u "${TOOL}" --precheck
    echo "rc=$?"
} >> "${LOG}" 2>&1
