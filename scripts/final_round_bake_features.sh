#!/usr/bin/env bash
# 마지막 모델 회차 — FA 의 새 재료를 **판정 창 전체로** 굽는다 (커버리지 선택지 (가), 리드 결정 2026-09-27).
#
# 왜: 원피처와 묶음 G1·G2·G4·G5 의 국장 캐시가 2025-05-21~ 뿐이어서 판정 창(2022-07~2026-06) 976세션 중 271(28%)만
# 덮는다. 국장 박스 국면 614세션에는 FA 의 새 재료가 한 칸도 없고, 그러면 채택 기준 ③("두 국면 모두")의 박스 다리가
# 사실상 C0 대 C0 이 된다. 이 스크립트가 그 구멍을 메운다.
#
#   setsid nohup scripts/final_round_bake_features.sh > /dev/null 2>&1 &
#
# **실행 일정은 리드가 잡는다.** 하룻밤 이상 걸리고(미장 원피처 300세션이 8시간이었다 — 국장은 여기서 ~1,000세션),
# BC(10/3)·6차 G8(10/4)·`final_round_bake.sh` 와 밤을 다툰다. 머신을 나누지 않는다(memory training-shares-no-machine).
#
# 하는 일 둘, 각각 이어 돌 수 있다:
#  ① 국장 원피처 — 새 캐시 디렉터리 data/_diag/kr-long 에 **달력을 직접 심고** diagnose_ic cache-extra.
#     기존 data/_diag 는 6차 패널의 입력이라 건드리지 않는다(bake_w_us_wide.sh 와 같은 이유).
#     달력은 판정 시작보다 60세션 앞(2022-04)부터 — BE 트랜스포머가 60세션 창을 쓴다.
#  ② 묶음 G1·G2·G4·G5 월 조각 — trial_ranker_sources.build_panel(collect=False). 이미 구운 달은 건너뛴다.
#     G8 은 뺀다(6차 판정 자체가 10/4). G3·G6·G7 은 미장 전용 자료라 국장에서 굽지 않는다.
#
# 끝나면 `tools/final_round_kit.py` 의 RAW_DIRS["KR"] 를 data/_diag/kr-long 으로 바꿔야 새 캐시를 읽는다 —
# **그 한 줄은 리드가 바꾼다**(해시 고정과 같은 결정이라 스크립트가 코드를 고치지 않는다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/final-round-bake-features-$(date +%Y%m%d).log"
LOCK="data/_locks/final-round-bake-features.lock"
OUT=data/_diag/kr-long
WARMUP_START="${WARMUP_START:-2022-04-01}"
BAKE_END="${BAKE_END:-2026-06-30}"
# GROUPS 는 bash 예약 변수(사용자 그룹 번호)라 쓰면 "1000" 이 들어간다(2026-09-27 22:37 실패) — BUNDLES 로.
BUNDLES="${BUNDLES:-G1 G2 G4 G5}"
ANALYSTS="chart event flow_kr fundamental regime risk"
mkdir -p logs data/_locks

exec 9>"${LOCK}"
if ! flock -n 9; then
  echo "$(date '+%F %T') 이미 돌고 있다" >> "${LOG}"
  exit 0
fi

{
  echo "=== $(date '+%F %T') FA 재료 전 창 굽기 · ${WARMUP_START}~${BAKE_END} ==="
  # 기다리는 것 둘. 자기 이름은 패턴에 없다(pgrep 자기매칭 — 복기 규칙 6).
  #  ① 무거운 연구 작업 — 겹치면 둘 다 느려지고 메모리 가드가 하나를 내린다.
  #  ② **운용 세션·대사·shadow** — 이쪽이 먼저다. 주문을 내는 중이거나 장부를 쓰는 중인 프로세스와
  #     창고 읽기를 겹치면 세션이 느려지고, 가드의 보호 대상이라 대신 이 굽기가 내려간다.
  #     00:40 이후에 돌리는 것이 전제지만(리드 일정), 밀린 세션이 있으면 그것도 기다린다.
  HEAVY="tools/(measure_ic|trial_[a-z_]+|final_round_controls|train_ranker|backfill_ic_history|diagnose_ic|compact_partitions)\.py"
  LIVE="tools/(run_session|reconcile_fills|reconcile_backlog|reconcile_snapshot|chase_orders)\.py|scripts/run_(daily|shadow[a-z0-9_]*)\.sh"
  while pgrep -f "${HEAVY}" > /dev/null || pgrep -f "${LIVE}" > /dev/null; do
    if pgrep -f "${LIVE}" > /dev/null; then
      echo "  $(date '+%T') 운용 세션·대사·shadow 가 도는 중 — 5분 뒤 다시 본다"
    else
      echo "  $(date '+%T') 다른 무거운 연구 작업이 도는 중 — 5분 뒤 다시 본다"
    fi
    sleep 300
  done
  export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1500MB}"
  export MALLOC_ARENA_MAX=2
  mkdir -p "${OUT}"
  FAILED=0

  echo "--- $(date '+%F %T') ① 달력 심기 (확장 패널의 세션 그대로) ---"
  WARMUP_START="${WARMUP_START}" BAKE_END="${BAKE_END}" OUT="${OUT}" nice -n 5 .venv/bin/python - <<'PY'
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, ".")
import pandas as pd

