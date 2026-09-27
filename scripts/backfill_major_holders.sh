#!/usr/bin/env bash
# 대량보유(5% 룰) 백필 — 전 상장사. 6차 G10 의 입력.
#
#     setsid nohup scripts/backfill_major_holders.sh > logs/backfill-major-holders.log 2>&1 &
#
# 회사당 콜 1건(최근 2년이 한 응답). 상장사 ~4,000 이라 DART 일 한도(20,000) 의 1/5 이다.
# 한도 초과면 도구가 rc=2 로 멈춘다 — 완료 표시(data/_progress/major-holders/done-YYYYMMDD.txt)가
# 남아 있으니 같은 날 다시 부르면 이어받는다.
#
# 끝나면 파티션을 접는다. 접수일이 1,250일쯤 되고 창고가 observed_date 로 파일을 나누므로
# 접지 않으면 51,000행이 파일 만 개가 된다(도구 docstring).
set -u -o pipefail
cd "$(dirname "$0")/.."

export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-4GB}"
PY=.venv/bin/python

echo "== 대량보유 백필 시작 $(date -Is)"
# 한도 초과(rc=2)면 그날은 더 못 한다 — 다시 부르지 않는다.
for attempt in 1 2 3; do
  "$PY" tools/collect_major_holders.py --flush-every 1000 --pause 0.12
  rc=$?
  echo "-- 시도 $attempt rc=$rc $(date -Is)"
  [ "$rc" -eq 0 ] && break
  [ "$rc" -eq 2 ] && { echo "한도 초과 — 오늘은 그만"; break; }
done

echo "== 파티션 접기 $(date -Is)"
"$PY" tools/compact_partitions.py --table major_holders --apply || echo "접기 실패 (데이터는 무결하다)"

echo "== 끝 $(date -Is) rc=$rc"
exit "$rc"
