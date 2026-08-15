#!/usr/bin/env bash
# 시황 브리핑 메일 — crontab 이 부른다. 매일 06:30 KST.
#
# **장이 열린 날만 보낸다.** 06:30 은 미장 마감(05:00 KST 서머타임) 뒤라
# "전날 국장 + 간밤 미장" 을 담는다.
#
# 기준은 **어제 둘 중 하나라도 열렸는가** 다. 국장만 보면 안 된다 — 2026-08-17
# 광복절 대체휴일처럼 국장은 쉬고 미장은 여는 날이 있고, 그날 메일을 통째로
# 거르면 간밤 미장이 어느 메일에도 안 실린다. 반대로 미국 독립기념일에는
# 국장 내용만 실린다. 빠진 쪽은 본문이 "미수집" 이 아니라 휴장이라고 말한다.
#
# 크론은 화~토(1-6)로 걸어 미장 월~금 마감을 덮는다. 한국 공휴일은 크론이
# 모르므로 여기서 달력에 묻는다 — exchange_calendars XKRX + 프로젝트 예외층.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/briefing-$(date +%Y%m).log"
mkdir -p logs

{
    echo "=== $(date '+%F %T') ==="
    if ! .venv/bin/python -c "
import sys
from datetime import date, timedelta
from quant_rl_trading.collectors.market_hours import Market, is_trading_day
y = date.today() - timedelta(days=1)
sys.exit(0 if any(is_trading_day(m, y) for m in (Market.KR, Market.US)) else 1)
"; then
        echo "어제는 국장·미장 모두 휴장 — 보내지 않는다"
        exit 0
    fi
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/send_briefing.py --send
    echo "rc=$?"
} >>"${LOG}" 2>&1
