"""지수 간 같은 날 일수익 괴리 점검 — 경고만. docs/design/data-contract.md §3-1.

    .venv/bin/python tools/check_index_divergence.py              # 최근 7일(수집 뒤 collect_daily.sh 가 부른다)
    .venv/bin/python tools/check_index_divergence.py --days 2300  # 과거 전체 훑기

계산은 데이터 품질 화면과 **같은 함수**(`dashboard/services/data_quality.index_divergence`)다 — 화면과 로그가 다른 답을 내지 않게.
매매·국면 판정을 막지 않는다. 종료코드: 0 괴리 없음(모름은 경고 아님) · 3 괴리 있음(사유·날짜·두 값을 출력) ·
2 임계 설정이 창고에 없음. collect_daily.sh 에선 부가 단계라 rc 는 요약 줄에만 남는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.dashboard.services import data_quality as dq  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store  # noqa: E402

ALERT_EXIT, NO_CONFIG_EXIT = 3, 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="되돌아볼 달력일")
    parser.add_argument("--as-of", help="기준 시각 ISO8601(기본 지금)")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else LiveClock().now()
    store = build_store(args.data_root)
    result = dq.index_divergence(store, as_of=as_of, lookback=args.days)
    for pair in result["pairs"]:
        worst = "—" if pair["max_abs_diff"] is None else f"{pair['max_abs_diff']:.2%}p"
        print(f"  {pair['a']} ↔ {pair['b']}: 잰 세션 {pair['measured']} · 모름 {pair['unknown']} · 최대 차 {worst} · 경고 {len(pair['alerts'])}")
    for line in dq.divergence_lines(result):
        print(f"  ⚠️ {line}")
    if result["threshold"] is None:
        return NO_CONFIG_EXIT
    return ALERT_EXIT if result["alert_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
