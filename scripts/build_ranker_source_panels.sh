#!/usr/bin/env bash
# 6차 학습 피처 조각 굽기 — 판정 없이 피처만 (docs/protocols/ranker-sources-round6-2026-09.md "커버리지만 허용").
# --smoke 는 세션 N개만 굽고 조각을 저장하지 않으므로, 전 세션 조각을 남기려면 build_panel 을 직접 부른다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/ranker-source-panels-$(date +%Y%m%d).log"
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1200MB}"
{
  echo "=== $(date '+%F %T') 피처 조각 굽기 시작 ==="
  nice -n 5 .venv/bin/python -u - <<'PY'
from pathlib import Path
from quant_rl_trading.store import Store
from tools.trial_pooled import load_kr, load_us
from tools.trial_ranker_sources import build_panel
store = Store(root=Path("data"))
kr, _ = load_kr(); us = load_us()
sessions = {"KR": sorted(kr["session"].unique()), "US": sorted(us["session"].unique())}
import os
groups = tuple(os.environ.get("GROUPS", "G1,G2,G3,G4,G5").split(","))
markets = tuple(os.environ.get("MARKETS", "KR,US").split(","))
for group in groups:
    for market in markets:
        if (group, market) in (("G3", "KR"), ("G4", "US")):
            continue  # 등록대로 그 시장은 전부 0 — 굽지 않는다
        # collect=False — 월 조각만 남긴다. 합치면 구운 달을 전부 되읽어 RSS 가 GB 로 간다.
        build_panel(store, group, market, sessions[market], collect=False)
        months = len(list(Path("data/_diag/ranker-sources").glob(f"{group}-{market}-*.parquet")))
        print(f"{group} {market}: 월 조각 {months}개", flush=True)
print("완료", flush=True)
PY
  echo "rc=$?"
} >> "$LOG" 2>&1
