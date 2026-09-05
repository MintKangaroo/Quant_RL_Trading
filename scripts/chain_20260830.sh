#!/usr/bin/env bash
# 8/30 체인: §7(compare-baselines) 완료 → 미장 IC → 시행 A → 파일럿 게이트. 단계마다 status 로그 한 줄.
# /tmp 는 재부팅에 지워지므로 여기(scripts/)에 둔다. 기동: setsid nohup bash scripts/chain_20260830.sh &
#
# **끝난 단계는 건너뛴다** (2026-08-31). 감시자가 재부팅 뒤 이 체인을 다시 띄우는데,
# 예전에는 그때마다 미장 IC(7시간)를 처음부터 다시 돌렸다. 이 단계들은 결과를 창고에
# 이미 저장한 일회성 측정이라 다시 잴 이유가 없다. 표식은 이 로그의 자기 기록이다.
cd "$(dirname "$0")/.."
S=logs/night-chain-20260829.log
say() { echo "$(date '+%F %T') $*" | tee -a "$S"; }
done_step() { grep -aq "$1" "$S" 2>/dev/null; }

say "체인(재기동) 시작 — §7 완료 대기"
until grep -q "^rc=" logs/compare-baselines-20260830.log 2>/dev/null; do sleep 60; done

if done_step "미장 IC 끝"; then
  say "미장 IC — 이미 끝났다(건너뜀)"
else
  say "§7 끝 ($(grep '^rc=' logs/compare-baselines-20260830.log | tail -1)) — 미장 IC 시작"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=2GB nice -n 5 .venv/bin/python -u tools/measure_ic.py --market US --sessions 300 --save > logs/ic-us-20260829.log 2>&1
  say "미장 IC 끝 rc=$?"
fi

if done_step "시행 A 끝"; then
  say "시행 A — 이미 끝났다(건너뜀)"
else
  say "시행 A 시작"
  QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB .venv/bin/python -u tools/trial_analyst_features.py --trial A --save > logs/trial-a-20260829.log 2>&1
  say "시행 A 끝 rc=$?"
fi

# **이미 도는 3회차 체인이 있으면 붙지 않는다.** 감시자가 이 체인을 되살렸는데
# 앞판이 띄운 chain_r7_full 이 고아로 살아 있으면, 확인 없이 부르는 순간 학습이
# 두 개가 되어 9.7GB 머신이 스왑 지옥에 빠진다 (2026-08-31 에 실제로 겪었다).
if pgrep -f "chain_r7_ful[l]" > /dev/null; then
  say "3회차 체인이 이미 돌고 있다 — 붙지 않는다. 이 판은 여기서 끝낸다"
  exit 0
fi

say "3회차 완주 체인 시작(파일럿→학습→판정→모의계좌)"
bash scripts/chain_r7_full.sh
say "3회차 체인 끝 rc=$? (0 = 모의계좌 투입까지 완료 / 2 = 관문 불합격)"
