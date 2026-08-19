#!/usr/bin/env bash
# 대시보드 전부 재기동. **하나만 올리고 끝내지 않는다.**
#
# Flask 개발 서버는 리로더가 없어 코드를 고쳐도 안 읽는다. 그런데 이 기계에는
# 화면이 여럿 떠 있다 — 실전 창고(5057·5073)와 shadow(5059), 그리고 휴대폰용
# (5058). 고칠 때마다 손으로 하나씩 올리다 보면 **실전 쪽을 빠뜨린다.**
#
# 실측 2026-08-19: 반나절 동안 5058·5059 만 올리고 5057(실전)은 낡은 코드로
# 두고 있었다. 데모에서만 고쳐지고 실전은 안 고쳐지는 상태다.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
mkdir -p logs

# "포트:창고" — 창고가 `-` 면 기본(실전).
TARGETS=(
  "5057:-"
  "5058:-"
  "5073:-"
  "5059:data/_shadow"
)

for spec in "${TARGETS[@]}"; do
    port="${spec%%:*}"
    root="${spec#*:}"
    pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "${pid}" ] && kill "${pid}" 2>/dev/null
done
sleep 3

for spec in "${TARGETS[@]}"; do
    port="${spec%%:*}"
    root="${spec#*:}"
    log="logs/dash-${port}.log"
    if [ "${root}" = "-" ]; then
        nohup .venv/bin/python -m flask --app quant_rl_trading.dashboard.app:create_app \
            run --host 0.0.0.0 --port "${port}" >>"${log}" 2>&1 &
    else
        QUANT_RL_DATA_ROOT="$(pwd)/${root}" nohup .venv/bin/python -m flask \
            --app quant_rl_trading.dashboard.app:create_app \
            run --host 0.0.0.0 --port "${port}" >>"${log}" 2>&1 &
    fi
done
sleep 7

# **올라왔는지 확인한다.** 띄우고 안 보면 죽은 것을 모른다.
fail=0
for spec in "${TARGETS[@]}"; do
    port="${spec%%:*}"
    code=$(curl -s -o /dev/null -w "%{http_code}" "localhost:${port}/trading" 2>/dev/null)
    label="${spec#*:}"
    [ "${label}" = "-" ] && label="실전" || label="shadow"
    if [ "${code}" = "200" ]; then
        echo "  ${port} (${label}) ✓"
    else
        echo "  ${port} (${label}) ✗ ${code}"
        fail=1
    fi
done
exit "${fail}"
