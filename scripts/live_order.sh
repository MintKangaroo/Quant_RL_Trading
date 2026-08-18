#!/usr/bin/env bash
# 실주문 배선 검증 — **무인 실행. 진짜 돈이 나간다.**
#
#   scripts/live_order.sh US SNAP 1
#   scripts/live_order.sh KR 027360 1
#
# ``preflight_live.sh`` 는 조회만 한다. 이 스크립트는 **주문을 낸다.**
# 사람이 22:35 에 앉아 있을 수 없어서 만든 자리다(사용자 요청 2026-08-17).
#
# ## 무인인데 왜 안전한가 — 게이트 다섯
#
# 1. ``--assume-yes`` 는 절차 확인만 승인한다. **위험 확인("…그래도
#    계속할까?")은 자동 거부**다 — 킬스위치·장외시간·거래제한이면 그 자리에서
#    멈춘다 (``verify_live_order.OVERRIDE_MARKER``)
# 2. ``execution.live_trading`` 이 켜져 있어야 한다. 2026-08-17 22:00 발효
# 3. **계좌 지문 고정.** ``.env`` 가 바뀌면 지문이 달라져 도구가 거부한다
# 4. ``LS_*ACCOUNT_KIND=real`` 선언이 있어야 한다. 모르는 것을 모의로
#    가정하지 않는다
# 5. ``--max-order-value`` 상한. 기본은 국장 5,000원 · 미장 $20
#
# ## 수량은 1주다
#
# 목적이 수익이 아니라 **배선 검증**이다(M3 완료기준 4번). 주문이 실제로
# 나가고 체결이 장부에 들어오는지만 본다. 어제 그 경로에서 장부 동결 결함이
# 둘 나왔다.
set -u

cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-US}"
SYMBOL="${2:-}"
QTY="${3:-1}"
#: 네 번째 인자가 "market" 이면 시장가로 낸다. **체결을 보는 것이 목적일 때만.**
#: 2026-08-17 미장 검증이 지정가로 나갔는데, 체결은 됐지만($5.41 지정가 →
#: $5.16 체결) 우리 조회가 그걸 못 봐서 미체결로 오판했다. 시장가로 두면
#: 체결 여부가 변수에서 빠진다.
ORDER_KIND="${4:-limit}"

if [ -z "${SYMBOL}" ]; then
    echo "종목코드가 없다: live_order.sh <KR|US> <symbol> [qty]" >&2
    exit 2
fi

mkdir -p logs
LOG="logs/live-order-${MARKET}-$(date +%Y%m%d).log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — ${MARKET} ${SYMBOL} × ${QTY} ${ORDER_KIND} (무인 실주문) ==="
    .venv/bin/python tools/verify_live_order.py \
        --market "${MARKET}" --symbol "${SYMBOL}" --quantity "${QTY}" \
        --live --assume-yes \
        $( [ "${ORDER_KIND}" = "market" ] && echo "--market-order" )
    echo "  종료코드 $?"
    echo
} >> "${LOG}" 2>&1
