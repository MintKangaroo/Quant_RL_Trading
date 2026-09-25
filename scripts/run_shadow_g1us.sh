#!/usr/bin/env bash
# 미장 G1 트랙 shadow — 시총 상위 500 을 시총 가중(종목 10% 상한)으로 들고 합성 점수 하위 10% 만 뺀다
# (docs/design/portfolio-construction.md "미장 G1 트랙", 시행 AG). 설정은 data/_g1us_shadow/config-overrides.yaml.
# 기존 미장 shadow(data/_shadow, 12:20)가 끝난 뒤 돈다(머신을 나누지 않는다). 첫 실행만 자본을 넣는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-g1us-$(date +%Y%m).log"
BOOK=data/_g1us_shadow
for _ in $(seq 1 80); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market US" > /dev/null || break
    sleep 30
done
EXTRA=()
[ -e "${BOOK}/.funded" ] || EXTRA=(--capital 503000000)
RC=0
{
    echo "=== $(date '+%F %T') G1 미장 shadow ==="
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market US --sandbox "${BOOK}" "${EXTRA[@]}"
    RC=$?
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
if [ ${#EXTRA[@]} -gt 0 ] && { [ ${RC} -eq 0 ] || [ ${RC} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
exit "${RC}"
