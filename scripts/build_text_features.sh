#!/usr/bin/env bash
# 시행 X — 공시 임베딩 적재. 원문이 완비된 달만 돈다(도구가 판단한다).
# 판정 창 2025-01~2026-06. 홀드아웃(2026-07~)은 넣지 않는다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/text-features-$(date +%Y%m%d).log"
# 원피처 굽기(diagnose_ic, 시행 W 선행 — 9/26 토 18:07~ 약 16시간)와 겹치면 건너뛴다. 9/25~30 이라 다른 날이 받는다.
if pgrep -f "tools/diagnose_i[c].py" > /dev/null; then
    echo "$(date '+%F %T') 원피처 굽기 중 — 이번 회차는 건너뛴다" >> "${LOG}"
    exit 0
fi
{
    echo "=== $(date '+%F %T') 공시 임베딩 ==="
    QUANT_RL_DUCKDB_MEMORY_LIMIT=800MB nice -n 5 .venv/bin/python -u tools/build_text_features.py \
        --stage embed --market KR --start 2025-01 --end 2026-06
    echo "rc=$?"
} >> "${LOG}" 2>&1
