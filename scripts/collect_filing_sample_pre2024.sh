#!/usr/bin/env bash
# 시행 X(공시 임베딩) 모델 선정·PCA 적합용 — 판정 창 밖(2024-01-01 전) 원문 표본, 유형마다 500건.
# 한 번 돌리는 작업이다. 이미 받은 것은 raw_path 가 차 있어 저절로 빠지므로 다시 돌려도 안전하다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
fail=0
for t in distress dilution earnings contract buyback split; do
    echo "--- $(date '+%F %T') ${t}"
    QUANT_RL_DUCKDB_MEMORY_LIMIT=700MB nice -n 10 .venv/bin/python tools/collect_filing_texts.py \
        --until 2024-01-01 --lookback 1500 --doc-type "${t}" --limit 500 || fail=$((fail + 1))
done
echo "=== $(date '+%F %T') 끝 · 실패 ${fail}"
exit "${fail}"
