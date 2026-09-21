#!/usr/bin/env bash
# 밤 시행 대기열 — 등록된 시행을 순서대로 한 번씩. 30분마다 불러도 안전하다:
#  · 이미 끝난 시행(로그에 '판정:')은 건너뛴다
#  · 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
# 순서: Z(베타·대형주) → AD(패자만 뺀다) → AE(오버레이 재측정). 셋 다 학습이 없어 가볍다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/collect_program_ls.py; do
    pgrep -f "${tool}" > /dev/null && exit 0
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 3500 ] && exit 0

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

# 시행 AC(트랜스포머)는 **5.8시간짜리**라 마지막이다. 시드 0 만(판정), 12스레드.
# 시드 1·2(기록 항목)는 다른 밤에 따로 — 등록 문서 "비용 실측과 실행 순서".
if ! grep -q "^판정:" logs/trial-price-transformer-AC.log 2>/dev/null    && ! pgrep -f tools/trial_price_transformer.py > /dev/null; then
    echo "$(date '+%F %T') 시행 AC 시작 (가용 ${AVAIL}MB · 약 6시간)" >> logs/night-trials.log
    {
        echo "=== $(date '+%F %T') 시행 AC (seed 0, 12 threads) ==="
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python -u tools/trial_price_transformer.py             --save --no-extra-seeds --threads 12
        echo "rc=$?"
    } >> logs/trial-price-transformer-AC.log 2>&1
    exit 0
fi
echo "$(date '+%F %T') 대기열 비었다 — 크론 두 줄 지울 것" >> logs/night-trials.log
