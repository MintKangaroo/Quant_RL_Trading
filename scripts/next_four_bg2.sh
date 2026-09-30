#!/usr/bin/env bash
# 시행 BG2(BE2 위 잔차 RL + 회전 벌점) 측정 — docs/protocols/next-four-2026-10.md ②. **한 번만.**
# 도구는 시행 BG 와 같은 tools/trial_final_residual_rl.py 에 `--baseline BE2 --turnover-kappa 1.0` 을 준 것이다
# (기본값 = 등록된 BG 그대로 · 실자료는 등록된 조합만 돈다 — 도구가 막는다).
#
#   기동: setsid nohup bash scripts/next_four_bg2.sh >> logs/next-four-bg2-runner.log 2>&1 < /dev/null &
#
# **준비만** — 크론·대기열은 등록 해시 고정 뒤에 리드가 건다. 30분마다 불러도 안전하다:
#  · 이미 끝났으면(로그에 '판정:') 즉시 종료 — 판정은 한 번뿐이다(시행 예산)
#  · 등록이 초안이거나 대조군·BE1 예측·굽기가 없으면 즉시 종료(도구보다 먼저, 패널 조립 전에 본다)
#  · 운영 도구·다른 무거운 학습이 돌거나 가용 메모리가 모자라면 즉시 종료
#  · 운영 창에는 새로 시작하지 않고, 평일은 **끝까지 운영 창에 안 걸리는 시각에만** 시작한다(총 ~2.5시간)
#  · 카나리 → 파일럿 관문 → 본 학습·판정. **관문에서 지면 본 학습을 안 한다**
#
# 비용(BG 9/29~30 실측 기준, BG2 는 같은 구조·같은 예산): 카나리+파일럿 ~15분 · 판정 ~2시간(4,000 업데이트 × 폴드 2 ×
# 시드 3 × 시장 2 + 파일럿 재확인 + 대조 재채점) · 최대 RSS 4~6GB. BG2 는 BE1 5시드 예측(시드당 310만 행)을
# **한 벌씩 흘려서** 평균하므로 +0.5GB 안쪽을 더 본다 → 가용 6,000MB 를 요구한다.
# 머신을 나누지 않는다(memory `training-shares-no-machine`).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/trial-next-four-BG2.log"
GUARD="logs/next-four-bg2-queue.log"
PROTOCOL="docs/protocols/next-four-2026-10.md"
MARKETS="${1:-KR,US}"      # 합동이 기본이다(공통 틀 §자료)
K=1.0                      # 등록값 TURN_K_BG2 — 여기서 바꾸면 도구가 거부한다
ARGS=(--i-registered --markets "${MARKETS}" --baseline BE2 --turnover-kappa "${K}")
mkdir -p logs data/_locks

# 한 번에 하나 — 크론이 겹쳐 불러도 둘이 돌지 않는다.
exec 9>"data/_locks/next-four-bg2.lock"
flock -n 9 || exit 0

# glibc 아레나를 묶는다 — 스레드가 아레나를 따로 잡으면 쓰고 난 메모리를 못 돌려줘 RSS 가 계속 는다.
export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=2
export QUANT_RL_DUCKDB_THREADS=2

# grep -c 는 0건이면 "0" 을 찍고 rc 1 이다 — `|| echo 0` 을 붙이면 "0\n0" 이 되어 비교가 깨진다.
DONE=$(grep -c '^판정:' "${LOG}" 2>/dev/null); DONE=${DONE:-0}
[ "${DONE}" -ge 1 ] && exit 0
pgrep -f "tools/trial_final_residual_r[l].py" > /dev/null && exit 0

# 사전등록이 초안(해시 미고정)인 동안은 시작하지 않는다(도구도 머리줄을 보고 거부한다).
if [ ! -f "${PROTOCOL}" ] || head -1 "${PROTOCOL}" | grep -q "초안"; then
    echo "$(date '+%F %T') 사전등록 ${PROTOCOL} 이 없거나 초안 — 해시 고정 전에는 안 돈다" >> "${GUARD}"
    exit 0
