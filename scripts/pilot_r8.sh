#!/usr/bin/env bash
# 4회차 파일럿 게이트 — docs/protocols/drl-round4-2026-09.md. 3회차 pilot_r7.sh 와 같고 셋만 다르다:
# 학습 창 2024-02-01 부터(ranker 이력 시작) · --reward-baseline candidates · run id.
set -uo pipefail
cd "$(dirname "$0")/.."
RUN="${1:-m4-round4-s0-c3-r8-pilot}"
UPDATES=150
TIMESTEPS=$((UPDATES * 32 * 512))
CKPT="data/rl_checkpoints/${RUN}.pt"
RESUME=""
[ -f "${CKPT}" ] && RESUME="--resume ${CKPT}"
echo "=== $(date '+%F %T') 파일럿 ${RUN} · ${UPDATES}업데이트 ${RESUME:+· 이어서 ${CKPT}} ==="
free -m | sed -n 2p
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/train_rl.py \
  --market KR --seed 0 --curriculum C2 --cash-action fixed --warm-start --reward-baseline candidates \
  --keep-checkpoints --lr-decay cosine --timesteps "${TIMESTEPS}" \
  --train-start 2024-02-01 --train-end 2025-12-31 --threads 6 \
  --checkpoint-every 10 --run-id "${RUN}" ${RESUME}
echo "train rc=$?"
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/select_checkpoint.py \
  --run "${RUN}" --train-window 2024-02-01:2025-12-31 --valid-window 2026-01-02:2026-06-30 \
  --envs 16 --steps 30 --every 2 --save
rc=$?
echo "gate rc=${rc}"
exit "${rc}"
