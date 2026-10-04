#!/usr/bin/env bash
# 2022 하락장을 판정·학습 창에 넣기 위한 FA 재료 굽기 — 2021-11-10 ~ 기존 캐시 첫 날 앞까지 (사용자 승인 2026-10-04, 자료 준비만).
#
#   setsid nohup scripts/fa2021_bake.sh > /dev/null 2>&1 &
#   # 묶음과 미장 원피처를 나란히(잠금을 나눈다):
#   STAGES=us-groups LOCK_SUFFIX=-groups PARALLEL_OK=1 setsid nohup scripts/fa2021_bake.sh > /dev/null 2>&1 &
#   STAGES=us-raw LOCK_SUFFIX=-usraw setsid nohup scripts/fa2021_bake.sh > /dev/null 2>&1 &
#
# **기존 캐시는 건드리지 않는다.** 등록된 시행(final-model-round 'KR+US-20220701-20260630', BE3 패널, vault-early …)이
# kr-long · w-us · ranker-sources · final-round 를 읽는다. 여기서는 전부 data/_diag/fa2021/ 아래 새 파일로만 굽는다.
#   raw-KR-seg/                     국장 원피처 2021-11-10~2022-03-31 (kr-long 은 2022-04-01~)
#   raw-US-seg-{YYYYH}/             미장 원피처 반기 조각 2021-11-10~2024-06-10 (w-us 는 2024-06-11~)
#   ranker-sources/{G}-{시장}-{월}  묶음 월 조각 — 기존 ranker-sources 에 없는 달만
#   raw-KR/ · raw-US/               이어 붙인 전 창 캐시(조각 + 기존 캐시 **복사본**) — `final_round_kit.FA2021` 이 읽는다
#
# 시간 관문(리드 2026-10-04): 평일 08:20~16:45 · 매일 21:40 이후(22:20~00:40 운용 창에 걸치지 않게) ~ 07:30 에는
# **새 단계를 시작하지 않고 기다린다.** 단계마다 이어 돌 수 있다(이미 있는 파일은 건너뛴다).
# 메모리: 단계마다 RSS 감시 — 4GB 를 넘으면 그 단계를 내리고 실패로 적는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/fa2021-bake-$(date +%Y%m%d).log"
LOCK="data/_locks/fa2021-bake${LOCK_SUFFIX:-}.lock"
OUT=data/_diag/fa2021
RSS_LIMIT_KB=$((4 * 1024 * 1024))
STAGES="${STAGES:-kr-raw kr-groups us-groups us-raw merge}"
mkdir -p logs data/_locks "${OUT}"

exec 9>"${LOCK}"
if ! flock -n 9; then
  echo "$(date '+%F %T') 이미 돌고 있다" >> "${LOG}"
  exit 0
fi

renice -n 10 -p $$ > /dev/null  # 자식 전부가 물려받는다 — 셸 함수 단계에도 nice 가 걸린다
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-1500MB}"
export MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
HEAVY="tools/(measure_ic|trial_[a-z_0-9]+|final_round_controls|train_ranker|backfill_ic_history|diagnose_ic|compact_partitions)\.py"
LIVE="tools/(run_session|reconcile_fills|reconcile_backlog|reconcile_snapshot|chase_orders)\.py|scripts/run_(daily|shadow[a-z0-9_]*)\.sh"

#: 날짜 관문(리드 2026-10-04) — 10/10(토)·10/11(일)은 TX 임베딩, 10/13(화)부터는 금고 판정이 머신을 쓴다.
#: 10/12(월) 저녁까지 못 끝나면 멈추고 보고한다(DEADLINE 지나면 기다리지 않고 rc 9 로 나간다).
BLOCKED_DAYS="${BLOCKED_DAYS:-2026-10-10 2026-10-11}"
DEADLINE="${DEADLINE:-2026-10-13}"

