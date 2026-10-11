#!/usr/bin/env bash
# 공시 원문 과거 백필 — 시행 TX 재료(2026-10-02 사용자 승인, docs/protocols/tx-filing-text-2026-10.md).
# 크론 매일 00:45. 07:30(KST) 또는 오늘 몫(collectors.dart_text_backfill_daily_cap = 15,000콜)에 닿으면 멈춘다.
# 01:20 정규 원문 수집기가 도는 동안은 도구가 기다린다. 남은 약 9.3만 건이 끝나면(로그 '멈춘 이유 done') 크론 줄을 지운다.
# rc 는 도구의 것을 그대로 낸다: 0 정상(몫·마감 포함) · 1 받을 것이 있는데 0건 · 2 키·설정 · 3 DART 가 막음.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/dart-backfill-$(date +%Y%m).log"
# 겹쳐 뜨지 않는다 — 앞 회차가 아직 돌면 이번 회차는 적고 끝낸다.
exec 9> data/_locks/dart-text-backfill.lock
if ! flock -n 9; then
    echo "$(date '+%F %T') 앞 회차가 아직 돈다 — 이번 회차는 건너뛴다" >> "${LOG}"
    exit 0
fi
# 2020-08~2021-08 목록·정기보고서(prior-list·prior-periodic)는 일단 뺀다(리드 10/2) — documents 시작일이 당겨지면
# 등록된 시행들의 연구 캐시(event 이력)를 다시 구울 때 값이 달라진다. tx_change 첫해가 필요해지면 그때 켠다.
# DART 가 자정 무렵 "시스템 점검"(status 800 → rc 3)을 낸다 — 10/9·10·11 세 밤이 첫 콜에서 접혔다. 30분 간격으로 다시 시도(05:30 까지).
STOP="${1:-07:30}"
for TRY in 1 2 3 4 5 6 7 8 9; do
    QUANT_RL_DUCKDB_MEMORY_LIMIT=600MB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 \
        nice -n 10 .venv/bin/python -u tools/backfill_filing_texts.py --stop-at "${STOP}" --phases events,periodic >> "${LOG}" 2>&1
    RC=$?
    [ "${RC}" -ne 3 ] && break
    [ "$(date +%H%M)" -ge 0530 ] && break
    echo "$(date '+%F %T') rc=3(DART 점검) — 30분 뒤 다시(${TRY}회째)" >> "${LOG}"
    sleep 1800
done
echo "$(date '+%F %T') rc=${RC}" >> "${LOG}"
exit "${RC}"
