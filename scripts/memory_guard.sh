#!/usr/bin/env bash
# 램 가드 — 가용 메모리가 바닥나기 **전에** 연구 작업을 내려 시스템 멈춤을 막는다.
#
# 2026-08-31 실측: 연구 작업이 겹치면 가용이 85~127MB 까지 떨어지고, 스왑 스래싱으로
# 시스템 전체(대시보드 포함)가 굳은 뒤 OOM 킬(2건)·재부팅(1건)으로 이어졌다. 커널
# OOM 킬러는 이미 늦은 시점에 아무나 쏜다 — 우리가 먼저, 우선순위가 낮은 것부터 내린다.
#
#   crontab: */2 * * * * .../scripts/memory_guard.sh
#
# 규칙
# - 가용(MemAvailable) < LIMIT_MB 면 희생 목록의 **첫 번째로 발견되는** 작업 하나를
#   SIGTERM 으로 내리고 로그에 남긴다. 한 번에 하나만 — 과잉 살상 방지.
# - 죽인 작업은 감시자(job_supervisor.sh, */10)가 메모리가 풀리면 다시 띄운다.
#   체크포인트·캐시가 있어 재시작이 싸다는 전제가 이 설계의 근거다.
# - **보호 대상**(절대 안 죽인다): 대시보드(flask), 모의계좌 세션·대사(run_session·
#   reconcile — 주문 중간에 죽이면 부분상태가 남는다), 감시자 자신.
# - 매시 정각 근처(분<2)에는 상태 한 줄을 남겨 추세를 볼 수 있게 한다.
set -u
cd "$(dirname "$0")/.." || exit 1
LOG=logs/memory-guard.log
LIMIT_MB=600
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }

avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
swap_used_mb=$(free -m | awk 'NR==3{print $3}')

# 매시 상태 한 줄 (분이 00~01 인 틱에서만)
if [ "$(date +%M)" -lt 2 ]; then
  say "상태 — 가용 ${avail_mb}MB · 스왑 ${swap_used_mb}MB"
fi

[ "$avail_mb" -ge "$LIMIT_MB" ] && exit 0

# 희생 순서 — 재시작이 싼 것부터. (대괄호로 pgrep 자기매칭 회피)
VICTIMS=(
  "trial_llm_analys[t]"        # agent_cache 덕에 재개가 공짜
  "trial_new_source[s]"        # 측정 재실행 싸다
  "measure_i[c].py"            # 세션 단위 재실행
  "compare_baselines_overnigh[t]" # 재실행 비싸지만 시스템 멈춤보단 낫다
  "train_r[l].py"              # 체크포인트에서 잇는다 — 마지막 수단
)

for pat in "${VICTIMS[@]}"; do
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
    case "$comm" in
      python*)
        rss_mb=$(awk '/VmRSS/ {print int($2/1024)}' "/proc/$pid/status" 2>/dev/null || echo "?")
        say "가용 ${avail_mb}MB < ${LIMIT_MB}MB — ${pat} (pid ${pid}, RSS ${rss_mb}MB) 를 내린다. 감시자가 나중에 되살린다"
        kill "$pid" 2>/dev/null
        exit 0
        ;;
    esac
  done
done

say "가용 ${avail_mb}MB < ${LIMIT_MB}MB — 내릴 연구 작업이 없다 (연구 외 원인, 직접 볼 것)"
exit 0
