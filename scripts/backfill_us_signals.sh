#!/usr/bin/env bash
# 미장 신호 이력 백필 — 여지 지도와 shadow 가 볼 과거를 연다 (2026-09-01).
#
# 미장 신호는 2026-08-19 부터만 있고 홀드아웃(2026-07-01) 이전은 **0행**이다.
# 그래서 `measure_headroom --market US` 가 "신호와 가격이 겹치는 세션이 없다" 로
# 끝난다 — **미장 편입이 정말 나은지 잴 수가 없다.** 이 스크립트가 그 과거를 만든다.
#
# 두 단계다(각 도구 문서 참고):
#   ① backfill_ic_history — 시점마다 그때 잴 수 있었던 IC 를 재고 채점 결과를 남긴다
#   ② backfill_signals    — 그 채점을 signals 로 적재한다
#
# **외부 호출이 없다.** 창고에 이미 있는 시세·재무·문서만 읽는다 — KRX 를 두들겨
# 차단당한 직후라(2026-09-01) 남의 서버를 안 쓰는 일부터 한다.
set -uo pipefail
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
WORK=data/ic-history-us
LOG=logs/backfill-us-signals-20260901.log
mkdir -p "$WORK"
{
  echo "=== $(date '+%F %T') [1/2] 미장 IC 이력 채점 2025-08~2026-06 ==="
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python tools/backfill_ic_history.py \
      --market US --start 2025-08 --end 2026-06 --work "$WORK" --save
  echo "[1/2] rc=$?"
  echo "=== $(date '+%F %T') [2/2] signals 적재 ==="
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python tools/backfill_signals.py \
      --market US --start 2025-08-01 --end 2026-06-30 --work "$WORK" --save
  echo "[2/2] rc=$?"
  echo "=== $(date '+%F %T') 끝 ==="
} >> "$LOG" 2>&1
