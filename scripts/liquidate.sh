#!/usr/bin/env bash
# 계좌 청산 — **보유 전량을 시장가로 판다. 진짜 돈이 나간다.**
#
#   scripts/liquidate.sh KR
#   scripts/liquidate.sh US
#
# 정규장에만 의미가 있다. 장외면 도구가 주문을 못 내고 실패로 끝난다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
mkdir -p logs
LOG="logs/liquidate-${MARKET}-$(date +%Y%m%d).log"
{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — ${MARKET} 전량 청산 ==="
    .venv/bin/python tools/liquidate.py --market "${MARKET}" --live --assume-yes
    echo "  종료코드 $?"
    echo
} >> "${LOG}" 2>&1
