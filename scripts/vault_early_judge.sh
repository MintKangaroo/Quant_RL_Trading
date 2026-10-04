#!/usr/bin/env bash
# 금고 앞당김(7/1~9/30) 판정 러너 — docs/protocols/vault-early-open-2026-10.md 의 실행 순서 5·6 을 10/13 에 자동으로.
#
#   크론(그날만 + 다음 날 재시도 — 둘 다 같은 스크립트, 끝난 단계는 건너뛴다):
#   45 16 13 10 * /home/mintkangaroo/Project/Quant_RL_Trading/scripts/vault_early_judge.sh
#   45 16 14 10 * /home/mintkangaroo/Project/Quant_RL_Trading/scripts/vault_early_judge.sh
#   **끝나면(logs/vault-early-judge-*.log 에 '판정 끝' 또는 '멈춘다') 이 두 줄을 크론에서 지운다.**
#
# 왜 크론인가: 9/30 19:29~10/1 10:49 호스트 정지로 세션 예약이 사라진 적이 있다. 크론은 작업 트리를 직접 돈다.
#
# 단계 (각 단계는 끝나면 표지 파일을 남기고, 재실행 때 건너뛴다):
#   0 보관   — 10/4 리허설에서 찾은 빈칸 둘을 다시 굽게 낡은 캐시를 _stale-20261013/ 로 옮긴다(지우지 않는다).
#              ① 국장 점수 7종은 61세션(9/30 없음 — 10/1 17:01 굽기 때 9/30 signals 가 아직 창고에 없었다)
#              ② 미장 점수·원피처·G4/G7 미장 9월 조각은 57세션(9/22~9/30 없음 — backfill 측정 시점이 9/30 공표라
#                 그 뒤 세션의 h5 라벨이 없었다). 미장 점수는 합집합 꼬리 블록(-01)만 옮긴다(앞 150세션은 그대로 쓴다).
#   1 미장점수 — backfill_ic_history --end 2026-10 --last-session 2026-09-30: 10월 시점(10/12 공표)으로 라벨을 다시 재
#              9/22~9/30 을 채점에 넣는다. 창 밖(10/1~) 라벨은 작업 파일에도 안 남긴다. --save 없음(창고에 안 적는다).
#   2 굽기1  — vault_judge --bake: ①′ 국장 타깃 h5(판정 가능일 이후라 이제 굽는다) · 국장 점수 재굽기 · 원피처 미장 달력 ·
#              내부자 미장 9월 조각. 미장 원피처가 아직 없으니 rc 3 이 정상이다.
#   3 미장원피처 — diagnose_ic cache-extra --market US (10/1 실측 37분).
#   4 굽기2  — vault_judge --bake 가 rc 0(선행 작업 없음)이어야 한다.
#   5 점검   — tools/vault_coverage.py: 국장 62 · 미장 64 세션을 캐시가 다 덮는지(세션 열만 센다). 빠지면 판정하지 않는다.
#   6 판정   — vault_judge --judge --window early --trials AQ,AR,AS,BD,BE2  (**--save 없이** — 창고에 아무것도 안 적는다)
#              표는 logs/vault-early-judge-result.txt 에도 남긴다.
#
# 판정 뒤 절차 (러너는 여기서 멈춘다 — 기록은 사람이 한다):
#   1) 리드가 logs/vault-early-judge-result.txt 의 시행별 표·판정을 사용자에게 보고한다(다섯 시행 각자 제 기준으로만 —
#      성적이 제일 좋은 것을 골라 올리지 않는다, 등록 문서 '다중검정').
#   2) 사용자가 확인하면 그때 기록: .venv/bin/python tools/vault_judge.py --judge --window early --trials AQ,AR,AS,BD,BE2 --save
#      (research_trials 5행 · holdout_access 1행 — 금고 소진. 같은 창을 두 번 기록하지 않는다)
#   3) 기록 직후 research.holdout.start → 2026-10-01 정정본은 **사용자 결정**(등록 문서 실행 순서 7).
#   4) 결과를 docs/trials-postmortem.md · memory 에. 이 크론 두 줄 삭제.
#
# 규칙: 판정 가능일(10/13) 전이면 아무것도 안 한다(도구도 --judge 를 rc 2 로 거부한다) · 운영 창(평일 08:20~16:45,
# 매일 22:20~00:40)·DART 백필 창(00:45~07:30)엔 시작하지 않는다 · 20:30 뒤엔 새 단계를 시작하지 않고 rc 4 로 끝낸다
# (다음 날 재시도 크론이 이어 받는다) · 다른 무거운 도구가 돌거나 가용 6GB 미만이면 기다린다 ·
# 같은 단계가 (실행을 넘어) 두 번 실패하면 STOP 표지를 남기고 rc 3 — 사람을 부른다. 조용한 실패 금지: 모든 결말에 rc 를 찍는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p data/_locks logs
LOG="logs/vault-early-judge-$(date +%Y%m%d).log"
exec >> "${LOG}" 2>&1
exec 9>data/_locks/vault-early-judge.lock
flock -n 9 || { echo "$(date '+%F %T') 이미 돈다 — rc 0"; exit 0; }
say() { echo "$(date '+%F %T') $*"; }
finish() { say "끝 rc=$1 — $2"; exit "$1"; }