fi

# **대조군·BE1 이 먼저다.** 없으면 도구가 rc=3 으로 멈추지만, 패널 조립(수 GB) 전에 여기서 본다.
# 꼬리표를 등록 창으로 못 박는다: `-smoke2` 짧은 창 파일이 관문을 통과하면 안 된다.
CTAG="KR+US-20220701-20260630"
MISSING=0
for arm in C0 C1; do
    for s in 0 1 2 3 4; do
        [ -f "data/_diag/final-round/pred-${arm}-seed${s}-${CTAG}.pkl" ] || MISSING=$((MISSING + 1))
    done
done
for s in 0 1 2 3 4; do
    [ -f "data/_diag/final-round/BE/seed${s}-judge.parquet" ] || MISSING=$((MISSING + 1))
done
if [ "${MISSING}" -gt 0 ]; then
    echo "$(date '+%F %T') 대조군·BE1 예측 ${MISSING}/15 없음 — scripts/final_round_bake.sh · final_round_BE.sh 먼저" >> "${GUARD}"
    exit 0
fi
if ! .venv/bin/python -c "from tools.final_round_kit import require_full_coverage; require_full_coverage()" \
        >> "${GUARD}" 2>&1; then
    echo "$(date '+%F %T') 굽기 관문 미달 — scripts/final_round_bake_features.sh 먼저" >> "${GUARD}"
    exit 0
fi

# **운영이 먼저, 그리고 머신을 나누지 않는다.** 이 목록이 도는 동안은 시작하지 않는다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/final_round_controls.py tools/trial_final_transformer.py \
            tools/trial_final_lambdarank.py tools/trial_final_dfl.py tools/freeze_be2.py tools/vault_judge.py \
            tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
            tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py \
            tools/collect_us_prices.py; do
    if pgrep -f "${tool}" > /dev/null; then
        echo "$(date '+%F %T') ${tool} 도는 중 — 건너뜀" >> "${GUARD}"
        exit 0
    fi
done
LOCK=logs/.research-lock
if [ -f "${LOCK}" ] && [ -n "$(find "${LOCK}" -mmin -120 2>/dev/null)" ]; then
    echo "$(date '+%F %T') 연구 잠금($(cat "${LOCK}")) — 건너뜀" >> "${GUARD}"
    exit 0
fi

AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 6000 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 6000MB — 건너뜀" >> "${GUARD}"
    exit 0
fi

