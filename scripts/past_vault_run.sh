#!/usr/bin/env bash
# 과거 금고(docs/protocols/past-vault-2026-10.md) 실행기.
#   bake  — 점수 굽기(TA → TTM, 가격만). 크론 10/11(일) 01:00 한 번. 이미 있으면 건너뛴다.
#   judge — 판정(10/13 16:45 KST 뒤에만 도구가 연다). 크론 10/13 16:50 — 금고 early 러너(vault_early_judge.sh)의 잠금이 풀릴 때까지 기다린다.
# 둘 다 끝나면(로그 '끝 rc=') 크론 줄을 지운다. 결과 표: logs/past-vault-judge-result.txt
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p data/_locks logs
MODE="${1:-}"
LOG="logs/past-vault-${MODE}-$(date +%Y%m%d).log"
exec >> "${LOG}" 2>&1
echo "=== $(date '+%F %T') 과거 금고 ${MODE} ==="
ENVS=(QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2)
case "${MODE}" in
  bake)
    N=$(.venv/bin/python tools/backfill_past_vault.py --check | sed -n 's/.*적재 \([0-9]*\).*/\1/p')
    echo "백필 적재 세션 ${N:-?}"
    if pgrep -f "tools/backfill_past_vault.py" > /dev/null || [ "${N:-0}" -lt 2800 ]; then
        echo "백필이 덜 됐다 — 굽지 않는다"; echo "끝 rc=4"; exit 4
    fi
    # 메모리가 모자라면 기다린다(10/11 아침 다른 프로젝트 solve.py 5.2GB 와 겹쳐 시스템이 굽기를 죽였다). 최대 8시간.
    for _ in $(seq 1 96); do
        AVAIL=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
        [ "${AVAIL}" -ge 6 ] && break
        echo "$(date '+%T') 가용 메모리 ${AVAIL}GB < 6GB — 5분 기다림"; sleep 300
    done
    env "${ENVS[@]}" nice -n 10 .venv/bin/python -u tools/past_vault_bake.py --what ta; RC=$?
    [ "${RC}" -eq 0 ] && { env "${ENVS[@]}" nice -n 10 .venv/bin/python -u tools/past_vault_bake.py --what ttm; RC=$?; }
    ;;
  judge)
    exec 9>data/_locks/vault-early-judge.lock
    flock -w 14400 9 || { echo "금고 early 러너 잠금을 4시간 기다렸다 — 손으로 돌릴 것"; echo "끝 rc=5"; exit 5; }
    env "${ENVS[@]}" nice -n 5 .venv/bin/python -u tools/past_vault_judge.py | tee logs/past-vault-judge-result.txt; RC=${PIPESTATUS[0]}
    ;;
  *) echo "사용: $0 bake|judge"; exit 2 ;;
esac
echo "끝 rc=${RC}"
exit "${RC}"
