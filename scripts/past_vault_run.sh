#!/usr/bin/env bash
# 과거 금고(docs/protocols/past-vault-2026-10.md) 실행기.
#   bake  — 점수 굽기(TA → TTM, 가격만). 크론 10/11(일) 01:00 한 번. 이미 있으면 건너뛴다.
#   judge — 판정(10/13 16:45 KST 뒤에만 도구가 연다). 크론 10/13 16:50 — 금고 early 러너(vault_early_judge.sh)의 잠금이 풀릴 때까지(22:10 KST 까지) 기다린다.
#           TF·TB·TG 다음에 N1(docs/protocols/past-vault-n1-2026-10.md). 22:10 이후엔 시작하지 않는다(운영 창 22:20 보호).
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
    exec 8>data/_locks/past-vault-bake.lock
    flock -n 8 || { echo "이미 굽는 중(다른 실행이 대기·진행 중) — rc 0"; exit 0; }
    if [ -f data/_past_vault/_bake/ta.pkl ] && [ -f data/_past_vault/_bake/ttm.pkl ]; then echo "둘 다 있다 — 할 일 없음"; echo "끝 rc=0"; exit 0; fi
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
    # 저녁 일정(2차 검토 10/11): 잠금 대기는 22:10 KST 까지(운영 창 22:20 보호). N1 결과 파일은 어느 경로든 먼저 지운다.
    N1_OUT=logs/past-vault-n1-result.txt
    rm -f "${N1_OUT}"
    DEADLINE=$(TZ=Asia/Seoul date -d "today 22:10" +%s)
    WAIT=$(( DEADLINE - $(date +%s) ))
    [ "${WAIT}" -lt 1 ] && WAIT=1
    exec 9>data/_locks/vault-early-judge.lock
    if ! flock -w "${WAIT}" 9; then
        echo "금고 early 러너 잠금을 22:10 KST 까지 기다렸다 — 10/14 재시도 필요(손으로: scripts/past_vault_run.sh judge)"
        echo "N1 거부: early 잠금 대기 22:10 초과 — 10/14 재시도" > "${N1_OUT}"
        echo "끝 rc=5"; exit 5
    fi
    env "${ENVS[@]}" nice -n 5 .venv/bin/python -u tools/past_vault_judge.py | tee logs/past-vault-judge-result.txt; RC=${PIPESTATUS[0]}
    echo "TF/TB/TG 끝 rc=${RC}"
    # N1(docs/protocols/past-vault-n1-2026-10.md) — TF·TB·TG 확정 뒤에 따로. 실패해도 위 결과 파일·RC 는 그대로 둔다(rc 는 줄로만 남긴다).
    # 거부·실패는 'N1 거부: 사유' 한 줄 → switch_report 가 '(거부: 사유)'.
    RC_N1_BAKE=-1; RC_N1_JUDGE=-1
    if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
        echo "N1 거부: 운영 창 — 22:10 KST 이후라 시작하지 않음, 10/14 재시도" > "${N1_OUT}"
    else
        MEM_OK=0
        for _ in $(seq 1 7); do      # 메모리 대기 ≥6GB, 최대 30분(5분 × 6) · 22:10 넘기지 않음
            AVAIL=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
            if [ "${AVAIL}" -ge 6 ]; then MEM_OK=1; break; fi
            [ "$(( $(date +%s) + 300 ))" -ge "${DEADLINE}" ] && break
            echo "$(date '+%T') N1: 가용 메모리 ${AVAIL}GB < 6GB — 5분 기다림"; sleep 300
        done
        if [ "${MEM_OK}" -ne 1 ]; then
            echo "N1 거부: 가용 메모리 6GB 미달(30분 대기·22:10 상한) — 10/14 재시도" > "${N1_OUT}"
        elif [ "$(date +%s)" -ge "${DEADLINE}" ]; then
            echo "N1 거부: 운영 창 — 22:10 KST 이후라 시작하지 않음, 10/14 재시도" > "${N1_OUT}"
        else
            env "${ENVS[@]}" nice -n 10 .venv/bin/python -u tools/past_vault_n1_bake.py; RC_N1_BAKE=$?
            if [ "${RC_N1_BAKE}" -ne 0 ]; then
                echo "N1 거부: 굽기 실패 rc=${RC_N1_BAKE}" > "${N1_OUT}"
            else
                env "${ENVS[@]}" nice -n 5 .venv/bin/python -u tools/past_vault_judge_n1.py > "${N1_OUT}" 2>&1; RC_N1_JUDGE=$?
                if [ "${RC_N1_JUDGE}" -ne 0 ] && ! grep -q "^N1 거부:" "${N1_OUT}"; then
                    echo "N1 거부: 판정 예외 rc=${RC_N1_JUDGE}(로그 위 참조)" >> "${N1_OUT}"
                fi
            fi
        fi
    fi
    cat "${N1_OUT}"
    echo "N1 굽기 rc=${RC_N1_BAKE} · N1 판정 rc=${RC_N1_JUDGE} (-1 = 돌지 않음 · 기존 판정 rc 에 섞지 않는다)"
    ;;
  *) echo "사용: $0 bake|judge"; exit 2 ;;
esac
echo "끝 rc=${RC}"
exit "${RC}"
