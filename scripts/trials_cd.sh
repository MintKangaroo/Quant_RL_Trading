#!/usr/bin/env bash
# 시행 D(내부자 순매수) → C(PEAD). **직렬로** 돈다 — 둘 다 GB 급이라 겹치면
# 9.7GB 머신에서 서로 밀어낸다(training-shares-no-machine). 3회차 파일럿이
# 불합격해 머신이 비었고, 다음 레버는 재료 쪽이므로 이 둘이 우선순위다.
set -uo pipefail
cd "$(dirname "$0")/.."
L=logs/trials-cd-20260901.log
for t in D C; do
  echo "=== $(date '+%F %T') 시행 ${t} 시작 ===" >> "$L"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1500MB nice -n 5 .venv/bin/python -u \
    tools/trial_new_sources.py --trial "${t}" --save >> "$L" 2>&1
  echo "시행 ${t} rc=$?" >> "$L"
done
echo "=== 시행 C·D 끝 ===" >> "$L"
