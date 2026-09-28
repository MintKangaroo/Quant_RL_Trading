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
from quant_rl_trading.store.prices import read_prices  # noqa: E402

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


# --------------------------------------------------------------------------- E1 보상 — 논리 주문 단위 구현 손실(기록용)

#: 증권사까지 가서 끝난 조각 — 계획 수량(분모)에 든다.
SENT_FINAL = frozenset({"filled", "cancelled", "expired", "abandoned"})
#: 증권사에 안 갔거나 거부된 조각 — 집행 품질이 아니라 게이트·예산·휴장의 몫이라 분모에서 뺀다.
NOT_SENT = frozenset({"risk_blocked", "withdrawn", "rejected", "planned", "reserved"})
#: 그 밖(submitting·sent·cancel_unknown·modify_unknown·paper)은 **모름** — 그 주문을 통째로 뺀다(CLAUDE.md 반영률 규칙과 같다).


def order_shortfall(
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    vwap: pd.DataFrame | None = None,
    unknown_fills: set[tuple[object, str]] | frozenset[tuple[object, str]] = frozenset(),
) -> pd.DataFrame:
    """논리 주문(세션·종목) 하나마다 **집행일 시가 대비 구현 손실**. 손해가 양수(bps). 순수 함수.

    - ``orders``: 창고 ``orders`` 행(모든 revision). 조각마다 **마지막** 상태, **첫** 수량·지정가를 쓴다. 시장가(지정가 없음)는 뺀다.
    - ``fills``: ``trades`` 행. ``order_id`` = ``세션|종목|조각[#n]``.
    - ``daily``: entity_id·day(date)·open·close. 집행일 = 세션일 **다음** 거래일(그 종목의 다음 행).
    - ``vwap``: entity_id·day·vwap(분봉, 선택). ``unknown_fills``: (집행일, 종목) — 체결이 주문번호 없이 스냅샷 정정으로만
      들어간 자리. 그 주문의 체결 수량을 모르므로 **모름**으로 뺀다(이중계상 절 참고).

    구현 손실 = Σ q×(p−O)×s + (Q−Σq)×(C−O)×s, bps = ÷(Q×O). O = 집행일 시가(개장 단일가 — 팔이 못 바꾸는 밤사이 갭을 뺀다),
    C = 집행일 종가(미체결 몫의 기회비용, 지평은 당일 마감으로 고정), s = 매수 +1·매도 −1, Q = 증권사까지 간 조각 수량 합.
    """
    columns = ["session_id", "entity_id", "side", "exec_day", "slices", "q_plan", "q_filled", "arrival",
               "prev_close", "fill_price", "notional", "is_bps", "exec_bps", "opp_bps", "vs_prev_close_bps",
               "vs_vwap_bps", "measured", "why"]
    if orders.empty:
        return pd.DataFrame(columns=columns)
    frame = orders.sort_values("revision")
    first = frame.drop_duplicates(["session_id", "entity_id", "slice_seq"], keep="first")
    last = frame.drop_duplicates(["session_id", "entity_id", "slice_seq"], keep="last")
    slices = first[["session_id", "entity_id", "slice_seq", "side", "quantity", "limit_price"]].merge(
        last[["session_id", "entity_id", "slice_seq", "status"]], on=["session_id", "entity_id", "slice_seq"])
    slices = slices[pd.to_numeric(slices["limit_price"], errors="coerce").fillna(0.0) > 0]

    got = fills[pd.to_numeric(fills["quantity"], errors="coerce").fillna(0.0) > 0].copy()
    got["slice_key"] = got["order_id"].astype(str).str.split("#").str[0]
    got["notional_fill"] = got["quantity"].astype(float) * got["price"].astype(float)
    filled = got.groupby("slice_key").agg(q=("quantity", "sum"), n=("notional_fill", "sum"))

    px = daily.copy()
    px["day"] = pd.to_datetime(px["day"]).dt.date
    px = px.sort_values(["entity_id", "day"])
    by_entity = {entity: g.reset_index(drop=True) for entity, g in px.groupby("entity_id")}
    vw = {} if vwap is None or vwap.empty else {
        (str(r.entity_id), r.day): float(r.vwap) for r in vwap.itertuples(index=False) if pd.notna(r.vwap)}

    out = []
    for (session, entity), group in slices.groupby(["session_id", "entity_id"], sort=True):
        session_day = pd.Timestamp(str(session).split("-", 1)[1]).date()
        sign = 1.0 if str(group["side"].iloc[0]).lower() == "buy" else -1.0
        row: dict[str, object] = {"session_id": session, "entity_id": entity, "side": str(group["side"].iloc[0]).lower(),
                                  "slices": len(group), "measured": False, "why": ""}
        statuses = set(group["status"].astype(str))
        unknown = statuses - SENT_FINAL - NOT_SENT
        hist = by_entity.get(entity)
        after = hist[hist["day"] > session_day] if hist is not None else None
        prev = hist[hist["day"] <= session_day] if hist is not None else None
        sent = group[group["status"].isin(SENT_FINAL)]
        if unknown:
            row["why"] = f"상태 모름({','.join(sorted(unknown))})"
        elif sent.empty:
            row["why"] = "증권사에 안 감"
        elif after is None or after.empty or prev is None or prev.empty:
            row["why"] = "집행일 시세 없음"
        else:
            exec_day = after["day"].iloc[0]
            row["exec_day"] = exec_day
            if (exec_day, entity) in unknown_fills:
                row["why"] = "스냅샷 정정 체결(주문 귀속 모름)"
            else:
                arrival, close = float(after["open"].iloc[0]), float(after["close"].iloc[0])
                prev_close = float(prev["close"].iloc[-1])
                keys = [f"{session}|{entity}|{int(seq)}" for seq in sent["slice_seq"]]
                hit = filled.reindex(keys).fillna(0.0)
                q_plan, q_fill, n_fill = float(sent["quantity"].sum()), float(hit["q"].sum()), float(hit["n"].sum())
                if arrival <= 0 or q_plan <= 0 or q_fill > q_plan + 1e-9:
                    row["why"] = "도착가·수량 이상"
                else:
                    base = q_plan * arrival
                    exec_cost = sign * (n_fill - q_fill * arrival)
                    opp_cost = sign * (q_plan - q_fill) * (close - arrival)
                    fill_price = n_fill / q_fill if q_fill > 0 else float("nan")
                    bench = vw.get((entity, exec_day))
                    row.update({
                        "q_plan": q_plan, "q_filled": q_fill, "arrival": arrival, "prev_close": prev_close,
                        "fill_price": fill_price, "notional": base,
                        "is_bps": (exec_cost + opp_cost) / base * 1e4,
                        "exec_bps": exec_cost / base * 1e4, "opp_bps": opp_cost / base * 1e4,
                        "vs_prev_close_bps": (sign * (n_fill - q_fill * prev_close) + sign * (q_plan - q_fill) * (close - prev_close))
                        / (q_plan * prev_close) * 1e4 if prev_close > 0 else float("nan"),
                        "vs_vwap_bps": sign * (fill_price - bench) / bench * 1e4 if bench and q_fill > 0 else float("nan"),
                        "measured": True,
                    })
        out.append(row)
    return pd.DataFrame(out).reindex(columns=columns)


