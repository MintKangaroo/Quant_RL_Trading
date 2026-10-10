#!/usr/bin/env bash
# TG 트랙 shadow — 국장, 모의 체결(docs/protocols/ta-floor-2026-10.md). 장부 data/_tg_shadow(처리, 대조는 data/_n24_shadow).
# 설정은 config/shadow/tg.config-overrides.yaml 을 첫 실행에 샌드박스로 복사한다. 신호는 scripts/run_ta_signal.sh(00:05).
# KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(run_shadow_z2.sh 와 같은 대기 패턴).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-tg-$(date +%Y%m).log"
# 등록(docs/protocols/ta-floor-2026-10.md): 첫 결정 세션 2026-10-14 — 그 세션을 도는 첫 크론은 10/15 00:45 다.
# 그 전 크론은 더 이른 세션으로 장부를 시작해 버린다. 그래서 그 전에는 아무것도 안 한다.
if [[ "$(date +%F)" < "2026-10-15" ]]; then
    echo "$(date '+%F %T') 등록 첫 세션(10/14) 전 — 건너뛴다" >> "${LOG}"
    exit 0
fi
for _ in $(seq 1 60); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
RC=0
for book in tg; do
    box="data/_${book}_shadow"
    first=""
    if [ ! -f "${box}/config-overrides.yaml" ]; then
        mkdir -p "${box}" && cp "config/shadow/${book}.config-overrides.yaml" "${box}/config-overrides.yaml"
        first="--capital 503000000"
    fi
    (
        echo "=== $(date '+%F %T') ${book} shadow (KR) ==="
        ulimit -v 16777216
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/run_session.py --market KR --sandbox "${box}" ${first}
        rc=$?
        echo "rc=${rc}"
        exit "${rc}"
    ) >> "${LOG}" 2>&1
    r=$?
    [ "${r}" -ne 0 ] && RC="${r}"
done
exit "${RC}"
