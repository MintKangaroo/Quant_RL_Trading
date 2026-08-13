"""창고 ↔ 장부. 여기만 store 를 안다.

## 왜 매번 처음부터 다시 접나

장부를 저장해 두고 이어 붙이면 빠르다. 그런데 그 순간 **"지금 장부" 와
"기록으로부터 재구성한 장부" 가 갈라질 수 있다.** 갈라지면 어느 쪽이 맞는지
판정할 방법이 없다 — 회계에서 그건 치명적이다.

체결 기록에서 매번 다시 접으면 장부는 언제나 기록의 함수다. 같은 as_of 로
두 번 접으면 반드시 같은 장부가 나온다(불변식 5, 결정론). 체결이 수만 건이
되면 그때 스냅샷 최적화를 고민하되, **재구성이 정답이라는 성질은 유지한다.**

## 배당 인식

``dividends`` 의 ``valid_from`` 이 배당락일이다. as_of 까지의 배당락은 전부
미수배당으로 계상하고, ``pay_date`` 가 지난 것만 현금으로 옮긴다 — NAV 는
그 이동으로 변하지 않는다 (accounting.md §4).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from lattice.accounting.book import KRW, USD, Book, Side, Trade
from lattice.accounting.rates import Rates

if TYPE_CHECKING:
    from lattice.store import Store

TRADES = "trades"
CAPITAL_FLOWS = "capital_flows"
DIVIDENDS = "dividends"
NAV_DAILY = "nav_daily"
FX = "fx"

#: 계좌 하나짜리 펀드다. 여러 계좌가 생기면 이 값이 인자로 올라간다.
ACCOUNT = "FUND"

#: 환율 entity_id. accounting.md §3 — 평가는 스냅샷 시각의 매매기준율.
FX_USDKRW = "FX:USDKRW"


def fx_rate(store: Store, *, as_of: datetime, lookback: int = 10) -> float:
    """스냅샷 시각의 원달러. **없으면 1.0 으로 때우지 않는다.**

    1.0 으로 때우면 해외 포지션이 1/1350 로 평가되어 NAV 가 통째로 무너지고,
    그 낙폭이 킬스위치를 건다. 없으면 없다고 말하는 편이 낫다.
    """
    frame = store.get(FX, as_of=as_of, entity=FX_USDKRW, lookback=lookback)
    if frame.empty:
        raise LookupError(
            f"{as_of.isoformat()} 시점 {FX_USDKRW} 환율이 없다. "
            "환율 없이 해외분을 평가하면 NAV 가 거짓이 된다"
        )
    latest = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    return float(latest["rate"])


def build_book(
    store: Store, *, as_of: datetime, rates: Rates, lookback: int | None = None
) -> Book:
    """as_of 까지 알 수 있었던 기록만으로 장부를 재구성한다.

    ``lookback`` 은 기본이 None(전체)이다. 회계는 **처음부터 전부** 봐야 한다 —
    창을 자르면 그 이전 매수가 사라져 보유 수량이 틀린다.
    """
    book = Book()

    flows = store.get(CAPITAL_FLOWS, as_of=as_of, lookback=lookback)
    trades = store.get(TRADES, as_of=as_of, lookback=lookback)
    dividends = store.get(DIVIDENDS, as_of=as_of, lookback=lookback)

    # 입출금이 먼저다. 돈이 들어오기 전에 체결이 있을 수는 없다.
    for row in _ordered(flows):
        currency = str(row["currency"])
        rate = 1.0 if currency == KRW else fx_rate(store, as_of=row["valid_from"])
        book = book.with_flow(
            currency=currency, amount=float(row["amount"]), fx_rate=rate
        )

    for row in _ordered(trades):
        book = book.with_trade(
            Trade(
                entity_id=str(row["entity_id"]),
                side=Side(str(row["side"])),
                quantity=float(row["quantity"]),
                price=float(row["price"]),
                currency=str(row["currency"]),
                fee=float(row["fee"]),
                tax=float(row["tax"]),
            )
        )

    for row in _ordered(dividends):
        currency = str(row["currency"])
        held = book.positions.get(str(row["entity_id"]))
        if held is None or held.quantity <= 0:
            # 배당락일에 안 갖고 있었으면 받을 것이 없다.
            continue
        gross = held.quantity * float(row["per_share"])
        net = rates.dividend_net(gross=gross, currency=currency)
        book = book.with_dividend(currency=currency, net_amount=net)

        pay_date = row.get("pay_date")
        if pd.notna(pay_date) and pd.Timestamp(pay_date) <= pd.Timestamp(as_of):
            book = book.with_dividend_paid(currency=currency, net_amount=net)

    return book


def _ordered(frame: pd.DataFrame) -> list[dict[str, object]]:
    """시간 순. 같은 시각이면 ``row_hash`` 로 가른다.

    파일 나열 순서에 결과가 의존하면 리플레이가 깨진다 — 같은 as_of 로 두 번
    돌렸을 때 평균단가가 달라진다.
    """
    if frame.empty:
        return []
    keys = [key for key in ("valid_from", "observed_at", "row_hash") if key in frame.columns]
    return frame.sort_values(keys).to_dict(orient="records")


def daily_inflow(store: Store, *, as_of: datetime, since: datetime) -> float:
    """(since, as_of] 구간의 순입금(원화환산). TWR 이 이걸 뺀다."""
    frame = store.get(CAPITAL_FLOWS, as_of=as_of)
    if frame.empty:
        return 0.0
    window = frame[
        (frame["valid_from"] > pd.Timestamp(since))
        & (frame["valid_from"] <= pd.Timestamp(as_of))
    ]
    total = 0.0
    for row in _ordered(window):
        currency = str(row["currency"])
        rate = 1.0 if currency == KRW else fx_rate(store, as_of=row["valid_from"])
        total += float(row["amount"]) * rate
    return total


def previous_snapshot(store: Store, *, as_of: datetime) -> dict[str, object] | None:
    """직전 회계 스냅샷. 없으면 None — 첫날이다."""
    frame = store.get(NAV_DAILY, as_of=as_of, entity=ACCOUNT)
    if frame.empty:
        return None
    return _ordered(frame)[-1]