def required_orders_per_arm(sigma_bps: float, delta_bps: float, *, comparisons: int = 3) -> int:
    """팔 하나 대 C0 의 평균 차 ``delta`` 를 잡는 데 필요한 팔당 주문 수 — 양측 α=0.05/비교수, 검정력 0.8, 정규 근사."""
    from statistics import NormalDist

    z = NormalDist().inv_cdf(1 - 0.05 / comparisons / 2) + NormalDist().inv_cdf(0.8)
    return int(np.ceil(2 * (z * sigma_bps / delta_bps) ** 2))


def _report_shortfall(store: Store, *, now, days: int) -> int:
    orders = store.get(ORDERS, as_of=now, lookback=days)
    trades = store.get(TRADES, as_of=now, lookback=days)
    if orders.empty:
        print("주문이 없다 — 잴 것이 없다.", file=sys.stderr)
        return 2
    prices = read_prices(store, as_of=now, lookback=days + 10, columns=["open", "close"]).copy()
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    daily = prices.drop_duplicates(["entity_id", "day"], keep="last")[["entity_id", "day", "open", "close"]]
    recon = trades[trades.get("source", pd.Series(dtype=str)).astype(str) == "snapshot_reconcile"]
    unknown = {(pd.Timestamp(v).tz_convert("Asia/Seoul").date(), str(e))
               for v, e in zip(recon["valid_from"], recon["entity_id"], strict=True)}
    bench = _same_day_benchmarks(store, now=now, days=days)
    table = order_shortfall(orders, trades, daily, vwap=bench, unknown_fills=unknown)
    ok = table[table["measured"].astype(bool)]
    print(f"=== E1 보상 기록 · 논리 주문 단위 구현 손실 · 최근 {days}일 · 창고 {store.root} ===")
    print(f"논리 주문 {len(table)}건 · 측정 {len(ok)} · 제외 {len(table) - len(ok)}")
    print(table.loc[~table["measured"].astype(bool), "why"].value_counts().to_string())
    if ok.empty:
        return 2
    w = ok["notional"].astype(float)
    sigma = float(ok["is_bps"].astype(float).std())
    print(f"\n집행일 시가 대비(손해 +): 금액가중 {float((ok['is_bps'] * w).sum() / w.sum()):.2f}bps · 중앙값 "
          f"{ok['is_bps'].median():.2f} · 표준편차 {sigma:.2f} · 체결 몫 {float((ok['exec_bps'] * w).sum() / w.sum()):.2f} · "
          f"기회비용 몫 {float((ok['opp_bps'] * w).sum() / w.sum()):.2f}")
    print(f"(참고) 전 세션 종가 대비 {float((ok['vs_prev_close_bps'] * w).sum() / w.sum()):.2f}bps · "
          f"분봉 VWAP 커버 {ok['vs_vwap_bps'].notna().mean() * 100:.0f}% · 체결률 {ok['q_filled'].sum() / ok['q_plan'].sum() * 100:.0f}%")
    sessions = ok["session_id"].nunique()
    print(f"세션 {sessions} · 세션당 측정 주문 {len(ok) / max(sessions, 1):.1f}")
    for delta in (5.0, 10.0, 20.0):
        n = required_orders_per_arm(sigma, delta)
        print(f"  팔 대 C0 차 {delta:>4.0f}bps 를 잡으려면 팔당 {n:,}건 · 4팔 {4 * n:,}건 ≈ {4 * n / max(len(ok) / max(sessions, 1), 1e-9):,.0f}세션")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--shortfall", action="store_true",
                        help="E1 보상 기록 — 논리 주문 단위 집행일 시가 대비 구현 손실과 필요 표본(기존 출력 대신)")
    args = parser.parse_args(argv)

    load_env()
    now = LiveClock().now()
    store = Store(root=Path(args.sandbox))
    if args.shortfall:
        return _report_shortfall(store, now=now, days=args.days)

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
    # **read_prices 를 경유한다** (불변식). raw store.get 은 휴장일 종가 0 을
    # 그대로 주고, 그 0 이 도착가가 되면 실행격차가 통째로 뒤집힌다.
    prices = read_prices(
        store, as_of=now, lookback=args.days + 10, columns=["close"]
    ).copy()
    prices["session_day"] = pd.to_datetime(prices["valid_from"]).dt.date
    # read_prices 는 as_of 해석을 마친 뷰라 observed_at 이 없다 — 세션일 기준으로
    # 한 행만 남기면 된다.
    arrival = (
        prices.drop_duplicates(subset=["entity_id", "session_day"], keep="last")
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
