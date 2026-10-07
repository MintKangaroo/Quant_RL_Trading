#!/usr/bin/env bash
# polyglot-ko-5.8b → GGUF(D:). 사용자 조건(10/7): 가용 6GB 아래면 멈춤 · 평일 09:00~15:30, 22:20~00:40, 00:45~07:30 금지.
# D:(exFAT, WSL drvfs) 쓰기가 약 3MB/s 라 f16 중간본(12GB) 대신 **q8_0 로 바로 변환**한 뒤 Q4_K_M 으로 다시 양자화한다(allow_requantize).
# 시간 창을 벗어나면 그 단계를 죽이고 rc 5 로 끝낸다 — 다음 창에서 다시 띄운다(이미 만든 파일은 건너뜀).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
export HF_HOME=/mnt/d/quant_rl_trading/hf HF_HUB_OFFLINE=1
LLAMA=${LLAMA_CPP_DIR:-/mnt/d/quant_rl_trading/tools/llama.cpp-ad21565}   # llama.cpp ad21565 의 변환 스크립트·conversion·gguf-py 만 복사
OUTD=/mnt/d/quant_rl_trading/gguf; mkdir -p "${OUTD}"
Q8="${OUTD}/polyglot-ko-5.8b-Q8_0.gguf"; Q4="${OUTD}/polyglot-ko-5.8b-Q4_K_M.gguf"
LOG=logs/bench-convert-polyglot-$(date +%Y%m%d).log
PY=.venv-bench/bin/python
log() { echo "$(date '+%F %T') $*" | tee -a "${LOG}"; }
window_ok() {
    local HM DOW; HM=$(date +%H%M); DOW=$(date +%u)
    if [ "${DOW}" -le 5 ] && [ "${HM}" -ge 0900 ] && [ "${HM}" -le 1530 ]; then return 1; fi
    if [ "${HM}" -ge 2220 ] || [ "${HM}" -le 0730 ]; then return 1; fi
    return 0
}
run_guarded() {  # 명령을 띄우고 10초마다 가용·시간 창을 본다
    "$@" >> "${LOG}" 2>&1 &
    local pid=$!
    while kill -0 "${pid}" 2>/dev/null; do
        local A; A=$(free -m | awk '/^Mem:/{print $7}')
        if [ "${A}" -lt 6000 ]; then log "멈춤: 가용 ${A}MB < 6000MB"; kill "${pid}"; wait "${pid}"; return 4; fi
        if ! window_ok; then log "멈춤: 시간 창 밖 $(date +%H%M)"; kill "${pid}"; wait "${pid}"; return 5; fi
        sleep 10
    done
    wait "${pid}"
}
SNAP=$(ls -d ${HF_HOME}/hub/models--EleutherAI--polyglot-ko-5.8b/snapshots/*/ 2>/dev/null | head -1)
[ -n "${SNAP}" ] && [ "$(ls ${SNAP}/*.safetensors 2>/dev/null | wc -l)" -ge 13 ] || { log "원본 미완(safetensors < 13)"; exit 2; }
window_ok || { log "시간 창 밖 — 시작 안 함"; exit 5; }
if [ ! -s "${Q8}" ]; then
    log "q8_0 변환 시작 ${SNAP}"
    PYTHONPATH="${LLAMA}/gguf-py" run_guarded nice -n 10 "${PY}" "${LLAMA}/convert_hf_to_gguf.py" "${SNAP}" --outtype q8_0 --outfile "${Q8}.part" || { rc=$?; rm -f "${Q8}.part"; log "q8_0 실패/중단 rc=${rc}"; exit ${rc}; }
    mv "${Q8}.part" "${Q8}"; log "q8_0 끝 $(du -h "${Q8}" | cut -f1)"
fi
if [ ! -s "${Q4}" ]; then
    log "Q4_K_M 재양자화 시작"
    run_guarded nice -n 10 "${PY}" - "${Q8}" "${Q4}.part" <<'PYEOF' || { rc=$?; rm -f "${Q4}.part"; log "Q4 실패/중단 rc=${rc}"; exit ${rc}; }
import sys, ctypes, llama_cpp
p = llama_cpp.llama_model_quantize_default_params()
p.ftype = llama_cpp.LLAMA_FTYPE_MOSTLY_Q4_K_M
p.allow_requantize = True
p.nthread = 6
rc = llama_cpp.llama_model_quantize(sys.argv[1].encode(), sys.argv[2].encode(), ctypes.byref(p))
sys.exit(rc)
PYEOF
    mv "${Q4}.part" "${Q4}"; log "Q4_K_M 끝 $(du -h "${Q4}" | cut -f1)"
fi
sha256sum "${Q8}" "${Q4}" | tee -a "${LOG}"
log "끝"
