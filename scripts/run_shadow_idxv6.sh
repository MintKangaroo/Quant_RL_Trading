#!/usr/bin/env bash
# 지수+V6 shadow — 국장, 모의 체결(docs/design/portfolio-construction.md "지수+V6 트랙"). KODEX200(069500) 한 종목 × V6 노출.
# 실자금 투입 관문 ②(docs/protocols/live-capital-entry-2026-11.md)의 "지수+V6" 대용을 계산값이 아니라 매매 기록으로 남긴다.
# 설정은 data/_idxv6_shadow/config-overrides.yaml (없으면 config/shadow/idxv6.config-overrides.yaml 을 복사한다).
#
# **정보 시점은 모의계좌와 같다.** 평일 17:05 에 **직전 거래일 d** 세션을 `--day d` 로 돌린다 — 모의계좌가 오늘 08:40 에 같은 d 로
# 결정했다(KRX 국면 지수 d 값은 오늘 08:32 아침 보충). 게이트가 as_of d 16:00 이라 지금 돌려도 오늘 정보는 안 보인다.
# 오늘이 휴장이면 돌지 않는다(모의계좌도 주문을 안 낸다). 그날 ETF 봉은 15:50 LS t8407(collect_benchmark_etf.py --source ls).
# 다른 KR 세션(샌드박스 포함)이 도는 중이면 기다린다(머신을 나누지 않는다). 첫 실행만 자본 5.03억(.funded).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-idxv6-$(date +%Y%m).log"
BOOK=data/_idxv6_shadow
TEMPLATE=config/shadow/idxv6.config-overrides.yaml
# 첫 세션은 10/6(10/7 실행) — 10/5 는 개천절 대체휴일이라 첫 LS 봉이 10/6 15:50 에 들어온다. 그 전 세션(10/2)은 그날 봉이
# 16:00 전에 관측된 적이 없어 신선도 게이트가 매수를 막는다 — 빈 세션으로 장부를 시작하지 않는다.
[[ "$(date +%F)" < "2026-10-07" ]] && { echo "$(date "+%F %T") 첫 세션 전 — 건너뜀" >> "${LOG}"; exit 0; }
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') 지수+V6 shadow (KR) ==="
    # 판정은 10(거래일, 직전 거래일 출력)·11(휴장)로만 답한다 — 그 밖의 값은 판정 실패(run_paper.sh 와 같은 규약).
    DAY=$(.venv/bin/python -c "
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from quant_rl_trading.collectors.market_hours import Market, is_trading_day, trading_days
today = datetime.now(ZoneInfo('Asia/Seoul')).date()
if not is_trading_day(Market.KR, today):
    sys.exit(11)
print([d for d in trading_days(Market.KR, today - timedelta(days=20), today) if d < today][-1])
sys.exit(10)
")
    VERDICT=$?
    if [ "${VERDICT}" -eq 11 ]; then
        echo "오늘은 국장 휴장이다 — 모의계좌도 주문을 안 냈다. 건너뜀"
    elif [ "${VERDICT}" -ne 10 ] || [ -z "${DAY}" ]; then
        echo "⚠️ 거래일 판정 실패(rc=${VERDICT}) — 세션을 돌리지 않았다"
        RC=3
    else
        mkdir -p "${BOOK}"
        if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
            cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
            echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
        elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
            echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
        fi
        EXTRA=()
        [ -e "${BOOK}/.funded" ] || EXTRA=(--capital 503000000)
        ulimit -v 16777216
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/run_session.py --market KR --day "${DAY}" --sandbox "${BOOK}" "${EXTRA[@]}"
        RC=$?
        echo "rc=${RC}"
        if [ ${#EXTRA[@]} -gt 0 ] && { [ ${RC} -eq 0 ] || [ ${RC} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
    fi
} >> "${LOG}" 2>&1
# 블록 마지막이 echo 면 스크립트 rc 는 늘 0 이다 — 크론이 보는 값은 이것 하나다(2026-09-26 점검).
exit "${RC:-1}"
