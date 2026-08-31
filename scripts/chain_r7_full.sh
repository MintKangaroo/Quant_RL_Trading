#!/usr/bin/env bash
# 3회차 완주 체인 — 파일럿 게이트 → (통과 시) 본 학습 → 검증폴드 최적 체크포인트 → 홀드아웃 판정
# → (통과 시) 모의계좌 투입. 사용자 사전 승인(2026-08-29 "준비되면 띄워줘" / 08-30 "AI 가 투자하도록").
# 각 관문에서 실패하면 **다음 단계를 안 간다** — 시도 예산(5회 중 3회 남음)을 아낀다.
set -uo pipefail
cd "$(dirname "$0")/.."
S=logs/chain-r7.log
RUN="${1:-m4-round3-s0-c2-r7}"
PILOT="${RUN}-pilot"
say() { echo "$(date '+%F %T') $*" | tee -a "$S"; }

say "=== 3회차 체인 시작 · run ${RUN}"
free -m | sed -n 2p | tee -a "$S"

# 이 판의 롤링 체크포인트가 있으면 --resume 인자를 만들어 준다.
#
# **없으면 46시간 학습이 재부팅 한 번에 0 으로 돌아간다.** train_rl 은 --resume 을
# 진작 갖고 있었는데 이 체인이 안 넘겨서, 감시자 주석의 "체인이 알아서 잇는다" 가
# 사실이 아니었다 (2026-08-31 발견). 이 기계는 8/30 에 두 번, 8/31 에 한 번 죽었다.
# `{run}.pt` 는 train_rl 이 매 체크포인트마다 원자적으로 갈아 끼우는 최신본이다.
resume_arg() {
  local path="data/rl_checkpoints/$1.pt"
  [ -f "${path}" ] && printf -- "--resume %s" "${path}"
}

say "[1/5] 파일럿 게이트 (150업데이트 + 검증폴드)"
bash scripts/pilot_r7.sh "${PILOT}" > logs/pilot-r7.log 2>&1
gate=$?
say "[1/5] 파일럿 rc=${gate} — $(grep -E '^파일럿 게이트' logs/pilot-r7.log | tail -1)"
if [ "${gate}" -ne 0 ]; then
  say "중단 — 파일럿 불합격. 본 학습을 띄우지 않는다(시도 예산 보존). logs/pilot-r7.log 참고"
  exit 2
fi

RESUME="$(resume_arg "${RUN}")"
say "[2/5] 본 학습 시작 — 15M 스텝(≈915업데이트), 시작점 반복 ≈124 ${RESUME:+· 이어서(${RESUME#--resume })}"
# 로그는 **덮어쓰지 않는다**(>>) — 이어 돌린 판이 앞 판의 기록을 지우면 어디서
# 끊겼는지 못 본다.
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB nice -n 5 .venv/bin/python -u tools/train_rl.py \
  --market KR --seed 0 --curriculum C2 --cash-action fixed --warm-start \
  --keep-checkpoints --lr-decay cosine --timesteps 15000000 \
  --train-start 2023-01-02 --train-end 2025-12-31 --threads 6 \
  --checkpoint-every 20 --run-id "${RUN}" ${RESUME} >> logs/train-r7.log 2>&1
say "[2/5] 본 학습 rc=$? — $(grep -a '^완료' logs/train-r7.log | tail -1)"

say "[3/5] 검증 폴드로 체크포인트 선택"
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/select_checkpoint.py \
  --run "${RUN}" --train-window 2023-01-02:2025-12-31 --valid-window 2026-01-02:2026-06-30 \
  --envs 16 --steps 30 --every 3 --save > logs/select-r7.log 2>&1
BEST=$(grep -a '^최선: 업데이트' logs/select-r7.log | tail -1 | sed -E 's/^최선: 업데이트 ([0-9]+).*/\1/')
say "[3/5] 최적 체크포인트 업데이트 ${BEST:-없음} — $(grep -a '^최선' logs/select-r7.log | tail -1)"
CKPT="data/rl_checkpoints/${RUN}-u${BEST}.pt"
[ -n "${BEST:-}" ] && [ -f "${CKPT}" ] || { say "중단 — 최적 체크포인트를 못 찾았다"; exit 3; }

say "[4/5] 홀드아웃(2026-07-01~08-22) 판정 — 금고 개봉 1회"
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/evaluate_policy.py \
  --checkpoint "${CKPT}" --envs 16 --save > logs/evaluate-r7.log 2>&1
say "[4/5] $(grep -aE '균등가중 대비 우위' logs/evaluate-r7.log | tail -1)"
.venv/bin/python tools/promotion_gate.py --run "${RUN}" > logs/promotion-r7.log 2>&1
prom=$?
say "[4/5] 승격 판정 rc=${prom} — $(tail -2 logs/promotion-r7.log | head -1)"
if [ "${prom}" -ne 0 ]; then
  say "중단 — OOS 불합격. 정책을 끼우지 않는다. 남은 시도와 다음 수를 사람에게 보고한다"
  exit 2
fi

say "[5/5] 모의계좌 투입 — promote_policy"
.venv/bin/python tools/promote_policy.py --checkpoint "${CKPT}" --modes paper >> "$S" 2>&1
say "완료 — 다음 08:40 세션부터 정책이 모의계좌에서 결정한다. 룰은 shadow 로 병주"
