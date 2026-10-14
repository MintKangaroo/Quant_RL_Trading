#!/usr/bin/env bash
# T2P 전방 shadow — 국장, 모의 체결(docs/design/t2p-shadow.md). 얼린 T2(업종·β·시총 잔차 정답 GBM) 알파 × P1-b′ 구성(N100 · 완충 500 · R20 · θ1.0).
# 등록 안 함 — 전방 기록(사용자 10/14). 짝 상대는 data/_p1b_shadow(같은 구성, ranker 알파).
# 설정은 data/_t2p_shadow/config-overrides.yaml (없으면 config/shadow/t2p.config-overrides.yaml 을 복사한다).
#
# 순서: ① tools/score_shadow_alpha.py --analyst t2fa — 얼린 T2 로 그 세션 점수를 signals 에(관찰 모드, 운영 가중치 0)
#       ② rc 0 이면 tools/run_session.py --sandbox data/_t2p_shadow
# **아직 켜지지 않는다**: t2fa Analyst 와 얼린 모델(data/models/t2fa/)이 없으면, 또는 anchor 다음날 전이면 로그 한 줄 남기고 rc 0.
# 크론 초안 `25 1 * * 2-6` — BLT(01:15) 뒤. 다른 KR 세션·채점이 도는 중이면 기다린다. 첫 실행만 자본 5.03억(.funded).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-t2p-$(date +%Y%m).log"
BOOK=data/_t2p_shadow
TEMPLATE=config/shadow/t2p.config-overrides.yaml
MODELS=data/models/t2fa
SOURCE="${TEMPLATE}"
[ -e "${BOOK}/config-overrides.yaml" ] && SOURCE="${BOOK}/config-overrides.yaml"
ANCHOR=$(sed -n 's/^selector\.rebalance_anchor: *"\{0,1\}\([0-9-]*\)"\{0,1\}.*$/\1/p' "${SOURCE}")
if [ -z "${ANCHOR}" ] || [[ ! "$(date +%F)" > "${ANCHOR}" ]]; then
    echo "$(date "+%F %T") 첫 세션(anchor ${ANCHOR:-없음}) 전 — 건너뜀" >> "${LOG}"
    exit 0
fi
if [ ! -d "${MODELS}" ]; then
    echo "$(date "+%F %T") 얼린 T2 모델(${MODELS})이 없다 — 아직 켜지지 않았다, 건너뜀" >> "${LOG}"
    exit 0
fi
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/(run_session.py --market KR|score_be2.py|score_shadow_alpha.py)" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') T2P shadow (KR) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2 \
        .venv/bin/python tools/score_shadow_alpha.py --analyst t2fa --market KR
    RC=$?
    echo "score rc=${RC}"
    if [ "${RC}" -eq 0 ]; then
        EXTRA=()
        [ -e "${BOOK}/.funded" ] || EXTRA=(--capital 503000000)
        QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
            .venv/bin/python tools/run_session.py --market KR --sandbox "${BOOK}" "${EXTRA[@]}"
        RC=$?
        echo "rc=${RC}"
        if [ ${#EXTRA[@]} -gt 0 ] && { [ ${RC} -eq 0 ] || [ ${RC} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
    fi
} >> "${LOG}" 2>&1
exit "${RC:-1}"
