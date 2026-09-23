"""Z2 트랙 주간 비교 — docs/design/portfolio-construction.md "Z2 트랙". 시행이 아니다(판정 기준 없음, 기록만).

    .venv/bin/python tools/compare_z2_track.py

Z2 shadow(data/_z2_shadow, 국장만, 모의 체결)와 모의계좌(data/_paper, 국장만, 실제 모의 체결)를 **Z2 첫 세션부터 같은 창**으로 나란히 놓는다.
둘 다 국장 원화 장부라 TWR(nav_daily.index_value)을 그대로 비교할 수 있다 — 기존 KR shadow(data/_shadow)는 미장 달러 슬리브가 섞여
있어 같은 줄에 못 놓는다. 체결 방식이 다르다는 차이(시뮬 vs 실제 모의 체결)는 그대로 남는다 — 비교표 아래에 적는다.
KODEX200(069500)도 같은 창으로 적는다(판정 벤치마크와 같은 수집).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

BOOKS = {"Z2 shadow": "data/_z2_shadow", "모의계좌": "data/_paper"}


def book_series(sandbox: str, now) -> pd.Series:
    store = Store(root=overlay.build(root=Path(sandbox), source=build_store(None).root, writable=JOURNAL).root)
    nav = store.get("nav_daily", as_of=now, lookback=120, columns=["valid_from", "index_value", "nav"])
    if nav.empty:
        return pd.Series(dtype=float)
    nav["day"] = pd.to_datetime(nav["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return nav.sort_values("valid_from").groupby("day")["index_value"].last()


def etf_series(now) -> pd.Series:
    # 판정 도구(verify_exit_criterion)와 같은 자리 — ETF 가격은 indices 표에 BENCHMARK_ETF 로 들어 있다.
    prices = build_store(None).get("indices", as_of=now, lookback=120, entity=BENCHMARK_ETF, columns=["valid_from", "close"])
    if prices.empty:
        return pd.Series(dtype=float)
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return prices.sort_values("valid_from").groupby("day")["close"].last()


def stats(s: pd.Series) -> tuple[float, float]:
    if len(s) < 2:
        return float("nan"), float("nan")
    rel = s / s.iloc[0]
    return float(rel.iloc[-1] - 1.0), float((rel / rel.cummax() - 1.0).min())


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    now = LiveClock().now()
    series = {name: book_series(path, now) for name, path in BOOKS.items()}
    series["KODEX200"] = etf_series(now)
    z2 = series["Z2 shadow"]
    if len(z2) < 2:
        print(f"{now:%Y-%m-%d} Z2 shadow 세션 {len(z2)}개 — 아직 비교할 창이 없다")
        return 0
    start = z2.index[0]
    print(f"=== {now.astimezone(ZoneInfo('Asia/Seoul')):%Y-%m-%d %H:%M} Z2 트랙 비교 · 창 {start}~{z2.index[-1]} ({len(z2)}세션) ===")
    for name, s in series.items():
        window = s[s.index >= start]
        ret, mdd = stats(window)
        print(f"  {name:10s} 수익 {ret:+.2%} · MDD {mdd:.2%} · 세션 {len(window)}")
    zr, _ = stats(z2)
    pr, _ = stats(series["모의계좌"][series["모의계좌"].index >= start])
    print(f"  Z2 − 모의계좌 {zr - pr:+.2%}p (체결 방식이 다르다: Z2 는 시뮬, 모의계좌는 LS 모의투자 실제 체결 — 체결 비용 차이가 섞인다)")
    print("  기록만 — 판정 기준이 없다. 20세션 뒤 사용자와 모의계좌 전환을 논의한다(portfolio-construction.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
