#!/usr/bin/env bash
# 시행 D1 v2(결정 중심 학습 재설계 — 채택 지표 t · 재학습마다 시드 5개 합동 결정) 본 측정 — **한 번만**.
# 사전등록 docs/protocols/decision-focused-v2-2026-10.md(v1 은 docs/protocols/decision-focused-2026-10.md — rc 7 로 판정 전 멈춤, 그대로 둔다).
#
#   setsid nohup scripts/final_round_D1v2.sh >> logs/final-D1v2-runner.log 2>&1 &
#
# **준비만** — 크론·대기열은 v2 등록 해시 고정 뒤에 리드가 건다. 30분마다 불러도 안전하다:
#  · 이미 끝났으면(로그에 '판정:' 두 줄 — 변형 둘) 즉시 종료 — 판정 예산은 변형마다 1회다
#  · 다른 무거운 도구가 돌거나 가용 메모리가 모자라면 즉시 종료
#  · 운영 창(수집·세션·회계·shadow)에는 새로 시작하지 않는다
# 순서(등록 v2 §자기 점검): ① 실자료 라벨 섞기로 여백(재학습별 시드 평균 t 의 최댓값)을 얼린다 ≈ 1.6시간(v1 실측, 판정과 같은 재학습, 예측 없음)
#   ② 합성 canary(그 여백을 건다, D1a − C1 ≥ 1%p) 10~25분 ③ 합성 라벨 섞기(그 여백을 건다, |D1a − C1| ≤ 2%p) 10~25분 ④ 판정 2~4시간.
# 끝난 (변형, 시드)는 data/_diag/final-round/D1/v2-*.parquet, 합동 결정 전 학습 조각은 v2-*-heads.pkl, 여백 조각은 margin-parts/v2-*.json — 중간에 내려가도 잇는다.
# 가드 목록(memory_guard VICTIMS · health_watch STOPPABLE)은 v1 과 같은 도구 이름(trial_final_dfl)이라 그대로 걸린다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/trial-final-dfl-D1v2.log"
GUARD="logs/final-D1v2-queue.log"
PROTOCOL="docs/protocols/decision-focused-v2-2026-10.md"
mkdir -p logs

# glibc 아레나를 묶는다(2026-09-21 트랜스포머). 망이 작아 스레드는 2 — 도구도 torch.set_num_threads(2).
export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=2

# grep -c 는 0건이면 "0" 을 찍고 rc 1 이다 — `|| echo 0` 을 붙이면 "0\n0" 이 되어 비교가 깨진다.
DONE=$(grep -c '^판정:' "${LOG}" 2>/dev/null); DONE=${DONE:-0}
[ "${DONE}" -ge 2 ] && exit 0
pgrep -f "tools/trial_final_df[l].py" > /dev/null && exit 0

# **대조군이 먼저다.** C0·C1 예측 캐시가 없으면 기준 ①~⑥ 을 못 잰다(도구도 rc=3 으로 멈추지만 패널 조립 전에 본다).
# 꼬리표를 등록 창으로 못 박는다: `-smoke2` 짧은 창 파일이 관문을 통과하면 안 된다.
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

# 원피처 굽기가 판정 창을 덮는가 — 달력 파일 몇 KB 만 읽는 싼 관문(수익을 안 본다).
if ! .venv/bin/python -c "from tools.final_round_kit import require_full_coverage; require_full_coverage()" \
        >> "${GUARD}" 2>&1; then
    echo "$(date '+%F %T') 굽기 관문 미달 — scripts/final_round_bake_features.sh 먼저" >> "${GUARD}"
    exit 0
fi

# 사전등록이 초안(해시 미고정)인 동안은 본 측정을 시작하지 않는다(도구도 머리줄을 보고 거부한다).
if grep -q "^> \*\*초안" "${PROTOCOL}" 2>/dev/null; then
    echo "$(date '+%F %T') 사전등록이 초안 — 해시 고정 전에는 안 돈다" >> "${GUARD}"
    exit 0
fi

