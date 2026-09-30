#!/usr/bin/env bash
# 시행 묶음 next-four(① TB · ③ RE · ④ DF) 본 판정 — 시행마다 **한 번만**. ②는 다른 도구·러너다.
# 사전등록 docs/protocols/next-four-2026-10.md — 머리줄이 초안이면 돌지 않는다(도구도 거부한다).
#
#   setsid nohup scripts/next_four.sh >> logs/next-four-runner.log 2>&1 &
#
# **준비만** — 크론·대기열은 등록 해시 고정 뒤에 리드가 건다. 30분마다 불러도 안전하다:
#  · 끝난 시행(그 로그에 '^판정:' 줄)은 건너뛴다 — 판정 예산은 시행마다 1회다
#  · 같은 시행이 두 번 실패(rc≠0)하면 더 부르지 않는다(9/29 크래시 루프 교훈 — 로그를 보고 사람이 푼다)
#  · 다른 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료, 운영 창(수집·세션·회계·shadow)엔 시작하지 않는다
# 순서 tb → re → df. 새 학습이 없다(회차가 구운 BE1·BF1·C0·C1 예측을 읽기만). 예상: TB 30~50분 · RE 30~50분(첫 회 HMM 굽기
# 시장당 ~3분 포함) · DF 20~40분, 최대 RSS 약 3~4GB(대조군 10벌 ≈1.2GB + 키 패널 + 장부 + 시드별 예측 둘).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
GUARD="logs/next-four-queue.log"
PROTOCOL="docs/protocols/next-four-2026-10.md"
mkdir -p logs

export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=2

pgrep -f "tools/trial_next_fou[r].py" > /dev/null && exit 0

if grep -q "^> \*\*초안" "${PROTOCOL}" 2>/dev/null || [ ! -f "${PROTOCOL}" ]; then
    echo "$(date '+%F %T') 사전등록이 초안(또는 없음) — 해시 고정 전에는 안 돈다" >> "${GUARD}"
    exit 0
fi

# 대조군·처리 예측 — 도구도 rc 3 으로 멈추지만 패널을 읽기 전에 본다. 꼬리표를 등록 창으로 못 박는다(-smoke 파일이 통과하면 안 된다).
CTAG="KR+US-20220701-20260630"
MISSING=0
for s in 0 1 2 3 4; do
    for arm in C0 C1 BF1; do
        [ -f "data/_diag/final-round/pred-${arm}-seed${s}-${CTAG}.pkl" ] || MISSING=$((MISSING + 1))
    done
    [ -f "data/_diag/final-round/BE/seed${s}-judge.parquet" ] || MISSING=$((MISSING + 1))
done
if [ "${MISSING}" -gt 0 ]; then
    echo "$(date '+%F %T') 입력 예측 ${MISSING}/20 없음(꼬리표 ${CTAG}) — 회차 캐시 확인" >> "${GUARD}"
    exit 0
fi

# **운영이 먼저, 그리고 머신을 나누지 않는다.**
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/final_round_controls.py tools/trial_final_transformer.py \
            tools/trial_final_lambdarank.py tools/trial_final_residual_rl.py tools/trial_final_dfl.py tools/freeze_be2.py \
            tools/vault_judge.py tools/run_daily.py tools/run_session.py tools/release_slices.py tools/backfill.py \
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
if pgrep -f "Project-Quant-RL-Trading/.*scratchpa[d]/.*\.py" > /dev/null; then
    echo "$(date '+%F %T') 스크래치 진단 중 — 건너뜀" >> "${GUARD}"
    exit 0
fi

AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4500 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4500MB — 건너뜀" >> "${GUARD}"
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

for CMD in tb re df; do
    LOG="logs/trial-next-four-${CMD}.log"
    FAILS="logs/.next-four-fail-${CMD}"
    if grep -q '^판정:' "${LOG}" 2>/dev/null; then
        continue
    fi
    NFAIL=$(cat "${FAILS}" 2>/dev/null); NFAIL=${NFAIL:-0}
    if [ "${NFAIL}" -ge 2 ]; then
        echo "$(date '+%F %T') ${CMD}: 실패 ${NFAIL}회 — 더 부르지 않는다(${LOG} 를 보고 ${FAILS} 를 지워라)" >> "${GUARD}"
        exit 0
    fi
    if in_ops_window; then
        echo "$(date '+%F %T') 운영 창($(date +%H%M)) — ${CMD} 건너뜀" >> "${GUARD}"
        exit 0
    fi
    AVAIL=$(free -m | awk '/^Mem:/{print $7}')
    if [ "${AVAIL}" -lt 4500 ]; then
        echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4500MB — ${CMD} 건너뜀" >> "${GUARD}"
        exit 0
    fi
    echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 ${CMD} 판정 시작" >> "${GUARD}"
    {
        echo "=== $(date '+%F %T') 시행 next-four ${CMD} ==="
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
            .venv/bin/python -u tools/trial_next_four.py "${CMD}" --i-registered --save
        rc=$?
        echo "rc=${rc}"
        # **조용한 실패는 rc 로 내보낸다**(2026-09-16).
        case "${rc}" in
            0) grep -q '^판정:' "${LOG}" || { echo "경고: rc=0 인데 판정 줄이 없다 — 실패로 본다"; rc=99; } ;;
            1) echo "멈춤 이유: 사전등록 거부(플래그·초안 머리줄)" ;;
            3) echo "멈춤 이유: 입력 예측(C0·C1·BE1·BF1) 또는 판정 패널 캐시 없음" ;;
            6) echo "멈춤 이유: TB 지수 구성 이력이 판정 세션의 90% 미만" ;;
            8) echo "멈춤 이유: HMM 확률이 판정 세션의 95% 미만(지수 종가 결측) 또는 7일 넘게 비었다" ;;
            9) echo "멈춤 이유: 처리·대조 채점 세션 불일치" ;;
            *) echo "멈춤 이유: 알 수 없음(rc=${rc}) — 위 로그를 봐라(메모리 가드에 내려졌을 수 있다)" ;;
        esac
        echo "${rc}" > "logs/.next-four-rc-${CMD}"
    } >> "${LOG}" 2>&1
    rc=$(cat "logs/.next-four-rc-${CMD}" 2>/dev/null); rc=${rc:-99}
    if [ "${rc}" -ne 0 ]; then
        echo $((NFAIL + 1)) > "${FAILS}"
        echo "$(date '+%F %T') ${CMD}: rc=${rc} — 여기서 멈춘다(다음 호출이 다시 본다)" >> "${GUARD}"
        exit "${rc}"
    fi
done
