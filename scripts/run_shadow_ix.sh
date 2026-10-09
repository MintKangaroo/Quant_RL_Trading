#!/usr/bin/env bash
# IX·IX0 트랙 shadow — 국장, 모의 체결(docs/protocols/ix-index-minus-losers-2026-10.md). 장부 data/_ix_shadow(처리)·data/_ix0_shadow(대조).
# 설정은 config/shadow/ix{,0}.config-overrides.yaml 을 첫 실행에 샌드박스로 복사한다. 두 장부는 같은 세션을 차례로 돈다(머신을 나누지 않는다).
# KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(run_shadow_z2.sh 와 같은 대기 패턴).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-ix-$(date +%Y%m).log"
# 등록(docs/protocols/ix-index-minus-losers-2026-10.md): 첫 세션은 2026-10-12 — 그 세션을 도는 첫 크론은 10/13 00:25 다.
# 그 전 크론(10/10 토 00:25)은 마지막 거래일 10/8 로 장부를 시작해 버린다. 그래서 그 전에는 아무것도 안 한다.
if [[ "$(date +%F)" < "2026-10-13" ]]; then
    echo "$(date '+%F %T') 등록 첫 세션(10/12) 전 — 건너뛴다" >> "${LOG}"
    exit 0
fi
for _ in $(seq 1 60); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
RC=0
for book in ix ix0; do
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
