#!/usr/bin/env bash
# 국장 공매도 백필 — pykrx 경로. **사용자가 2026-09-01 에 명시적으로 허가했다.**
#
# 이 레포는 원래 "pykrx 는 약관상 자동화 수집이 금지" 라는 이유로 이 경로를 안 썼다
# (collectors/panels.py OPENAPI_PANELS 주석). 정식 KRX Open API 에는 공매도 경로가
# 없어서(/sto/*·/idx/* 뿐) 대안이 없었고, 그래서 shorting 표가 한 번도 안 찼다.
# 위험을 아는 상태에서 소유자가 택했으므로 그 결정을 여기 적어 남긴다.
#
# **부하를 최소로 둔다** — 하루 한 번, 최근 며칠만. 과거 백필은 이 스크립트가 아니라
# 사람이 tools/backfill.py 를 직접 부른다(한 번만 하면 되는 일이다).
set -uo pipefail
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
SESSIONS="${1:-5}"
LOG="logs/collect-$(date +%Y%m).log"
RC=0
{
    echo "=== $(date '+%F %T') shorting(KR) 최근 ${SESSIONS}세션 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=900MB .venv/bin/python tools/backfill.py \
        --market KR --table shorting --sessions "${SESSIONS}"
    RC=$?
    echo "  공매도 rc=${RC}"
} >>"${LOG}" 2>&1
exit "${RC}"
