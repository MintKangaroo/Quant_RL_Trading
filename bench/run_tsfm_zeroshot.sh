#!/usr/bin/env bash
# TSFM 제로샷 진단 러너(docs/diag/tsfm-zeroshot.md §1) — **크론 없음**, 리드가 손으로 띄운다. CPU 벤치 뒤에 이어 돌려도 된다:
#
#   setsid nohup nice -n 5 bash -c 'bench/run_cpu_bench.sh; bench/run_tsfm_zeroshot.sh' > /dev/null 2>&1 &
#
# 1 extract(실전 .venv, 창고 읽기 — as_of 2026-06-30 고정, 금고 안 읽음) → 2 추론(.venv-bench, 입력 계열만) → 3 score(실전 .venv).
# Kronos 는 D: 에 있다 — data/_bench/D_DRIVE_OK 가 있을 때만(chkdsk 정상 판정 뒤 리드가 만든다) 돌고, 없으면 "미측정(디스크 점검 대기)".
# 그 칸만 나중에: BENCH_TSFM_MODELS="kronos_small kronos_base" bench/run_tsfm_zeroshot.sh
# 시간 창: 평일 08:30~15:45·22:20~00:40·00:45~07:30 금지(시작할 때 본다 — 추론 중에는 모델마다 가용 6GB 를 본다). 모델마다 90분 예산.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/tsfm-zeroshot-$(date +%Y%m%d-%H%M).log"
THREADS="${BENCH_THREADS:-10}"
export OMP_NUM_THREADS="${THREADS}" MALLOC_ARENA_MAX=2 HF_HUB_OFFLINE=1
log() { echo "$(date '+%F %T') $*" | tee -a "${LOG}"; }
HM=$(date +%H%M); DOW=$(date +%u)
if { [ "${DOW}" -le 5 ] && [ "${HM}" -ge 0830 ] && [ "${HM}" -le 1545 ]; } || [ "${HM}" -ge 2220 ] || [ "${HM}" -le 0730 ]; then
    log "시간 창 밖(${HM}, 요일 ${DOW}) — 시작 안 함"; exit 5; fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -ge 6000 ] || { log "가용 ${AVAIL}MB < 6000MB — 시작 안 함"; exit 4; }
log "=== TSFM 제로샷 시작 · 스레드 ${THREADS} ==="
nice -n 10 .venv/bin/python tools/diag_tsfm_zeroshot.py extract >> "${LOG}" 2>&1 || { log "extract 실패 rc=$?"; exit 1; }
/usr/bin/time -v -o "${LOG%.log}.infer.time" nice -n 10 .venv-bench/bin/python bench/tsfm_zeroshot_infer.py --threads "${THREADS}" ${BENCH_TSFM_MODELS:+--models ${BENCH_TSFM_MODELS}} >> "${LOG}" 2>&1
IRC=$?
log "추론 rc=${IRC} 최대RSS=$(awk -F: '/Maximum resident/{printf "%.0f", $2/1024}' "${LOG%.log}.infer.time")MB"
.venv/bin/python tools/diag_tsfm_zeroshot.py score 2>> "${LOG}" | tee -a "${LOG}"
SRC=${PIPESTATUS[0]}
log "=== 끝 · 추론 rc=${IRC} · score rc=${SRC} ==="
[ "${IRC}" -eq 0 ] && [ "${SRC}" -eq 0 ] || exit 1
