#!/usr/bin/env bash
# DF2 두 번째 금고용 BF1 얼리기 — `tools/freeze_be2.py --arm BF1 --verify` 를 **조건이 될 때 한 번** 돌린다.
# 등록 docs/protocols/df2-2026-10.md(해시 dbac9e2be1db35ef). 결과: data/models/bf1/bf1-v1.0.0-20260630-*(be2 Analyst 폴더와 따로).
#
#   RECOVER_AFTER="2026-10-01 16:45" setsid nohup nice -n 10 scripts/freeze_bf1.sh > /dev/null 2>&1 &
#
# 조건(모두): 가용 ≥ 6,500MB · 시작은 16:45~21:30 에만(약 20분 걸린다 — 22:35 저녁 체인 전에 끝난다. 장 중·15:15~16:40
# 마감 작업·22:35~23:35 운영 창을 다 피한다) · 다른 무거운 도구(freeze_·trial_·vault_judge) 없음 · 연구 잠금 없음 ·
# RECOVER_AFTER 가 있으면 그 시각 뒤의 logs/reboot-recover-*.log 가 '복구 끝' 까지 찍혔을 것. 안 되면 5분 뒤 다시(최대 48시간).
# 패턴은 [x] 로 감싸 이 스크립트·pgrep 자신을 잡지 않는다(자기매칭 — 메모리 background-job-hygiene).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
WAIT_LOG="logs/freeze-bf1-wait.log"
OUT="data/models/bf1"
DONE="${OUT}/bf1-v1.0.0-20260630.json"
export MALLOC_ARENA_MAX=2
export QUANT_RL_LGB_THREADS=6          # 판정 러너(final_round_BF.sh)와 같은 스레드
export OMP_NUM_THREADS=6

ready() {
    local HM DOW AVAIL
    HM=$(date +%H%M); DOW=$(date +%u)
    AVAIL=$(free -m | awk '/^Mem:/{print $7}')
    if [ "${AVAIL}" -lt 6500 ]; then REASON="가용 ${AVAIL}MB < 6500MB"; return 1; fi
    if [ "${HM}" -lt 1645 ] || [ "${HM}" -gt 2130 ]; then REASON="시작 창 16:45~21:30 밖(${HM}, 요일 ${DOW})"; return 1; fi
    if [ -n "${RECOVER_AFTER:-}" ]; then
        local LAST
        LAST=$(ls -t logs/reboot-recover-*.log 2>/dev/null | head -1)
        if [ -z "${LAST}" ] || [ "$(stat -c %Y "${LAST}")" -lt "$(date -d "${RECOVER_AFTER}" +%s)" ] \
           || ! grep -q "복구 끝" "${LAST}"; then
            REASON="${RECOVER_AFTER} 뒤 복구 로그가 아직 끝나지 않았다(${LAST:-없음})"; return 1
        fi
    fi
    if pgrep -f "tools/(freeze_[a-z0-9_]+|trial_[a-z0-9_]+|vault_judg[e])\.py" > /dev/null; then
        REASON="무거운 도구 도는 중: $(pgrep -af 'tools/(freeze_[a-z0-9_]+|trial_[a-z0-9_]+|vault_judg[e])\.py' | head -1 | cut -c1-120)"
        return 1
    fi
    if [ -f logs/.research-lock ] && [ -n "$(find logs/.research-lock -mmin -120 2>/dev/null)" ]; then
        REASON="연구 잠금($(cat logs/.research-lock))"; return 1
    fi
    return 0
}

for _ in $(seq 1 576); do
    if [ -f "${DONE}" ]; then
        echo "$(date '+%F %T') 이미 얼렸다(${DONE}) — 끝" >> "${WAIT_LOG}"
        exit 0
    fi
    REASON=""
    if ready; then
        LOG="logs/freeze-bf1-$(date +%Y%m%d-%H%M).log"
        echo "freeze_bf1(BF1 얼리기) $(date '+%F %T') pid $$" > logs/.research-lock
        echo "$(date '+%F %T') 시작 → ${LOG}" >> "${WAIT_LOG}"
        /usr/bin/time -v nice -n 10 .venv/bin/python -W ignore tools/freeze_be2.py --arm BF1 --verify --out "${OUT}" >> "${LOG}" 2>&1
        RC=$?
        echo "rc=${RC}" >> "${LOG}"
        rm -f logs/.research-lock
        echo "$(date '+%F %T') 끝 rc=${RC}" >> "${WAIT_LOG}"
        exit "${RC}"
    fi
    echo "$(date '+%F %T') 대기 — ${REASON}" >> "${WAIT_LOG}"
    sleep 300
done
echo "$(date '+%F %T') 48시간 동안 조건이 안 됐다 — 포기(사람이 본다)" >> "${WAIT_LOG}"
exit 1
