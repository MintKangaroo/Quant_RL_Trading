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
  "5059:data/_paper"
  "5060:data/_shadow"
)

# 화면 프로세스의 메모리 상한. DuckDB 기본값은 RAM 의 80% 라 다섯 화면이 서로 밀어내고,
# 한 화면이 1.5GB 까지 부풀었다(2026-08-30 실측, 스왑 3.8GB). glibc 아레나도 묶는다.
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-512MB}"
export MALLOC_ARENA_MAX=2

for spec in "${TARGETS[@]}"; do
    port="${spec%%:*}"
    root="${spec#*:}"
    pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "${pid}" ] && kill "${pid}" 2>/dev/null
done
sleep 3

# 화면 프로세스의 메모리 상한. DuckDB 기본값은 RAM 의 80% 라 다섯 화면이 서로 밀어내고,
# 한 화면이 1.5GB 까지 부풀었다(2026-08-30 실측, 스왑 3.8GB). glibc 아레나도 묶는다.
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-512MB}"
export MALLOC_ARENA_MAX=2

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

# **토큰을 미리 데운다.** LS OAuth 발급이 4.8초쯤 걸려서, 안 하면 그 값을
# **처음 화면을 여는 사람이 낸다**(실측 2026-08-19: /api/trading 첫 호출
# 5.2초 → 이후 0.9초). 백그라운드로 던지고 기다리지 않는다 — 실패해도
# 화면은 종가로 그려지므로 여기서 막을 이유가 없다.
# 두 탭을 다 데운다 — **캐시가 다르다.** 트레이딩은 종목(t8407), 마켓은
# 지수·ETF(t1511·g3104)라 한쪽을 데워도 다른 쪽은 여전히 첫 콜을 낸다.
# 화면 프로세스의 메모리 상한. DuckDB 기본값은 RAM 의 80% 라 다섯 화면이 서로 밀어내고,
# 한 화면이 1.5GB 까지 부풀었다(2026-08-30 실측, 스왑 3.8GB). glibc 아레나도 묶는다.
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-512MB}"
export MALLOC_ARENA_MAX=2

for spec in "${TARGETS[@]}"; do
    port="${spec%%:*}"
    curl -s -o /dev/null --max-time 40 "localhost:${port}/api/trading" &
    curl -s -o /dev/null --max-time 40 "localhost:${port}/api/market" &
done

# **올라왔는지 확인한다.** 띄우고 안 보면 죽은 것을 모른다.
fail=0
# 화면 프로세스의 메모리 상한. DuckDB 기본값은 RAM 의 80% 라 다섯 화면이 서로 밀어내고,
# 한 화면이 1.5GB 까지 부풀었다(2026-08-30 실측, 스왑 3.8GB). glibc 아레나도 묶는다.
export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-512MB}"
export MALLOC_ARENA_MAX=2

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
