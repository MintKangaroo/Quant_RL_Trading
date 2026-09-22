#!/usr/bin/env bash
# 시행 W 선행 — 미장 원피처를 **점수 패널과 같은 창(2024-06-11~)** 으로 굽는다 (docs/protocols/raw-feature-ranker-2026-09.md 정정 2).
#
# 기존 data/_diag/features-*-US.pkl 은 2025-07-02~ 300세션이라 미장 패널(ic-history-us, 2024-06-11~)의 앞 1년이 비어 있다.
# 비면 rank-gauss 뒤 0 이 되어 처리군이 그 해를 모르는 채 학습·판정된다(④ 다른 시장 기준이 처리에 불리하게 기운다).
# **별도 폴더(data/_diag/w-us)에 굽는다** — data/_diag 의 점수·달력은 6차 패널의 입력이라 건드리지 않는다.
# 무겁다(미장 300세션이 약 8시간 → 이 창은 ~500세션). 머신을 나누지 않는다: 주간 IC·시행이 돌면 기다린다.
# 같은 날 09:00 파티션 압축과 02:30 텍스트 피처는 diagnose_ic 가 돌면 스스로 건너뛴다(9/22 가드).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
OUT=data/_diag/w-us
LOG="logs/bake-w-us-wide-$(date +%Y%m%d).log"
A="chart event flow_us fundamental regime risk"
done_all=1
for a in ${A}; do [ -f "${OUT}/features-${a}-US.pkl" ] || done_all=0; done
[ "${done_all}" -eq 1 ] && { echo "$(date '+%F %T') 이미 다 구웠다" >> "${LOG}"; exit 0; }
{
  echo "=== $(date '+%F %T') 미장 원피처 넓은 창 굽기 ==="
  while pgrep -f "tools/(measure_ic|trial_|train_ranker|backfill_ic_history|compact_partitions)[a-z_]*\.py" > /dev/null; do
    echo "  $(date '+%T') 다른 무거운 작업이 도는 중 — 기다린다"; sleep 300
  done
  mkdir -p "${OUT}"
  # 달력 = 미장 점수 패널의 세션 그대로(2024-06-11~). 판정은 홀드아웃 전만 쓰지만 굽기는 패널 끝까지.
  .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
import pandas as pd
from tools.trial_pooled import load_us
us = load_us()
cal = pd.DataFrame({"session": sorted(us["session"].unique())})
cal.to_pickle("data/_diag/w-us/calendar-US.pkl")
print(f"달력 {len(cal)}세션 {cal['session'].min()}~{cal['session'].max()}", flush=True)
PY
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB MALLOC_ARENA_MAX=2 nice -n 5 .venv/bin/python -u tools/diagnose_ic.py \
      cache-extra --market US --analyst ${A} --cache-dir "${OUT}"
  echo "rc=$?"
  echo "=== $(date '+%F %T') 끝 ==="
} >> "${LOG}" 2>&1
