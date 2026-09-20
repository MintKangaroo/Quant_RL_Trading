#!/usr/bin/env bash
# 확장 패널 — 랭커 모델·신호를 2022-07 까지 늘린다.
# 기본 Analyst signals 는 2021-11 부터 있다(2026-09-20 확인). 시행 L 설정의 최소 학습창이 150세션이라
# 라벨을 아는 마지막 날 2022-06-30 부터가 첫 모델이고, 그 모델은 2022-07-01 부터 쓸 수 있다.
# 모델은 월말마다 하나씩(워크포워드) — 세션 S 는 usable_from <= S 인 모델만 쓴다(누수 금지).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/extend-ranker-$(date +%Y%m%d).log"
{
    echo "=== $(date '+%F %T') 랭커 모델 확장 학습 (2022-06-30 ~ 2024-01-31 월말) ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1200MB nice -n 10 .venv/bin/python -u tools/train_ranker.py \
        --schedule 2022-06-30 2024-01-31 --threads 6
    echo "train rc=$?"
    echo "=== $(date '+%F %T') 랭커 신호 백필 (2022-07-01 ~ 2024-01-31) ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1200MB nice -n 10 .venv/bin/python -u tools/backfill_ranker_signals.py \
        --market KR --start 2022-07-01 --end 2024-01-31
    echo "backfill rc=$?"
} >> "${LOG}" 2>&1