# **운영이 먼저, 그리고 머신을 나누지 않는다.** 이 목록이 도는 동안은 시작하지 않는다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py \
            tools/trial_ranker_sources.py tools/final_round_controls.py tools/trial_final_transformer.py \
            tools/trial_final_lambdarank.py tools/trial_final_residual_rl.py \
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

echo "$(date '+%F %T') 가용 ${AVAIL}MB — 시행 D1 v2 본 측정 시작(여백 굽기 포함 4~6시간)" >> "${GUARD}"
{
    echo "=== $(date '+%F %T') 시행 D1 v2 — 결정 중심 학습(D1a·D1b) · 채택 지표 t · 재학습별 시드 합동 결정 ==="
    # **판정 전 셋** — ① 실자료 라벨 섞기로 여백을 얼린다(학습 창 안 내부 검증만, 판정 블록 수익 안 봄) ② 합성 canary 를 그 여백을 건 채로
    # ③ 합성 라벨 섞기를 그 여백을 건 채로(거짓 채택 억제). 여백 파일은 등록 해시·백본·채택 규칙을 적는다 — 이미 있으면 다시 굽지 않는다
    # (해시·규칙이 다르면 도구가 rc 7 로 멈추니 파일을 지우고 다시 굽는다).
    rc=0
    MARGIN=data/_diag/final-round/D1/shuffle-margin-v2.json
    if [ ! -f "${MARGIN}" ]; then
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
            .venv/bin/python -u tools/trial_final_dfl.py shuffle --track v2 --i-registered || { rc=7; echo "여백 굽기(실자료 shuffle) 실패"; }
    fi
    if [ "${rc}" -eq 0 ]; then
        nice -n 5 .venv/bin/python -u tools/trial_final_dfl.py canary --track v2 --i-registered || { rc=7; echo "자기 점검 canary 불통과"; }
    fi
    if [ "${rc}" -eq 0 ]; then
        nice -n 5 .venv/bin/python -u tools/trial_final_dfl.py shuffle --synthetic --track v2 --i-registered \
            || { rc=7; echo "자기 점검 합성 라벨 섞기(여백 건 채) 불통과"; }
    fi
    if [ "${rc}" -eq 0 ]; then
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 \
            .venv/bin/python -u tools/trial_final_dfl.py judge --track v2 --i-registered --variant both --save
        rc=$?
    fi
    echo "rc=${rc}"
    # **조용한 실패는 rc 로 내보낸다**(2026-09-16). 판정 줄이 변형마다 하나 — 둘이 없으면 실패다.
    case "${rc}" in
        0) DONE=$(grep -c '^판정:' "${LOG}" 2>/dev/null); [ "${DONE:-0}" -ge 2 ] || echo "경고: rc=0 인데 판정 줄이 둘이 아니다 — 실패로 본다" ;;
        1) echo "멈춤 이유: 사전등록 거부(플래그·초안 머리줄) 또는 판정 키 누락 — 위 로그를 봐라" ;;
        3) echo "멈춤 이유: 대조군 예측 없음 — scripts/final_round_bake.sh 먼저" ;;
        4) echo "멈춤 이유: 원피처 굽기가 판정 창을 못 덮는다 — scripts/final_round_bake_features.sh 먼저" ;;
        5) echo "멈춤 이유: 채점 첫 세션 앞에 직전 재조정(10세션)이 없다 — 등록 창·MIN_TRAIN 이 바뀌었나 확인" ;;
        7) echo "멈춤 이유: 여백 굽기 실패·canary 불통과·합성 섞기 불통과·여백이 등록 해시/채택 규칙과 안 맞음 — 로그를 봐라(해시가 바뀌었으면 v2 여백 파일을 지우고 다시)" ;;
        6) echo "멈춤 이유: D1b 지수 구성 이력이 판정 세션의 90% 미만 — index_members·market_stats 백필 먼저" ;;
        *) echo "멈춤 이유: 알 수 없음(rc=${rc}) — 위 로그를 봐라" ;;
    esac
} >> "${LOG}" 2>&1
