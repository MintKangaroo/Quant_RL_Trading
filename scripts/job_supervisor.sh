#!/usr/bin/env bash
# 오래 도는 연구 작업이 **재부팅·세션 종료에도 이어지게** 한다 (2026-08-30).
#
# 그날 하루에 WSL 이 세 번 죽었고(12:26·18:24 …) 그때마다 §7 비교·시행 G·체인이 통째로
# 사라졌다. 사람이 다시 띄우기 전까지 아무도 몰랐다. 이 스크립트를 10분마다 돌려
# **끝나지 않은 작업이 안 돌고 있으면 다시 띄운다.**
#
#   crontab: */10 * * * * .../scripts/job_supervisor.sh
#            @reboot     .../scripts/job_supervisor.sh
#
# 규칙
# - 완료 표시가 로그에 있으면 다시 안 띄운다(작업마다 자기 표시가 있다).
# - `pgrep -f` 는 **자기 셸도 매칭**하므로 대괄호 패턴으로 피한다(background-job-hygiene).
# - 재시작이 싼 작업만 여기 둔다. 학습(train_rl)은 체크포인트가 있어 체인이 알아서 잇는다.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=logs/supervisor.log
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# 살아 있나 — **python 프로세스만** 센다. `bash -c "... python ..."` 래퍼도 같은 문자열을
# 명령줄에 들고 있어서, python 이 죽고 래퍼만 남은 상태를 "돌고 있다" 로 오판한다
# (2026-08-30 실측). 대괄호 패턴으로 자기 셸 매칭은 이미 피한다.
running() {
  local pid comm
  for pid in $(pgrep -f "$1" 2>/dev/null); do
    comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
    case "$comm" in python*) return 0;; esac
  done
  return 1
}

start() { # 이름 · 완료표시패턴 · 완료표시파일 · 프로세스패턴 · 명령
  local name="$1" done_pat="$2" done_file="$3" proc_pat="$4" cmd="$5"
  if [ -f "$done_file" ] && grep -aq "$done_pat" "$done_file"; then return 0; fi
  if running "$proc_pat"; then return 0; fi
  say "$name 이 안 돌고 있다 — 다시 띄운다"
  setsid bash -c "$cmd" > /dev/null 2>&1 < /dev/null &
}

# 1) §7 베이스라인 비교 (섹터 버그 수정판)
start "§7" "^rc=" logs/compare-baselines-20260830.log "compare_baselines_overnigh[t]" \
  "cd $(pwd) && QUANT_RL_DUCKDB_MEMORY_LIMIT=2GB nice -n 5 .venv/bin/python -u tools/compare_baselines_overnight.py --start 2026-04-01 --end 2026-06-30 >> logs/compare-baselines-20260830.log 2>&1; echo rc=\$? >> logs/compare-baselines-20260830.log"

# 2) 시행 G — LLM Analyst. agent_cache 덕에 이어 돌리면 이미 채점한 세션은 공짜다.
#
# **§7 이 도는 동안은 띄우지 않는다** (2026-08-30). 둘을 겹쳐 돌렸더니 9.7GB 머신에서
# 가용이 200MB 로 떨어져 시행 G 가 두 번 죽었고, 감시자가 그때마다 되살려 캐시 재생만
# 반복했다. 재시작이 싸다고 해서 겹쳐 돌려도 되는 것은 아니다(training-shares-no-machine).
if grep -aq "^rc=" logs/compare-baselines-20260830.log 2>/dev/null; then
start "시행G" "판정 G" logs/trial-g-20260830.log "trial_llm_analys[t]" \
  "cd $(pwd) && QUANT_RL_DUCKDB_MEMORY_LIMIT=600MB nice -n 5 .venv/bin/python -u tools/trial_llm_analyst.py --sessions 60 --sample 120 --save >> logs/trial-g-20260830.log 2>&1"
else
  say "시행G 는 §7 이 끝난 뒤에 띄운다 (메모리)"
fi

# 3) 체인 — §7 뒤 미장 IC → 시행 A → 3회차(파일럿→학습→판정→모의계좌)
start "체인" "3회차 체인 끝" logs/night-chain-20260829.log "chain_2026083[0]" \
  "cd $(pwd) && bash scripts/chain_20260830.sh"
