#!/usr/bin/env bash
# 마지막 모델 회차 · 시행 BF(LambdaRank v2 — NDCG@100 · 10분위 라벨) 본 측정 — **한 번만**.
# 사전등록 docs/protocols/final-model-round-2026-10.md.
#
#   setsid nohup scripts/final_round_BF.sh >> logs/final-BF-runner.log 2>&1 &
#
# 30분마다 불러도 안전하다(크론은 **등록 해시 고정 뒤에** 건다):
#  · 이미 끝났으면(로그에 '판정:') 즉시 종료 — 판정 예산은 1회다
#  · 대조군 캐시가 없으면 시작하지 않는다 — 다 돌린 뒤 멈추면 하룻밤을 버린다
#  · 다른 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료 (학습 중엔 머신을 나누지 않는다, 8/28)
#  · 운영 창(세션·미장·마감·저녁 체인)에는 새로 시작하지 않는다
# 시드 하나가 ~4~6시간(41블록 × 6~9분)이고 시드가 다섯이다 — **하룻밤에 다 못 끝난다.**
# 그래서 시드별 예측을 캐시한다: 가드에 내려가거나 밤이 끝나도 다음 회차가 남은 시드만 돈다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/trial-final-lambdarank-BF.log"
GUARD="logs/final-BF-queue.log"
mkdir -p logs
say() { echo "$(date '+%F %T') $*" >> "${GUARD}"; }

# glibc 아레나를 묶는다 — LightGBM 스레드가 아레나를 따로 잡으면 RSS 가 계속 는다(9/21 교훈).
export MALLOC_ARENA_MAX=2
# LightGBM 스레드. 등록 대상이 아니다(deterministic=True 라 스레드를 줄여도 같은 모델이 나온다) —
# 머신을 다른 작업과 나눠 써야 할 때 여기서 줄인다.
export QUANT_RL_LGB_THREADS=6
export OMP_NUM_THREADS=${QUANT_RL_LGB_THREADS}

grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
pgrep -f "tools/trial_final_lambdarank.p[y]" > /dev/null && exit 0

# **대조군이 먼저다.** C0·C1 예측 캐시(`final_round_controls.py --bake`)가 없으면 기준 ①~⑥ 을 못 잰다.
if ! ls data/_diag/final-round/pred-C1-seed4-*.pkl > /dev/null 2>&1; then
    say "대조군 캐시 없음 — tools/final_round_controls.py --bake 먼저"
    exit 0
fi

# 사전등록이 초안(해시 미고정)인 동안은 본 측정을 시작하지 않는다.
if grep -q "^> \*\*초안" docs/protocols/final-model-round-2026-10.md 2>/dev/null; then
    say "사전등록이 초안 — 해시 고정 전에는 안 돈다"
    exit 0
fi

# **운영이 먼저다.** 같은 회차의 다른 시행(BE·BG)과도 절대 겹치지 않는다.
for tool in tools/trial_final_transformer.py tools/trial_final_residual_rl.py \
            tools/final_round_controls.py tools/diagnose_ic.py tools/backfill_ic_history.py \
            tools/measure_ic.py tools/train_ranker.py tools/trial_ranker_sources.py \
            tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
            tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py \
            tools/collect_us_prices.py; do
    if pgrep -f "${tool}" > /dev/null; then
        say "${tool} 도는 중 — 건너뜀"
        exit 0
    fi
done

# 연구 잠금·스크래치 진단 — 같이 돌면 둘 다 죽는다(2026-09-21).
LOCK=logs/.research-lock
if [ -f "${LOCK}" ] && [ -n "$(find "${LOCK}" -mmin -120 2>/dev/null)" ]; then
    say "연구 잠금($(cat "${LOCK}")) — 건너뜀"
    exit 0
fi
if pgrep -f "Project-Quant-RL-Trading/.*scratchpa[d]/.*\.py" > /dev/null; then
    say "스크래치 진단 중 — 건너뜀"
    exit 0
fi

AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4500 ]; then
    say "가용 ${AVAIL}MB < 4500MB — 건너뜀"
    exit 0
fi

#   08:30~09:10 아침 세션 · 12:00~12:40 미장 · 15:15~16:40 마감·대사·수집 · 22:35~23:35 국장 저녁 체인
HM=$(date +%H%M); DOW=$(date +%u)
if [ "${DOW}" -le 5 ] && { { [ "${HM}" -ge 830 ] && [ "${HM}" -le 910 ]; } \
    || { [ "${HM}" -ge 1200 ] && [ "${HM}" -le 1240 ]; } \
    || { [ "${HM}" -ge 1515 ] && [ "${HM}" -le 1640 ]; } \
    || { [ "${HM}" -ge 2235 ] && [ "${HM}" -le 2335 ]; }; }; then
    say "운영 창(${HM}) — 건너뜀"
    exit 0
fi

# 스모크 먼저 — 합성 자료라 3초다. 시행 X 교훈: 판정 도구 전 스모크를 건너뛰면 0행으로 돈다.
if ! .venv/bin/python -u tools/trial_final_lambdarank.py --smoke >> "${LOG}" 2>&1; then
    say "스모크 실패 — 본 측정을 돌리지 않는다"
    exit 1
fi

say "가용 ${AVAIL}MB — 시행 BF 본 측정 시작(시드 5, 시드별 캐시로 이어 돈다)"
{
    echo "=== $(date '+%F %T') 시행 BF — LambdaRank v2 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
        .venv/bin/python -u tools/trial_final_lambdarank.py --save
    rc=$?
    echo "rc=${rc}"
    # **조용한 실패는 rc 로 내보낸다**(2026-09-16). 판정 줄이 없으면 실패다.
    # rc 규약: 2 = 측정일 전 · 3 = 대조군 캐시 없음 · 4 = 굽기 관문 불통과(원피처가 판정 창을 못 덮는다)
    #          5 = 처리와 대조의 채점 세션이 다르다(kit block_rows 이음매) — 셋 다 **판정 예산을 안 쓴다**
    case "${rc}" in
      2|3|4|5) echo "예산 미소진(rc=${rc}) — 원인을 고치고 다시 부르면 된다" ;;
      *) grep -q "^판정:" "${LOG}" || echo "경고: rc=${rc} 인데 판정 줄이 없다 — 실패로 본다" ;;
    esac
} >> "${LOG}" 2>&1
say "종료 — 로그 ${LOG}"
