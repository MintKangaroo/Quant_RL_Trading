#!/usr/bin/env bash
# 모의계좌 실운용 (backtest.md §9) — **LS 모의투자 계좌로 주문이 실제로 나간다.**
#
#   scripts/run_paper.sh session    08:40  전날 데이터로 결정하고 주문을 보낸다
#   scripts/run_paper.sh reconcile  15:45  계좌 체결(t0425)을 trades 에 적는다
#
# 장부는 data/_paper 오버레이 하나뿐이다. 실전 창고(data/)에도, shadow(data/_shadow)
# 에도 쓰지 않는다 — 두 장부의 차이가 곧 체결 비용이다.
#
# 첫 실행의 자본은 청산 뒤 계좌 예수금과 같아야 한다: CAPITAL 을 그때 한 번만 준다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
STEP="${1:-session}"
CAPITAL="${2:-0}"
SANDBOX="data/_paper"
LOG="logs/paper-$(date +%Y%m).log"
RC=0
{
    echo "=== $(date '+%F %T') step=${STEP} ==="
    ulimit -v 8388608
    export QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2
    case "${STEP}" in
        session)
            # **오늘이 국장 휴장이면 주문을 내지 않는다** (2026-09-24 추석). 세션은 직전 거래일 데이터로 결정하고 **오늘** 주문을
            # 내는데, 휴장일엔 증권사가 전부 "모의투자 영업일이 아닙니다"(01410)로 거부했다. 해가 없었지만 로그가 거부로 가득 차고
            # 그 세션의 주문은 '거부' 로 적혀 다음 거래일에 다시 나가지 않는다. 다음 거래일 08:40 이 직전 거래일로 새로 결정한다.
            if ! .venv/bin/python -c "import sys; from datetime import datetime; from zoneinfo import ZoneInfo; from quant_rl_trading.collectors.market_hours import Market, is_trading_day; sys.exit(0 if is_trading_day(Market.KR, datetime.now(ZoneInfo('Asia/Seoul')).date()) else 1)"; then
                echo "오늘은 국장 휴장이다 — 주문을 내지 않는다"
                RC=0
            else
            .venv/bin/python tools/run_session.py --market KR \
                --sandbox "${SANDBOX}" --live-broker --capital "${CAPITAL}"
            RC=$?
            fi
            ;;
        reconcile)
            # 모의계좌는 SC3 를 받지 못한다 — 그날 취소를 주문체결내역 조회로 먼저 확인한다(execution-safety.md 2026-09-23).
            # 거부·미발견은 미확정으로 남아 아래 대사가 그대로 rc 로 알린다. 이 줄의 rc 는 로그만.
            .venv/bin/python tools/confirm_cancels_inquiry.py --sandbox "${SANDBOX}"
            echo "cancel-inquiry rc=$?"
            .venv/bin/python tools/reconcile_fills.py --market KR --sandbox "${SANDBOX}"
            RC=$?
            # 주문별 대사 **뒤에** 스냅샷 대사로 잔여 드리프트를 청소한다. 이 순서라야
            # 오늘 체결이 이미 order_id 로 기록돼 스냅샷 delta 가 잔여만 남고 이중계상이
            # 없다(장중 실행 금지 — reconcile 은 마감 뒤 15:45 에 돈다). 스냅샷 실패가
            # 주문대사 rc 를 덮지 않게 rc 는 따로 로그만.
            .venv/bin/python tools/reconcile_snapshot.py --market KR --sandbox "${SANDBOX}" --apply
            echo "snapshot rc=$?"
            # 정산금액 대조 — 오늘 정산(D+2)된 세션의 체결 합계를 LS 거래내역과 맞춘다.
            # 불일치는 rc=1 로 로그에 남기되 주문대사 rc 를 덮지 않는다(원인 조사는 사람 몫).
            .venv/bin/python tools/settlement_check.py --market KR --sandbox "${SANDBOX}"
            echo "settlement rc=$?"
            ;;
        *)
            echo "모르는 단계: ${STEP} (session|reconcile)"; RC=2
            ;;
    esac
    echo "rc=${RC}"
} >>"${LOG}" 2>&1
exit "${RC}"
