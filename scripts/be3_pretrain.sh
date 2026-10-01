#!/usr/bin/env bash
# 시행 BE3(BE1 + 자기지도 사전학습) — 사전학습 한 번 → 시드별 미세조정 5 → 판정 1회.
# 사전등록 docs/protocols/be3-pretrain-2026-10.md — 머리줄이 초안이면 돌지 않는다(도구도 거부한다).
#
#   setsid nohup scripts/be3_pretrain.sh >> logs/be3-pretrain-runner.log 2>&1 &
#
# **준비만** — 크론·대기열은 등록 해시 고정 뒤에 리드가 건다. 30분마다 불러도 안전하다. **한 번 부르면 한 단계만** 한다:
#   pretrain(없으면) → train seed 0 → … → seed 4 → judge. 단계마다 운영 창·가용 메모리를 다시 본다(시드 하나 ≈ 1.8시간).
#  · 끝난 단계는 건너뛴다 — 체크포인트(pretrain.pt)·시드 캐시(seed{s}-judge/train.parquet)·판정 로그의 '^판정:' 줄
#  · 같은 단계가 두 번 실패(rc≠0)하면 더 부르지 않는다(9/29 크래시 루프 교훈 — 로그를 보고 사람이 푼다)
#  · 다른 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료, 운영 창(수집·세션·회계·shadow)엔 시작하지 않는다
# 예상(등록 문서 §계산량): 사전학습 ≈ 2~3.5시간·RSS ≈ 2.5GB · 시드당 ≈ 1.8시간·RSS ≈ 5GB · 판정 ≈ 30~50분·RSS ≈ 4GB.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
GUARD="logs/be3-pretrain-queue.log"
PROTOCOL="docs/protocols/be3-pretrain-2026-10.md"
DIR="data/_diag/final-round/BE3"
mkdir -p logs

export MALLOC_ARENA_MAX=2

pgrep -f "tools/trial_be3_pretrai[n].py" > /dev/null && exit 0

if grep -q "^> \*\*초안" "${PROTOCOL}" 2>/dev/null || [ ! -f "${PROTOCOL}" ]; then
    echo "$(date '+%F %T') 사전등록이 초안(또는 없음) — 해시 고정 전에는 안 돈다" >> "${GUARD}"
    exit 0
fi

# 대조군(C0·C1)과 회차 BE1 예측 — 판정의 재료. 꼬리표를 등록 창으로 못 박는다(-smoke 파일이 통과하면 안 된다).
CTAG="KR+US-20220701-20260630"
MISSING=0
for s in 0 1 2 3 4; do
    for arm in C0 C1; do
        [ -f "data/_diag/final-round/pred-${arm}-seed${s}-${CTAG}.pkl" ] || MISSING=$((MISSING + 1))
    done
    [ -f "data/_diag/final-round/BE/seed${s}-judge.parquet" ] || MISSING=$((MISSING + 1))
done
if [ "${MISSING}" -gt 0 ]; then
    echo "$(date '+%F %T') 대조·BE1 예측 ${MISSING}/15 없음(꼬리표 ${CTAG}) — 회차 캐시 확인" >> "${GUARD}"
    exit 0
fi

# **운영이 먼저, 그리고 머신을 나누지 않는다.**
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/final_round_controls.py tools/trial_final_transformer.py \
            tools/trial_final_lambdarank.py tools/trial_final_residual_rl.py tools/trial_final_dfl.py tools/freeze_be2.py \
            tools/trial_next_four.py tools/vault_judge.py tools/run_daily.py tools/run_session.py tools/release_slices.py \
            tools/backfill.py tools/refresh_accounting.py tools/collect_prices_ls.py tools/collect_indices_ls.py \
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
if pgrep -f "Project-Quant-RL-Trading/.*scratchpa[d]/.*\.py" > /dev/null; then
    echo "$(date '+%F %T') 스크래치 진단 중 — 건너뜀" >> "${GUARD}"
    exit 0
fi

#   운영 창: 08:30~09:10 아침 세션 · 12:00~12:40 미장 · 15:15~16:40 마감·대사·수집 · 22:35~23:35 국장 저녁 체인
in_ops_window() {
    local HM DOW
    HM=$(date +%H%M); DOW=$(date +%u)
    [ "${DOW}" -le 5 ] && { { [ "${HM}" -ge 830 ] && [ "${HM}" -le 910 ]; } \
        || { [ "${HM}" -ge 1200 ] && [ "${HM}" -le 1240 ]; } \
        || { [ "${HM}" -ge 1515 ] && [ "${HM}" -le 1640 ]; } \
        || { [ "${HM}" -ge 2235 ] && [ "${HM}" -le 2335 ]; }; }
}

