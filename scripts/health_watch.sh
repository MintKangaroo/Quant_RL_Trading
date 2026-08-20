#!/usr/bin/env bash
# 오래 걸리는 작업을 지켜본다. **경보와 정지 두 가지를 한다.**
#
#   정체  자기 프로세스가 도는데 로그가 안 커진다  → 경보
#   완료  로그가 안 커지는 것이 정상이다            → 조용
#   대기  그 작업의 프로세스가 없다                  → 조용
#   램    전체 사용량이 한계에 닿았다                → **정지**
#
# 저장소 안에 둔다. 예전에는 /tmp 에만 있어서 재부팅하면 사라졌다.

set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

# "이름:로그경로:정체한계(초):프로세스패턴"
WATCH=(
  "IC백필:logs/ic-history-full.log:1800:backfill_ic_history\.py"
  "RL캐시:logs/rl-cache.log:1800:build_rl_cache\.py"
  "RL학습:logs/rl-train.log:1800:train_rl\.py"
  "카나리:logs/canary.log:1800:verify_oracle_canary\.py"
)

# -- 램 한계 ------------------------------------------------------------------
#
# **전체 사용량 기준이다** (사용자 결정 2026-08-19). 이 기계는 9,706MB 이고
# 9.0GiB 를 넘기면 멈춘다. `used = MemTotal - MemAvailable` 로 센다 — free 의
# `used` 는 buff/cache 를 빼서 실제 압박보다 작게 나오고, 그 숫자를 믿으면
# 여유가 있는 줄 알다가 OOM 을 맞는다.
#
# 왜 경보가 아니라 정지인가: 여기까지 오면 곧 커널이 **아무나** 죽인다.
# 그러면 하필 대시보드나 수집이 죽어서 화면이 조용히 낡는다. 우리가 먼저
# **다시 돌릴 수 있는 것**을 골라 멈추는 편이 낫다.
STOP_USED_MB=9216          # 9.0 GiB
WARN_AVAIL_MB=700

# 멈춰도 되는 작업. **전부 이어받기가 되는 것들이다** — build_rl_cache 는 구운
# 세션을 건너뛰고 이어받고, 백필은 ingest_run 으로 막힌다. 대시보드·수집은
# 여기 없다. 그건 멈추면 화면이 낡거나 그날 데이터가 빈다.
STOPPABLE='build_rl_cache\.py|backfill_ic_history\.py|backfill_signals\.py|train_rl\.py|run_grid\.py|verify_oracle_canary\.py'

used_mb() { awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print int((t-a)/1024)}' /proc/meminfo; }
avail_mb() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }

stop_one() {
  # 램을 제일 많이 쓰는 것부터 하나씩. 한 번에 다 죽이면 멈출 필요가
  # 없었던 것까지 멈춘다.
  #
  # **자기 자신과 자기 자식은 건드리지 않는다.** pkill 로 패턴만 보고 쏘면
  # 워처가 스스로를 죽인 적이 있다.
  local pid rss name
  # **이름은 패턴에 걸린 토큰에서 뽑는다.** `$3` 을 쓰면 언제나
  # `.venv/bin/python` 이 나와서 어느 작업을 멈췄는지 알 수 없다.
  read -r pid rss name < <(
    ps -eo pid,rss,cmd --sort=-rss --no-headers \
      | grep -E "$STOPPABLE" \
      | grep -v grep \
      | awk -v me="$$" -v pat="$STOPPABLE" '
          $1 == me { next }
          {
            script = "?"
            for (i = 3; i <= NF; i++) if ($i ~ pat) { script = $i; break }
            print $1, int($2/1024), script
            exit
          }'
  )
  [ -n "${pid:-}" ] || return 1
  # SIGTERM 이다. SIGKILL 로 죽이면 쓰다 만 parquet 이 남을 수 있는데,
  # 이 작업들은 임시 파일 → os.replace 라 TERM 이면 깨끗하게 끝난다.
  kill -TERM "$pid" 2>/dev/null || return 1
  echo "MEM_STOP :: ${used}MB 사용 — ${name##*/}(pid ${pid} · ${rss}MB) 를 멈췄다. 이어받기 되는 작업이다"
  return 0
}

declare -A LAST_SIZE LAST_MOVE
NOW=$(date +%s)
for w in "${WATCH[@]}"; do
  IFS=: read -r name path _ _ <<<"$w"
  LAST_SIZE[$name]=$(stat -c %s "$path" 2>/dev/null || echo 0)
  LAST_MOVE[$name]=$NOW
done
LOW_MEM_AT=0

while true; do
  sleep 60
  NOW=$(date +%s)

  for w in "${WATCH[@]}"; do
    IFS=: read -r name path limit pattern <<<"$w"
    [ -f "$path" ] || continue

    if tail -3 "$path" 2>/dev/null | grep -qE "완료 —|=== .*종료|rc=0"; then
      LAST_MOVE[$name]=$NOW
      continue
    fi
    if ! pgrep -f "$pattern" >/dev/null 2>&1; then
      LAST_MOVE[$name]=$NOW
      continue
    fi

    size=$(stat -c %s "$path" 2>/dev/null || echo 0)
    if [ "$size" -ne "${LAST_SIZE[$name]}" ]; then
      LAST_SIZE[$name]=$size
      LAST_MOVE[$name]=$NOW
      continue
    fi
    stalled=$(( NOW - LAST_MOVE[$name] ))
    if [ "$stalled" -gt "$limit" ]; then
      echo "STALL :: ${name} 이 도는데 로그가 $((stalled/60))분째 안 커진다 — $(tail -1 "$path" 2>/dev/null | head -c 140)"
      LAST_MOVE[$name]=$NOW
    fi
  done

  # -- 램 --------------------------------------------------------------------
  used=$(used_mb)
  if [ "$used" -ge "$STOP_USED_MB" ]; then
    # 한 번에 하나. 다음 순회에서 다시 재고, 그래도 넘으면 또 하나 멈춘다.
    stop_one || echo "MEM_STOP :: ${used}MB 사용 — 멈출 수 있는 작업이 없다. $(ps -eo rss,cmd --sort=-rss --no-headers | awk 'NR==1{printf "최대 %dMB %s", $1/1024, $2}')"
    LOW_MEM_AT=$NOW
    continue
  fi

  avail=$(avail_mb)
  if [ "$avail" -lt "$WARN_AVAIL_MB" ] && [ $(( NOW - LOW_MEM_AT )) -gt 900 ]; then
    echo "MEM_LOW :: 가용 ${avail}MB · 사용 ${used}MB (정지선 ${STOP_USED_MB}MB) — $(ps -eo rss,cmd --sort=-rss --no-headers | awk 'NR==1{printf "최대 %dMB %s", $1/1024, $2}')"
    LOW_MEM_AT=$NOW
  fi
done
