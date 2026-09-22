#!/usr/bin/env bash
# 시행 대기열 — 등록된 시행을 순서대로 한 번씩. **밤을 기다리지 않는다**(사용자 지시 2026-09-21):
# 자원이 남으면 낮에도 돈다. 대신 운영 구간은 아래 창으로 비켜 간다. 30분마다 불러도 안전하다:
#  · 이미 끝난 시행(로그에 '판정:')은 건너뛴다
#  · 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
# 순서(2026-09-22 밤): AI(국면 배합) → AJ(국면 원-핫) → AK(무너질 종목 분류기) → AL(불확실성 종목 수). 전부 GBM 이라 한 번에 하나.
# 크론은 **10분마다 상시**(사용자 지시 9/22: 자원 여유가 되면 낮에도). AI 는 낮에 메모리 부족으로 죽은 적이 있어 AVAIL 문턱을 지킨다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
# glibc 아레나를 묶는다 — 여러 스레드가 아레나를 따로 잡으면 쓰고 난 메모리를 못 돌려줘 RSS 가 계속 는다
# (2026-09-21 트랜스포머: 3.3 → 4.4GB, 시간당 0.4GB). 대시보드 재기동 스크립트와 같은 값이다.
export MALLOC_ARENA_MAX=2

# **운영이 먼저다.** 연구 시행은 운영 작업이 도는 동안 시작하지 않는다 — 22:40 국장 수집 → 22:55 run_daily →
# 23:05 shadow → 23:20 회계는 내일 세션의 입력이고, 미장 TWAP 조각(20분마다)은 shadow 주문이다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/collect_program_ls.py \
            tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
            tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py tools/collect_us_prices.py; do
    if pgrep -f "${tool}" > /dev/null; then
        echo "$(date '+%F %T') 운영 도구(${tool}) 도는 중 — 건너뜀" >> logs/night-trials.log
        exit 0
    fi
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 3500 ] && exit 0

# **연구 잠금** — 진단(스크래치 스크립트)을 돌리는 동안은 새 시행을 시작하지 않는다(2026-09-21 트랜스포머 사망).
# 스크래치 진단은 도구 목록에 없어 위 pgrep 이 못 본다. 잠금 파일이 2시간 넘게 남아 있으면 버려진 것으로 본다.
LOCK=logs/.research-lock
if [ -f "${LOCK}" ] && [ -n "$(find "${LOCK}" -mmin -120 2>/dev/null)" ]; then
    echo "$(date '+%F %T') 연구 잠금 중($(cat "${LOCK}")) — 건너뜀" >> logs/night-trials.log
    exit 0
fi
# **이 프로젝트의** 스크래치만 본다 — 다른 프로젝트의 Claude 세션 스크래치(9/22 13:30~ CTI-graph probe.py)까지 잡아
# 대기열이 40분 넘게 기록 없이 멈췄다. 건너뛸 때는 이유를 적는다(조용한 종료는 못 찾는다).
if pgrep -f "Project-Quant-RL-Trading/.*scratchpa[d]/.*\.py" > /dev/null; then
    echo "$(date '+%F %T') 스크래치 진단 중 — 건너뜀" >> logs/night-trials.log
    exit 0
fi

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

run_one "시행 AI" tools/trial_regime_blend.py         logs/trial-regime-blend-AI.log         || exit 0
run_one "시행 AJ" tools/trial_ranker_market_state.py  logs/trial-ranker-market-state-AJ.log  || exit 0
run_one "시행 AK" tools/trial_collapse_classifier.py  logs/trial-collapse-classifier-AK.log  || exit 0
run_one "시행 AL" tools/trial_uncertainty_breadth.py  logs/trial-uncertainty-breadth-AL.log  || exit 0
echo "$(date '+%F %T') 대기열 비었다 — 크론 두 줄 지울 것" >> logs/night-trials.log
