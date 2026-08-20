#!/usr/bin/env bash
# 재부팅 뒤 자동 복구. 크론의 `@reboot` 에 걸어 둔다.
#
# ## 왜 필요한가
#
# 이 기계는 WSL2 라 **리눅스 VM 의 수명이 Windows 에 묶여 있다.** 실측
# 2026-08-19 23:03:17 에 systemd 가 순서대로 내려갔고(OOM 아님, 크래시 아님)
# 19시간 뒤에 다시 떴다. 그 사이 8/20 크론이 통째로 빠졌다 — 수집·세션·
# shadow 전부. shadow 하루치는 M3 의 10거래일 카운터에서 그냥 날아갔다.
#
# 리눅스 쪽에서 종료를 막을 방법은 없다. **막을 수 없으면 따라잡는다.**
#
# ## 하는 일과 안 하는 일
#
#   항상    대시보드 4포트 · 헬스워처       — 안 떠 있으면 화면이 조용히 낡는다
#   조건부  국장 수집 · 세션 · shadow · 회계 — 창고에 빈 것이 있을 때만
#   안 함   미장 시세 수집                   — 6,647종목 × 2시간 51분. 재부팅
#                                              직후에 밟을 경로가 아니다.
#                                              브리핑이 06:30 에 말해 준다.
#   안 함   실주문                           — `live_order.sh` 는 크론에 없다.
#                                              사람이 낸다. 복구가 대신 내지
#                                              않는다.
#
# ## 무엇이 빠졌는지 어떻게 아는가
#
# 로그를 안 본다. **창고를 본다.** `tools/plan_recovery.py` 가 기대 세션과
# 창고의 마지막 세션을 견줘 `NEED <작업>` 을 찍는다. 로그로 세면 하루 두 번
# 도는 수집(15:55·22:40)을 못 가르고, rc=0 으로 끝난 0행도 못 가른다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs
LOG="logs/reboot-recover-$(date +%Y%m%d-%H%M%S).log"

exec >>"${LOG}" 2>&1
echo "=== $(date '+%F %T') 재부팅 복구 ==="

# -- 1. 네트워크를 기다린다 ---------------------------------------------------
#
# `@reboot` 은 네트워크보다 먼저 뜰 수 있다. 그 상태로 LS 를 부르면 인증부터
# 실패하고, 실패는 "원본이 안 냈다" 와 로그에서 구별이 안 된다.
for i in $(seq 1 30); do
    if curl -s -o /dev/null --max-time 5 https://openapi.ls-sec.co.kr; then
        echo "  네트워크 준비됨 (${i}회째)"
        break
    fi
    sleep 10
done

# -- 2. 항상 올리는 것 --------------------------------------------------------
bash scripts/restart_dashboards.sh
echo "  대시보드 rc=$?"

if ! pgrep -f "health_watch\.sh" >/dev/null; then
    nohup bash scripts/health_watch.sh >>logs/health-watch.log 2>&1 &
    echo "  헬스워처 기동"
fi

# -- 3. 장 중이면 따라잡지 않는다 ---------------------------------------------
#
# 09:00~15:30 에는 정규 수집기가 2분마다 돌고 있다. 그 위에 무거운 백필을
# 얹으면 램과 API 한도를 같이 먹는다. 어차피 15:55 크론이 곧 온다.
# **10# 를 붙인다.** `0930` 은 bash 산술에서 8진수로 읽혀 "잘못된 숫자" 로
# 터진다. 그러면 09~10시 재부팅에서만 복구가 죽는다 — 하필 장 중에.
HHMM=$((10#$(date +%H%M)))
DOW=$(date +%u)
if [ "${DOW}" -le 5 ] && [ "${HHMM}" -ge 900 ] && [ "${HHMM}" -lt 1530 ]; then
    echo "  장 중이라 따라잡기는 건너뛴다 (정규 크론이 곧 돈다)"
    exit 0
fi

# -- 4. 무엇이 비었는지 묻는다 ------------------------------------------------
PLAN=$(.venv/bin/python tools/plan_recovery.py --market KR 2>&1)
echo "${PLAN}"

need() { echo "${PLAN}" | grep -q "^NEED ${1} "; }

if need collect; then
    echo "  -- 국장 수집 따라잡기"
    bash scripts/collect_daily.sh KR
    echo "  수집 rc=$?"
fi

if need session; then
    echo "  -- 세션 따라잡기"
    bash scripts/run_daily.sh KR
    echo "  세션 rc=$?"
    bash scripts/run_shadow.sh KR
    echo "  shadow rc=$?"
fi

# 회계는 늘 다시 찍는다. **값이 달라졌을 때만 정정본이 쌓이므로**(불변식 4)
# 헛돌아도 행이 안 는다. 반대로 빠뜨리면 NAV 가 하루 밀린 채로 굳는다.
bash scripts/refresh_accounting.sh KR
echo "  회계 rc=$?"

echo "=== $(date '+%F %T') 복구 끝 ==="
