"""화면 확인용 창고 — `data/_demo` 에 보유·주문·NAV 곡선을 심는다.

    uv run python tools/seed_demo.py
    QUANT_RL_DATA_ROOT=data/_demo uv run python -m flask \\
        --app quant_rl_trading.dashboard.app:create_app run --port 5058

**목업을 화면 코드에 박지 않는 이유가 있다.** 프론트에 가짜 숫자를 넣으면
그 코드가 남고, 언젠가 실전 화면에서도 그 값이 나온다. 여기서 만드는 것은
데이터이지 코드가 아니다 — 화면은 평소처럼 창고를 읽을 뿐이고, 헤더 배지가
창고 경로에서 모드를 유도해 **DEMO 라고 말한다**.

시세·유니버스·신호는 오버레이로 **실전 창고를 그대로 읽는다**. 지어내는 것은
"우리가 무엇을 샀나"(trades·orders)와 그 결과(capital_flows·nav_daily)뿐이고,
평가액은 실제 종가로 회계가 계산한다. 그래서 화면의 손익은 진짜 가격 움직임
이다 — 매수 시점만 우리가 정한 것이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.accounting import ledger as ledger_module  # noqa: E402
from quant_rl_trading.accounting import snapshot as snapshot_module  # noqa: E402
from quant_rl_trading.accounting.rates import Rates  # noqa: E402
from quant_rl_trading.accounting import ledger  # noqa: E402
from quant_rl_trading.backtest.loop import SEOUL  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

#: 이 창고에서만 새로 쓰는 표. 나머지는 실전 창고를 읽는다.
WRITABLE = frozenset({
    "capital_flows", "orders", "trades", "nav_daily", "realized_weights",
})

CAPITAL = 100_000_000.0
#: 보유로 만들 종목. 국장 대형주 — 실제 시세가 창고에 있는 것들이다.
HOLDINGS = {
    "KR:005930": 0.09,   # 삼성전자
    "KR:000660": 0.08,   # SK하이닉스
    "KR:035420": 0.07,   # NAVER
    "KR:005380": 0.06,   # 현대차
    "KR:051910": 0.05,   # LG화학
    "KR:035720": 0.05,   # 카카오
}
FEE_RATE = 0.00015
TAX_RATE = 0.0018
SNAPSHOT_TIME = time(15, 40)


def close_on(store: Store, entity: str, *, as_of: datetime) -> float | None:
    frame = store.get(
        "prices", as_of=as_of, entity=entity, lookback=10, market="KR", columns=["close"]
    )
    if frame.empty:
        return None
    row = frame.sort_values("valid_from").iloc[-1]
    value = float(row["close"])
    return value if value > 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/_demo"))
    parser.add_argument("--sessions", type=int, default=40, help="NAV 곡선 길이(거래일)")
    parser.add_argument("--fresh", action="store_true", help="기존 데모 창고를 비운다")
    args = parser.parse_args(argv)

    load_env()
    source = build_store(None).root
    layer = overlay.build(root=args.root, source=source, writable=WRITABLE)
    if args.fresh:
        layer.clear()
    store = Store(root=args.root)

    # 창고에 실제로 있는 마지막 거래일들. 달력이 아니라 데이터를 따른다 —
    # 수집이 멈춘 날 뒤를 그리면 화면이 창고보다 많이 아는 것이 된다.
    # 시계는 주입으로만 얻는다 (불변식 2). 도구라고 예외를 두면 그 예외가
    # 언젠가 라이브 경로로 새어 들어간다.
    now = LiveClock().now()
    latest = store.get(
        "prices", as_of=now, lookback=90, market="KR",
        entity="KR:005930", columns=["close"],
    )
    if latest.empty:
        print("실전 창고에 시세가 없다. 백필을 먼저 돌린다.", file=sys.stderr)
        return 1
    sessions = sorted({pd.Timestamp(v).date() for v in latest["valid_from"]})
    sessions = [d for d in sessions if d in set(trading_days(Market.KR, sessions[0], sessions[-1]))]
    sessions = sessions[-args.sessions:]
    entry_day, *rest = sessions
    if not rest:
        print("세션이 모자라다.", file=sys.stderr)
        return 1

    entry_at = datetime.combine(entry_day, SNAPSHOT_TIME, tzinfo=SEOUL)
    # 예치금. **`loop.seed_capital` 을 쓰지 않는다** — 그쪽 run id
    # (`backtest-seed-<날짜>`)는 실전 창고 매니페스트와 겹쳐서, 이미 기록된
    # 것으로 판정되면 **조용히 0행을 쓰고 지나간다.** 그러면 매수만 반영돼
    # 현금이 음수가 되고 NAV 가 마이너스로 뜬다 (실제로 그렇게 나왔다).
    store.append(
        "capital_flows",
        [{
            "entity_id": ledger.ACCOUNT,
            "valid_from": entry_at,
            "observed_at": entry_at,
            "source": "demo",
            "currency": "KRW",
            "amount": CAPITAL,
            "kind": "deposit",
        }],
        ingest_run_id=f"demo-capital-{entry_day:%Y%m%d}",
    )

    # 1. 매수 — 첫 세션 종가로 산 것으로 한다. 수량은 목표 비중을 종가로 나눈다.
    orders: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    for entity, weight in HOLDINGS.items():
        price = close_on(store, entity, as_of=entry_at)
        if price is None:
            print(f"  {entity}: 시세 없음 — 건너뛴다")
            continue
        quantity = float(int(CAPITAL * weight / price))
        if quantity <= 0:
            continue
        order_id = f"demo-{entity}-{entry_day:%Y%m%d}"
        common = {
            "entity_id": entity,
            "valid_from": entry_at,
            "observed_at": entry_at,
            "source": "demo",
            "market": "KR",
        }
        orders.append({
            **common,
            # orders 의 자연키는 (entity_id, session_id, slice_seq) 다.
            # order_id 는 trades 쪽 컬럼이라 여기 넣으면 스키마가 거부한다.
            "slice_seq": 0,
            "side": "buy",
            "quantity": quantity,
            "limit_price": price,
            "status": "filled",
            "target_weight": weight,
            "session_id": f"demo-{entry_day:%Y%m%d}",
            "reason": "데모 창고 — 화면 확인용",
        })
        trades.append({
            **common,
            "order_id": order_id,
            "side": "buy",
            "quantity": quantity,
            "price": price,
            "currency": "KRW",
            "fee": round(price * quantity * FEE_RATE),
            "tax": 0.0,  # 매수에는 거래세가 없다
        })
        print(f"  {entity}  {quantity:>6,.0f}주 @ {price:>10,.0f}")

    if not trades:
        print("살 수 있는 종목이 없었다.", file=sys.stderr)
        return 1

    store.append("orders", orders, ingest_run_id=f"demo-orders-{entry_day}")
    store.append("trades", trades, ingest_run_id=f"demo-trades-{entry_day}")

    # 1-2. 최근 리밸런싱 — 주문·체결 표를 채운다. 마지막 세션에 일부를 팔고
    #      일부를 더 산다. **상태를 세 가지로 섞는다**(체결·부분·거부) — 배지가
    #      실제로 어떻게 보이는지 확인하는 것이 이 창고의 목적이다.
    last_day = sessions[-1]
    last_at = datetime.combine(last_day, SNAPSHOT_TIME, tzinfo=SEOUL)
    rebal_orders: list[dict[str, object]] = []
    rebal_trades: list[dict[str, object]] = []
    plan = [
        ("KR:005930", "sell", 0.30, "filled"),
        ("KR:035720", "buy", 0.20, "partial"),
        ("KR:051910", "sell", 0.25, "rejected"),
    ]
    held = {row["entity_id"]: float(row["quantity"]) for row in trades}
    for seq, (entity, side, ratio, status) in enumerate(plan):
        price = close_on(store, entity, as_of=last_at)
        if price is None or entity not in held:
            continue
        quantity = float(int(held[entity] * ratio)) or 1.0
        common = {
            "entity_id": entity,
            "valid_from": last_at,
            "observed_at": last_at,
            "source": "demo",
            "market": "KR",
        }
        rebal_orders.append({
            **common,
            "slice_seq": seq,
            "side": side,
            "quantity": quantity,
            "limit_price": price,
            "status": status,
            "target_weight": HOLDINGS[entity],
            "session_id": f"demo-{last_day:%Y%m%d}",
            "reason": {
                "filled": "리밸런싱 — 목표 비중 초과",
                "partial": "리밸런싱 — 유동성 부족으로 일부만",
                "rejected": "가격제한 — 하한가 이탈",
            }[status],
        })
        if status == "rejected":
            continue
        # 부분 체결이면 절반만 체결된 것으로 남긴다.
        done = quantity if status == "filled" else float(int(quantity / 2)) or 1.0
        rebal_trades.append({
            **common,
            "order_id": f"demo-{last_day:%Y%m%d}|{entity}|{side}",
            "side": side,
            "quantity": done,
            "price": price,
            "currency": "KRW",
            "fee": round(price * done * FEE_RATE),
            "tax": round(price * done * TAX_RATE) if side == "sell" else 0.0,
        })
    if rebal_orders:
        store.append("orders", rebal_orders, ingest_run_id=f"demo-orders-{last_day}")
    if rebal_trades:
        store.append("trades", rebal_trades, ingest_run_id=f"demo-trades-{last_day}")
    print(f"  리밸런싱 {len(rebal_orders)}건 (체결 {len(rebal_trades)}건) @ {last_day}")

    # 2. NAV 곡선 — 세션마다 회계를 돌린다. 평가액은 **실제 종가**로 계산되므로
    #    곡선의 모양은 지어낸 것이 아니라 그 기간 시장이 한 일이다.
    written = 0
    for day in sessions:
        as_of = datetime.combine(day, SNAPSHOT_TIME, tzinfo=SEOUL)
        clock = ReplayClock(as_of)
        try:
            rates = Rates.from_store(store, as_of=as_of)
            book = ledger_module.build_book(store, as_of=as_of, rates=rates)
            snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
            snapshot_module.write(store, clock, snapshot=snapshot)
        except Exception as error:  # 환율 미수집 등 — 그날은 건너뛴다
            print(f"  {day}: 스냅샷 실패 ({type(error).__name__}: {error})")
            continue
        written += 1
        if day == sessions[-1]:
            print(
                f"\n마지막 세션 {day}  NAV {snapshot.valuation.nav:,.0f}"
                f"  지수 {snapshot.index_value:.2f}  낙폭 {snapshot.drawdown:.2%}"
            )

    print(f"\n스냅샷 {written}/{len(sessions)}세션 · 창고 {args.root}")
    print(
        "화면:  QUANT_RL_DATA_ROOT=%s uv run python -m flask "
        "--app quant_rl_trading.dashboard.app:create_app run --port 5058" % args.root
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
