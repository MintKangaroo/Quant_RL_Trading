#!/usr/bin/env bash
# 금고 앞당김(7/1~9/30) 굽기 대기열 — `vault_judge.py --plan --window early` 의 ①③④⑤ 를 한 번에 하나씩.
#
#   setsid nohup bash scripts/vault_early_bake_queue.sh >> logs/vault-early-bake-queue.log 2>&1 < /dev/null &
#
# 9/30 19:29~10/1 10:49 호스트 정지로 세션 예약(10/1 16:37 굽기 시작)이 사라져 크론 밖에서 다시 건다.
# 규칙: 운영 창(평일 08:20~16:45 · 매일 22:20~00:40)엔 시작하지 않는다 · 다른 무거운 도구가 돌면 기다린다 ·
# 가용 6GB 미만이면 기다린다 · 결과 파일이 있으면 건너뛴다 · 같은 단계가 두 번 실패하면 멈추고 사람을 부른다.
# ①′(타깃 h5)과 판정은 10/13 이후 — 여기서 하지 않는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p data/_locks logs
exec 9>data/_locks/vault-early-bake-queue.lock
flock -n 9 || { echo "$(date '+%F %T') 이미 돈다"; exit 0; }
say() { echo "$(date '+%F %T') $*"; }

# 순서는 --bake 의 선행 조건을 따른다(10/1 17:02 첫 시도가 rc 3 — 미장 점수 조각·9/30 FA 가 먼저였다).
# ②(미장 점수) → ③·④(원피처) → --bake(①국장 점수·⑤내부자 G4·G7, 미장 달력은 ② 뒤).
STEPS=(
  "②미장점수|data/_diag/vault-early/ic-history-us|.venv/bin/python -u tools/backfill_ic_history.py --market US --start 2026-07 --end 2026-09 --work data/_diag/vault-early/ic-history-us --sessions 120"
  "③국장원피처|data/_diag/vault-early/raw-KR/features-chart-KR.pkl|.venv/bin/python -u tools/diagnose_ic.py cache-extra --market KR --cache-dir data/_diag/vault-early/raw-KR --analyst chart event flow_kr fundamental regime risk"
  "④미장원피처|data/_diag/vault-early/raw-US/features-chart-US.pkl|.venv/bin/python -u tools/diagnose_ic.py cache-extra --market US --cache-dir data/_diag/vault-early/raw-US --analyst chart event flow_us fundamental regime risk"
  "⑤내부자|data/_diag/ranker-sources/G7-US-202609.parquet|.venv/bin/python -u tools/vault_judge.py --bake --window early"
)

blocked() {
  local HM DOW
  HM=$((10#$(date +%H%M))); DOW=$(date +%u)
  if [ "${DOW}" -le 5 ] && [ "${HM}" -ge 820 ] && [ "${HM}" -lt 1645 ]; then return 0; fi
  if [ "${HM}" -ge 2220 ] || [ "${HM}" -lt 40 ]; then return 0; fi
  return 1
}
heavy() {
  # 대괄호로 자기 매칭을 피한다(이 스크립트의 명령줄엔 이 문자열이 없다).
  # BF1 얼리기 대기 스크립트(10/1 16:45 이후 시작)가 살아 있는 동안도 기다린다 — DF2 준비가 먼저다.
  pgrep -f "tools/(freeze_be[2]|trial_[a-z0-9_]+|vault_judg[e]|backfill_ic_histor[y]|diagnose_i[c]|train_ranke[r])\.py|scripts/freeze_bf[1]\.sh" > /dev/null
}

for row in "${STEPS[@]}"; do
  IFS='|' read -r name out cmd <<<"${row}"
  fails=0
  while :; do
    if [ -e "${out}" ]; then say "${name}: 있음 — 다음"; break; fi
    if blocked; then sleep 300; continue; fi
    if heavy; then sleep 300; continue; fi
    avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
    if [ "${avail}" -lt 6000 ]; then sleep 300; continue; fi
    say "${name}: 시작 (가용 ${avail}MB) — ${cmd}"
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB OMP_NUM_THREADS=4 nice -n 5 ${cmd} >> "logs/vault-early-bake-${name}.log" 2>&1
    rc=$?
    say "${name}: rc=${rc}"
    if [ -e "${out}" ]; then break; fi
    fails=$((fails + 1))
    if [ "${fails}" -ge 2 ]; then say "${name}: 두 번 실패(결과 파일 없음) — 멈춘다. logs/vault-early-bake-${name}.log 확인"; exit 3; fi
    sleep 600
  done
done
say "굽기 끝 — ①′ 타깃·판정은 10/13 이후 (vault_judge --plan --window early)"
