#!/usr/bin/env bash
# 마지막 모델 회차 — 대조군 C0·C1 예측 굽기 (docs/protocols/final-model-round-2026-10.md §대조).
#
# **준비만 해 둔 스크립트다. 일정은 팀 리드가 잡는다** — BC(10/3)·6차 G8(10/4) 같은 무거운 작업과 겹치면
# 둘 다 느려지고 메모리 가드가 하나를 내린다(2026-08-28 OOM 두 번·3시간 손실 → memory `training-shares-no-machine`).
#
#   setsid nohup scripts/final_round_bake.sh > /dev/null 2>&1 &
#
# 하는 일: FA 패널 조각을 굽고(없으면), C0(점수 6)·C1(FA) 워크포워드 예측을 시드 5개로 캐시한다.
# **수익·IC·판정은 계산하지 않는다** — 그것은 10/5 이후 시행 도구의 일이다(사전등록 "중간 들여다보기 금지").
#
# 이어 돌 수 있다: 패널 조각과 (군, 시드) 별 예측이 파일로 남고, 이미 있는 것은 건너뛴다.
# 가드 목록에 올라가 있다: scripts/memory_guard.sh VICTIMS · scripts/health_watch.sh STOPPABLE (final_round_controls).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/final-round-bake-$(date +%Y%m%d).log"
LOCK="data/_locks/final-round-bake.lock"
mkdir -p logs data/_locks

exec 9>"${LOCK}"
if ! flock -n 9; then
  echo "$(date '+%F %T') 이미 돌고 있다 — 겹쳐 돌리지 않는다" >> "${LOG}"
  exit 0
fi

{
  echo "=== $(date '+%F %T') 대조군 굽기 시작 ==="
  # 기다리는 것 둘. 자기 자신은 패턴에서 뺀다(pgrep 자기매칭 — 규칙 6 을 두 번 어겼다).
  #  ① 무거운 연구 작업 ② **운용 세션·대사·shadow** — 주문 중·장부 쓰는 중과 창고 읽기를 겹치지 않는다
  #  (가드의 보호 대상이라, 겹치면 대신 이 굽기가 내려간다).
  HEAVY="tools/(measure_ic|trial_[a-z_]+|train_ranker|backfill_ic_history|diagnose_ic|compact_partitions)\.py"
  LIVE="tools/(run_session|reconcile_fills|reconcile_backlog|reconcile_snapshot|chase_orders)\.py|scripts/run_(daily|shadow[a-z0-9_]*)\.sh"
  while pgrep -f "${HEAVY}" > /dev/null || pgrep -f "${LIVE}" > /dev/null; do
    if pgrep -f "${LIVE}" > /dev/null; then
      echo "  $(date '+%T') 운용 세션·대사·shadow 가 도는 중 — 5분 뒤 다시 본다"
    else
      echo "  $(date '+%T') 다른 무거운 연구 작업이 도는 중 — 5분 뒤 다시 본다"
    fi
    sleep 300
  done
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB MALLOC_ARENA_MAX=2 nice -n 5 \
    .venv/bin/python -u tools/final_round_controls.py --bake
  rc=$?
  echo "rc=${rc}"
  echo "=== $(date '+%F %T') 끝 ==="
  exit "${rc}"
} >> "${LOG}" 2>&1
