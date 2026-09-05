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
# - 재시작이 싼 작업만 여기 둔다. 학습(train_rl)은 체인이 --resume 으로 체크포인트에서
#   이어 붙인다 (2026-08-31 에 실제로 그렇게 만들었다 — 그 전까지 이 줄은 거짓이었다).
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

# 오케스트레이터(체인)용 — **bash 프로세스**가 살아 있으면 돌고 있는 것으로 본다.
# 체인은 단계마다 다른 python(measure_ic·trial·train_rl)을 자식으로 돌리므로 python
# comm 만 세는 running() 으로는 "현재 자식 이름이 안 맞아" 죽은 걸로 오판하고 두 번째
# 체인을 띄운다 (2026-08-31 실측: 미장 IC 가 두 개 돌았다). 단발 래퍼와 달리 체인의
# 각 단계는 foreground 블로킹이라 bash 가 살아 있으면 진짜로 일하는 중이다.
running_orch() {
  local pid comm
  for pid in $(pgrep -f "$1" 2>/dev/null); do
    comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
    case "$comm" in bash|python*) return 0;; esac
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
# 체인은 bash 오케스트레이터라 running_orch 로 센다 (running() 은 python 자식 이름이
# 단계마다 바뀌어 오판한다 — 위 running_orch 주석 참고).
if [ -f logs/night-chain-20260829.log ] && grep -aq "3회차 체인 끝" logs/night-chain-20260829.log; then
  :
elif running_orch "chain_2026083[0]"; then
  :
else
  say "체인 이 안 돌고 있다 — 다시 띄운다"
  setsid bash -c "cd $(pwd) && bash scripts/chain_20260830.sh" > /dev/null 2>&1 < /dev/null &
fi

# 4) 3회차 체인을 **직접** 지킨다. 바깥 체인(chain_20260830)이 죽고 이것만 고아로
# 남는 경우가 있고(2026-08-31 실측), 반대로 이것만 죽는 경우도 있다. 학습은
# --resume 으로 체크포인트에서 이어지므로 되살리는 값이 크다 — 46시간짜리다.
# 바깥 체인이 살아 있으면 그쪽이 알아서 부르므로 여기서는 손대지 않는다.
if [ -f logs/chain-r7.log ] && grep -aq "^.*완료 — 다음 08:40\|중단 —" logs/chain-r7.log; then
  :
elif running_orch "chain_r7_ful[l]" || running_orch "chain_2026083[0]"; then
  :
elif [ -f logs/chain-r7.log ]; then
  say "3회차 체인이 시작됐는데 안 돌고 있다 — 체크포인트에서 이어 띄운다"
  setsid bash -c "cd $(pwd) && bash scripts/chain_r7_full.sh" > /dev/null 2>&1 < /dev/null &
fi

# 5) 시행 C·D (PEAD·내부자) — 3회차 파일럿 불합격 뒤 재료 쪽으로 방향을 튼 작업.
# 직렬 스크립트라 running_orch 로 세고, 완료 표식은 자기 로그에 남긴다.
if [ -f logs/trials-cd-20260901.log ] && grep -aq "시행 C·D 끝" logs/trials-cd-20260901.log; then
  :
elif running_orch "trials_c[d]" || running "trial_new_source[s]"; then
  :
else
  say "시행 C·D 가 안 돌고 있다 — 다시 띄운다"
  setsid bash -c "cd $(pwd) && bash scripts/trials_cd.sh" > /dev/null 2>&1 < /dev/null &
fi

# 6) 미장 신호 백필 — 여지 지도가 미장을 잴 수 있게 과거를 연다 (2026-09-01).
# 외부 호출이 없어 재시작이 안전하다. 두 단계 중 어디서 죽어도 처음부터 다시
# 돌면 되고, 창고가 중복을 거부하므로 두 번 적재되지 않는다.
# **일시정지 표식**이 있으면 안 띄운다. 야간 수집·run_daily 와 겹치면 서로 메모리를
# 뺏는다(2026-09-01: run_daily 가 8GB ulimit 에 걸려 죽었다). 사람이 자리를 비켜
# 주려고 멈춘 것을 감시자가 10분 뒤 되살리면 그 배려가 무의미해진다.
if [ -f logs/.pause-us-backfill ]; then
  :
# 완료 표식은 `=== ... 끝 ===` 이라 `끝$` 로는 안 맞는다 — 그러면 감시자가 끝난
# 작업을 영원히 되살린다(2026-09-02 실측: 1단계가 rc=0 으로 끝났는데 또 띄웠다).
elif [ -f logs/backfill-us-signals-20260901.log ] && grep -aq "\[2/2\] rc=0" logs/backfill-us-signals-20260901.log; then
  :
elif running_orch "backfill_us_signal[s]" || running "backfill_ic_histor[y]" || running "backfill_signal[s]"; then
  :
elif [ -f logs/backfill-us-signals-20260901.log ]; then
  say "미장 신호 백필이 안 돌고 있다 — 다시 띄운다"
  setsid bash -c "cd $(pwd) && bash scripts/backfill_us_signals.sh" > /dev/null 2>&1 < /dev/null &
fi
