#!/usr/bin/env bash
# 브리핑 직전 보충 수집 — **지수·시총·거시만.** 시세는 안 받는다.
#
# ## 왜 필요한가
#
# 브리핑은 06:30 에 도는데, 그 앞의 수집들이 전부 **그보다 이른 시각에**
# 돌면서 그날 값을 못 받아 온다:
#
#   22:40  collect_daily.sh KR   KRX 가 당일 시총·지수를 아직 안 준다 → 0행
#   06:30  send_briefing.sh      그래서 낡은 값으로 쓴다
#   08:40  collect_daily.sh US   미장 시세는 브리핑 **뒤에** 들어온다
#
# 실측 2026-08-19: 22:40 수집에서 시세는 2,872행이 들어왔는데 같은 KRX 호출의
# `market_stats=0` · `indices=0` 이었다. 그리고 **아침에 그대로 다시 부르니
# 5,744행 · 40행 · 4행이 들어왔다.** 데이터가 없던 게 아니라 그 시각에 원본이
# 아직 안 냈던 것이다. 그래서 실패로도 안 잡힌다(rc=0, "미공표 대기 0").
#
# ## 왜 시세는 안 받나
#
# 미장 시세는 6,647종목 × 1호출이라 **2~3시간**이 든다(실측 2시간 51분).
# 브리핑 앞에 둘 수 있는 물건이 아니다. 그건 08:40 자리를 지킨다 —
# 브리핑의 "거래대금 상위" 만 하루 늦고, 지수·시총은 맞게 된다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
LOG="logs/refresh-$(date +%Y%m).log"

export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1200MB}"
export QUANT_RL_DUCKDB_THREADS="${QUANT_RL_DUCKDB_THREADS:-2}"

{
    echo "=== $(date '+%F %T') 브리핑 전 보충 ==="

    # 국장 시총·지수. 어제 세션까지 받는다 — 창을 2로 두면 휴장이 끼어도
    # 마지막 거래일이 창 안에 들어온다.
    for T in shares indices-krx indices-board; do
        .venv/bin/python tools/backfill.py --market KR --table "$T" --sessions 2
        echo "  KR ${T} rc=$?"
    done

    # 미장 지수·거시(FRED). 미장 마감 05:00~06:00 KST 뒤라 그날 값이 있다.
    .venv/bin/python tools/collect_macro.py
    echo "  거시·미장지수 rc=$?"

    # 미장 지수 **대용 ETF** 4종(SPY·QQQ·DIA·SOXX). 종목 4개라 몇 초다.
    #
    # FRED 는 마감 4시간이 지나도 그날 값을 안 준다 — 실측 2026-08-19 08:55
    # KST 에 SP500·NASDAQ·SOX·VIX 가 08-17 에 멈춰 있었고 다우 계열만 08-18
    # 이었다. 같은 시각 LS 는 네 ETF 모두 08-18 을 줬다. 그래서 위의 FRED 를
    # **대체하지 않고 옆에 세운다** — 브리핑이 둘을 따로 적는다(ETF 는 지수가
    # 아니다).
    .venv/bin/python tools/collect_us_prices.py --source etf --sessions 3
    echo "  미장 대용 ETF rc=$?"
} >>"${LOG}" 2>&1
