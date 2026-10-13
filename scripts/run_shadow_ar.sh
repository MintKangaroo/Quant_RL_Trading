#!/usr/bin/env bash
# AR shadow — 국장, 모의 체결(docs/design/c1-ar-shadow.md · 시행 AR docs/protocols/insider-forward-2026-09.md, 금고 early 두 시장 통과).
# 얼린 AR 처리 GBM(점수 6 + is_us + 내부자 G4·G7, 시드 0·1·2 — 금고 판정과 같은 파일, data/models/ar_insider)으로 매일 채점해,
# 현행 모의계좌와 같은 규칙(상위 24·완충 72·C10·리스크 패리티·V6)에 알파만 바꿔 끼운 장부. 기록만 — 실전 반영은 사용자 재승인.
# 설정은 data/_ar_shadow/config-overrides.yaml (없으면 config/shadow/ar.config-overrides.yaml 을 복사한다).
#
# 순서: ① tools/score_shadow_alpha.py --analyst ar_insider — ar_insider 점수를 signals 에(관찰 모드, 운영 가중치 0). FA 를 쓰지 않는다.
#       ② tools/run_session.py --sandbox data/_ar_shadow — ①이 rc 0 일 때만.
# 모델 설치가 먼저다(한 번): .venv/bin/python tools/install_ar_shadow_models.py --apply — 없으면 ①이 rc 4 로 멈춘다.
# 다른 KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다). 첫 실행만 자본 5.03억(.funded).
# 크론 줄은 리드가 건다(제안: `5 1 * * 2-6` — 월~금 세션을 다음날 01:05 에, C1 shadow 00:55 뒤).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-ar-$(date +%Y%m).log"
BOOK=data/_ar_shadow
TEMPLATE=config/shadow/ar.config-overrides.yaml
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') AR shadow (KR) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2 \
        .venv/bin/python tools/score_shadow_alpha.py --analyst ar_insider --market KR
    RC=$?
    echo "score rc=${RC}"
    if [ "${RC}" -ne 0 ]; then
        echo "ar_insider 점수가 없다(rc ${RC}) — 오늘 shadow 세션을 돌리지 않는다. 사유는 위 줄. 고친 뒤 이 스크립트를 다시 돌리면 이어 간다."
    else
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
