#!/usr/bin/env bash
# 미장 과거 Analyst 점수 — 확장 패널의 미장 몫. **새 API 호출 없다**(창고 원천으로 채점만 한다).
#
# 구간 2022-07 ~ 2024-06: 기존 작업 디렉터리(data/ic-history-us)가 2024-06-11 부터 508세션을 이미 들고
# 있고, 등록된 판정 시작은 2022-07 이다(시행 Z·AA·AB).
#
# **기존 디렉터리를 늘리지 않는다.** 점수 블록 파일 이름이 달력에서의 위치 번호라, 구간을 앞으로 늘리면
# 같은 이름이 다른 세션을 가리킨다(조용한 오염). 그래서 별도 디렉터리에 굽고 읽을 때 이어 붙인다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/us-scores-early-$(date +%Y%m%d).log"
{
    echo "=== $(date '+%F %T') 미장 과거 점수 (2022-07 ~ 2024-06) ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 10 .venv/bin/python -u tools/backfill_ic_history.py \
        --market US --start 2022-07 --end 2024-06 \
        --work data/ic-history-us-early \
        --analyst chart event flow_us fundamental regime risk
    echo "rc=$?"
} >> "${LOG}" 2>&1