# 운영 창 — 08:30~09:10 아침 세션 · 12:00~12:40 미장 · 15:15~16:40 마감·대사·수집 · 22:35~00:40 저녁 체인·정규 작업.
# 평일은 **시작 시각 창**만 허용한다: 00:40~05:30 · 16:40~20:00 (~2.5시간 뒤에도 다음 운영 창 전에 끝난다).
HM=$((10#$(date +%H%M))); DOW=$(date +%u)
if [ "${HM}" -ge 2235 ] || [ "${HM}" -lt 40 ]; then
    echo "$(date '+%F %T') 운영 창(${HM}) — 건너뜀" >> "${GUARD}"
    exit 0
fi
if [ "${DOW}" -le 5 ]; then
    if ! { { [ "${HM}" -ge 40 ] && [ "${HM}" -le 530 ]; } || { [ "${HM}" -ge 1640 ] && [ "${HM}" -le 2000 ]; }; }; then
        echo "$(date '+%F %T') 평일 시작 창 밖(${HM}) — 건너뜀" >> "${GUARD}"
        exit 0
    fi
elif { [ "${HM}" -ge 830 ] && [ "${HM}" -le 910 ]; } || { [ "${HM}" -ge 1515 ] && [ "${HM}" -le 1640 ]; }; then
    echo "$(date '+%F %T') 운영 창(${HM}) — 건너뜀" >> "${GUARD}"
    exit 0
fi

echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 BG2(${MARKETS}) 시작" >> "${GUARD}"
{
    echo "=== $(date '+%F %T') 시행 BG2 — BE2 위 잔차 RL + 회전 벌점 k ${K} · ${MARKETS} ==="

    # ① 카나리 — 배관 점검과 **필요 스텝 산수**. 판정이 아니다. 여기서 죽으면(rc≠0) 뒤로 가지 않는다.
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py canary "${ARGS[@]}" --updates 80
    rc=$?
    echo "카나리 rc=${rc}"
    if [ "${rc}" -ne 0 ]; then
        echo "멈춤 이유: 카나리가 rc=${rc} — 3 대조군·BE1 없음 · 4 굽기 · 5 워밍업 창 · 1 등록 거부. 위 로그를 봐라"
        exit 0
    fi

    # ② 파일럿 관문 — 검증 우위 > 0 이 아니면 rc=3. **rc 3 은 '자료 없음'(SystemExit 3)과 겹치므로** 로그의
    #    관문 줄('불통과 — 본 학습 안 한다')을 보고서야 기각으로 적는다. BG 러너는 이 둘을 가르지 않았다.
    before=$(wc -l < "${LOG}")
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py pilot "${ARGS[@]}" --updates 60
    rc=$?
    echo "파일럿 rc=${rc}"
    if [ "${rc}" -ne 0 ]; then
        if tail -n +"$((before + 1))" "${LOG}" | grep -q "불통과 — 본 학습 안 한다"; then
            echo "판정: 기각 — 파일럿 관문 불통과(검증 우위 ≤ 0). 본 학습 안 함"
        else
            echo "멈춤 이유: 파일럿이 관문 판정 없이 rc=${rc} — 자료·등록 문제다(기각 아님). 위 로그를 봐라"
        fi
        exit 0
    fi

    # ③ 본 학습 + 판정 — 시드 3 · 예산 4,000 업데이트(BG 와 같다, 근거는 scripts/final_round_BG.sh) · --save 로 시행 예산 1회 소진.
    #    **위 ①이 찍은 '필요 그래디언트 스텝' 이 16,000 보다 훨씬 크면 결과를 '기각' 으로 읽지 않는다**(카나리 144배 오판).
    before=$(wc -l < "${LOG}")
    nice -n 5 .venv/bin/python -u tools/trial_final_residual_rl.py judge "${ARGS[@]}" --updates 4000 --save
    rc=$?
    echo "판정 rc=${rc}"
    case "${rc}" in
        0) DONE=$(grep -c '^판정:' "${LOG}" 2>/dev/null); [ "${DONE:-0}" -ge 1 ] || echo "경고: rc=0 인데 판정 줄이 없다 — 실패로 본다" ;;
        3) if tail -n +"$((before + 1))" "${LOG}" | grep -q "파일럿 불통과 — 본 학습을 하지 않는다"; then
               echo "판정: 기각 — 판정 안 파일럿 관문 불통과(시장 하나라도 검증 우위 ≤ 0). 본 학습 안 함"
           else
               echo "멈춤 이유: 대조군·BE1 예측 없음(rc 3) — scripts/final_round_bake.sh · final_round_BE.sh 먼저"
           fi ;;
        4) echo "멈춤 이유: 원피처 굽기가 판정 창을 못 덮는다 — scripts/final_round_bake_features.sh 먼저" ;;
        5) echo "멈춤 이유: 에피소드 워밍업 창 부족 — 등록 창·WARMUP_SESSIONS 확인" ;;
        1) echo "멈춤 이유: 사전등록 거부(플래그·초안·k 불일치) 또는 판정 키 누락 — 위 로그를 봐라" ;;
        *) echo "멈춤 이유: 알 수 없음(rc=${rc}, 137·143 이면 메모리) — 위 로그를 봐라" ;;
    esac
} >> "${LOG}" 2>&1
