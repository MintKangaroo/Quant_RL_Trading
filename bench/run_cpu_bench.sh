#!/usr/bin/env bash
# 알파 탐색 Phase 0 — HF 모델 CPU 벤치 러너(docs/design/alpha-research-program.md §5.2). **크론에 걸지 않는다** — 리드가 손으로 띄운다:
#
#   setsid nohup nice -n 5 bench/run_cpu_bench.sh > /dev/null 2>&1 &          # 기본 묶음(가벼운 것 → 무거운 것)
#   BENCH_MODELS="ttm qwen3_8b" setsid nohup bench/run_cpu_bench.sh > /dev/null 2>&1 &
#
# 일정: 10/9(휴일)~10/10 낮. DART 백필(~10/8 새벽)이 끝난 뒤, TX 임베딩(10/11~12) 전.
# 합성 입력만 쓴다 — 창고·수익·라벨을 읽지 않는다. 실전 .venv 와 무관하다(.venv-bench).
# 조건(모델마다 시작 전에 다시 본다): 평일 08:30~15:45 아님 · 22:30~23:50 아님(미장 세션·저녁 체인) · 가용 ≥ 모델별 문턱 ·
# 다른 무거운 도구 없음 · 연구 잠금 없음. 안 맞으면 그 모델을 건너뛰고 이유를 적는다(조용히 넘어가지 않는다).
# 패턴은 [x] 로 감싸 pgrep 자기매칭을 피한다(메모리 background-job-hygiene).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
PY=.venv-bench/bin/python
[ -x "${PY}" ] || { echo ".venv-bench 없음"; exit 2; }
STAMP=$(date +%Y%m%d-%H%M)
OUT="data/_bench/cpu-${STAMP}"
LOG="logs/bench-cpu-${STAMP}.log"
mkdir -p "${OUT}" logs
THREADS="${BENCH_THREADS:-10}"
export OMP_NUM_THREADS="${THREADS}" MKL_NUM_THREADS="${THREADS}" MALLOC_ARENA_MAX=2 HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false

# 모델 → 필요한 가용 메모리(MB). 가벼운 것부터.
declare -A NEED=(
  [ttm]=2000 [chronos_bolt]=2500 [moirai2]=2500 [chronos2]=3000 [timesfm]=3500
  [tabpfn_v2]=4000 [tabpfn_v25]=4000
  [exaone4_1p2b]=2500 [qwen3_1p7b]=3500 [qwen3_8b]=7000 [chronogpt]=9500
)
MODELS="${BENCH_MODELS:-ttm chronos_bolt moirai2 chronos2 timesfm tabpfn_v2 tabpfn_v25 exaone4_1p2b qwen3_1p7b qwen3_8b chronogpt}"

log() { echo "$(date '+%F %T') $*" | tee -a "${LOG}"; }

ready() {  # $1 = 필요 MB
    local HM DOW AVAIL
    HM=$(date +%H%M); DOW=$(date +%u)
    if [ "${DOW}" -le 5 ] && [ "${HM}" -ge 0830 ] && [ "${HM}" -le 1545 ] && [ -z "${BENCH_IGNORE_MARKET:-}" ]; then
        REASON="평일 장 시간(${HM})"; return 1; fi
    if [ "${HM}" -ge 2230 ] && [ "${HM}" -le 2350 ]; then REASON="저녁 운영 창(${HM})"; return 1; fi
    AVAIL=$(free -m | awk '/^Mem:/{print $7}')
    if [ "${AVAIL}" -lt "$1" ]; then REASON="가용 ${AVAIL}MB < $1MB"; return 1; fi
    if pgrep -f "tools/(freeze_[a-z0-9_]+|trial_[a-z0-9_]+|vault_judg[e]|diag_[a-z0-9_]+)\.py|scripts/(final_round_[A-Za-z0-9_]+|backfill_[a-z_]+|build_text_feature[s])\.sh" > /dev/null; then
        REASON="무거운 작업 도는 중: $(pgrep -af 'tools/(freeze_[a-z0-9_]+|trial_[a-z0-9_]+|vault_judg[e]|diag_[a-z0-9_]+)\.py|scripts/(final_round_[A-Za-z0-9_]+|backfill_[a-z_]+|build_text_feature[s])\.sh' | head -1 | cut -c1-120)"
        return 1; fi
    if [ -f logs/.research-lock ] && [ -n "$(find logs/.research-lock -mmin -120 2>/dev/null)" ]; then
        REASON="연구 잠금($(cat logs/.research-lock))"; return 1; fi
    return 0
}

log "=== CPU 벤치 시작 · 스레드 ${THREADS} · 모델: ${MODELS} · 결과 ${OUT} ==="
FAIL=0; SKIP=0
for m in ${MODELS}; do
    need=${NEED[$m]:-4000}
    if ! ready "${need}"; then log "건너뜀 ${m}: ${REASON}"; SKIP=$((SKIP+1)); continue; fi
    log "--- ${m} (가용 $(free -m | awk '/^Mem:/{print $7}')MB) ---"
    /usr/bin/time -v -o "${OUT}/${m}.time" timeout 3600 "${PY}" bench/cpu_bench.py "${m}" --threads "${THREADS}" --out "${OUT}/${m}.json" \
        > "${OUT}/${m}.stdout" 2> "${OUT}/${m}.stderr"
    rc=$?
    rss=$(awk -F: '/Maximum resident set size/{gsub(/ /,"",$2); printf "%.0f", $2/1024}' "${OUT}/${m}.time" 2>/dev/null)
    wall=$(awk -F': ' '/Elapsed \(wall clock\)/{print $2}' "${OUT}/${m}.time" 2>/dev/null)
    log "${m} rc=${rc} 최대RSS=${rss:-?}MB 벽시계=${wall:-?} $(tail -c 400 "${OUT}/${m}.stdout" 2>/dev/null | tr '\n' ' ')"
    [ "${rc}" -ne 0 ] && FAIL=$((FAIL+1))
done
log "=== 끝 · 실패 ${FAIL} · 건너뜀 ${SKIP} ==="
# 실패·건너뜀이 있으면 rc 로 알린다(조용한 실패 금지 — 메모리 silent-failure-needs-nonzero-rc)
if [ "${FAIL}" -gt 0 ]; then exit 1; fi
if [ "${SKIP}" -gt 0 ]; then exit 3; fi
exit 0