out = Path(os.environ["OUT"])
lo = date.fromisoformat(os.environ["WARMUP_START"])
hi = date.fromisoformat(os.environ["BAKE_END"])
path = out / "calendar-KR.pkl"
if path.exists():
    days = pd.read_pickle(path)["session"]  # invariant-allow: data-access — 작업 캐시
    print(f"달력이 이미 있다: {len(days)}세션 {min(days)}~{max(days)}", flush=True)
    raise SystemExit(0)
# 확장 패널의 세션 = 국장 점수 조각의 세션. 같은 달력을 써야 붙는 비율이 100% 가 된다.
scores = pd.read_pickle("data/_diag-long/scores-risk-KR.pkl")  # invariant-allow: data-access — 작업 캐시
days = sorted({d for d in pd.to_datetime(scores["session"]).dt.date if lo <= d <= hi})
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame({"session": days}).to_pickle(path)  # invariant-allow: data-access — 작업 캐시
print(f"달력 {len(days)}세션 {days[0]}~{days[-1]} → {path}", flush=True)
PY
  rc=$?; echo "  달력 rc=${rc}"; [ "${rc}" -ne 0 ] && FAILED=$((FAILED + 1))

  echo "--- $(date '+%F %T') ② 국장 원피처 (Analyst 파일 단위로 이어 구움) ---"
  nice -n 5 .venv/bin/python -u tools/diagnose_ic.py cache-extra --market KR --analyst ${ANALYSTS} --cache-dir "${OUT}"
  rc=$?; echo "  원피처 rc=${rc}"; [ "${rc}" -ne 0 ] && FAILED=$((FAILED + 1))

  echo "--- $(date '+%F %T') ③ 묶음 월 조각 ${BUNDLES} (이미 구운 달은 건너뜀) ---"
  for G in ${BUNDLES}; do
    echo "  $(date '+%T') ${G}"
    GROUP="${G}" WARMUP_START="${WARMUP_START}" BAKE_END="${BAKE_END}" nice -n 5 .venv/bin/python -u - <<'PY'
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, ".")
import pandas as pd

from quant_rl_trading.store import Store
from tools.trial_ranker_sources import build_panel

group = os.environ["GROUP"]
lo, hi = date.fromisoformat(os.environ["WARMUP_START"]), date.fromisoformat(os.environ["BAKE_END"])
scores = pd.read_pickle("data/_diag-long/scores-risk-KR.pkl")  # invariant-allow: data-access — 작업 캐시
days = sorted({d for d in pd.to_datetime(scores["session"]).dt.date if lo <= d <= hi})
del scores
# collect=False — 월 조각만 남기고 합치지 않는다. 합치면 이미 구운 달까지 전부 메모리로 되읽는다(6차 교훈).
build_panel(Store(root=Path("data")), group, "KR", days, collect=False)
print(f"{group} KR 조각 굽기 끝 ({len(days)}세션)", flush=True)
PY
    rc=$?; echo "    ${G} rc=${rc}"; [ "${rc}" -ne 0 ] && FAILED=$((FAILED + 1))
  done

  echo "=== $(date '+%F %T') 끝 · 실패 ${FAILED} ==="
  echo "다음: tools/final_round_kit.py 의 RAW_DIRS['KR'] 를 ${OUT} 로 바꾸고(리드), --precheck 로 커버리지 재확인"
  exit "${FAILED}"
} >> "${LOG}" 2>&1
