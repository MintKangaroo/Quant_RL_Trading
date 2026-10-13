#!/usr/bin/env bash
# C1 shadow — 국장, 모의 체결(docs/design/c1-ar-shadow.md · 시행 C1G docs/protocols/c1-gbm-fa-second-2026-10.md).
# 얼린 C1(GBM · FA 76열, BE2 사이드카 안 GBM 시드 0~4 — 금고 early 판정과 같은 파일)으로 매일 채점해, 현행 모의계좌와 같은 규칙
# (상위 24·완충 72·C10·리스크 패리티·V6)에 알파만 바꿔 끼운 장부. 기록만 — 판정은 금고 second 창(11/23), 실전 반영은 사용자 재승인.
# 설정은 data/_c1_shadow/config-overrides.yaml (없으면 config/shadow/c1.config-overrides.yaml 을 복사한다).
#
# 순서: ① tools/score_shadow_alpha.py --analyst c1fa — 그 세션 FA 가 없으면 먼저 적고(score_be2 와 같은 함수), c1fa 점수를 signals 에
#          (관찰 모드, 운영 가중치 0). FA 를 쓸 수 있으므로 BE2 채점과 같은 잠금(data/_locks/score-be2.lock)을 잡는다.
#       ② tools/run_session.py --sandbox data/_c1_shadow — ①이 rc 0 일 때만.
# 다른 KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다). 첫 실행만 자본 5.03억(.funded).
# 크론 줄은 리드가 건다(제안: `55 0 * * 2-6` — 월~금 세션을 다음날 00:55 에, 00:05~00:45 shadow 들 뒤).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-c1-$(date +%Y%m).log"
BOOK=data/_c1_shadow
TEMPLATE=config/shadow/c1.config-overrides.yaml
# 대기 패턴은 be2·hmm·z2 와 같다 — 샌드박스 세션(`--market KR --sandbox …`)까지 본다.
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
RC=0
{
    echo "=== $(date '+%F %T') C1 shadow (KR) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    ulimit -v 16777216
    mkdir -p data/_locks
    exec 9>data/_locks/score-be2.lock
    flock -w 3600 9 || echo "BE2 채점 잠금 1시간 초과 — 그대로 시도한다(같은 세션을 두 번 안 쓴다)"
    # ① FA(없으면)·c1fa 점수 — 실전 창고에 쓴다(fa_features·signals). 스레드는 둘.
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2 \
        .venv/bin/python tools/score_shadow_alpha.py --analyst c1fa --market KR
    RC=$?
    flock -u 9; exec 9>&-
    echo "score rc=${RC}"
    if [ "${RC}" -ne 0 ]; then
        echo "c1fa 점수가 없다(rc ${RC}) — 오늘 shadow 세션을 돌리지 않는다. 사유는 위 줄. 고친 뒤 이 스크립트를 다시 돌리면 이어 간다."
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
# 블록 마지막이 echo 면 스크립트 rc 는 늘 0 이다 — 크론이 보는 값은 이것 하나다(2026-09-26 점검).
exit "${RC:-1}"
