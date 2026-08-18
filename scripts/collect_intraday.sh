#!/usr/bin/env bash
# 분봉 수집 — **장중에 반복해서 돈다.** 일봉과 자리가 다르다.
#
#   scripts/collect_intraday.sh KR live     장중 구간(1m·5m)
#   scripts/collect_intraday.sh KR close    마감 구간(15m·1H·4H)
#
# ## 왜 일봉(collect_daily.sh) 에 안 넣었나
#
# 일봉은 하루 한 번, 마감 뒤 확정된 값을 받는다. 분봉은 **장중 내내 새 봉이
# 생긴다** — 하루 한 번 받으면 차트가 장 시작 시점에 얼어붙는다. 그래서
# ``ingest_run_id`` 도 분 단위까지 쪼개져 있다(intraday_collector.py).
#
# ## 구간을 둘로 나눈 근거 — 500봉 상한
#
# LS 는 한 번에 최근 500봉만 준다. 그러면 구간마다 담기는 기간이 다르다:
#
#     1m   500봉 ≈ 1.3거래일    ← 오늘 안에서 계속 바뀐다
#     5m   500봉 ≈ 6.4거래일    ← 오늘 안에서 계속 바뀐다
#     15m  500봉 ≈ 19거래일     ← 하루에 26봉만 는다
#     1H   500봉 ≈ 77거래일     ← 하루에 7봉
#     4H   500봉 ≈ 300거래일+   ← 하루에 2봉
#
# **장중에 자주 받아야 하는 것은 1m·5m 뿐이다.** 15m 이상은 마감 뒤 한 번이면
# 다음 날까지 화면이 정확하다. 전부 30분마다 받으면 4시간봉을 하루 13번
# 받는 셈인데, 그 사이 봉이 하나도 안 늘어난 적이 대부분이다.
#
# ## 비용 — 실측 2026-08-18
#
#     12종목 × 3.1초(레이트리밋) = 37초 / 구간   (실측 36초)
#
#     장중  1m+5m   × 13회(30분마다, 6.5시간)  = 26파일 · API 16분
#     마감  15m·1H·4H × 1회                    =  3파일 · API  2분
#     ─────────────────────────────────────────────────────────
#     하루 29파일 · 한 달(22거래일) 약 640파일
#
# **파일 개수를 세는 이유가 있다.** 읽기 비용은 데이터 양이 아니라 파일
# 개수에 붙는다 — flows 를 합쳐 49배, indices 를 19,374→1,558 로 줄인 것이
# 이 저장소의 반복된 교훈이다. 한 달에 640개면 분기마다 한 번
# ``tools/compact_store.py --table prices_intraday --apply`` 로 충분하다.
#
# ## crontab (등록은 사람이 한다)
#
#     # 국장 장중 — 09:00~15:30 KST, 30분마다
#     0,30 9-15 * * 1-5  /home/mintkangaroo/Project/Quant_RL_Trading/scripts/collect_intraday.sh KR live
#     # 국장 마감 뒤
#     45 15 * * 1-5      /home/mintkangaroo/Project/Quant_RL_Trading/scripts/collect_intraday.sh KR close
#     # 미장 장중 — 22:30~05:00 KST, 30분마다
#     0,30 22-23,0-4 * * 1-6  /home/mintkangaroo/Project/Quant_RL_Trading/scripts/collect_intraday.sh US live
#     # 미장 마감 뒤
#     15 5 * * 2-6       /home/mintkangaroo/Project/Quant_RL_Trading/scripts/collect_intraday.sh US close
#
# 크론 시간은 넉넉히 잡아도 된다 — **장 밖이면 스크립트가 스스로 안 돈다**
# (아래 장중 판정). 서머타임으로 미장 시각이 한 시간 밀려도 알아서 걸러진다.
set -u

cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
PHASE="${2:-live}"

case "${PHASE}" in
    live)  INTERVALS="1m 5m" ;;
    close) INTERVALS="15m 1H 4H" ;;
    *) echo "phase 는 live | close 다: $PHASE" >&2; exit 2 ;;
esac

mkdir -p logs
LOG="logs/intraday-${MARKET}-$(date +%Y%m).log"

#: **장 밖이면 안 돈다.** 크론 시간을 손으로 계산하지 않기 위한 자리다 —
#: 휴장일·서머타임·조기폐장을 스크립트가 알 이유가 없고, market_hours 가
#: 이미 안다. 여기서 시간을 다시 계산하면 그 둘이 언젠가 어긋난다.
#:
#: close 단계는 마감 **직후**라 정규장이 아니다. 그래서 거래일인지만 본다.
GUARD=$(.venv/bin/python - "$MARKET" "$PHASE" <<'PY'
import sys
from datetime import datetime, UTC
from quant_rl_trading.collectors.market_hours import (
    Market, is_regular_session, is_trading_day, local_time,
)

market = Market(sys.argv[1])
phase = sys.argv[2]
now = datetime.now(UTC)
here = local_time(market, now)
if not is_trading_day(market, here.date()):
    print(f"skip 휴장 — {here.date()}")
elif phase == "live" and not is_regular_session(market, now):
    print(f"skip 장 밖 — {here:%H:%M} 현지")
else:
    print("go")
PY
) || GUARD="skip 장중 판정 실패"

if [ "${GUARD}" != "go" ]; then
    echo "$(date '+%F %T %Z') ${MARKET} ${PHASE} — ${GUARD}" >> "${LOG}"
    exit 0
fi

RC=0
{
    echo "=== $(date '+%F %T %Z') — ${MARKET} ${PHASE} (${INTERVALS}) ==="
    for interval in ${INTERVALS}; do
        .venv/bin/python tools/collect_intraday.py --market "${MARKET}" --interval "${interval}"
        code=$?
        # **0행을 성공으로 적지 않는다.** 수집 도구가 rc 로 구분해 준다.
        [ "${code}" -ne 0 ] && RC="${code}" && echo "  ${interval} 종료코드 ${code}"
    done
    echo
} >> "${LOG}" 2>&1

exit "${RC}"
