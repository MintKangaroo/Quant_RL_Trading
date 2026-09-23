#!/usr/bin/env bash
# 6차 학습 측정 — G1~G6 을 등록된 순서로 한 번씩 (docs/protocols/ranker-sources-round6-2026-09.md).
#
#     scripts/measure_ranker_round6.sh            # 기록까지 (--save)
#     DRY=1 scripts/measure_ranker_round6.sh      # 기록 없이
#
# **순서가 규약이다.** 묶음이 채택되면 다음 묶음의 대조는 그 피처를 포함한 랭커다(누적).
# 그래서 한 묶음이 끝나야 다음을 걸 수 있고, 이 스크립트가 그 사슬을 잇는다.
#
# 2026-10-01 전에는 도구가 스스로 막는다(rc=2). 이 스크립트는 그 rc 를 실패로 보지 않는다.
#
# 자원(2026-09-18 실측, 판정 직전까지): 묶음마다 기본 패널 적재 70초 · 최대 RSS 3.3~3.8GB.
# 판정(LightGBM 5블록 × 대조·처리 2모델)은 그 위에 얹힌다. **머신을 나누지 않는다**
# (memory training-shares-no-machine) — 12:00 미장 점수(RSS 2.7GB)·토 14:00 주간 IC 와 겹치지 않게.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/ranker-round6-$(date +%Y%m%d).log"

# **메모리 가드** (2026-09-20). 묶음 하나가 패널 적재만 3.3~3.8GB 를 쓴다. 다른 무거운 작업과
# 겹치면 OOM 으로 사슬이 통째로 멈춘다 — 이미 잰 묶음은 건너뛰므로 다음 회차가 이어받지만,
# 겹치는 줄 알면서 시작할 이유는 없다.
for tool in tools/diagnose_ic.py tools/backfill_ic_history.py tools/measure_ic.py tools/train_ranker.py; do
    if pgrep -f "${tool}" > /dev/null; then
        echo "$(date '+%F %T') ${tool} 가 도는 중 — 이번 회차는 건너뛴다" >> "${LOG}"
        exit 0
    fi
done
# **시행 AM(포트 분산) 판정이 먼저다** (2026-09-22). AM 이 채택되면 6차 ② 기준을 새 구성으로 다시 정의하는 정정을 측정 전에
# 내야 하고, 둘 다 GBM 이라 머신을 나눌 수도 없다. AM 로그에 '판정:' 이 없으면 기다린다(10/2 02:07 크론이 AM 을 돌린다).
if ! grep -q "^판정:" logs/trial-portfolio-variance-AM.log 2>/dev/null; then
    echo "$(date '+%F %T') 시행 AM 판정 전 — 이번 회차는 건너뛴다" >> "${LOG}"
    exit 0
fi
pgrep -f "tools/trial_portfolio_varianc[e]" > /dev/null && { echo "$(date '+%F %T') 시행 AM 도는 중 — 건너뛴다" >> "${LOG}"; exit 0; }
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "${AVAIL}" -lt 4500 ]; then
    echo "$(date '+%F %T') 가용 ${AVAIL}MB < 4500MB — 이번 회차는 건너뛴다" >> "${LOG}"
    exit 0
fi
SAVE="--save"; [ "${DRY:-0}" = "1" ] && SAVE=""
ADOPTED=""
{
  echo "=== $(date '+%F %T') 6차 측정 시작 · save=${SAVE:-없음} ==="
  for g in G1 G2 G3 G4 G5 G6 G7 G8; do  # G8 = 국장 잠정실적(2026-09-23 추가 등록)
    echo "--- $(date '+%F %T') ${g} · 대조 누적 [${ADOPTED:-없음}] ---"
    # **이미 잰 묶음은 다시 안 잰다.** 사전등록은 묶음당 1회이고, 다시 돌리면 시행 예산을
    # 두 번 쓰면서 "여러 번 재고 좋은 것을 골랐다" 가 된다. 판단 근거는 창고 기록이다.
    prior=$(QUANT_RL_DUCKDB_MEMORY_LIMIT=400MB .venv/bin/python tools/round6_prior_verdict.py "${g}" 2>/dev/null)
    if [ -n "${prior}" ]; then
      echo "${g} 는 이미 쟀다 — 건너뛴다 (창고 기록: ${prior})"
      [ "${prior}" = "채택" ] && ADOPTED="${ADOPTED:+${ADOPTED},}${g}"
      continue
    fi
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1200MB QUANT_RL_DUCKDB_THREADS=2 \
      nice -n 5 .venv/bin/python -u tools/trial_ranker_sources.py \
        --group "${g}" ${ADOPTED:+--adopted "${ADOPTED}"} ${SAVE} > "logs/round6-${g}.out" 2>&1
    rc=$?
    grep -vE "FutureWarning|RuntimeWarning|pct_change|c /=" "logs/round6-${g}.out"
    echo "${g} rc=${rc}"
    if [ "${rc}" -eq 2 ]; then
      echo "측정 창이 아직 아니다 — 여기서 멈춘다."
      break
    fi
    if [ "${rc}" -ne 0 ]; then
      echo "★ ${g} 가 rc=${rc} 로 죽었다 — 사슬을 멈춘다(다음 묶음의 대조가 틀어진다)."
      break
    fi
    # **채택이면 누적한다.** 판정문에서 읽는다 — 사람이 손으로 옮기면 한 번은 틀린다.
    if grep -q "^판정: 채택" "logs/round6-${g}.out"; then
      ADOPTED="${ADOPTED:+${ADOPTED},}${g}"
      echo "${g} 채택 → 다음 대조 누적 [${ADOPTED}]"
    else
      echo "${g} 기각 → 대조 그대로"
    fi
  done
  echo "=== $(date '+%F %T') 끝 · 최종 채택 [${ADOPTED:-없음}] ==="
} >> "${LOG}" 2>&1
