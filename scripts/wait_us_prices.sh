#!/usr/bin/env bash
# 오늘 미장 시세가 창고에 들어왔는지 기다린다. **파이프라인은 데이터 뒤에 돈다.**
#
# 왜: 미장 수집(08:40)은 6,600종목을 종목당 1콜로 받아 2~3시간 걸린다. 재부팅이나 API 지연으로
# 끊기면 run_daily(12:00)·run_shadow(12:20)가 **낡은 시세로 후보를 뽑는다** — 2026-09-18 에
# 그렇게 돌았고 품질 게이트가 막아 주문 0 으로 끝났다(us-sleeve-mostly-cash).
#
# 판단은 로그가 아니라 **창고**로 한다(reboot_recover 와 같은 규칙): `plan_recovery.py --market US`
# 가 "NEED collect US 시세" 를 안 찍으면 준비된 것이다.
#
# 마감(기본 13:30 KST)을 넘기면 기다리지 않고 0 을 돌려준다 — 그 뒤는 품질 게이트가 막는다.
# 세션을 통째로 건너뛰면 그날의 사실이 창고에서 사라지고, 그게 더 나쁘다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

DEADLINE="${US_WAIT_DEADLINE:-13:30}"
INTERVAL="${US_WAIT_INTERVAL:-300}"

# 0 = 준비됨 · 1 = 아직 · 2 = 판정 실패. **판정이 죽으면 준비된 것이 아니다**(2026-09-26 점검) — 예전엔 plan_recovery 가
# 크래시해 출력이 비면 grep 이 못 찾아 "준비됨" 으로 읽었다. 로그가 거짓말을 하며 낡은 시세로 세션이 돌 수 있었다.
ready() {
    local out rc
    out=$(QUANT_RL_DUCKDB_MEMORY_LIMIT=400MB .venv/bin/python tools/plan_recovery.py --market US 2>&1)
    rc=$?
    if [ "${rc}" -ne 0 ]; then
        echo "  $(date '+%T') 시세 판정 실패(rc=${rc}): $(printf '%s' "${out}" | tail -1)"
        return 2
    fi
    printf '%s' "${out}" | grep -q "NEED collect   US 시세" && return 1
    return 0
}

while true; do
    ready
    STATE=$?
    if [ "${STATE}" -eq 0 ]; then
        echo "  $(date '+%T') 미장 시세 준비됨"
        exit 0
    fi
    if [ "$(date +%H:%M)" \> "${DEADLINE}" ]; then
        echo "  $(date '+%T') 마감 ${DEADLINE} 초과 — 기다리지 않고 진행한다(품질 게이트가 막는다)"
        exit 0
    fi
    echo "  $(date '+%T') 미장 시세 대기 — ${INTERVAL}초 뒤 다시 본다"
    sleep "${INTERVAL}"
done
