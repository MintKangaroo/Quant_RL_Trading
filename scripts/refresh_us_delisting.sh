#!/usr/bin/env bash
# 미장 상폐 판정 — **전체 창으로 주 1회.**
#
# ## 왜 일일 수집과 갈라 놓나
#
# 상폐 판정은 "마지막 봉이 패널 끝에서 10세션 이상 떨어졌나" 다
# (`us_universe_panel.DEAD_SESSIONS`). 그래서 **창이 짧으면 근거가 창 밖에
# 있다** — 20세션만 보면 그 창 초반에만 나온 종목이 전부 상폐로 찍히는데,
# 실제로는 창 밖에서 멀쩡히 거래됐을 수 있다.
#
# 일일 수집(`collect_daily.sh US`)은 `--sessions` 로 짧게 돌면서 상폐를
# 건너뛴다. 새로 상장된 종목은 그쪽이 매일 잡고, 사라진 종목은 이쪽이
# 주 1회 잡는다. **둘을 한 자리에 두면 5년 스캔이 매일 돈다.**
#
# 상폐 행은 멱등하다(`delisting_run_id` 가 세션당 하나) — 같은 판정을 두 번
# 넣지 않는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
LOG="logs/us-delisting-$(date +%Y%m).log"

export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1500MB}"
export QUANT_RL_DUCKDB_THREADS="${QUANT_RL_DUCKDB_THREADS:-2}"

{
    echo "=== $(date '+%F %T') 미장 상폐 판정 (전체 창) ==="
    # --sessions 를 **주지 않는다.** 그것이 이 스크립트의 존재 이유다.
    .venv/bin/python tools/backfill.py --market US --table universe
    echo "  rc=$?"
} >>"${LOG}" 2>&1
