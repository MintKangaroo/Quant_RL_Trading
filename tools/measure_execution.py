"""집행 비용 측정 — 결정가와 체결가 사이에서 새는 돈을 잰다.

RL 3회차 파일럿이 "상위 24 안에서 배분을 바꿔 얻을 알파가 없다"를 확정한 뒤
(검증 반영률 0.95 인데도 균등가중을 못 이겼다), RL 의 역할을 **선정·배분에서
집행으로** 옮기기로 했다 (2026-09-01 사용자 결정). 그 첫 걸음은 모델이 아니라
**측정**이다 — 슬리피지가 애초에 작으면 RL 집행으로 얻을 것도 없고, 그 사실을
46시간 태우기 전에 아는 것이 3회차에서 배운 교훈이다.

재는 것은 **실행격차(implementation shortfall)**: 결정한 순간의 시장가 대비 실제로
얼마에 샀나. 부호는 **손해가 양수**다 — 매수는 비싸게 살수록, 매도는 싸게 팔수록
양수. 그래야 "줄여야 할 값" 하나로 읽힌다.

    slip_bps = (체결가 - 결정가) / 결정가 × 10000 × (매수 +1 / 매도 -1)

**결정가는 ``limit_price`` 가 아니라 그 세션이 본 마지막 종가다.** 지정가는 체결을
보장하려고 기준가 위에 슬리피지 상한(execution.max_slippage)만큼 일부러 얹은 값이라,
그 대비로 재면 "버퍼를 얼마나 남겼나"가 나올 뿐 집행 품질과 무관하다 (2026-09-01 에
실제로 -147bps 라는 무의미한 값이 나왔다). 세션은 전 거래일 종가로 결정하므로 그
종가가 도착가(arrival price)다.

수수료·세금은 **따로 낸다.** 둘을 합치면 "우리가 고칠 수 있는 것"(슬리피지)과
"고정비"(수수료율)가 한 숫자에 섞여, 개선 여지를 못 본다.

    python tools/measure_execution.py --sandbox data/_paper --days 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

ORDERS = "orders"
TRADES = "trades"
PRICES = "prices"


def _decision_prices(orders: pd.DataFrame) -> pd.DataFrame:
    """주문 → (order_id, 지정가, 방향, 세션일).

    ``order_id`` 는 ``session|entity|slice`` 로 trades 와 맞춘다. 같은 주문의
    revision 이 여럿이면 **가장 낮은 revision**(최초 결정)을 쓴다 — 재호가로
    바뀐 지정가를 나중에 참고할 때 최초 값이어야 한다.
    """
    frame = orders[orders["limit_price"].astype(float) > 0].copy()
    if frame.empty:
        return frame
    frame["revision"] = frame.get("revision", 2)
    frame = frame.sort_values("revision").drop_duplicates(
        subset=["session_id", "entity_id", "slice_seq"], keep="first"
    )
    frame["order_id"] = (
        frame["session_id"].astype(str)
        + "|"
        + frame["entity_id"].astype(str)
        + "|"
        + frame["slice_seq"].astype(str)
    )
    # 세션일 = session_id 의 뒷부분(``KR-2026-08-31``). 그날 종가가 도착가다.
    frame["session_day"] = pd.to_datetime(
        frame["session_id"].astype(str).str.rsplit("-", n=3).str[-3:].str.join("-"),
        errors="coerce",
    ).dt.date
    return frame[["order_id", "entity_id", "side", "limit_price", "quantity", "session_day"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)

    load_env()
    now = LiveClock().now()
    store = Store(root=Path(args.sandbox))

    orders = store.get(ORDERS, as_of=now, lookback=args.days)
    trades = store.get(TRADES, as_of=now, lookback=args.days)
    if orders.empty or trades.empty:
        print("주문 또는 체결이 없다 — 잴 것이 없다.", file=sys.stderr)
        return 2

    decisions = _decision_prices(orders)
    if decisions.empty:
        print("지정가 주문이 없다 — 실행격차를 잴 기준이 없다.", file=sys.stderr)
        return 2

    # trades.order_id 는 부분체결 때 `주문id#n` 형태가 될 수 있다(fills.py
    # _trade_order_id). `#` 앞으로 잘라 원 주문에 맞춘다.
    fills = trades.copy()
    fills["order_id"] = fills["order_id"].astype(str).str.split("#").str[0]
    fills = fills[fills["quantity"].astype(float) > 0]

    merged = fills.merge(decisions, on="order_id", how="inner", suffixes=("", "_ord"))
    if merged.empty:
        print("주문과 체결이 order_id 로 안 맞는다 — 대사가 안 된 구간일 수 있다.", file=sys.stderr)
        return 2

    # **도착가를 붙인다** — 세션이 결정할 때 본 마지막 종가. 이것이 실행격차의
    # 기준이다(지정가가 아니다 — 모듈 docstring 참고).
    prices = store.get(PRICES, as_of=now, lookback=args.days + 10)
    prices = prices.copy()
    prices["session_day"] = pd.to_datetime(prices["valid_from"]).dt.date
    arrival = (
        prices.sort_values("observed_at")
        .drop_duplicates(subset=["entity_id", "session_day"], keep="last")
        [["entity_id", "session_day", "close"]]
        .rename(columns={"close": "arrival"})
    )
    merged = merged.merge(arrival, on=["entity_id", "session_day"], how="left")
    missing = int(merged["arrival"].isna().sum())
    merged = merged[merged["arrival"].astype(float) > 0]
    if merged.empty:
        print("도착가(세션일 종가)를 못 붙였다 — 실행격차를 잴 수 없다.", file=sys.stderr)
        return 2

    sign = np.where(merged["side"].astype(str).str.lower() == "buy", 1.0, -1.0)
    decision = merged["arrival"].astype(float)
    filled = merged["price"].astype(float)
    merged["slip_bps"] = (filled - decision) / decision * 10000.0 * sign
    # 지정가 대비는 참고값 — "버퍼를 얼마나 남겼나" 이지 집행 품질이 아니다.
    merged["vs_limit_bps"] = (
        (filled - merged["limit_price"].astype(float))
        / merged["limit_price"].astype(float) * 10000.0 * sign
    )
    merged["gross"] = filled * merged["quantity"].astype(float)
    merged["cost_bps"] = (
        (merged["fee"].astype(float) + merged["tax"].astype(float))
        / merged["gross"].replace(0.0, np.nan)
        * 10000.0
    )
    merged["day"] = pd.to_datetime(merged["observed_at"]).dt.date

    # 금액가중이 진실이다 — 1주짜리 체결과 1억짜리 체결을 같은 무게로 평균내면
    # 실제로 새는 돈과 무관한 숫자가 나온다.
    total_gross = merged["gross"].sum()
    w_slip = float((merged["slip_bps"] * merged["gross"]).sum() / total_gross)
    w_cost = float((merged["cost_bps"].fillna(0.0) * merged["gross"]).sum() / total_gross)

    print(f"=== 집행 비용 · 최근 {args.days}일 · 창고 {store.root} ===")
    print(f"체결 {len(merged):,}건 · 거래대금 {total_gross:,.0f}원"
          + (f" · 도착가 못 찾아 제외 {missing}건" if missing else ""))
    print("기준: 세션일 종가(도착가) 대비. 손해가 양수다.\n")
    print(f"{'구분':<22}{'금액가중':>12}{'중앙값':>10}{'표준편차':>10}")
    print("─" * 54)
    print(f"{'실행격차(슬리피지)':<20}{w_slip:>12.2f}{merged['slip_bps'].median():>10.2f}{merged['slip_bps'].std():>10.2f}  bps")
    print(f"{'수수료·세금':<21}{w_cost:>12.2f}{merged['cost_bps'].median():>10.2f}{merged['cost_bps'].std():>10.2f}  bps")
    print(f"{'합계':<23}{w_slip + w_cost:>12.2f}{'':>10}{'':>10}  bps")
    w_lim = float((merged["vs_limit_bps"] * merged["gross"]).sum() / total_gross)
    print(f"{'(참고) 지정가 대비':<19}{w_lim:>12.2f}{'':>10}{'':>10}  bps ← 남긴 버퍼, 집행 품질 아님")

    by_side = merged.groupby(merged["side"].astype(str).str.lower()).apply(
        lambda g: pd.Series({
            "건수": len(g),
            "거래대금": g["gross"].sum(),
            "슬리피지bps": (g["slip_bps"] * g["gross"]).sum() / g["gross"].sum(),
        }),
        include_groups=False,
    )
    print("\n방향별:")
    print(by_side.to_string())

    print("\n일자별 (금액가중 슬리피지 bps):")
    daily = merged.groupby("day").apply(
        lambda g: (g["slip_bps"] * g["gross"]).sum() / g["gross"].sum(),
        include_groups=False,
    )
    print(daily.tail(10).to_string())

    # **판단은 사람이 한다** — 여기서는 크기만 말한다. 연 환산은 회전율에
    # 달렸으므로 지어내지 않는다.
    annual_hint = w_slip + w_cost
    print(
        f"\n한 번 사고팔 때 왕복 대략 {annual_hint * 2:.0f}bps "
        f"({annual_hint * 2 / 100:.2f}%) 가 비용으로 나간다."
    )
    print(
        "RL 집행이 노릴 수 있는 것은 위 '실행격차' 뿐이다 — 수수료·세금은 고정비다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
