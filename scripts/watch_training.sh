#!/usr/bin/env bash
# M4 학습 감시견 — 죽으면 체크포인트에서 이어 돌린다.
#
# **왜 필요한가.** 본 학습은 33시간짜리인데 WSL2 는 Windows 가 자면 같이
# 죽는다(이미 겪었다). 사람이 33시간 동안 지켜볼 수는 없다.
#
# **무한 재시작은 안 한다.** 진짜 버그로 죽는 중이면 되살릴수록 로그만
# 늘고 원인은 안 보인다. MAX_RESTARTS 번까지만 살리고 그 뒤로는 멈춘 채
# 둔다 — 사람이 로그를 읽어야 하는 상태라는 뜻이다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

RUN_ID="${1:-m4-main-s0-r2}"
# **로그와 추가 인자를 밖에서 받는다.** 로그를 고정하면 옛 판의 "완료 —" 를
# 보고 새 판 감시를 즉시 끝내고, 재시작 명령에 학습 인자(--c1·--train-start)
# 가 빠지면 **되살아난 판이 조용히 다른 설정**이 된다 — 죽는 것보다 나쁘다.
LOG_OVERRIDE="${2:-}"
EXTRA_ARGS="${3:-}"
CKPT="data/rl_checkpoints/${RUN_ID}.pt"
LOG="${LOG_OVERRIDE:-logs/train-m4-main.log}"
WATCH_LOG="logs/watchdog.log"
MAX_RESTARTS=5
INTERVAL=60

restarts=0
say() { echo "$(date '+%F %T') $*" >>"${WATCH_LOG}"; }
say "감시 시작 · run=${RUN_ID} · 최대 재시작 ${MAX_RESTARTS}회"

while true; do
    sleep "${INTERVAL}"

    # 완료했으면 감시를 끝낸다. **성공을 재시작으로 착각하지 않는다.**
    if grep -aq "^완료 —" "${LOG}" 2>/dev/null; then
        say "학습 완료 — 감시 종료"
        exit 0
    fi

    if pgrep -f "train_rl.py.*${RUN_ID}" >/dev/null 2>&1; then
        continue
    fi

    # 죽었다. 체크포인트가 없으면 이어 돌릴 수 없다 — 되살려도 처음부터라
    # 오히려 나쁘다(같은 run-id 로 창고 중복 적재까지 난다).
    #
    # **되살린 판이 첫 체크포인트(20업데이트) 전에 또 죽으면** 새 경로엔 파일이
    # 없다. 그때는 직전 체크포인트로 물러선다 — 2026-08-28 00:58 OOM 이 그 경우였고,
    # 여기서 포기해 3시간을 잃었다.
    if [ ! -f "${CKPT}" ] && [ -n "${PREV_CKPT:-}" ] && [ -f "${PREV_CKPT}" ]; then
        say "새 체크포인트가 아직 없다 — 직전 것으로 물러선다: ${PREV_CKPT}"
        CKPT="${PREV_CKPT}"
    fi
    if [ ! -f "${CKPT}" ]; then
        say "죽었는데 체크포인트가 없다 — 되살리지 않는다. 사람이 볼 것"
        exit 1
    fi

    restarts=$((restarts + 1))
    if [ "${restarts}" -gt "${MAX_RESTARTS}" ]; then
        say "재시작 ${MAX_RESTARTS}회를 넘었다 — 멈춘다. 진짜 원인을 봐야 한다"
        exit 1
    fi

    # **run-id 를 바꾼다.** append-only 창고는 같은 ingest_run_id 재적재를
    # 거부한다(실측 2026-08-23). 이어 돌리는 것은 정책이지 기록이 아니다.
    NEW_ID="${RUN_ID}-c${restarts}"
    say "죽었다 — 체크포인트에서 이어 돌린다 (${restarts}/${MAX_RESTARTS} · run=${NEW_ID})"
    QUANT_RL_DUCKDB_MEMORY_LIMIT=800MB QUANT_RL_DUCKDB_THREADS=2 \
        nohup .venv/bin/python -u -W ignore tools/train_rl.py \
        --market KR --seed 0 --checkpoint-every 20 ${EXTRA_ARGS} \
        --run-id "${NEW_ID}" --resume "${CKPT}" \
        >>"${LOG}" 2>&1 &
    PREV_CKPT="${CKPT}"
    CKPT="data/rl_checkpoints/${NEW_ID}.pt"
    say "새 체크포인트 경로: ${CKPT}"
done
