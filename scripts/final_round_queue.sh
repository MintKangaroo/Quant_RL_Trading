#!/usr/bin/env bash
# 마지막 모델 회차 대기열 — BF → BG → D1 을 **한 번에 하나씩** 돌린다(사용자 2026-09-29).
#
#   setsid nohup scripts/final_round_queue.sh >> logs/final-round-queue.log 2>&1 < /dev/null &
#
# 9/29 새벽의 크래시 루프(15분 크론이 OOM 으로 죽은 BF 를 네 번 다시 띄웠다)를 되풀이하지 않으려고 만든다:
#  · 다른 프로젝트의 큰 학습(Short_Big_Money chart-cnn)이 도는 동안은 기다린다
#  · 시행마다 **시작 기준 = 최대 RSS(P) − 인정 스왑(최대 1GB) + 0.8GB ≤ 가용** — P 는 아래 표(실측·추정)
#  · 한 시행이 판정 없이 메모리로 죽음(rc 137·143)으로 **두 번** 죽으면 대기열을 멈추고 사람을 부른다(자동 재시도는 두 번까지)
#  · 각 러너는 자기 관문(운영 창·겹침·등록 여부)을 따로 본다 — 여기서는 순서·메모리·재시도만 맡는다
# 5분마다 본다. 끝나면(셋 다 '판정:') 스스로 끝난다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOCK=data/_locks/final-round-queue.lock
mkdir -p data/_locks logs
exec 9>"${LOCK}"
flock -n 9 || { echo "$(date '+%F %T') 대기열이 이미 돈다"; exit 0; }

# 시행 · 러너 · 판정 로그 · 필요한 판정 줄 수 · 최대 RSS(MB). 2026-09-29 메모리 줄이기(1b6e3f8·19d48af) 뒤 추정:
# BF ≈4.8GB(±0.5, 마지막 블록 ~350만 행 외삽) · BG ≈5.0~5.3GB · D1 ≈5.3GB(자료 준비) — 구성요소 합, 실측 뒤 갱신한다.
TRIALS=(
  "BF|scripts/final_round_BF.sh|logs/trial-final-lambdarank-BF.log|1|${P_BF:-5300}"
  "BG|scripts/final_round_BG.sh|logs/trial-final-residual-rl-BG.log|1|${P_BG:-5300}"
  "D1|scripts/final_round_D1.sh|logs/trial-final-dfl-D1.log|2|${P_D1:-5300}"
)
MARGIN_MB=800
SWAP_CREDIT_MAX_MB=1024
MAX_OOM=2
say() { echo "$(date '+%F %T') $*"; }

for row in "${TRIALS[@]}"; do
  IFS='|' read -r name runner log need peak <<<"${row}"
  ooms=0
  while :; do
    done_n=$(grep -c '^판정:' "${log}" 2>/dev/null); done_n=${done_n:-0}
    if [ "${done_n}" -ge "${need}" ]; then say "${name}: 판정 있음 — 다음"; break; fi
    if pgrep -f "run_chart_cnn_minutes_nigh[t]|measure_chart_cnn_minute[s]" > /dev/null; then
      sleep 300; continue   # 다른 프로젝트 학습이 먼저다(사용자: 21:00 쯤 끝난다)
    fi
    avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
    swapf=$(awk '/^SwapFree:/ {print int($2/1024)}' /proc/meminfo)
    credit=$(( swapf < SWAP_CREDIT_MAX_MB ? swapf : SWAP_CREDIT_MAX_MB ))
    need_mb=$(( peak - credit + MARGIN_MB ))
    if [ "${avail}" -lt "${need_mb}" ]; then
      say "${name}: 가용 ${avail}MB < 필요 ${need_mb}MB(P ${peak} − 스왑 ${credit} + ${MARGIN_MB}) — 5분 뒤"
      sleep 300; continue
    fi
    say "${name}: 가용 ${avail}MB ≥ 필요 ${need_mb}MB — 러너 실행"
    before=$(grep -cE '^rc=(137|143)' "${log}" 2>/dev/null); before=${before:-0}
    bash "${runner}"
    after=$(grep -cE '^rc=(137|143)' "${log}" 2>/dev/null); after=${after:-0}
    done_n=$(grep -c '^판정:' "${log}" 2>/dev/null); done_n=${done_n:-0}
    if [ "${done_n}" -ge "${need}" ]; then say "${name}: 끝 — $(grep '^판정:' "${log}" | tail -1)"; break; fi
    if [ "${after}" -gt "${before}" ]; then
      ooms=$((ooms + 1))
      say "${name}: 메모리로 죽음(rc 137·143) ${ooms}/${MAX_OOM}"
      if [ "${ooms}" -ge "${MAX_OOM}" ]; then
        say "${name}: OOM 이 ${MAX_OOM}번 — 대기열을 멈춘다. 메모리 줄이기·P 갱신 뒤 사람이 다시 띄운다"
        exit 3
      fi
    fi
    sleep 300   # 러너가 관문(운영 창·겹침·가용)으로 그냥 나왔으면 5분 뒤 다시
  done
done
say "대기열 끝 — BF·BG·D1 판정 완료"
