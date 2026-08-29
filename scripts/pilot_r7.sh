#!/usr/bin/env bash
# 3회차 파일럿 게이트 (rl-training.md "3회차 설계") — 본 학습 전에 150업데이트를 돌리고
# 검증 폴드(2026-01-02~06-30)로 체크포인트마다 우위를 잰다. rc=0 통과 / 2 실패.
#
#   bash scripts/pilot_r7.sh [run-id]
set -uo pipefail
cd "$(dirname "$0")/.."
RUN="${1:-m4-round3-s0-c2-pilot}"
UPDATES=150
TIMESTEPS=$((UPDATES * 32 * 512))
echo "=== $(date '+%F %T') 파일럿 ${RUN} · ${UPDATES}업데이트 ==="
free -m | sed -n 2p
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/train_rl.py \
  --market KR --seed 0 --curriculum C2 --cash-action fixed --warm-start \
  --keep-checkpoints --lr-decay cosine --timesteps "${TIMESTEPS}" \
  --train-start 2023-01-02 --train-end 2025-12-31 --threads 6 \
  --checkpoint-every 10 --run-id "${RUN}"
echo "train rc=$?"
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/select_checkpoint.py \
  --run "${RUN}" --train-window 2023-01-02:2025-12-31 --valid-window 2026-01-02:2026-06-30 \
  --envs 16 --steps 30 --every 2 --save
rc=$?
echo "gate rc=${rc}"
exit "${rc}"
