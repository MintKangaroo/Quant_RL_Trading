#!/usr/bin/env bash
# BLT shadow — 국장, 모의 체결(docs/design/blt-shadow.md). 얼린 BE2 알파(be2 신호) × P1-b′ 구성(N100 · 완충 500 · R20 · θ1.0),
# 나머지는 현행 모의계좌 규칙 그대로. 기록만 — 판정은 긴 과거 주 시험 + 금고 second 확인(be2-low-turnover-2026-10.md).
# 설정은 data/_blt_shadow/config-overrides.yaml (없으면 config/shadow/blt.config-overrides.yaml 을 복사한다).
#
# be2 점수는 scripts/run_score_be2.sh(크론 22:56, 일일 실행기 직후)가 이미 낸다 — 여기서는 채점하지 않는다. 점수가 없으면 선정이
# fault alpha_stale(rc 3)로 멈춘다(묵은 점수로 조용히 고르지 않는다). 채점 잠금(data/_locks/score-be2.lock)이 잡혀 있으면 풀릴 때까지 기다린다.
# 크론 초안 `15 1 * * 2-6` — 월~금 세션을 다음날 01:15 에(P1-b′ 00:40 · C1 00:55 · AR 01:05 뒤). 다른 KR 세션이 도는 중이면 기다린다.
# 첫 세션은 템플릿의 selector.rebalance_anchor(2026-10-15) — 그 다음날 전에는 아무것도 하지 않는다. 첫 실행만 자본 5.03억(.funded).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-blt-$(date +%Y%m).log"
BOOK=data/_blt_shadow
TEMPLATE=config/shadow/blt.config-overrides.yaml
SOURCE="${TEMPLATE}"
[ -e "${BOOK}/config-overrides.yaml" ] && SOURCE="${BOOK}/config-overrides.yaml"
ANCHOR=$(sed -n 's/^selector\.rebalance_anchor: *"\{0,1\}\([0-9-]*\)"\{0,1\}.*$/\1/p' "${SOURCE}")
# anchor 세션은 다음날 새벽에 돈다 — 오늘 날짜가 anchor 보다 커야 첫 세션이다.
if [ -z "${ANCHOR}" ] || [[ ! "$(date +%F)" > "${ANCHOR}" ]]; then
    echo "$(date "+%F %T") 첫 세션(anchor ${ANCHOR:-없음}) 전 — 건너뜀" >> "${LOG}"
    exit 0
fi
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/(run_session.py --market KR|score_be2.py)" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') BLT shadow (KR) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    mkdir -p data/_locks
    exec 9>data/_locks/score-be2.lock
    flock -w 3600 9 || echo "BE2 채점 잠금 1시간 초과 — 그대로 시도한다(점수가 없으면 선정이 alpha_stale 로 멈춘다)"
    flock -u 9; exec 9>&-
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