allowed_now() {
  local hm dow today
  hm=$((10#$(date +%H%M))); dow=$(date +%u); today=$(date +%F)
  if [[ ! "${today}" < "${DEADLINE}" ]]; then
    echo "  $(date '+%F %T') 마감(${DEADLINE}) — 멈추고 보고한다"; exit 9
  fi
  case " ${BLOCKED_DAYS} " in *" ${today} "*) return 1 ;; esac
  [ "${hm}" -ge 2140 ] && return 1
  [ "${hm}" -lt 730 ] && return 1
  if [ "${dow}" -le 5 ] && [ "${hm}" -ge 820 ] && [ "${hm}" -lt 1645 ]; then return 1; fi
  return 0
}

wait_gate() {
  # 기다리는 것 셋: 시간 관문 · 운용 세션·대사·shadow · 다른 무거운 연구 작업(자기 이름은 패턴에 없다 — 복기 규칙 6).
  while true; do
    if ! allowed_now; then
      echo "  $(date '+%F %T') 시간 관문 밖 — 10분 뒤 다시 본다"; sleep 600; continue
    fi
    if pgrep -f "${LIVE}" > /dev/null; then
      echo "  $(date '+%T') 운용 세션·대사·shadow 가 도는 중 — 5분 뒤"; sleep 300; continue
    fi
    # PARALLEL_OK=1 — 같은 굽기의 다른 단계(미장 원피처)와 나란히 도는 묶음 굽기. 둘 다 합쳐 RSS 4GB 안쪽이다.
    if [ -z "${PARALLEL_OK:-}" ] && pgrep -f "${HEAVY}" > /dev/null; then
      echo "  $(date '+%T') 다른 무거운 연구 작업이 도는 중 — 5분 뒤"; sleep 300; continue
    fi
    avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    if [ "${avail}" -lt $((6 * 1024 * 1024)) ]; then
      echo "  $(date '+%T') 가용 메모리 $((avail / 1024))MB < 6GB — 5분 뒤"; sleep 300; continue
    fi
    return 0
  done
}

run_guarded() {
  # 명령을 띄우고(스크립트 전체가 nice 10) RSS 를 30초마다 본다. 넘으면 내리고 rc=137.
  "$@" &
  local pid=$! peak=0 rss
  while kill -0 "${pid}" 2> /dev/null; do
    rss=$(ps -o rss= --ppid "${pid}" -p "${pid}" 2> /dev/null | awk '{s+=$1} END {print s+0}')
    rss=$((rss + $(pgrep -P "${pid}" | xargs -r -I{} ps -o rss= --ppid {} 2> /dev/null | awk '{s+=$1} END {print s+0}')))
    [ "${rss}" -gt "${peak}" ] && peak=${rss}
    if [ "${rss}" -gt "${RSS_LIMIT_KB}" ]; then
      echo "  RSS $((rss / 1024))MB > 4GB — 내린다"; kill "${pid}"; wait "${pid}"; return 137
    fi
    sleep 30
  done
  wait "${pid}"; local rc=$?
  echo "  최대 RSS ≈ $((peak / 1024))MB"
  return "${rc}"
}

seed_calendar() {  # $1 시장 $2 시작 $3 끝 $4 디렉터리 — 기존 점수 패널의 세션 그대로
  MARKET="$1" LO="$2" HI="$3" DIR="$4" .venv/bin/python - <<'PY'
import glob, os, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
import pandas as pd
m, d = os.environ["MARKET"], Path(os.environ["DIR"])
lo, hi = date.fromisoformat(os.environ["LO"]), date.fromisoformat(os.environ["HI"])
path = d / f"calendar-{m}.pkl"
if path.exists():
    raise SystemExit(0)
if m == "KR":
    s = pd.read_pickle("data/_diag-long/scores-risk-KR.pkl")["session"]  # invariant-allow: data-access — 작업 캐시
else:
    parts = [pd.read_parquet(p, columns=["session"])["session"]  # invariant-allow: data-access — IC 이력 캐시
             for root in ("data/ic-history-us-early", "data/ic-history-us")
             for p in sorted(glob.glob(f"{root}/scores-risk-*.parquet"))]
    s = pd.concat(parts)
days = sorted({x for x in pd.to_datetime(s).dt.date if lo <= x <= hi})
d.mkdir(parents=True, exist_ok=True)
pd.DataFrame({"session": days}).to_pickle(path)  # invariant-allow: data-access — 작업 캐시
print(f"  달력 {m} {len(days)}세션 {days[0]}~{days[-1]} → {path}", flush=True)
PY
}

FAILED=0
step() {  # $1 이름, 나머지 명령
  local name="$1"; shift
  wait_gate
  echo "--- $(date '+%F %T') ${name} ---"
  run_guarded "$@"; local rc=$?
  echo "  ${name} rc=${rc}"
  [ "${rc}" -ne 0 ] && FAILED=$((FAILED + 1))
  return 0
}

bake_groups() {  # $1 시장 $2 묶음들 $3 시작 $4 끝
  MARKET="$1" BUNDLES="$2" LO="$3" HI="$4" OUT="${OUT}" .venv/bin/python -u - <<'PY'
import glob, os, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
import pandas as pd
from quant_rl_trading.store import Store
from tools import trial_ranker_sources as trs
m = os.environ["MARKET"]
lo, hi = date.fromisoformat(os.environ["LO"]), date.fromisoformat(os.environ["HI"])
cal = pd.read_pickle(f"{os.environ['OUT']}/calendar-{m}.pkl")["session"]  # invariant-allow: data-access — 작업 캐시
days = sorted(x for x in pd.to_datetime(cal).dt.date if lo <= x <= hi)
# 새 디렉터리에만 쓴다 — 기존 ranker-sources 에 있는 달은 굽지 않는다(그 달은 기존 조각을 그대로 읽는다).
trs.CACHE = Path(os.environ["OUT"]) / "ranker-sources"
store = Store(root=Path("data"))
for g in os.environ["BUNDLES"].split():
    have = {Path(p).stem.rsplit("-", 1)[-1] for p in glob.glob(f"data/_diag/ranker-sources/{g}-{m}-*.parquet")}  # invariant-allow: data-access — 작업 파일 목록
    todo = [x for x in days if f"{x:%Y%m}" not in have]
    if not todo:
        print(f"  {g} {m}: 굽을 달 없음", flush=True); continue
    trs.build_panel(store, g, m, todo, collect=False)
    print(f"  {g} {m} 끝 ({len(todo)}세션)", flush=True)
PY
}

{
  echo "=== $(date '+%F %T') fa2021 굽기 · 단계 ${STAGES} ==="
  # 달력 둘 — 묶음 굽기가 쓰는 전 구간 달력(국장 2021-11-10~2022-03-31, 미장 2021-11-10~2024-06-10)
  seed_calendar KR 2021-11-10 2022-03-31 "${OUT}"
  seed_calendar US 2021-11-10 2024-06-10 "${OUT}"

  for st in ${STAGES}; do
    case "${st}" in
      kr-raw)
        seed_calendar KR 2021-11-10 2022-03-31 "${OUT}/raw-KR-seg"
        step "국장 원피처 2021-11-10~2022-03-31" .venv/bin/python -u tools/diagnose_ic.py cache-extra \
            --market KR --analyst chart event flow_kr fundamental regime risk --cache-dir "${OUT}/raw-KR-seg" ;;
      kr-groups)
        step "국장 묶음 G1 G2 G5 (2021-11~2022-03)" bake_groups KR "G1 G2 G5" 2021-11-10 2022-03-31 ;;
      us-groups)
        step "미장 묶음 G1 G2 G3 G5 G6 G7 (2021-11~2024-05)" bake_groups US "G1 G2 G3 G5 G6 G7" 2021-11-10 2024-06-10 ;;
      us-raw)
        for seg in 2021H2:2021-11-10:2021-12-31 2022H1:2022-01-01:2022-06-30 2022H2:2022-07-01:2022-12-31 \
                   2023H1:2023-01-01:2023-06-30 2023H2:2023-07-01:2023-12-31 2024H1:2024-01-01:2024-06-10; do
          IFS=: read -r tag lo hi <<< "${seg}"
          dir="${OUT}/raw-US-seg-${tag}"
          seed_calendar US "${lo}" "${hi}" "${dir}"
          # Analyst 하나씩 따로 띄운다 — 단계 하나가 1시간 안팎이 되어 시간 관문 사이에 끊어 돌 수 있다.
          for a in chart event flow_us fundamental regime risk; do
            [ -f "${dir}/features-${a}-US.pkl" ] && continue
            step "미장 원피처 ${tag} ${a}" .venv/bin/python -u tools/diagnose_ic.py cache-extra \
                --market US --analyst "${a}" --cache-dir "${dir}"
          done
        done ;;
      merge)
        # 미장 잇기는 flow_us 1,100만 행 때문에 최대 RSS 6.8GB 였다(2026-10-04) — 4GB 감시에 걸린다. 다시 이을 일이 있으면
        # 잇기만 따로, 가용 메모리 넉넉할 때 손으로 돌린다(tools/fa2021_merge.py --market US).
        step "이어 붙이기 raw-KR · raw-US" .venv/bin/python -u tools/fa2021_merge.py ;;
      panel)
        # 확장 FA 패널 — 시장·반기 조각으로(tools/fa2021_build_panel.py). 끝나면 회차 패널과 겹치는 세션 비교 · 채움률.
        P="${OUT}/panel/panel-KR-KR+US-20211110-20260630.parquet"
        step "FA 패널 KR" .venv/bin/python -u tools/fa2021_build_panel.py --market KR
        step "FA 패널 US" .venv/bin/python -u tools/fa2021_build_panel.py --market US
        step "회차 패널과 비교(국장)" .venv/bin/python -u tools/fa2021_build_panel.py --verify
        step "채움률(월)" .venv/bin/python -u tools/fa_coverage.py "${P}" "${P/panel-KR-/panel-US-}" --end 2024-07-31 ;;
    esac
  done
  echo "=== $(date '+%F %T') 끝 · 실패 ${FAILED} ==="
  exit "${FAILED}"
} >> "${LOG}" 2>&1
