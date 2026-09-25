#!/usr/bin/env bash
# 시행 BB 전방 기록(shadow 승격 후보) — 같은 K200 유동시총 포트 둘: HMM 노출(data/_k200hmm_shadow) vs 규칙 V6(data/_k200v6_shadow).
# HMM 행동은 22:50 tools/v2_hmm_daily.py 가 창고(exposure_actions)에 적는다. 다른 KR shadow 가 도는 중이면 기다린다. 첫 실행만 자본.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-hmm-$(date +%Y%m).log"
[[ "$(date +%F)" < "2026-09-28" ]] && { echo "$(date "+%F %T") 첫 세션 전 — 건너뜀" >> "${LOG}"; exit 0; }
for _ in $(seq 1 80); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
rc_all=0
for BOOK in data/_k200hmm_shadow data/_k200v6_shadow; do
    (
        echo "=== $(date '+%F %T') ${BOOK} (KR) ==="
        EXTRA=()
        [ -e "${BOOK}/.funded" ] || EXTRA=(--capital 503000000)
        ulimit -v 16777216
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/run_session.py --market KR --sandbox "${BOOK}" "${EXTRA[@]}"
        rc=$?
        echo "rc=${rc}"
        if [ ${#EXTRA[@]} -gt 0 ] && { [ ${rc} -eq 0 ] || [ ${rc} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
        exit ${rc}
    ) >> "${LOG}" 2>&1
    rc=$?
    [ ${rc} -ne 0 ] && rc_all=${rc}
done
exit ${rc_all}
