#!/usr/bin/env bash
# 시행 AQ 전방 장부 둘 — 72종목(data/_w72_shadow) · 대조 24종목(data/_n24_shadow), 국장·모의 체결
# (docs/protocols/breadth72-forward-2026-09.md). 차이는 폭 하나. 첫 실행만 자본을 넣는다(입금 행이 없을 때).
# Z2 shadow 가 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-forward-$(date +%Y%m).log"
CAPITAL=503000000
# 등록: 첫 세션 2026-09-28(C10 anchor). 휴장 중 돌면 직전 거래일(9/23)로 장부가 먼저 선다.
[[ "$(date +%F)" < "2026-09-28" ]] && { echo "$(date "+%F %T") 첫 세션 전 — 건너뜀" >> "${LOG}"; exit 0; }
for _ in $(seq 1 60); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
rc_all=0
for BOOK in data/_w72_shadow data/_n24_shadow; do
    (
        echo "=== $(date '+%F %T') 전방 shadow ${BOOK} (KR) ==="
        EXTRA=()
        if [ ! -e "${BOOK}/.funded" ]; then EXTRA=(--capital "${CAPITAL}"); fi
        ulimit -v 16777216
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/run_session.py --market KR --sandbox "${BOOK}" "${EXTRA[@]}"
        rc=$?
        echo "rc=${rc}"
        # 입금은 첫 성공 세션에만 — rc 0(정상)·2(차단, 세션은 돌았다) 모두 장부가 섰다.
        if [ ${#EXTRA[@]} -gt 0 ] && { [ ${rc} -eq 0 ] || [ ${rc} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
        exit ${rc}
    ) >> "${LOG}" 2>&1
    rc=$?
    [ ${rc} -ne 0 ] && rc_all=${rc}
done
exit ${rc_all}
