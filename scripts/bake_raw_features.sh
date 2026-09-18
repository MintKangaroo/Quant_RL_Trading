#!/usr/bin/env bash
# 원피처 캐시 굽기 — 시행 W(원피처 랭커)의 선행 조건 (docs/protocols/raw-feature-ranker-2026-09.md).
#
#     scripts/bake_raw_features.sh            # 두 시장, 같은 창(최근 300세션)
#
# **두 시장을 같은 창으로 새로 굽는다.** 국장 캐시는 2026-08-22 것이라 끝이 다르다 — 한쪽만 새것이면
# 시장 간 비교가 어긋난다. 무거운 작업이다(국장 Analyst 당 300세션 ~13분, 미장은 종목이 네 배).
# 머신을 나누지 않는다(memory training-shares-no-machine) — 일요일 파티션 압축 뒤에 돈다.
#
# 압축이 아직 돌고 있으면 **기다린다.** 캐시 굽기는 창고를 통째로 읽으므로 압축과 겹치면 둘 다 느리고,
# 압축이 원본을 지우는 순간 읽던 목록이 깨질 수 있다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/bake-raw-features-$(date +%Y%m%d).log"
SESSIONS="${SESSIONS:-300}"
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1500MB}"
{
  echo "=== $(date '+%F %T') 원피처 캐시 굽기 · 최근 ${SESSIONS}세션 ==="
  # 자기 자신을 세지 않도록 스크립트 이름이 아니라 압축 도구 이름으로 찾는다(memory background-job-hygiene).
  while pgrep -f "tools/compact_partitions.py" > /dev/null; do
    echo "  $(date '+%T') 파티션 압축이 도는 중 — 기다린다"; sleep 120
  done
  FAILED=0
  for M in KR US; do
    # 시장마다 있는 Analyst 만. flow_kr 은 국장, flow_us 는 미장 것이다.
    if [ "$M" = "KR" ]; then A="chart event flow_kr fundamental regime risk volume"; else A="chart event flow_us fundamental regime risk volume"; fi
    echo "--- $(date '+%F %T') ${M} · 점수·타깃·달력 ---"
    nice -n 5 .venv/bin/python -u tools/diagnose_ic.py cache --market "$M" --sessions "$SESSIONS" --analyst $A
    rc=$?; echo "  ${M} cache rc=${rc}"; [ "$rc" -ne 0 ] && { FAILED=$((FAILED + 1)); continue; }
    echo "--- $(date '+%F %T') ${M} · Analyst 내부 원피처 ---"
    nice -n 5 .venv/bin/python -u tools/diagnose_ic.py cache-extra --market "$M" --analyst $A
    rc=$?; echo "  ${M} cache-extra rc=${rc}"; [ "$rc" -ne 0 ] && FAILED=$((FAILED + 1))
  done
  echo "=== $(date '+%F %T') 끝 · 실패 ${FAILED} ==="
  exit "$FAILED"
} >> "$LOG" 2>&1
