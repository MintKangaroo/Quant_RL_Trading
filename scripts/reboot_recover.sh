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
#
# ## 빠졌다고 다 따라잡지는 않는다
#
# 세션을 돌리는 것은 창고에 쓰는 일이고, 창고는 append-only 라 **먼저 쓴 쪽이
# 이긴다**(불변식 4). 그래서 "돌릴 수 있나" 가 아니라 **"지금 돌리면 정규
# 크론이 낼 답과 같은 답이 나오나"** 를 묻는다(`--gate`). 아니면 안 돌리고
# 미룬다 — 미룬 것은 `logs/recovery-deferrals.log` 에 남고 다음 복구가
# `--follow-up` 으로 채워졌는지 다시 묻는다.
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

# -- 4b. 미장 수집도 따라잡는다 (2026-09-20 추가) -----------------------------
#
# 위 머리말은 "미장 시세 수집은 안 한다 — 2시간 51분짜리라 재부팅 직후에 밟을 경로가 아니다"
# 였다. 그 판단이 9/18 에 값을 치렀다: 재부팅으로 수집이 5,600/6,602 에서 끊겼고, 12:00
# run_daily·12:20 shadow 가 낡은 시세로 돌아 품질 게이트에 걸려 주문 0 으로 끝났다.
#
# 그래서 **창을 좁혀서** 다시 넣는다. 수집기는 (세션, 1,700종목 배치) 매니페스트로 이어받기가
# 되므로 끊긴 데서 이어 약 25분이면 끝난다 — 처음부터 2시간 51분이 아니다.
#
#   · 화~토(미장 마감 다음 날)이고 08:40~11:40 KST 사이일 때만. 12:00 run_daily 전에 끝나야 한다.
#   · 창고가 오늘 미장 세션을 아직 모를 때만(plan_recovery).
#   · 가용 메모리 4GB 이상일 때만. 국장 장중과 겹쳐도 US appkey 는 별개라 API 는 안 부딪친다.
US_PLAN=$(.venv/bin/python tools/plan_recovery.py --market US 2>&1)
US_DOW=$(date +%u); US_HM=$(date +%H%M)
US_AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if echo "${US_PLAN}" | grep -q "^NEED collect   US " \
   && [ "${US_DOW}" -ge 2 ] && [ "${US_DOW}" -le 6 ] \
   && [ "${US_HM}" -ge 0840 ] && [ "${US_HM}" -le 1140 ] \
   && [ "${US_AVAIL}" -ge 4000 ]; then
    echo "  -- 미장 수집 따라잡기 (가용 ${US_AVAIL}MB · 이어받기)"
    nohup bash scripts/collect_daily.sh US > /dev/null 2>&1 &
    echo "  미장 수집 백그라운드 시작 pid $!"
elif echo "${US_PLAN}" | grep -q "^NEED collect   US "; then
    echo "  -- 미장 수집 필요하지만 창 밖이거나 메모리 부족 (요일 ${US_DOW} · ${US_HM} · ${US_AVAIL}MB)"
fi

# -- 5. 세션은 **입력이 다 들어온 뒤에만** 따라잡는다 ---------------------------
#
# 크론이 22:40 수집 → 22:55 run_daily → 23:05 run_shadow 순인 데는 이유가
# 있다(파이프라인은 데이터 뒤에 돈다). **복구 경로에는 그 규칙이 안 걸려
# 있었다.** 2026-08-20 18:51 복구가 shadow 를 먼저 돌렸고, 그때는 8/19 주문이
# 아직 없어서 그날 결정을 지금 창고 내용으로 지어냈다. 그 체결 26행/562주가
# `backtest-trades-KR-2026-08-19` 로 박혔고, 23:05 정규 실행이 30행/632주를
# 얻었지만 같은 run_id 라 막혔다 — 경고만 남고 회계는 낡은 26행을 쓴다.
#
# 체결은 되돌릴 수 없다(append-only, 불변식 4). 그러니 **낡은 답을 쓰고 고치는
# 대신 아예 안 쓴다.** 못 돌린 세션은 정규 크론이 가져간다.
#
# 관문을 두 번 묻는 이유: 시세는 수집이 고칠 수 있지만, 오늘 Analyst 가
# 살았는지는 `run_daily.sh` 가 돌아야 알 수 있다.
gate() {
    .venv/bin/python tools/plan_recovery.py --market KR --gate "$1" 2>&1
}

if need session; then
    echo "  -- 세션 따라잡기"
    G=$(gate prices); RC=$?
    echo "  ${G}"
    if [ "${RC}" -ne 0 ]; then
        echo "  세션을 건너뛴다 — 정규 크론(22:55·23:05)이 가져간다"
    else
        bash scripts/run_daily.sh KR
        echo "  세션 rc=$?"

        G=$(gate session); RC=$?
        echo "  ${G}"
        if [ "${RC}" -ne 0 ]; then
            echo "  shadow 를 건너뛴다 — 정규 크론(23:05)이 가져간다"
        else
            bash scripts/run_shadow.sh KR
            echo "  shadow rc=$?"
        fi
    fi
fi

# 지난번에 미룬 세션이 그 뒤에 채워졌는지 **같은 관문으로** 다시 묻는다.
# 미룬 것을 로그에만 적어 두면 아무도 안 읽는다.
.venv/bin/python tools/plan_recovery.py --follow-up 2>&1 | sed 's/^/  /'

# 회계는 늘 다시 찍는다. **값이 달라졌을 때만 정정본이 쌓이므로**(불변식 4)
# 헛돌아도 행이 안 는다. 반대로 빠뜨리면 NAV 가 하루 밀린 채로 굳는다.
bash scripts/refresh_accounting.sh KR
echo "  회계 rc=$?"

echo "=== $(date '+%F %T') 복구 끝 ==="
