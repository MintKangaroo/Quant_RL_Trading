#!/usr/bin/env bash
# 등록된 시행 하나를 한 번 돌린다(크론용). 이미 '판정:' 이 있으면 건너뛰고, 무거운 작업·메모리 부족이면 건너뛰며 이유를 적는다.
#     scripts/run_trial_once.sh <도구.py> <로그> [등록 전 점검 로그]
# 세 번째 인자를 주면 그 로그에 '측정 진행' 이 있어야만 돈다(복기 규칙 ③ — 점검을 통과한 시행만 예산을 쓴다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
TOOL="$1"; LOG="$2"; PRE="${3:-}"
grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
if [ -n "${PRE}" ] && ! grep -q "측정 진행" "${PRE}" 2>/dev/null; then
    echo "$(date '+%F %T') 등록 전 점검을 통과하지 않았거나 아직 안 했다(${PRE}) — 돌리지 않는다" >> "${LOG}"; exit 0
fi
# **파이썬 프로세스만 찾는다** — 이 스크립트는 인자로 tools/trial_*.py 를 받아 그냥 "tools/trial_" 로 찾으면 자기 자신이 잡힌다
# (2026-09-23 20:07 AN 점검이 "다른 무거운 작업" 으로 건너뜀 — memory background-job-hygiene 의 pgrep 자기매칭).
if pgrep -f "bin/python[^ ]* .*tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 4500 ] && { echo "$(date '+%F %T') 가용 ${AVAIL}MB — 건너뜀" >> "${LOG}"; exit 0; }
{
    echo "=== $(date '+%F %T') ${TOOL} ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u "${TOOL}" --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
