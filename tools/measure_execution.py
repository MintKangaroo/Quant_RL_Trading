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
INTRADAY = "prices_intraday"


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



def _same_day_benchmarks(store: Store, *, now, days: int) -> pd.DataFrame:
    """분봉 → 종목·일자별 (VWAP, 시가).

    VWAP 은 **거래량 가중 대표가격** Σ(대표가×거래량)/Σ(거래량) 으로 낸다.
    대표가는 (고+저+종)/3 이다. 봉마다의 종가를 그냥 평균내면 거래가 없던 봉이
    있던 봉과 같은 무게를 가져 실제 체결 분포와 어긋난다.

    ``value`` 열을 안 쓰는 이유: **단위가 백만원이다** (2026-09-01 실측 —
    959주 × 26,000원 = 25백만 인데 ``value`` 는 25). 그걸 원 단위로 착각하면
    VWAP 이 백만분의 1 이 되고 실행격차가 99억 bps 로 나온다(실제로 그랬다).
    거래량만 쓰면 그 함정 자체가 없다.
    """
    try:
        bars = store.get(INTRADAY, as_of=now, lookback=days + 2)
    except Exception:
        return pd.DataFrame(columns=["entity_id", "day", "vwap", "day_open"])
    if bars.empty:
        return pd.DataFrame(columns=["entity_id", "day", "vwap", "day_open"])
    bars = bars.copy()
    bars["ts"] = pd.to_datetime(bars["valid_from"])
    bars["day"] = bars["ts"].dt.date
    bars["volume"] = bars["volume"].astype(float)
    typical = (
        bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)
    ) / 3.0
    bars["pv"] = typical * bars["volume"]
    grouped = bars.sort_values("ts").groupby(["entity_id", "day"])
    out = grouped.agg(
        pv=("pv", "sum"),
        traded_volume=("volume", "sum"),
        day_open=("open", "first"),
    ).reset_index()
    out["vwap"] = np.where(
        out["traded_volume"] > 0, out["pv"] / out["traded_volume"], np.nan
    )
    return out[["entity_id", "day", "vwap", "day_open"]]


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

    # **같은 날 안의 벤치마크 — 여기가 집행 품질이다.** 도착가(전 세션 종가) 대비는
    # 그날 시장이 갭한 만큼을 같이 재서, 하락장에서는 가만히 있어도 "잘 샀다" 로
    # 보인다(2026-09-01 실측 -104bps 가 그랬다). 그날의 VWAP 과 비교하면 시장
    # 움직임이 분자·분모에서 함께 상쇄되고 **"같은 날 남들 평균보다 잘 샀나"**만
    # 남는다 — 그것이 집행이 실제로 통제하는 부분이다.
    # 체결 시각 기준 일자 — 분봉 벤치마크와 맞추려면 **실제 체결일**이어야 한다
    # (주문 행은 세션일로 찍히므로 그것과 다르다).
    merged["day"] = pd.to_datetime(merged["observed_at"]).dt.date
    bench = _same_day_benchmarks(store, now=now, days=args.days)
    merged = merged.merge(bench, on=["entity_id", "day"], how="left")
    for col, label in (("vwap", "vs_vwap_bps"), ("day_open", "vs_open_bps")):
        base = merged[col].astype(float)
        merged[label] = np.where(base > 0, (filled - base) / base * 10000.0 * sign, np.nan)
    merged["gross"] = filled * merged["quantity"].astype(float)
    merged["cost_bps"] = (
        (merged["fee"].astype(float) + merged["tax"].astype(float))
        / merged["gross"].replace(0.0, np.nan)
        * 10000.0
    )
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

    print("\n같은 날 벤치마크 대비 — **여기가 집행 품질이다** (시장 움직임이 상쇄된다):")
    for col, name in (("vs_vwap_bps", "당일 VWAP 대비"), ("vs_open_bps", "당일 시가 대비")):
        ok = merged[merged[col].notna()]
        if ok.empty:
            print(f"  {name:<16} 데이터 없음 (분봉 미수집 종목)")
            continue
        w = float((ok[col] * ok["gross"]).sum() / ok["gross"].sum())
        cover = ok["gross"].sum() / total_gross * 100
        verdict = "비싸게 샀다" if w > 0 else "싸게 샀다"
        print(f"  {name:<16}{w:>9.2f} bps  ({verdict}) · 중앙값 {ok[col].median():>7.2f} · 거래대금 커버 {cover:.0f}%")

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

    print("\n일자별 (금액가중 bps · 손해가 양수):")
    def _wavg(g: pd.DataFrame, col: str) -> float:
        ok = g[g[col].notna()]
        return float((ok[col] * ok["gross"]).sum() / ok["gross"].sum()) if len(ok) else float("nan")
    daily = merged.groupby("day").apply(
        lambda g: pd.Series({
            "건수": len(g),
            "도착가대비": _wavg(g, "slip_bps"),
            "VWAP대비": _wavg(g, "vs_vwap_bps"),
            "시가대비": _wavg(g, "vs_open_bps"),
        }),
        include_groups=False,
    )
    print(daily.tail(10).to_string())

    # **판단은 사람이 한다** — 여기서는 크기와 표본만 말한다. 연 환산은 회전율에
    # 달렸고, 며칠짜리 표본으로 연 비용을 말하면 그 자체가 거짓말이 된다.
    days_n = merged["day"].nunique()
    print(f"\n수수료·세금 {w_cost:.2f}bps 는 고정비다 — 집행으로 줄일 수 없다.")
    print(
        f"집행이 실제로 통제하는 것은 'VWAP 대비' 하나뿐이고, 지금 표본은 {days_n}일 "
        f"· 체결 {len(merged):,}건이다."
    )
    if days_n < 20:
        print(
            "  ⚠️  **표본이 작아 판정 불가.** 일자별 표의 부호가 뒤집히는지 보라 —"
            " 뒤집히면 지금 값은 실력이 아니라 그날 시장이다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
