#!/usr/bin/env bash
# AR 미장 shadow — 미장, 모의 체결(docs/design/c1-ar-shadow.md '미장' · 시행 AR, 금고 early 두 시장 통과 · 사용자 10/13 "두 시장 다").
# 얼린 AR 처리 GBM(국장 장부와 같은 파일, data/models/ar_insider)으로 미장 세션을 채점해, 현행 미장 shadow 규칙(상위 24 · 완충 48 ·
# 10세션 재조정 · 리스크 패리티 · V6 — 미장 평활 없음 ranker.smoothing_span_us 0)에 알파만 바꿔 끼운 달러 장부. 기록만 — 실전 반영은 사용자 재승인.
# 설정은 data/_ar_us_shadow/config-overrides.yaml (없으면 config/shadow/ar_us.config-overrides.yaml 을 복사한다).
#
# 순서: ⓪ 첫 실행만 — tools/fund_shadow_usd.py 로 달러 $370,000 입금(원화 입금은 달러 현금 0 → 주문 0, G1 9/25 사고)
#       ① 일일 실행기(run_daily US, 12:00)가 끝나고 시세가 다 들어왔는지(wait_us_prices, 기존 미장 shadow 와 같은 판정)
#       ② tools/score_shadow_alpha.py --analyst ar_insider --market US — ar_insider 점수를 signals 에(관찰 모드, 운영 가중치 0)
#       ③ tools/run_session.py --market US --sandbox data/_ar_us_shadow — ②가 rc 0 일 때만
# 기존 미장 shadow(12:20 data/_shadow · 12:45 G1)가 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다).
# 크론 줄은 리드가 건다(제안: `5 13 * * 2-6` — 화~토 13:05, 월~금 미장 세션).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-ar-us-$(date +%Y%m).log"
BOOK=data/_ar_us_shadow
TEMPLATE=config/shadow/ar_us.config-overrides.yaml
for _ in $(seq 1 120); do
    pgrep -f "bin/python[^ ]* tools/run_(session|dail[y]).py --market US" > /dev/null || break
    sleep 30
done
# 시세 미완이면 미룬다 — 기존 미장 shadow 와 같은 판정(rc=6). 반쪽 시세의 신호를 append-only 창고에 박지 않는다.
if ! scripts/wait_us_prices.sh >> "${LOG}" 2>&1; then
    echo "rc=6 미장 시세 미완 — AR 미장 세션 미룸" >> "${LOG}"
    exit 6
fi
RC=0
{
    echo "=== $(date '+%F %T') AR shadow (US) ==="
    mkdir -p "${BOOK}"
    if [ ! -e "${BOOK}/config-overrides.yaml" ]; then
        cp "${TEMPLATE}" "${BOOK}/config-overrides.yaml"
        echo "덮어쓰기 설정 설치: ${TEMPLATE} → ${BOOK}/config-overrides.yaml"
    elif ! cmp -s "${TEMPLATE}" "${BOOK}/config-overrides.yaml"; then
        echo "경고: ${BOOK}/config-overrides.yaml 이 템플릿과 다르다 — 손대지 않고 그대로 쓴다"
    fi
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2 \
        .venv/bin/python tools/score_shadow_alpha.py --analyst ar_insider --market US
    RC=$?
    echo "score rc=${RC}"
    if [ "${RC}" -ne 0 ]; then
        echo "ar_insider 미장 점수가 없다(rc ${RC}) — 오늘 shadow 세션을 돌리지 않는다. 사유는 위 줄."
    else
        if [ ! -e "${BOOK}/.funded" ]; then
            QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python tools/fund_shadow_usd.py --sandbox "${BOOK}" --usd 370000 --apply
            FRC=$?
            echo "달러 입금 rc=${FRC}"
            # 0 = 넣었다 · 3 = 이미 있다(다시 넣지 않는다) — 둘 다 세션을 돌린다
            if [ "${FRC}" -ne 0 ] && [ "${FRC}" -ne 3 ]; then RC=${FRC}; fi
        fi
        if [ "${RC}" -eq 0 ]; then
            QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
                .venv/bin/python tools/run_session.py --market US --sandbox "${BOOK}"
            RC=$?
            echo "rc=${RC}"
            if [ ! -e "${BOOK}/.funded" ] && { [ ${RC} -eq 0 ] || [ ${RC} -eq 2 ]; }; then touch "${BOOK}/.funded"; fi
        fi
    fi
} >> "${LOG}" 2>&1
exit "${RC:-1}"
