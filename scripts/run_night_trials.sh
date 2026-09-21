#!/usr/bin/env bash
# 시행 대기열 — 등록된 시행을 순서대로 한 번씩. **밤을 기다리지 않는다**(사용자 지시 2026-09-21):
# 자원이 남으면 낮에도 돈다. 대신 운영 구간은 아래 창으로 비켜 간다. 30분마다 불러도 안전하다:
#  · 이미 끝난 시행(로그에 '판정:')은 건너뛴다
#  · 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
# 순서: Z → AD → AE(오버레이) → AF(상한 완화) → AC(트랜스포머, 마지막·몇 시간짜리).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

# **운영이 먼저다.** 연구 시행은 운영 작업이 도는 동안 시작하지 않는다 — 22:40 국장 수집 → 22:55 run_daily →
# 23:05 shadow → 23:20 회계는 내일 세션의 입력이고, 미장 TWAP 조각(20분마다)은 shadow 주문이다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/collect_program_ls.py \
            tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
            tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py; do
    pgrep -f "${tool}" > /dev/null && exit 0
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 3500 ] && exit 0

# **운영이 몰리는 창에는 아무것도 새로 시작하지 않는다.** 그 사이에 시행을 띄우면 수집·세션이 메모리와
# CPU 를 나눠 쓰게 된다(2026-09-20 에 그렇게 OOM 이 났다). 이미 돌던 것은 nice 로 양보하며 계속 간다.
#   08:30~09:10  아침 세션(08:40 모의계좌 주문)·KRX 보충
#   12:00~12:40  미장 run_daily·shadow
#   15:15~16:40  마감 취소·대사·국장 수집·종가/지수·회계·리뷰
#   22:35~23:35  국장 저녁 체인(수집 → run_daily → shadow → 회계)
HM=$(date +%H%M)
DOW=$(date +%u)
if [ "${DOW}" -le 5 ] && { { [ "${HM}" -ge 830 ] && [ "${HM}" -le 910 ]; }    || { [ "${HM}" -ge 1200 ] && [ "${HM}" -le 1240 ]; }    || { [ "${HM}" -ge 1515 ] && [ "${HM}" -le 1640 ]; }    || { [ "${HM}" -ge 2235 ] && [ "${HM}" -le 2335 ]; }; }; then
    echo "$(date '+%F %T') 운영 창(${HM}) — 건너뜀" >> logs/night-trials.log
    exit 0
fi

run_one() {  # $1=이름 $2=도구 $3=로그
    grep -q "^판정:" "$3" 2>/dev/null && return 0
    pgrep -f "$2" > /dev/null && return 1
    echo "$(date '+%F %T') $1 시작 (가용 ${AVAIL}MB)" >> logs/night-trials.log
    {
        echo "=== $(date '+%F %T') $1 ==="
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python -u "$2" --save
        echo "rc=$?"
    } >> "$3" 2>&1
    return 1  # 한 번에 하나만 — 다음 회차가 다음 시행을 잡는다
}

run_one "시행 Z"  tools/trial_beta_megacap.py       logs/trial-beta-megacap-Z.log      || exit 0
run_one "시행 AD" tools/trial_index_minus_losers.py logs/trial-index-minus-losers-AD.log || exit 0
run_one "시행 AE" tools/trial_overlay_extended.py   logs/trial-overlay-extended-AE.log   || exit 0
run_one "시행 AF" tools/trial_cap_relax.py           logs/trial-cap-relax-AF.log          || exit 0

# 시행 AC(트랜스포머)는 몇 시간짜리라 마지막이다. 시드 0 만(판정), **8스레드** — 12 코어 중 4 개는
# 밤 운영(수집·세션·TWAP 조각)에 남긴다. 그만큼 느려지지만(약 7시간) 운영을 굶기지 않는다.
# 시드 1·2(기록 항목)는 다른 밤에 따로 — 등록 문서 "비용 실측과 실행 순서".
if ! grep -q "^판정:" logs/trial-price-transformer-AC.log 2>/dev/null    && ! pgrep -f tools/trial_price_transformer.py > /dev/null; then
    echo "$(date '+%F %T') 시행 AC 시작 (가용 ${AVAIL}MB · 약 7시간, 8스레드)" >> logs/night-trials.log
    {
        echo "=== $(date '+%F %T') 시행 AC (seed 0, 12 threads) ==="
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python -u tools/trial_price_transformer.py             --save --no-extra-seeds --threads 8
        echo "rc=$?"
    } >> logs/trial-price-transformer-AC.log 2>&1
    exit 0
fi
echo "$(date '+%F %T') 대기열 비었다 — 크론 두 줄 지울 것" >> logs/night-trials.log
