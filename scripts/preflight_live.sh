#!/usr/bin/env bash
# 실주문 사전점검 — 개장 직후 크론이 돌린다. **주문은 나가지 않는다.**
#
#   scripts/preflight_live.sh KR 027360
#   scripts/preflight_live.sh US WEN
#
# tools/preflight_live_order.py 는 조회 TR 만 부른다 — 주문·정정·취소를 부르는
# 코드가 그 파일에 없다. 그래서 무인으로 돌려도 나갈 경로가 존재하지 않는다.
# 사람은 이 로그를 보고 verify_live_order.py 를 한 번 실행하면 된다.
set -u

cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
SYMBOL="${2:-}"
QTY="${3:-1}"

if [ -z "${SYMBOL}" ]; then
    echo "종목코드가 없다: preflight_live.sh <KR|US> <symbol> [qty]" >&2
    exit 2
fi

mkdir -p logs
LOG="logs/preflight-${MARKET}-$(date +%Y%m%d).log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — ${MARKET} ${SYMBOL} × ${QTY} ==="
    .venv/bin/python tools/preflight_live_order.py \
        --market "${MARKET}" --symbol "${SYMBOL}" --quantity "${QTY}"
    echo "  종료코드 $?"
    echo
} >> "${LOG}" 2>&1
