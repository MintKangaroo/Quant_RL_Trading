#!/usr/bin/env bash
# BE2 채점 — 일일 실행기(run_daily.sh KR) **직후**, 실전 그림자(run_shadow.sh KR 23:05)보다 먼저 (2026-10-11).
#
#   scripts/run_score_be2.sh          크론 22:56 — 일일 실행기가 끝날 때까지 기다렸다 채점(마감 23:50)
#   scripts/run_score_be2.sh once     재부팅 복구 — 한 번만 시도, 기다리지 않는다
#
# ## 왜 옮겼나
#
# 10/13 BE2 가 채택되면 실전 설정 selector.weights_override = {be2: 1.0, risk: 1.0} 이 10/15 08:40 모의계좌 세션부터
# 적용된다(tools/apply_switch.py). 채점이 BE2 shadow 러너(00:10) 안에만 있으면 실전 그림자(23:05 data/_shadow)는 그날 be2
# 없이 돌고, 채점이 실패한 날 모의계좌도 be2 없이 돈다 — 지금은 선정이 fault alpha_stale(rc 3)로 멈추지만(selector.md §5 0번)
# 멈추지 않게 하는 것이 이 스크립트의 일이다. run_shadow_be2.sh 는 그대로 score_be2 를 부르고, 이미 있으면 통과한다.
#
# ## 일일 실행기 완료는 시각이 아니라 창고로 안다
#
# ① 일일 실행기 프로세스(tools/run_daily.py --market KR)가 떠 있으면 기다린다.
# ② tools/score_be2.py 가 rc 3 이면(FA 점수 열이 비었다 = 그 세션 일일 실행기 신호가 창고에 아직 없다) 1분 뒤 다시 —
#    22:56 에 일일 실행기가 아직 안 떴어도 이 관문이 잡는다. 마감(23:50)까지 안 되면 rc 3 을 그대로 낸다.
# rc: 0 정상(적었거나 이미 있었다) · 1 세션 없음 · 3 FA 못 만듦 · 4 점수 안 냄(모델·창 미달) — tools/score_be2.py 그대로.
#
# 잠금 data/_locks/score-be2.lock — 크론·재부팅 복구·run_shadow_be2.sh 가 겹치면 **기다린다**(나중 것은 '이미 있다' 로 통과).
# run_shadow.sh KR 도 이 잠금이 풀릴 때까지 기다린다 — 채점 중에 실전 그림자가 be2 없이 돌지 않게.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p data/_locks logs
MODE="${1:-wait}"
DEADLINE="${SCORE_BE2_DEADLINE:-23:50}"
LOG="logs/score-be2-$(date +%Y%m).log"
RC=1
{
    echo "=== $(date '+%F %T') BE2 채점 (KR, ${MODE}) ==="
    exec 9>data/_locks/score-be2.lock
    if ! flock -w 3600 9; then
        echo "잠금을 1시간 못 얻었다 — 다른 채점이 멈춰 있다. rc 5"
        RC=5
    else
        ulimit -v 16777216
        # 마감은 시작 시각 기준 epoch 로 — "HH:MM" 문자열 비교는 자정을 넘기면 풀리지 않는다(독립 검토 10/11).
        END_TS=$(date -d "today ${DEADLINE}" +%s)
        [ "$(date +%s)" -gt "${END_TS}" ] && END_TS=$(( $(date +%s) + 3600 ))   # 마감 뒤 손 실행: 1시간 상한
        while true; do
            # 대괄호: pgrep 이 자기 명령줄을 매칭하지 않게(background-job-hygiene).
            if [ "${MODE}" != "once" ] && pgrep -f "bin/python[^ ]* tools/run_dail[y].py --market KR" > /dev/null; then
                if [ "$(date +%s)" -gt "${END_TS}" ]; then echo "$(date '+%T') 마감 ${DEADLINE} — 일일 실행기가 아직 돈다. rc 3"; RC=3; break; fi
                sleep 30
                continue
            fi
            QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 MALLOC_ARENA_MAX=2 \
                .venv/bin/python tools/score_be2.py --market KR
            RC=$?
            echo "$(date '+%T') score rc=${RC}"
            if [ "${RC}" -ne 3 ] || [ "${MODE}" = "once" ]; then break; fi
            if [ "$(date +%s)" -gt "${END_TS}" ]; then
                echo "마감 ${DEADLINE} — 일일 실행기 신호가 끝내 없다. 실전 세션은 fault alpha_stale(rc 3)로 멈춘다"
                break
            fi
            echo "  일일 실행기 신호가 아직 없다 — 60초 뒤 다시"
            sleep 60
        done
    fi
    echo "rc=${RC}"
} >> "${LOG}" 2>&1
exit "${RC}"
