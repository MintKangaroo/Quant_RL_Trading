#!/usr/bin/env bash
# 시행 X(공시 텍스트 임베딩) — W 판정 뒤 매일 한 번 부른다. 판정이 이미 있으면 건너뛴다.
# 대조 = 6차 채택 묶음 누적(+ W). **W 가 채택되면 대조에 원피처를 넣는 배선이 이 스크립트에 없다 — 멈추고 사람에게 넘긴다.**
# 선행: 임베딩 적재(scripts/build_text_features.sh, 9/25~30). 기준값은 X 등록 문서(② −0.01 · ④ −0.005).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG=logs/trial-filing-text-X.log
grep -q "^판정:" "${LOG}" 2>/dev/null && exit 0
W=$(grep -h "^판정:" logs/trial-raw-feature-ranker-W.log 2>/dev/null | tail -1)
[ -z "${W}" ] && { echo "$(date '+%F %T') 시행 W 판정 전 — 기다린다" >> "${LOG}"; exit 0; }
if echo "${W}" | grep -q "채택"; then
    echo "$(date '+%F %T') 시행 W 채택 — 대조에 원피처를 넣는 배선이 필요하다. 자동으로 돌리지 않는다(사람 확인)" >> "${LOG}"; exit 0
fi
if pgrep -f "tools/(trial_|measure_ic|train_ranker|diagnose_ic)[a-z_]*\.py" > /dev/null; then
    echo "$(date '+%F %T') 다른 무거운 작업이 도는 중 — 건너뜀" >> "${LOG}"; exit 0
fi
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "${AVAIL}" -lt 5000 ] && { echo "$(date '+%F %T') 가용 ${AVAIL}MB — 건너뜀" >> "${LOG}"; exit 0; }
ADOPTED=""
for g in G1 G2 G3 G4 G5 G6 G7; do
    v=$(QUANT_RL_DUCKDB_MEMORY_LIMIT=400MB .venv/bin/python tools/round6_prior_verdict.py "${g}" 2>/dev/null)
    [ -z "${v}" ] && { echo "$(date '+%F %T') 6차 ${g} 미기록 — 기다린다" >> "${LOG}"; exit 0; }
    [ "${v}" = "채택" ] && ADOPTED="${ADOPTED:+${ADOPTED},}${g}"
done
{
    echo "=== $(date '+%F %T') 시행 X · 대조 누적 [${ADOPTED:-없음}] ==="
    MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1000MB nice -n 5 .venv/bin/python -u tools/trial_ranker_sources.py \
        --group X --adopted "${ADOPTED}" --top-margin 0.01 --other-floor -0.005 --save
    echo "rc=$?"
} >> "${LOG}" 2>&1
