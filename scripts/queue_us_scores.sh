#!/usr/bin/env bash
# 미장 과거 점수 — **메모리가 넉넉할 때만** 돈다. 야간 크론이 30분마다 불러도 안전하다:
#  · 이미 구운 블록은 backfill_ic_history 가 건너뛴다(이어받기)
#  · 같은 작업이 이미 돌고 있으면 즉시 종료
#  · 끝났으면(6종 version 파일) 즉시 종료 — 그때 크론 두 줄을 지운다
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
WORK=data/ic-history-us-early
NEED_MB=${NEED_MB:-4000}

done_count=$(ls "${WORK}"/scores-*.version 2>/dev/null | wc -l)
[ "${done_count}" -ge 6 ] && exit 0

# **자기 자신을 세지 않는다** — 스크립트 이름이 아니라 도구 이름으로 찾는다(background-job-hygiene).
for tool in tools/backfill_ic_history.py tools/diagnose_ic.py tools/train_ranker.py \
            tools/backfill_ranker_signals.py tools/measure_ic.py tools/trial_ranker_sources.py; do
    pgrep -f "${tool}" > /dev/null && exit 0
done

avail=$(free -m | awk '/^Mem:/{print $7}')
if [ "${avail}" -lt "${NEED_MB}" ]; then
    echo "$(date '+%F %T') 가용 ${avail}MB < ${NEED_MB}MB — 건너뜀" >> logs/us-scores-early-queue.log
    exit 0
fi
echo "$(date '+%F %T') 가용 ${avail}MB — 미장 과거 점수 이어서" >> logs/us-scores-early-queue.log
exec scripts/backfill_us_scores_early.sh
