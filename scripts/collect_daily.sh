#!/usr/bin/env bash
# 일일 수집 — 시세·유니버스·수급·거시지표를 창고에 채운다.
#
#   scripts/collect_daily.sh KR
#
# **이 스크립트가 없어서 창고가 이틀 낡아 있었다.** run_daily.sh 는 점수만
# 내고 수집하지 않는다. 수집은 백필 도구를 사람이 돌릴 때만 들어왔고, 그래서
# 2026-08-12 이후 시세가 멈춘 채 shadow 가 낡은 값으로 돌았다.
#
# 순서가 있다. 수집 → run_daily(16:10) → run_shadow(16:30) 이다. 게이트가
# observed_at <= as_of 로 거르므로 수집이 늦으면 그날 데이터가 아예 안 보인다
# — 버그가 아니라 규칙대로인데, 순서를 틀리면 조용히 0건이 된다.
#
# 최근 3거래일을 다시 받는다. 하루만 받으면 연휴·장애로 하루를 놓쳤을 때
# 영영 빈칸으로 남는다. 이미 받은 세션은 매니페스트가 건너뛴다(멱등).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
SESSIONS="${SESSIONS:-3}"
LOG="logs/collect-$(date +%Y%m).log"

export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-2GB}"
export QUANT_RL_DUCKDB_THREADS="${QUANT_RL_DUCKDB_THREADS:-2}"

{
    echo "=== $(date '+%F %T') market=${MARKET} sessions=${SESSIONS} ==="

    # 1. 시세 + 유니버스. --table 을 안 주면 이 둘이다.
    .venv/bin/python tools/backfill.py --market "${MARKET}" --sessions "${SESSIONS}"
    echo "  시세·유니버스 rc=$?"

    # 2. 수급. **날짜축(KRX)이다** — 종목축(LS)은 991종목을 한 종목씩 받아
    #    하루에 4시간이 든다. 이쪽은 주체별 한 콜씩, 전 종목이 한 번에 온다.
    if [ "${MARKET}" = "KR" ]; then
        .venv/bin/python tools/backfill.py --market KR --table flows --sessions "${SESSIONS}"
        echo "  수급 rc=$?"
    fi

    # 3. 거시지표. 발표 일정과 실측값 — 미장은 21:30 KST 발표라 저녁 실행이
    #    그날 것을 잡는다.
    .venv/bin/python tools/collect_macro.py
    echo "  거시 rc=$?"

    # 4. 환율. **여기 없으면 회계가 멈춘다** — NAV 는 환율 없이 계산을 거부한다
    #    (accounting). 예전에 fx 가 0행이라 회계가 테스트 위에서만 돌던 적이
    #    있고, 1,400행을 백필한 뒤로는 **갱신하는 사람이 없어 7일이 밀려 있었다**
    #    (2026-08-14 발견). 백필 도구를 그대로 쓰되 창을 짧게 준다.
    #    인자 없이 부르면 "30일 전 ~ 어제" 다. 같은 구간 run_id 는 결정론적이라
    #    창고가 중복을 거부하므로 매일 다시 받아도 안전하다.
    .venv/bin/python tools/collect_fx.py
    echo "  환율 rc=$?"
} >>"${LOG}" 2>&1