VAULT=data/_diag/vault-early
STATE="${VAULT}/_judge-run"
STALE="${VAULT}/_stale-20261013"
RESULT=logs/vault-early-judge-result.txt
JUDGE_FROM=2026-10-13
DEADLINE=2030       # 이 시각(HHMM) 뒤엔 새 단계를 시작하지 않는다 — 판정 단계가 1~1.5시간이라 22:20 운영 창 전에 끝나게
mkdir -p "${STATE}"
PY=".venv/bin/python -u"
ENVS="QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB OMP_NUM_THREADS=4"

say "=== 금고 early 판정 러너 시작 (pid $$) ==="
[ -e "${STATE}/STOP" ] && finish 3 "STOP 표지가 있다($(cat "${STATE}/STOP")) — 사람이 확인하고 지운 뒤 다시"
[ -e "${STATE}/done-6" ] && finish 0 "이미 판정했다 — ${RESULT}. 크론 두 줄을 지운다"
if [[ "$(date +%F)" < "${JUDGE_FROM}" ]]; then
  finish 2 "판정 가능일 ${JUDGE_FROM} 전이다 — 아무것도 안 한다(창 끝 9/30 의 h5 라벨이 아직 안 닫혔다)"
fi

blocked() {
  local HM DOW
  HM=$((10#$(date +%H%M))); DOW=$(date +%u)
  if [ "${DOW}" -le 5 ] && [ "${HM}" -ge 820 ] && [ "${HM}" -lt 1645 ]; then return 0; fi
  if [ "${HM}" -ge 2220 ] || [ "${HM}" -lt 730 ]; then return 0; fi
  return 1
}
late() { [ $((10#$(date +%H%M))) -ge $((10#${DEADLINE})) ]; }
heavy() {
  # 대괄호로 자기 매칭을 피한다. 이 러너가 부른 자식은 이 러너가 끝날 때까지 기다리므로 여기서 걸리지 않는다.
  pgrep -f "tools/(freeze_be[2]|trial_[a-z0-9_]+|vault_judg[e]|backfill_ic_histor[y]|diagnose_i[c]|train_ranke[r]|score_be[2])\.py|scripts/freeze_bf[1]\.sh|scripts/vault_early_bake_queu[e]\.sh" > /dev/null
}
wait_turn() {
  # 시작해도 되는 때까지 기다린다. 마감을 넘기면 rc 4(다음 날 재시도 크론이 이어 받는다).
  local avail
  while :; do
    if late || blocked; then finish 4 "시작 가능 시각을 넘겼다(마감 ${DEADLINE}·운영 창) — 다음 재시도에서 이어 간다"; fi
    if heavy; then say "다른 무거운 도구가 돈다 — 5분 기다림"; sleep 300; continue; fi
    avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
    if [ "${avail}" -lt 6000 ]; then say "가용 ${avail}MB < 6000 — 5분 기다림"; sleep 300; continue; fi
    say "가용 ${avail}MB"
    return 0
  done
}
failed() {
  # 단계 실패를 세고(실행을 넘어 누적), 두 번째면 STOP.
  local n
  n=$(( $(cat "${STATE}/fail-$1" 2>/dev/null || echo 0) + 1 ))
  echo "${n}" > "${STATE}/fail-$1"
  if [ "${n}" -ge 2 ]; then
    echo "$1 단계 두 번 실패 $(date '+%F %T')" > "${STATE}/STOP"
    finish 3 "$1 단계 두 번 실패 — 멈춘다. ${LOG} 확인 뒤 ${STATE}/STOP 을 지우고 다시"
  fi
  finish 1 "$1 단계 실패(${n}번째) — 다음 재시도에서 다시"
}
run() {
  # run <단계> <허용 rc 목록(공백)> <명령...> — 허용 rc 가 아니면 실패.
  local step=$1 ok=$2; shift 2
  wait_turn
  say "[$step] 시작 — $*"
  env ${ENVS} nice -n 5 "$@"
  local rc=$?
  say "[$step] rc=${rc}"
  for want in ${ok}; do [ "${rc}" = "${want}" ] && return 0; done
  failed "${step}"
}
done_step() { touch "${STATE}/done-$1"; say "[$1] 끝"; }
have() { [ -e "${STATE}/done-$1" ]; }

# 0 보관 — 한 번만. 옮길 것이 없으면(이미 옮겼거나 처음부터 없으면) 그냥 지나간다.
if ! have 0; then
  mkdir -p "${STALE}/ic-history-us" "${STALE}/ranker-sources"
  for f in "${VAULT}"/scores-{chart,event,flow_kr,fundamental,ranker,regime,risk}-KR.pkl; do
    [ -e "${f}" ] && mv -v "${f}" "${STALE}/"
  done
  for f in "${VAULT}"/ic-history-us/scores-*-01.parquet; do
    [ -e "${f}" ] && mv -v "${f}" "${STALE}/ic-history-us/"
  done
  [ -d "${VAULT}/raw-US" ] && mv -v "${VAULT}/raw-US" "${STALE}/raw-US"
  for f in data/_diag/ranker-sources/G4-US-202609.parquet data/_diag/ranker-sources/G7-US-202609.parquet; do
    [ -e "${f}" ] && mv -v "${f}" "${STALE}/ranker-sources/"
  done
  done_step 0
fi

# 1 미장 점수·라벨 — 10월 시점을 더해 9/22~9/30 을 넣는다(창 밖 라벨은 버린다).
if ! have 1; then
  run 1 "0" ${PY} tools/backfill_ic_history.py --market US --start 2026-07 --end 2026-10 --last-session 2026-09-30 \
      --work "${VAULT}/ic-history-us" --sessions 120
  done_step 1
fi

# 2 굽기1 — 미장 원피처가 없으니 rc 3 이 정상. ①′ 타깃이 생겼는지 본다.
if ! have 2; then
  run 2 "0 3" ${PY} tools/vault_judge.py --bake --window early
  [ -e "${VAULT}/targets-KR-h5.pkl" ] || failed 2
  [ -e "${VAULT}/raw-US/calendar-US.pkl" ] || failed 2
  done_step 2
fi

# 3 미장 원피처
if ! have 3; then
  run 3 "0" ${PY} tools/diagnose_ic.py cache-extra --market US --cache-dir "${VAULT}/raw-US" \
      --analyst chart event flow_us fundamental regime risk
  done_step 3
fi

# 4 굽기2 — 선행 작업이 하나도 남지 않아야 한다.
if ! have 4; then
  run 4 "0" ${PY} tools/vault_judge.py --bake --window early
  done_step 4
fi

# 5 점검 — 창 전체를 덮지 못하면 판정하지 않는다(창을 조용히 줄이지 않는다).
if ! have 5; then
  run 5 "0" ${PY} tools/vault_coverage.py --window early
  done_step 5
fi

# 6 판정 — --save 없이. 표를 결과 파일에도 남긴다.
if ! have 6; then
  wait_turn
  say "[6] 시작 — vault_judge --judge --window early --trials AQ,AR,AS,BD,BE2 (--save 없음)"
  {
    echo "# 금고 early 판정 — $(date '+%F %T') · --save 없음(창고에 안 적었다, 시행 미소진)"
    echo "# 기록은 사용자 확인 뒤: .venv/bin/python tools/vault_judge.py --judge --window early --trials AQ,AR,AS,BD,BE2 --save"
  } > "${RESULT}.part"
  env ${ENVS} nice -n 5 /usr/bin/time -v -o "${STATE}/time-6.txt" ${PY} tools/vault_judge.py --judge --window early \
      --trials AQ,AR,AS,BD,BE2 2>&1 | tee -a "${RESULT}.part"
  rc=${PIPESTATUS[0]}
  grep -E "Maximum resident|Elapsed \(wall" "${STATE}/time-6.txt" | sed 's/^/# /' >> "${RESULT}.part"
  say "[6] rc=${rc}"
  if [ "${rc}" != 0 ] || ! grep -q "=== 요약 ===" "${RESULT}.part"; then
    failed 6
  fi
  for t in AQ AR AS BD BE2; do
    grep -q "^${t}: " "${RESULT}.part" || { say "[6] 요약에 ${t} 줄이 없다"; failed 6; }
  done
  mv "${RESULT}.part" "${RESULT}"
  done_step 6
fi
finish 0 "판정 끝 — ${RESULT} 를 리드가 사용자에게 보고 → 확인 뒤 --save. 크론 두 줄을 지운다"
