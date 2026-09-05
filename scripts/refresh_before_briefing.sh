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

# 실패한 단계 수. **셸의 `echo rc=$?` 가 rc 를 삼킨다** — 그동안 이 스크립트는
# 무엇이 죽어도 0 으로 나갔다(같은 사고: 커밋 7ad5680). 이제 수집기가
# "원본이 안 냄"(정상) 과 "우리가 못 받음"(실패) 을 구별해 rc 로 알려주므로,
# 그 rc 를 여기서 세서 그대로 내보낸다.
FAILED=0

note() {   # note <이름> <rc>
    echo "  $1 rc=$2"
    if [ "$2" -ne 0 ]; then
        FAILED=$((FAILED + 1))
    fi
}

{
    echo "=== $(date '+%F %T') 브리핑 전 보충 ==="

    # 국장 시총·지수. 어제 세션까지 받는다 — 창을 2로 두면 휴장이 끼어도
    # 마지막 거래일이 창 안에 들어온다.
    for T in shares indices-krx indices-board; do
        .venv/bin/python tools/backfill.py --market KR --table "$T" --sessions 2
        note "KR ${T}" "$?"
    done
    # KRX 는 전날 지수를 이 시각에 안 준다(실측 — 다음날 15:55 에야 온다). LS t1511 이
    # 같은 종가를 마감 직후부터 주므로 그것으로 채운다. 이미 있으면 할 일 없음.
    .venv/bin/python tools/collect_indices_ls.py
    note "KR 지수(LS t1511)" "$?"

    # 미장 지수·거시(FRED). 미장 마감 05:00~06:00 KST 뒤라 그날 값이 있다.
    .venv/bin/python tools/collect_macro.py
    note "거시·미장지수" "$?"
    # FRED 는 전날 미장 지수를 미국 오후에 낸다 → 06:30 브리핑은 이틀 전 종가였다.
    # Yahoo 는 마감 몇 분 뒤 그날 종가를 준다. 같은 entity 라 FRED 가 오면 정정본이 된다.
    .venv/bin/python tools/collect_indices_us.py
    note "미장 지수(Yahoo)" "$?"
    .venv/bin/python tools/collect_fx_yahoo.py
    note "환율(Yahoo)" "$?"
    # 시총 상위 60 + ETF 의 전날 종가 — 브리핑 "시가총액 상위 전일대비" 가 06:30 에 비지 않게
    .venv/bin/python tools/collect_prices_us_top.py
    note "미장 시총상위 종가(Yahoo)" "$?"
    # 거시지표 시장 예측치(컨센서스)
    .venv/bin/python tools/collect_consensus_ff.py
    note "컨센서스(FF)" "$?"

    # 미장 지수 **대용 ETF** 4종(SPY·QQQ·DIA·SOXX). 종목 4개라 몇 초다.
    #
    # FRED 는 마감 4시간이 지나도 그날 값을 안 준다 — 실측 2026-08-19 08:55
    # KST 에 SP500·NASDAQ·SOX·VIX 가 08-17 에 멈춰 있었고 다우 계열만 08-18
    # 이었다. 같은 시각 LS 는 네 ETF 모두 08-18 을 줬다. 그래서 위의 FRED 를
    # **대체하지 않고 옆에 세운다** — 브리핑이 둘을 따로 적는다(ETF 는 지수가
    # 아니다).
    .venv/bin/python tools/collect_us_prices.py --source etf --sessions 3
    note "미장 대용 ETF" "$?"

    # FRED 지수가 아직이면 **브리핑 직전까지 몇 분 간격으로 다시 묻는다.**
    #
    # 2026-08-22 06:30 발송분이 머리말에 `미장 2026-08-21` 을 달고 8/20 종가를
    # 실었다. ETF 는 05:20 에 8/21 이 들어와 있었고 FRED 지수만 없었다 —
    # 06:00 KST 는 17:00 ET, 마감 한 시간 뒤라 **FRED 공표 경계에 정확히
    # 걸쳐 있다.** 그 전까지 세션 D 가 D+1 06:00 관측으로 들어오던 것은 FRED
    # 가 06:00 에 낸다는 뜻이 아니라 **우리가 06:00 에 물었다는 뜻**이다
    # (`observed_at` 은 내가 알 수 있었던 시각이다 — 불변식 3).
    #
    # 브리핑을 늦추는 쪽은 안 골랐다. 늦춰도 공표 시각의 흔들림은 그대로라
    # 언젠가 또 걸리고, 그 대가로 매일 메일이 늦는다. 재시도는 걸린 날에만
    # 값을 치른다. 한 번 더 부르는 비용도 싸다 — FRED 호출 몇 초다.
    #
    # **마감을 둔다.** 브리핑이 06:30 이라 06:25 까지만 기다린다. 그때까지도
    # 안 들어오면 그건 그날의 사실이고, 브리핑이 "2026-08-21 지수가 아직 안
    # 들어왔다" 를 표에 적는다 — 조용히 8/20 을 오늘 것처럼 싣던 자리다.
    RETRY_DEADLINE=625   # HHMM. 브리핑(06:30) 5분 전.
    RETRY_SLEEP=240
    for attempt in 1 2 3 4 5; do
        if .venv/bin/python tools/us_index_ready.py; then
            break
        fi
        if [ "$((10#$(date +%H%M)))" -ge "${RETRY_DEADLINE}" ]; then
            echo "  06:25 넘김 — 재시도 중단. 브리핑이 안 들어왔다고 적는다"
            break
        fi
        sleep "${RETRY_SLEEP}"
        .venv/bin/python tools/collect_macro.py
        # 재시도의 rc 는 세지 않는다. 다음 회차가 성공할 수 있고, 끝내 못
        # 받으면 위의 첫 호출이 이미 한 번 셌다.
        echo "  미장 지수 재시도 ${attempt} rc=$?"
    done

    echo "실패한 단계 ${FAILED}개"
} >>"${LOG}" 2>&1

# **0 으로 나가지 않는다.** 이 rc 를 크론이 보고, 사람이 안 볼 때도 실패가
# 실패로 남는다.
exit "$((FAILED > 0))"
