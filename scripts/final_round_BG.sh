#!/usr/bin/env bash
# 시행 BG(잔차 RL) 측정 — docs/protocols/final-model-round-2026-10.md. **준비만 해 둔 것이다:**
# 사전등록 해시 고정 + 사용자 승인 + 10/5 이후여야 도구 자체가 돈다(require_registered).
#
#   기동: setsid nohup bash scripts/final_round_BG.sh > /dev/null 2>&1 &
#
# 30분마다 불러도 안전하다:
#  · 이미 끝났으면(로그에 '판정:') 즉시 종료 — 판정은 한 번뿐이다(시행 예산)
#  · 운영 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
#  · 카나리 → 파일럿 관문 → 본 학습·판정 순서. **관문에서 지면 본 학습을 안 한다**(3·4회차가 이걸로 수십 시간을 아꼈다)
#
# 머신을 나누지 않는다(memory `training-shares-no-machine`). BE 트랜스포머·BF LambdaRank 와 **같은 밤에 돌리지 않는다** —
# 공통 틀이 정한 대로 밤마다 하나씩이다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
# glibc 아레나를 묶는다 — 스레드가 아레나를 따로 잡으면 쓰고 난 메모리를 못 돌려줘 RSS 가 계속 는다.
export MALLOC_ARENA_MAX=2
export QUANT_RL_DUCKDB_THREADS=2

LOG="logs/trial-final-residual-rl-BG.log"
QLOG="logs/trial-bg-queue.log"
MARKETS="${1:-KR,US}"   # 합동이 기본이다(공통 틀 §자료)

grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0

# **운영이 먼저다.** 같은 규칙을 scripts/run_night_trials.sh 와 공유한다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/trial_price_transformer.py tools/trial_lambdarank.py \
            tools/trial_final_transformer.py tools/trial_final_lambdarank.py \
            tools/run_daily.py tools/run_session.py tools/backfill.py tools/refresh_accounting.py; do
    if pgrep -f "${tool}" > /dev/null; then
        echo "$(date '+%F %T') ${tool} 도는 중 — 건너뜀" >> "${QLOG}"
        exit 0
    fi
done
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4000 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4000MB — 건너뜀" >> "${QLOG}"
    exit 0
fi

echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 BG(${MARKETS}) 시작" >> "${QLOG}"
{
    echo "=== $(date '+%F %T') 시행 BG 잔차 RL · ${MARKETS} ==="

    # ① 카나리 — 배관 점검과 **필요 스텝 산수**. 판정이 아니다.
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py canary \
        --i-registered --markets "${MARKETS}" --updates 80
    echo "카나리 rc=$?"

    # ② 파일럿 관문 — 검증 우위 > 0 이 아니면 rc=3 으로 멈춘다.
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py pilot \
        --i-registered --markets "${MARKETS}" --updates 60
    rc=$?
    echo "파일럿 rc=${rc}"
    if [ "${rc}" -ne 0 ]; then
        echo "판정: 기각 — 파일럿 관문 불통과(검증 우위 ≤ 0). 본 학습 안 함"
        exit 0
    fi

    # ③ 본 학습 + 판정 — 시드 3, --save 로 시행 예산 1회 소진.
    #
    # **예산 4,000 업데이트(= 그래디언트 스텝 16,000)의 근거.** 업데이트 하나가 0.20초(2026-09-27 실측, 위상
    # 10벌·스레드 2)이고 적합 횟수가 **폴드 2 × 시드 3 × 시장 2 = 12벌**이라 4,000 × 0.20초 × 12 ≈ 2.7시간이다.
    # 폴드가 필요한 이유: C0·C1 은 블록마다 앞만 보고 예측하는 워크포워드라, BG 가 앞 80%로 배우고 100%를
    # 채점받으면 자기 학습 구간을 채점받는다. 그 오염을 없애는 값이 폴드다(그리고 예산을 12벌로 나눈다).
    # 필요 스텝은 정렬도 r 로 c/r², c ≈ 203:
    #   r 0.11 → 16,000 (이 예산과 같다) · r 0.043 → 110,000 (12벌이면 18시간 — 시장을 밤마다 하나씩)
    #   r 0.02 → 508,000 (이 설계로는 못 닿는다 — 그때는 '예산 미달' 이지 '기각' 이 아니다)
    # **위 ①이 찍은 '필요 그래디언트 스텝' 이 이 값보다 훨씬 크면 결과를 '기각' 으로 읽지 않는다** —
    # 예산을 안 찍고 "안 배운다" 를 말하지 않는다(rl-postmortem §1, 카나리 144배 오판).
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py judge \
        --i-registered --markets "${MARKETS}" --updates 4000 --save
    echo "판정 rc=$?"
} >> "${LOG}" 2>&1
