"""스냅샷 대사 — 장부 보유를 **브로커 실계좌**에 맞춘다.

주문별 대사(``reconcile_fills``)는 브로커가 안 돌려주는 오래된 주문의 체결을
회수하지 못한다(4일 지나면 "주문 없음"). 그래서 대사가 며칠 실패하면 장부가
브로커보다 적게 기록돼 굳는다 — 2026-08-31 에 9종목이 어긋났고 계좌는 40%
투자인데 장부는 13% 로 보였다.

이 도구는 **브로커 보유 수량을 진실로 놓고**, 차이만큼 정정 거래를 append 해서
장부 포지션을 계좌와 일치시킨다. 현금과 원가는 build_book 이 그 거래로 자동
유도한다(브로커 평균단가로 매수하므로 원가·현금이 계좌에 수렴한다).

멱등하다: delta = 브로커 − 장부 를 매번 다시 재므로, 한 번 맞추면 다음엔 0 이다.

    python tools/reconcile_snapshot.py --market KR             # 계획만 (dry-run)
    python tools/reconcile_snapshot.py --market KR --apply     # 정정 거래 적재
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_rl_trading.accounting import ledger as L  # noqa: E402
from quant_rl_trading.accounting import nav as NAV  # noqa: E402
from quant_rl_trading.accounting.rates import Rates  # noqa: E402
from quant_rl_trading.accounting.snapshot import last_prices  # noqa: E402
from quant_rl_trading.accounting.book import Side as BookSide  # noqa: E402
from quant_rl_trading.broker import balance as B  # noqa: E402
from quant_rl_trading.broker.fills import _fetch_fill_rows  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.dashboard.services.account import _client  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store import overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

TRADES = "trades"
SOURCE = "snapshot_reconcile"
QTY_EPS = 0.5


def fill_price_from_rows(rows: dict[str, dict], *, code: str, side: str) -> float | None:
    """당일 체결 조회(t0425 행)에서 그 종목·방향의 **체결가중평균**. 체결이 없으면 None.

    정정 수량이 어느 주문에서 빠졌는지는 모르지만, 같은 날 같은 방향 체결의 평균이
    브로커 평균매입단가보다 훨씬 가깝다(2026-09-07 실측: 평균매입단가로 적은 매도 정정
    6건이 실제보다 +6만 원, 이 값으로 적으면 0). ``rows`` 는 주문번호→행이고 행의
    ``expcode``·``medosu``·``cheqty``·``cheprice`` 를 본다(국장 t0425).
    """
    qty = 0.0
    amt = 0.0
    for row in rows.values():
        if str(row.get("expcode", "")).strip() != code:
            continue
        medosu = str(row.get("medosu", ""))
        row_side = "sell" if "매도" in medosu else ("buy" if "매수" in medosu else "")
        if row_side != side:
            continue
        q = float(row.get("cheqty") or 0)
        px = float(row.get("cheprice") or 0)
        if q <= 0 or px <= 0:
            continue
        qty += q
        amt += q * px
    return (amt / qty) if qty > 0 else None


def _currency(market: str) -> str:
    return "USD" if market == "US" else "KRW"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--apply", action="store_true", help="정정 거래를 실제로 적재한다 (없으면 dry-run)")
    args = parser.parse_args(argv)

    load_env()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    now = LiveClock().now()
    market = args.market
    prefix = f"{market}:"

    # 1) 브로커 실보유
    found = B.fetch(_client(store, as_of=now, market=market))
    if found is None or found.unavailable:
        print("브로커 조회 실패/unavailable — 대사 불가", file=sys.stderr)
        return 2
    broker = {
        str(h["entity_id"]): (float(h["quantity"]), float(h["avg_price"]))
        for h in found.holdings
        if str(h["entity_id"]).startswith(prefix) and float(h["quantity"]) > 0
    }

    # 2) 장부 보유
    rates = Rates.from_store(store, as_of=now)
    book = L.build_book(store, as_of=now, rates=rates)
    ledger = {e: p.quantity for e, p in book.positions.items() if e.startswith(prefix)}

    # 3) 차이 → 정정 거래
    currency = _currency(market)
    # 당일 체결 조회 — 정정 단가의 1순위. 실패하면 평균매입단가로 물러난다(그 사실을 적는다).
    fills_today = _fetch_fill_rows(_client(store, as_of=now, market=market), market, as_of=now)
    if isinstance(fills_today, str):
        print(f"체결 조회 실패({fills_today}) — 정정 단가를 평균매입단가로 잡는다", file=sys.stderr)
        fills_today = {}
    rows = []
    print(f"{'종목':12s} {'브로커':>9s} {'장부':>9s} {'정정':>9s} {'단가':>10s}  근거")
    for entity in sorted(set(broker) | set(ledger)):
        b_qty, avg = broker.get(entity, (0.0, 0.0))
        l_qty = ledger.get(entity, 0.0)
        delta = b_qty - l_qty
        if abs(delta) <= QTY_EPS:
            continue
        side = "buy" if delta > 0 else "sell"
        filled = fill_price_from_rows(fills_today, code=entity.split(":", 1)[1], side=side)
        if filled:
            price, basis = filled, "당일 체결가중평균"
        elif avg > 0:
            price, basis = avg, "브로커 평균매입단가(체결 조회에 없음)"
        else:
            price, basis = (last_prices(store, as_of=now, entities=[entity]).get(entity) or 0.0), "마지막 종가"
        if price <= 0:
            print(f"{entity:12s} {b_qty:>9.0f} {l_qty:>9.0f}   단가 없음 — 건너뜀", file=sys.stderr)
            continue
        # 정정도 거래다 — 브로커 현금은 수수료·세금을 뗀 값이므로 장부도 같은 규칙으로 뗀다.
        # 0 으로 두면 장부 현금이 브로커보다 그만큼 많아진다(매도 0.165%).
        fee, tax = rates.costs(side=BookSide(side), gross=abs(delta) * price, currency=currency)
        print(f"{entity:12s} {b_qty:>9.0f} {l_qty:>9.0f} {delta:>+9.0f} {price:>10,.0f}  {side} · {basis}")
        rows.append(
            {
                "entity_id": entity,
                "market": market,
                "side": side,
                "quantity": abs(delta),
                "price": price,
                "currency": currency,
                "fee": float(fee),
                "tax": float(tax),
                "order_id": f"snapshot-recon-{now.date().isoformat()}|{entity}",
                "valid_from": now,
                "observed_at": now,
                "source": SOURCE,
            }
        )

    if not rows:
        print("\n차이 없음 — 장부가 이미 계좌와 일치한다.")
        return 0

    # 4) 적용 뒤 검증 — NAV 가 브로커 순자산에 붙는가
    if not args.apply:
        print(f"\n[dry-run] 정정 거래 {len(rows)}건. 적재하려면 --apply")
        return 0

    run_id = f"{SOURCE}-{market}-{now.isoformat()}"
    if store.ingest_run_recorded(TRADES, run_id):
        print("이미 적재된 run — 중복 방지")
        return 0
    written = int(store.append(TRADES, rows, ingest_run_id=run_id, source=SOURCE))

    book2 = L.build_book(store, as_of=now, rates=Rates.from_store(store, as_of=now))
    fx = L.fx_rate(store, as_of=now)
    positions = {e: p.quantity for e, p in book2.positions.items() if p.quantity > 0}
    prices = last_prices(store, as_of=now, entities=sorted(positions))
    val = NAV.value(book2, prices=prices, fx_rate=fx)
    print(f"\n적재 {written}행.")
    print(f"장부 NAV {val.nav:,.0f} · 브로커 순자산 {found.net_asset:,.0f} · 차이 {val.nav - found.net_asset:+,.0f}")
    # 남은 불일치 확인
    left = 0
    for entity in sorted(set(broker) | set(positions)):
        b_qty = broker.get(entity, (0.0, 0.0))[0]
        l_qty = positions.get(entity, 0.0)
        if abs(b_qty - l_qty) > QTY_EPS:
            left += 1
    print(f"남은 종목 불일치: {left}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
