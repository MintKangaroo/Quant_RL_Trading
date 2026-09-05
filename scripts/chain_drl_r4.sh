#!/usr/bin/env bash
# 4회차 체인 — docs/protocols/drl-round4-2026-09.md 의 순서 0~6. 끝난 단계는 로그 표식으로 건너뛴다.
# 기동: setsid nohup bash scripts/chain_drl_r4.sh > /dev/null 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
S=logs/chain-drl-r4.log
RUN="m4-round4-s0-c3-r8"; PILOT="${RUN}-pilot"
say() { echo "$(date '+%F %T') $*" | tee -a "$S"; }
done_step() { grep -aq "$1" "$S" 2>/dev/null; }
export QUANT_RL_DUCKDB_THREADS=2
ulimit -v 16777216
say "=== 4회차 체인 시작 ($(free -m | sed -n 2p))"

say "[0/6] 전 Analyst 한계기여 재측정 완료 대기 (logs/ic-full-20260903.log)"
until [ "$(grep -ac '^rc=' logs/ic-full-20260903.log 2>/dev/null)" -ge 2 ]; do sleep 120; done
say "[0/6] 끝 — $(grep -a '^rc=' logs/ic-full-20260903.log | tr '\n' ' ')"

if ! done_step "\[1/6\] 끝"; then
  say "[1/6] ranker 모델 열 확장 2024-01-31~2025-05-31"
  .venv/bin/python -u tools/train_ranker.py --schedule 2024-01-31 2025-05-31 --threads 6 >> logs/train-ranker-schedule-20260903.log 2>&1
  say "[1/6] 끝 rc=$?"
fi
if ! done_step "\[2/6\] 끝"; then
  say "[2/6] ranker 신호 백필 KR 2024-02-01~2026-09-02 · US 2025-08-05~2026-09-02"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/backfill_ranker_signals.py --market KR --start 2024-02-01 --end 2026-09-02 > logs/backfill-ranker-signals-KR.log 2>&1
  a=$?
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/backfill_ranker_signals.py --market US --start 2025-08-05 --end 2026-09-02 > logs/backfill-ranker-signals-US.log 2>&1
  say "[2/6] 끝 rc=${a}/$? — $(tail -1 logs/backfill-ranker-signals-KR.log) / $(tail -1 logs/backfill-ranker-signals-US.log)"
fi
if ! done_step "\[3/6\] 끝"; then
  say "[3/6] ranker 워크포워드 IC 이력 (KR, 2024-08~2026-08, --save --run-tag ranker)"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/backfill_ic_history.py --analyst ranker --market KR --start 2024-08 --end 2026-08 --work data/ic-history-kr --save --run-tag ranker > logs/ic-history-ranker-KR.log 2>&1
  say "[3/6] 끝 rc=$? — $(grep -a '적재\|행' logs/ic-history-ranker-KR.log | tail -1)"
fi
if ! done_step "\[4/6\] 끝"; then
  say "[4/6] RL 캐시 재굽기 KR 2024-02-01~2026-08-31 (--rebuild)"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/build_rl_cache.py --start 2024-02-01 --end 2026-08-31 --market KR --rebuild > logs/rl-cache-r4.log 2>&1
  say "[4/6] 끝 rc=$? — $(tail -1 logs/rl-cache-r4.log)"
fi
if ! done_step "\[5/6\] 끝"; then
  say "[5/6] 파일럿 게이트 (150업데이트 + 검증폴드)"
  bash scripts/pilot_r8.sh "${PILOT}" > logs/pilot-r8.log 2>&1
  gate=$?
  say "[5/6] 끝 rc=${gate} — $(grep -aE '^파일럿 게이트|^최선' logs/pilot-r8.log | tail -1)"
  if [ "${gate}" -ne 0 ]; then
    say "중단 — 파일럿 불합격. 본 학습을 띄우지 않는다(시도 예산 보존). logs/pilot-r8.log"
    exit 2
  fi
fi
if ! done_step "\[6/6\] 끝"; then
  RESUME=""; [ -f "data/rl_checkpoints/${RUN}.pt" ] && RESUME="--resume data/rl_checkpoints/${RUN}.pt"
  say "[6/6] 본 학습 8M 스텝 ${RESUME:+(이어서)}"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/train_rl.py \
    --market KR --seed 0 --curriculum C2 --cash-action fixed --warm-start --reward-baseline candidates \
    --keep-checkpoints --lr-decay cosine --timesteps 8000000 \
    --train-start 2024-02-01 --train-end 2025-12-31 --threads 6 \
    --checkpoint-every 20 --run-id "${RUN}" ${RESUME} >> logs/train-r8.log 2>&1
  say "본 학습 rc=$? — $(grep -a '^완료' logs/train-r8.log | tail -1)"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/select_checkpoint.py \
    --run "${RUN}" --train-window 2024-02-01:2025-12-31 --valid-window 2026-01-02:2026-06-30 \
    --envs 16 --steps 30 --every 3 --save > logs/select-r8.log 2>&1
  BEST=$(grep -a '^최선: 업데이트' logs/select-r8.log | tail -1 | sed -E 's/^최선: 업데이트 ([0-9]+).*/\1/')
  CKPT="data/rl_checkpoints/${RUN}-u${BEST}.pt"
  [ -n "${BEST:-}" ] && [ -f "${CKPT}" ] || { say "중단 — 최적 체크포인트를 못 찾았다"; exit 3; }
  say "홀드아웃 판정 — 금고 개봉 1회 (u${BEST})"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/evaluate_policy.py --checkpoint "${CKPT}" --envs 16 --save > logs/evaluate-r8.log 2>&1
  say "$(grep -aE '균등가중 대비 우위' logs/evaluate-r8.log | tail -1)"
  .venv/bin/python tools/promotion_gate.py --run "${RUN}" > logs/promotion-r8.log 2>&1
  prom=$?
  say "[6/6] 끝 승격 판정 rc=${prom} — $(tail -2 logs/promotion-r8.log | head -1)"
  [ "${prom}" -ne 0 ] && { say "OOS 불합격 — 정책을 끼우지 않는다. 사람에게 보고"; exit 2; }
  say "통과 — 모의계좌 투입은 사람 확인 뒤 promote_policy (자동으로 끼우지 않는다)"
fi
say "체인 끝"
