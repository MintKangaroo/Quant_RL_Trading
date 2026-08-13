"""수수료·세금률. **전부 `store.config` 에서 온다** (불변식 10).

코드에 숫자를 박으면 화면과 학습 설정이 갈라진다. 여기서는 as_of 시점의
설정을 한 번 읽어 동결한 값 묶음을 만들고, 계산 함수들은 그것만 본다 —
`fills.FillParams` 와 같은 구조다.

세율은 바뀐다(증권거래세는 실제로 몇 번 바뀌었다). ``as_of`` 로 읽으므로
과거 백테스트는 그 시점 세율을 그대로 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from lattice.accounting.book import KRW, USD, Side, Trade

if TYPE_CHECKING:
    from lattice.store import Store


@dataclass(frozen=True)
class Rates:
    """as_of 시점의 비용·세율."""

    fee_kr: float
    fee_us: float
    transaction_tax_kr: float
    dividend_tax_kr: float
    dividend_tax_us: float
    capital_gains_us: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> Rates:
        return cls(
            fee_kr=float(store.config("accounting.fee_kr", as_of=as_of)),
            fee_us=float(store.config("accounting.fee_us", as_of=as_of)),
            transaction_tax_kr=float(
                store.config("accounting.transaction_tax_kr", as_of=as_of)
            ),
            dividend_tax_kr=float(store.config("accounting.dividend_tax_kr", as_of=as_of)),
            dividend_tax_us=float(store.config("accounting.dividend_tax_us", as_of=as_of)),
            capital_gains_us=float(store.config("accounting.capital_gains_us", as_of=as_of)),
        )

    # -- 비용 계산 --------------------------------------------------------------

    def costs(self, *, side: Side, gross: float, currency: str) -> tuple[float, float]:
        """(수수료, 세금). **증권거래세는 매도에만 붙는다.**

        매수에도 붙이면 왕복 비용이 두 배로 잡히고, 백테스트가 실제보다
        비관적이 된다 — 비관적인 백테스트도 틀린 백테스트다.
        """
        if currency == USD:
            return abs(gross) * self.fee_us, 0.0
        fee = abs(gross) * self.fee_kr
        tax = abs(gross) * self.transaction_tax_kr if side is Side.SELL else 0.0
        return fee, tax

    def priced(
        self,
        *,
        entity_id: str,
        side: Side,
        quantity: float,
        price: float,
        currency: str = KRW,
    ) -> Trade:
        """비용을 계산해 붙인 체결. Executor 가 이걸로 장부에 넣는다."""
        fee, tax = self.costs(side=side, gross=quantity * price, currency=currency)
        return Trade(
            entity_id=entity_id,
            side=side,
            quantity=quantity,
            price=price,
            currency=currency,
            fee=fee,
            tax=tax,
        )

    def dividend_net(self, *, gross: float, currency: str) -> float:
        """세후 배당. 계상 시점에 이미 차감한다 (accounting.md §4)."""
        rate = self.dividend_tax_us if currency == USD else self.dividend_tax_kr
        return gross * (1.0 - rate)

    def capital_gains_provision(self, *, realized_usd_krw: float, allowance: float) -> float:
        """해외 양도세 충당액. 연간 실현이익에서 공제액을 뺀 뒤 세율.

        음수면 0 이다 — 손실인 해에 세금을 돌려받지는 않는다(이월공제는 별도
        주제이고, 지어내지 않는다).
        """
        taxable = max(0.0, realized_usd_krw - allowance)
        return taxable * self.capital_gains_us
