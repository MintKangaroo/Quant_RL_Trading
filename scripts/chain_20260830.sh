#!/usr/bin/env bash
# 8/30 체인: §7(compare-baselines) 완료 → 미장 IC → 시행 A → 파일럿 게이트. 단계마다 status 로그 한 줄.
# /tmp 는 재부팅에 지워지므로 여기(scripts/)에 둔다. 기동: setsid nohup bash scripts/chain_20260830.sh &
cd "$(dirname "$0")/.."
S=logs/night-chain-20260829.log
say() { echo "$(date '+%F %T') $*" | tee -a "$S"; }
say "체인(재기동) 시작 — §7 완료 대기"
until grep -q "^rc=" logs/compare-baselines-20260829.log 2>/dev/null; do sleep 60; done
say "§7 끝 ($(grep '^rc=' logs/compare-baselines-20260829.log | tail -1)) — 미장 IC 시작"
QUANT_RL_DUCKDB_MEMORY_LIMIT=2GB nice -n 5 .venv/bin/python -u tools/measure_ic.py --market US --sessions 300 --save > logs/ic-us-20260829.log 2>&1
say "미장 IC 끝 rc=$? — 시행 A 시작"
QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/trial_analyst_features.py --trial A --save > logs/trial-a-20260829.log 2>&1
say "시행 A 끝 rc=$? — 3회차 완주 체인 시작(파일럿→학습→판정→모의계좌)"
bash scripts/chain_r7_full.sh
say "3회차 체인 끝 rc=$? (0 = 모의계좌 투입까지 완료 / 2 = 관문 불합격)"
