#!/usr/bin/env bash
# 미장 G1 트랙 shadow — 시총 상위 500 을 시총 가중(종목 10% 상한)으로 들고 합성 점수 하위 10% 만 뺀다
# (docs/design/portfolio-construction.md "미장 G1 트랙", 시행 AG). 설정은 data/_g1us_shadow/config-overrides.yaml.
# 기존 미장 shadow(data/_shadow, 12:20)가 끝난 뒤 돈다(머신을 나누지 않는다). 자본은 달러로 미리 넣었다($370k, .funded) —
# 원화로 넣으면 미장 주문가능금액(달러 현금)이 0 이라 주문이 안 나간다(2026-09-25 첫 시험 세션 주문 0).
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
