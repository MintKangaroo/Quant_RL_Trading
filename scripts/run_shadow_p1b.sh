#!/usr/bin/env bash
# P1-b′ shadow — 국장, 모의 체결(docs/protocols/p1b-prime-2026-10.md "shadow 장부"). B1′ 구성(N100 · 완충 500 · R20 · 비용 인지 θ1.0)에
# 나머지는 현행 모의계좌 규칙 그대로. 기록만 — 판정은 금고 second 창(11/23)이다. 비교 상대는 N24 shadow(현행 = B0 의 실전형).
# 설정은 data/_p1b_shadow/config-overrides.yaml (없으면 config/shadow/p1b.config-overrides.yaml 을 복사한다).
#
# 크론 `40 0 * * 2-6` — 월~금 세션을 다음날 00:40 에(run_session 이 스냅샷이 지난 마지막 거래일을 잡는다). 22:55 파이프라인이 랭커
# 점수를 만든 뒤라 N24 shadow(23:45)와 같은 정보 시점이다. 다른 KR 세션(샌드박스 포함)·BE2 채점이 도는 중이면 기다린다(머신을 나누지 않는다).
# 첫 실행만 자본 5.03억(.funded).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-p1b-$(date +%Y%m).log"
BOOK=data/_p1b_shadow
TEMPLATE=config/shadow/p1b.config-overrides.yaml
# 첫 세션 2026-10-08(10/9 00:40 실행) — 템플릿의 rebalance_anchor 와 같다. 그 전에 돌면 anchor 전 세션으로 장부가 먼저 선다.
[[ "$(date +%F)" < "2026-10-09" ]] && { echo "$(date "+%F %T") 첫 세션 전 — 건너뜀" >> "${LOG}"; exit 0; }
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/(run_session.py --market KR|score_be2.py)" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') P1-b′ shadow (KR) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    EXTRA=()
    [ -e "${BOOK}/.funded" ] || EXTRA=(--capital 503000000)
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market KR --sandbox "${BOOK}" "${EXTRA[@]}"
    RC=$?
    echo "rc=${RC}"
    if [ ${#EXTRA[@]} -gt 0 ] && { [ ${RC} -eq 0 ] || [ ${RC} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
} >> "${LOG}" 2>&1
# 블록 마지막이 echo 면 스크립트 rc 는 늘 0 이다 — 크론이 보는 값은 이것 하나다(2026-09-26 점검).
exit "${RC:-1}"
