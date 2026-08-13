"""장부 — 체결·배당·입출금을 받아 **상태**로 접는다. 순수 코드.

store 도 Clock 도 모른다. 입력만으로 결정되므로 백테스트와 라이브가 같은
코드를 쓴다 (불변식 5).

## 발생주의(trade-date)

체결 시점에 손익·수수료를 인식한다. 결제일(T+2)이 아니다. 결제일 기준으로
하면 백테스트와 실전이 이틀씩 어긋나고, 그 어긋남이 보상 함수에 그대로
들어간다 (accounting.md §1).

**주문가능금액은 NAV 와 별개다.** 미결제 대금 때문에 NAV 가 있어도 못 사는
경우가 있다. 그건 Executor 가 볼 문제이고 여기서는 다루지 않는다 — 다만
없는 개념인 척하지 않으려고 여기 적어 둔다.

## 왜 평균단가를 들고 있나

세금(해외 양도세)이 취득가를 필요로 한다. 매도 때마다 되짚어 계산하면 정정
체결 하나에 과거 전부가 흔들린다. 이동평균법으로 그때그때 접는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

KRW = "KRW"
USD = "USD"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Trade:
    """체결 하나. 수수료·세금은 체결가에 녹이지 않고 따로 든다.

    녹이면 나중에 "비용이 얼마였나" 를 물을 수 없고, 슬리피지 실측(M3 완료
    기준)과 수수료를 구분할 수 없다.
    """

    entity_id: str
    side: Side
    quantity: float
    price: float
    currency: str = KRW
    fee: float = 0.0
    tax: float = 0.0

    @property
    def gross(self) -> float:
        return self.quantity * self.price

    @property
    def cost(self) -> float:
        return self.fee + self.tax


@dataclass(frozen=True)
class Position:
    quantity: float = 0.0
    #: 이동평균 취득단가(거래통화). 수수료를 포함한다 — 실제로 나간 돈이다.
    avg_cost: float = 0.0
    currency: str = KRW

    @property
    def book_value(self) -> float:
        return self.quantity * self.avg_cost


@dataclass(frozen=True)
class Book:
    """어느 한 시점의 장부. 불변이다 — 적용할 때마다 새 장부가 나온다.

    불변으로 두는 이유는 리플레이 때문이다. 같은 시작 장부에 같은 체결을
    먹이면 반드시 같은 장부가 나와야 하고, 제자리 수정은 그 보장을 깨는
    가장 흔한 방법이다.
    """

    cash: dict[str, float] = field(default_factory=lambda: {KRW: 0.0, USD: 0.0})
    positions: dict[str, Position] = field(default_factory=dict)
    #: 미수배당(세후). 통화별.
    accrued_dividend: dict[str, float] = field(default_factory=lambda: {KRW: 0.0, USD: 0.0})
    #: 미지급 수수료·세금. 체결 즉시 현금에서 빼는 구조라 보통 0 이지만,
    #: 익월 청구되는 비용이 생기면 여기 쌓인다.
    payable: dict[str, float] = field(default_factory=lambda: {KRW: 0.0, USD: 0.0})
    #: 실현손익(거래통화별 누적). 양도세 계산과 귀속 분석이 이걸 쓴다.
    realized_pnl: dict[str, float] = field(default_factory=lambda: {KRW: 0.0, USD: 0.0})
    #: 해외 양도세 충당금. **NAV 에 넣지 않는다** (accounting.md §5).
    tax_provision: float = 0.0
    #: 그날 들어온 순입금. TWR 이 이걸 빼고 수익률을 낸다.
    inflow: float = 0.0

    # -- 적용 ------------------------------------------------------------------

    def with_trade(self, trade: Trade) -> Book:
        """체결 하나를 반영한 새 장부."""
        cash = dict(self.cash)
        positions = dict(self.positions)
        current = positions.get(trade.entity_id, Position(currency=trade.currency))

        if trade.side is Side.BUY:
            cash[trade.currency] = (
                cash.get(trade.currency, 0.0) - trade.gross - trade.cost
            )
            quantity = current.quantity + trade.quantity
            # 취득가에 수수료를 포함한다. 실제로 나간 돈이 취득원가다.
            spent = current.book_value + trade.gross + trade.cost
            positions[trade.entity_id] = Position(
                quantity=quantity,
                avg_cost=spent / quantity if quantity else 0.0,
                currency=trade.currency,
            )
            return replace(self, cash=cash, positions=positions)

        # 매도. 팔 수 없는 수량을 파는 것은 회계가 아니라 버그다.
        if trade.quantity > current.quantity + 1e-9:
            raise ValueError(
                f"{trade.entity_id}: 보유 {current.quantity} 인데 {trade.quantity} 매도"
            )
        cash[trade.currency] = cash.get(trade.currency, 0.0) + trade.gross - trade.cost
        quantity = current.quantity - trade.quantity

        # 실현손익은 **장부에 누적한다.** 매수·매도가 다른 타입을 돌려주게
        # 만들면 호출부가 매번 분기해야 하고, 그 분기는 반드시 한 곳에서 빠진다.
        realized = (trade.price - current.avg_cost) * trade.quantity - trade.cost
        pnl = dict(self.realized_pnl)
        pnl[trade.currency] = pnl.get(trade.currency, 0.0) + realized

        if quantity <= 1e-9:
            positions.pop(trade.entity_id, None)
        else:
            # 평균단가는 매도로 바뀌지 않는다. 남은 주식의 취득원가는 그대로다.
            positions[trade.entity_id] = replace(current, quantity=quantity)

        return replace(self, cash=cash, positions=positions, realized_pnl=pnl)

    def with_dividend(self, *, currency: str, net_amount: float) -> Book:
        """배당락일에 **미수배당(세후)** 으로 계상한다.

        입금일 인식이 아니다. 입금일 기준이면 배당락일에 주가가 떨어진 만큼
        NAV 가 그대로 빠지고, 보상 함수가 그걸 실제 낙폭으로 오인한다
        (accounting.md §4). 배당은 손실이 아니다.
        """
        accrued = dict(self.accrued_dividend)
        accrued[currency] = accrued.get(currency, 0.0) + net_amount
        return replace(self, accrued_dividend=accrued)

    def with_dividend_paid(self, *, currency: str, net_amount: float) -> Book:
        """입금. 미수배당 → 현금. **NAV 는 변하지 않는다.**"""
        accrued = dict(self.accrued_dividend)
        cash = dict(self.cash)
        accrued[currency] = accrued.get(currency, 0.0) - net_amount
        cash[currency] = cash.get(currency, 0.0) + net_amount
        return replace(self, accrued_dividend=accrued, cash=cash)

    def with_flow(self, *, currency: str, amount: float, fx_rate: float) -> Book:
        """입출금. 기준통화 환산액을 ``inflow`` 에 누적한다.

        TWR 이 이 값을 빼고 수익률을 낸다 — 빠지면 입금일에 수익이 난 것으로
        오인한다 (accounting.md §6).
        """
        cash = dict(self.cash)
        cash[currency] = cash.get(currency, 0.0) + amount
        rate = fx_rate if currency != KRW else 1.0
        return replace(self, cash=cash, inflow=self.inflow + amount * rate)

    def with_tax_provision(self, amount: float) -> Book:
        """해외 양도세 충당. 일간 NAV 에는 안 들어간다 (accounting.md §5).

        연간 정산이라 일간에 넣으면 매도할 때마다 NAV 가 튄다. 대신 세전·세후를
        둘 다 보고한다 — 학습은 세전, 리포트는 양쪽.
        """
        return replace(self, tax_provision=self.tax_provision + amount)

    def reset_flow(self) -> Book:
        """스냅샷을 찍고 나면 그날의 유입은 소진된다."""
        return replace(self, inflow=0.0)
