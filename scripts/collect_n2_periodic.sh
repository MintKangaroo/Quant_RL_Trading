#!/usr/bin/env bash
# N2(Lazy Prices) 진단용 과거 정기보고서 섹션 수집 — 2016-01 ~ 2021-08-16, 창고 밖 data/_diag/n2_periodic/(tools/collect_n2_periodic.py).
# 크론(리드 확인 뒤 등록) 매일 00:50. TX 원문 백필(00:45, 같은 잠금)이 끝날 때까지 최대 1시간 기다리고, TX 로그의 마지막
# '멈춘 이유' 가 done 이 아니면 도구가 그냥 끝난다(rc 0). 일 몫은 TX 백필과 같은 장부·같은 몫(1.5만)을 나눠 쓴다.
# 약 5~6만 콜(목록 ~0.7천 + 원본 ~5만) ≈ 4밤. 다 받으면(로그 '멈춘 이유 done') 크론 줄을 지운다.
# rc: 0 정상(몫·마감·TX 대기 포함) · 1 받을 것이 있는데 0건 · 2 키·설정 · 3 DART 가 막음.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/n2-periodic-$(date +%Y%m).log"
exec 9> data/_locks/dart-text-backfill.lock
if ! flock -w 3600 9; then
    echo "$(date '+%F %T') TX 원문 백필이 1시간 넘게 잠금을 쥐고 있다 — 이번 회차는 건너뛴다" >> "${LOG}"
    exit 0
fi
STOP="${1:-07:30}"
# DART 자정 무렵 점검(status 800 → rc 3) — 30분 간격으로 다시(05:30 까지). TX 스크립트와 같은 규칙.
for TRY in 1 2 3 4 5 6 7 8 9; do
    QUANT_RL_DUCKDB_MEMORY_LIMIT=500MB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 \
        nice -n 10 .venv/bin/python -u tools/collect_n2_periodic.py --stop-at "${STOP}" >> "${LOG}" 2>&1
    RC=$?
    [ "${RC}" -ne 3 ] && break
    [ "$(date +%H%M)" -ge 0530 ] && break
    echo "$(date '+%F %T') rc=3(DART 점검) — 30분 뒤 다시(${TRY}회째)" >> "${LOG}"
    sleep 1800
done
echo "$(date '+%F %T') rc=${RC}" >> "${LOG}"
exit "${RC}"
