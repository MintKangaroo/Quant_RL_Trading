#!/usr/bin/env bash
# Z2 트랙 shadow — 국장, 모의 체결(docs/design/portfolio-construction.md "Z2 트랙"). 설정은 data/_z2_shadow/config-overrides.yaml.
# 비교 상대는 같은 체결 방식의 KR shadow(data/_shadow, 23:05) — 차이는 K200 필터와 유동시총 가중 둘뿐이다.
# KR shadow 세션이 도는 중이면 끝날 때까지 기다린다(머신을 나누지 않는다).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/shadow-z2-$(date +%Y%m).log"
# 대기 패턴은 forward·hmm 과 같게 넓힌다 — 예전 패턴(`--market KR$`·`--market KR --capital`)은 샌드박스 세션
# (`--market KR --sandbox …`)을 못 봐서 9/28 부터 장부 넷과 겹쳐 돌 수 있었다(2026-09-26 점검).
for _ in $(seq 1 60); do
    pgrep -f "bin/python[^ ]* tools/run_session.py --market KR" > /dev/null || break
    sleep 30
done
{
    echo "=== $(date '+%F %T') Z2 shadow (KR) ==="
    ulimit -v 16777216
    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        .venv/bin/python tools/run_session.py --market KR --sandbox data/_z2_shadow
    RC=$?
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
# 블록 마지막이 echo 면 스크립트 rc 는 늘 0 이다 — 크론이 보는 값은 이것 하나다(2026-09-26 점검).
exit "${RC:-1}"