# 다음 단계 하나를 고른다. 단계 이름 · 필요한 가용 메모리(MB) · 도구 인자.
STEP=""; NEED=0; ARGS=""
if [ ! -f "${DIR}/pretrain.pt" ]; then
    STEP="pretrain"; NEED=4000; ARGS="pretrain --i-registered"
else
    for s in 0 1 2 3 4; do
        if [ ! -f "${DIR}/seed${s}-judge.parquet" ] || [ ! -f "${DIR}/seed${s}-train.parquet" ]; then
            STEP="seed${s}"; NEED=6500; ARGS="train --i-registered --seeds ${s}"
            break
        fi
    done
    if [ -z "${STEP}" ]; then
        if grep -q '^판정:' "logs/trial-be3-pretrain-judge.log" 2>/dev/null; then
            exit 0        # 끝났다 — 판정 예산은 1회다
        fi
        STEP="judge"; NEED=4500; ARGS="judge --i-registered --save"
    fi
fi

LOG="logs/trial-be3-pretrain-${STEP}.log"
FAILS="logs/.be3-pretrain-fail-${STEP}"
NFAIL=$(cat "${FAILS}" 2>/dev/null); NFAIL=${NFAIL:-0}
if [ "${NFAIL}" -ge 2 ]; then
    echo "$(date '+%F %T') ${STEP}: 실패 ${NFAIL}회 — 더 부르지 않는다(${LOG} 를 보고 ${FAILS} 를 지워라)" >> "${GUARD}"
    exit 0
fi
if in_ops_window; then
    echo "$(date '+%F %T') 운영 창($(date +%H%M)) — ${STEP} 건너뜀" >> "${GUARD}"
    exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt "${NEED}" ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < ${NEED}MB — ${STEP} 건너뜀" >> "${GUARD}"
    exit 0
fi

echo "$(date '+%F %T') 가용 ${AVAIL}MB — BE3 ${STEP} 시작" >> "${GUARD}"
{
    echo "=== $(date '+%F %T') 시행 BE3 ${STEP} ==="
    # 스레드: 트랜스포머는 코어를 몇 개만 빼앗겨도 4.7배 느려진다(회차 교훈 ③) — BE1 과 같은 12, 혼자 돈다.
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
        .venv/bin/python -u tools/trial_be3_pretrain.py ${ARGS}
    rc=$?
    echo "rc=${rc}"
    # **조용한 실패는 rc 로 내보낸다**(2026-09-16).
    case "${rc}" in
        0) if [ "${STEP}" = "judge" ]; then grep -q '^판정:' "${LOG}" || { echo "경고: rc=0 인데 판정 줄이 없다 — 실패로 본다"; rc=99; }; fi ;;
        1) echo "멈춤 이유: 사전등록 거부(플래그·초안 머리줄)" ;;
        3) echo "멈춤 이유: 입력(대조군·BE1·BE3 예측·체크포인트·판정 패널 캐시) 없음" ;;
        4) echo "멈춤 이유: 원피처 캐시가 판정 창을 못 덮는다(kit 굽기 관문)" ;;
        5) echo "멈춤 이유: 60세션 창 미달(kit 창 관문)" ;;
        7) echo "멈춤 이유: 사전학습 누수 관문 — 컷오프 뒤 행 또는 체크포인트·판정 축 불일치" ;;
        9) echo "멈춤 이유: 처리·대조 채점 세션 불일치(회차 캐시 꼬리표)" ;;
        *) echo "멈춤 이유: 알 수 없음(rc=${rc}) — 위 로그를 봐라(메모리 가드에 내려졌을 수 있다)" ;;
    esac
    echo "${rc}" > "logs/.be3-pretrain-rc-${STEP}"
} >> "${LOG}" 2>&1
rc=$(cat "logs/.be3-pretrain-rc-${STEP}" 2>/dev/null); rc=${rc:-99}
if [ "${rc}" -ne 0 ]; then
    echo $((NFAIL + 1)) > "${FAILS}"
    echo "$(date '+%F %T') ${STEP}: rc=${rc} — 여기서 멈춘다(다음 호출이 다시 본다)" >> "${GUARD}"
    exit "${rc}"
fi
echo "$(date '+%F %T') ${STEP}: 끝(rc=0)" >> "${GUARD}"
