#!/usr/bin/env bash
# 마지막 모델 회차 · 시행 BE(세트 트랜스포머) 본 측정 — **한 번만**. 사전등록 docs/protocols/final-model-round-2026-10.md.
#
#   setsid nohup scripts/final_round_BE.sh >> logs/final-BE-runner.log 2>&1 &
#
# 30분마다 불러도 안전하다(크론은 **등록 해시 고정 뒤에** 건다):
#  · 이미 끝났으면(로그에 '판정:') 즉시 종료 — 판정 예산은 1회다
#  · 다른 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
#  · 운영 창(수집·세션·회계·shadow)에는 새로 시작하지 않는다
# 한 시드가 ~1.8시간, 다섯 시드 합쳐 ~9시간이다. 끝난 시드는 data/_diag/final-round/BE/ 에 예측을 남기므로
# 중간에 내려가도(메모리 가드·WSL2 재부팅) 다음 회차가 남은 시드만 잇는다 — 이틀로 쪼개도 된다.
# 메모리 가드 목록에는 들어가 있다(scripts/memory_guard.sh VICTIMS, trial_price_transforme[r] 바로 뒤 —
# 거의 마지막 순서다). 트랜스포머에 붙은 "스왑 여유가 있으면 두다" 예외는 이 도구엔 안 붙었지만,
# 시드 단위로 이어 돌기 때문에 내려도 잃는 것은 진행 중인 시드 하나(~1.8시간)뿐이다.
# health_watch.sh 의 STOPPABLE 은 `trial_[a-z_]+\.py` 라 이미 잡는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/trial-final-transformer-BE.log"
GUARD="logs/final-BE-queue.log"
mkdir -p logs

# glibc 아레나를 묶는다 — 스레드마다 아레나를 따로 잡으면 RSS 가 시간당 0.4GB 늘어난다(2026-09-21 트랜스포머).
export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=12

grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
pgrep -f "tools/trial_final_transformer.p[y]" > /dev/null && exit 0

# **대조군이 먼저다.** C0·C1 예측 캐시(`final_round_controls.py --bake`)가 없으면 판정 기준 ①~⑥ 을 못 잰다.
# 도구도 rc=3 으로 멈추지만, 9시간짜리 학습을 다 돌린 뒤 멈추면 하룻밤을 버린다 — 여기서 먼저 본다.
# 꼬리표를 등록 창으로 못 박는다: `-smoke2` 짧은 창 파일이 관문을 통과하면 안 된다(그건 배선 확인용이다).
CTAG="KR+US-20220701-20260630"
MISSING=0
for arm in C0 C1; do
    for s in 0 1 2 3 4; do
        [ -f "data/_diag/final-round/pred-${arm}-seed${s}-${CTAG}.pkl" ] || MISSING=$((MISSING + 1))
    done
done
if [ "${MISSING}" -gt 0 ]; then
    echo "$(date '+%F %T') 대조군 캐시 ${MISSING}/10 없음(꼬리표 ${CTAG}) — scripts/final_round_bake.sh 먼저" >> "${GUARD}"
    exit 0
fi

# **ⓒ 원피처 굽기가 판정 창을 덮는가.** 도구도 rc=4 로 멈추지만, 패널 조립(수 분)과 대조군 읽기 앞에서
# 먼저 보는 것이 싸다. 수익을 보지 않는 관문이다(달력 파일 몇 KB 만 읽는다).
if ! .venv/bin/python -c "from tools.final_round_kit import require_full_coverage; require_full_coverage()" \
        >> "${GUARD}" 2>&1; then
    echo "$(date '+%F %T') 굽기 관문 미달 — scripts/final_round_bake_features.sh 먼저" >> "${GUARD}"
    exit 0
fi

# 사전등록이 초안(해시 미고정)인 동안은 본 측정을 시작하지 않는다.
if grep -q "^> \*\*초안" docs/protocols/final-model-round-2026-10.md 2>/dev/null; then
    echo "$(date '+%F %T') 사전등록이 초안 — 해시 고정 전에는 안 돈다" >> "${GUARD}"
    exit 0
fi

# **운영이 먼저다.** 이 목록이 도는 동안은 시작하지 않는다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/trial_final_lambdarank.py tools/trial_final_residual_rl.py \
            tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
            tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py \
            tools/collect_us_prices.py; do
    if pgrep -f "${tool}" > /dev/null; then
        echo "$(date '+%F %T') ${tool} 도는 중 — 건너뜀" >> "${GUARD}"
        exit 0
    fi
done

# 연구 잠금·스크래치 진단 — 같이 돌면 둘 다 죽는다(2026-09-21).
LOCK=logs/.research-lock
if [ -f "${LOCK}" ] && [ -n "$(find "${LOCK}" -mmin -120 2>/dev/null)" ]; then
    echo "$(date '+%F %T') 연구 잠금($(cat "${LOCK}")) — 건너뜀" >> "${GUARD}"
    exit 0
fi
if pgrep -f "Project-Quant-RL-Trading/.*scratchpa[d]/.*\.py" > /dev/null; then
    echo "$(date '+%F %T') 스크래치 진단 중 — 건너뜀" >> "${GUARD}"
    exit 0
fi

AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4500 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4500MB — 건너뜀" >> "${GUARD}"
    exit 0
fi

#   08:30~09:10 아침 세션 · 12:00~12:40 미장 · 15:15~16:40 마감·대사·수집 · 22:35~23:35 국장 저녁 체인
HM=$(date +%H%M); DOW=$(date +%u)
if [ "${DOW}" -le 5 ] && { { [ "${HM}" -ge 830 ] && [ "${HM}" -le 910 ]; } \
    || { [ "${HM}" -ge 1200 ] && [ "${HM}" -le 1240 ]; } \
    || { [ "${HM}" -ge 1515 ] && [ "${HM}" -le 1640 ]; } \
    || { [ "${HM}" -ge 2235 ] && [ "${HM}" -le 2335 ]; }; }; then
    echo "$(date '+%F %T') 운영 창(${HM}) — 건너뜀" >> "${GUARD}"
    exit 0
fi

echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 BE 본 측정 시작(10~13시간)" >> "${GUARD}"
{
    echo "=== $(date '+%F %T') 시행 BE — 세트 트랜스포머 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
        .venv/bin/python -u tools/trial_final_transformer.py --save --threads 12
    rc=$?
    echo "rc=${rc}"
    # **조용한 실패는 rc 로 내보낸다**(2026-09-16). 판정 줄이 없으면 실패다.
    # kit 이 종료 코드를 나눠 뒀다 — 왜 멈췄는지 로그가 말해야 다음 회차가 무엇을 기다릴지 안다.
    case "${rc}" in
        0) grep -q "^판정:" "${LOG}" || echo "경고: rc=0 인데 판정 줄이 없다 — 실패로 본다" ;;
        3) echo "멈춤 이유: 대조군 예측 없음 — scripts/final_round_bake.sh 먼저" ;;
        4) echo "멈춤 이유: 원피처 굽기가 판정 창을 못 덮는다 — scripts/final_round_bake_features.sh 먼저" ;;
        5) echo "멈춤 이유: 60세션 창이 판정 첫 세션을 못 채운다 — 등록 창·MIN_TRAIN 이 바뀌었나 확인" ;;
        *) echo "멈춤 이유: 알 수 없음(rc=${rc}) — 위 로그를 봐라" ;;
    esac
} >> "${LOG}" 2>&1
