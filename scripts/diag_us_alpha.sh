#!/usr/bin/env bash
# 미장 알파 진단(docs/diag/us-alpha.md) — 미장 단독 예측 굽기 넷 → 계산 → 표. 진단이지 시행이 아니다(창고 쓰기 0).
#
#   setsid nohup scripts/diag_us_alpha.sh > /dev/null 2>&1 &
#
# 시간 관문(리드 2026-10-07): 무거운 단계는 저녁 16:45~21:15 에만 **새로 시작**한다(21:15 에 시작한 단계도 22:20 전에 끝난다는 추정 —
# C1 굽기 한 벌 ≈ 1시간). 10/9·10/10 낮은 CPU 벤치, 10/10~11 은 TX 임베딩이라 그날은 통째로 쉰다. 10/13 부터는 금고 판정 — 그 전에 못 끝나면 rc 9.
# 이어 돌 수 있다 — 예측 파일이 시드 다섯 벌 다 있으면 그 굽기는 건너뛴다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/diag-us-alpha-$(date +%Y%m%d).log"
LOCK="data/_locks/diag-us-alpha.lock"
OUT=data/_diag/us-alpha
RSS_LIMIT_KB=$((4 * 1024 * 1024))
mkdir -p logs data/_locks "${OUT}"
exec >> "${LOG}" 2>&1

exec 9>"${LOCK}"
if ! flock -n 9; then
  echo "$(date '+%F %T') 이미 돌고 있다"
  exit 0
fi

renice -n 10 -p $$ > /dev/null
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1500MB}"
export MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
HEAVY="tools/(measure_ic|trial_[a-z_0-9]+|final_round_controls|train_ranker|backfill_ic_history|diagnose_ic|compact_partitions|diag_style_hedge_cost|fa2021_[a-z_]+|embed_[a-z_]+)\.py"
LIVE="tools/(run_session|reconcile_fills|reconcile_backlog|reconcile_snapshot|chase_orders)\.py|scripts/run_(daily|shadow[a-z0-9_]*)\.sh"
BLOCKED_DAYS="${BLOCKED_DAYS:-2026-10-10 2026-10-11}"
DEADLINE="${DEADLINE:-2026-10-13}"

allowed_now() {
  local hm today
  hm=$((10#$(date +%H%M))); today=$(date +%F)
  if [[ ! "${today}" < "${DEADLINE}" ]]; then
    echo "  $(date '+%F %T') 마감(${DEADLINE}) — 멈추고 보고한다"; exit 9
  fi
  case " ${BLOCKED_DAYS} " in *" ${today} "*) return 1 ;; esac
  [ "${hm}" -ge 1645 ] && [ "${hm}" -lt 2115 ]
}

wait_gate() {
  while true; do
    if ! allowed_now; then sleep 600; continue; fi
    if pgrep -f "${LIVE}" > /dev/null; then
      echo "  $(date '+%T') 운용 세션·대사·shadow 가 도는 중 — 5분 뒤"; sleep 300; continue
    fi
    if pgrep -f "${HEAVY}" > /dev/null; then
      echo "  $(date '+%T') 다른 무거운 연구 작업이 도는 중 — 5분 뒤"; sleep 300; continue
    fi
    avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    if [ "${avail}" -lt $((6 * 1024 * 1024)) ]; then
      echo "  $(date '+%T') 가용 메모리 $((avail / 1024))MB < 6GB — 5분 뒤"; sleep 300; continue
    fi
    return 0
  done
}

run_guarded() {  # RSS 를 30초마다 본다. 4GB 를 넘으면 내리고 rc=137.
  "$@" &
  local pid=$! peak=0 rss
  while kill -0 "${pid}" 2> /dev/null; do
    rss=$(ps -o rss= -p "${pid}" 2> /dev/null | awk '{s+=$1} END {print s+0}')
    [ "${rss}" -gt "${peak}" ] && peak=${rss}
    if [ "${rss}" -gt "${RSS_LIMIT_KB}" ]; then
      echo "  RSS $((rss / 1024))MB > 4GB — 내린다"; kill "${pid}"; wait "${pid}"; return 137
    fi
    sleep 30
  done
  wait "${pid}"; local rc=$?
  echo "  최대 RSS ≈ $((peak / 1024))MB"
  return "${rc}"
}

have_preds() {  # $1 군 $2 묶음(C0 C1 또는 C1)
  local arm="$1" n=0 want=0
  for a in $2; do
    for s in 0 1 2 3 4; do
      want=$((want + 1))
      ls "${OUT}/pred-${arm}/pred-${a}-seed${s}-"*.pkl > /dev/null 2>&1 && n=$((n + 1))
    done
  done
  [ "${n}" -eq "${want}" ]
}

FAILED=0
step() {  # $1 이름, 나머지 명령 — rc 7(파이썬 쪽 시간 관문)이면 다시 기다렸다 돈다
  local name="$1"; shift
  while true; do
    wait_gate
    echo "--- $(date '+%F %T') ${name} ---"
    run_guarded "$@"; local rc=$?
    echo "  ${name} rc=${rc}"
    [ "${rc}" -eq 7 ] && { sleep 600; continue; }
    [ "${rc}" -ne 0 ] && FAILED=$((FAILED + 1))
    return "${rc}"
  done
}

echo "=== $(date '+%F %T') 미장 알파 진단 시작 ==="
PY=".venv/bin/python -u tools/diag_us_alpha.py"
# 계산 배선을 먼저 확인한다(시드 0 · N24 · 있는 예측만, 원본은 smoke/) — 굽기 네 시간 뒤에 계산이 깨지면 그날 저녁을 잃는다.
if [ ! -f "${OUT}/smoke/results.json" ]; then
  if ! step "smoke run" env US_ALPHA_SMOKE=1 taskset -c 0-3 ${PY} run; then
    echo "=== $(date '+%F %T') 스모크 실패 — 굽기 전에 멈춘다 ==="; exit 1
  fi
  US_ALPHA_SMOKE=1 ${PY} report > "${OUT}/smoke/report.md" 2>&1
fi
for arm in old new cut blank; do
  want="C0 C1"; [ "${arm}" = blank ] && want="C1"
  if have_preds "${arm}" "${want}"; then echo "  ${arm}: 예측 있음 — 건너뛴다"; continue; fi
  step "bake ${arm}" taskset -c 0-3 ${PY} bake --arm "${arm}"
done
step "run" taskset -c 0-3 ${PY} run || true
if [ -f "${OUT}/results.json" ]; then
  ${PY} report > "${OUT}/report.md" 2>&1
  echo "  표 → ${OUT}/report.md"
fi
${PY} shadow > "${OUT}/shadow.txt" 2>&1
echo "=== $(date '+%F %T') 끝 · 실패 단계 ${FAILED} ==="
[ "${FAILED}" -eq 0 ] || exit 1
