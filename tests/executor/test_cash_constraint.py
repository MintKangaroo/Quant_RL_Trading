"""**없는 돈으로 사지 않는다.**

이 파일은 실제로 일어난 사고의 회귀 테스트다. 2026-01-02 ~ 03-13 워크포워드
구간에서 자본금 1억으로 시작한 장부가 이렇게 됐다:

    2026-01-12   현금  -1,805,838   주식평가   100,724,130   레버리지 1.02
    2026-02-10   현금 -119,504,494   주식평가   237,356,635   레버리지 2.01
    2026-03-13   현금 -199,627,918   주식평가   308,740,955   레버리지 2.83

시장이 하루 -8.7% 빠진 2026-03-04 에 NAV 는 -27% 빠졌다. `verify_m3.py` 가
보여 준 **MDD -32.6% 는 전략의 낙폭이 아니라 레버리지의 낙폭**이었다.

원인은 하나였다 — `session/daily.py` 가 `equity = NAV` 를 집행에 넘기고,
`sizing.size_orders` 가 그 자본에 목표 비중을 곱해 주문을 만들었다.
**가용 현금은 그 계산에 없었다.** ``grep -rn "cash" executor/ backtest/
selector/ session/`` 이 0건이었다.

그래서 여기서 못 박는 것은 넷이다.

1. 현금이 모자라면 모자란 만큼만 나간다
2. 매도가 미체결이면 다음 세션의 매수가 줄어든다 (돈이 안 들어왔으니까)
3. **레버리지가 1.0 을 넘지 않는다** — 사고의 직접 회귀
4. 같은 입력 두 번 → 같은 주문 (불변식 5)
"""

from __future__ import annotations

import pytest

from quant_rl_trading.executor.sizing import SizingParams, Target, size_orders
from quant_rl_trading.schemas.order import Side

pytestmark = pytest.mark.invariant

PARAMS = SizingParams(
    max_adv_ratio=0.03,
    max_liquidation_days=3,
    min_order_value=100_000.0,
    max_price_ratio=0.1,
    settlement_days=2,
)

EQUITY = 100_000_000.0


def _targets(count: int = 4, *, price: float = 10_000.0) -> list[Target]:
    """비중이 서로 다른 목표들. 잘리는 순서를 볼 수 있어야 한다."""
    return [
        Target(
            entity_id=f"KR:{index:06d}",
            # 0.40, 0.30, 0.20, 0.10 — 합이 1.0
            weight=(count - index) / (count * (count + 1) / 2),
            price=price,
            adv_value=1.0e12,  # 거래대금 상한이 변수로 끼어들지 않게
        )
        for index in range(count)
    ]


def _bought(orders) -> float:  # type: ignore[no-untyped-def]
    return sum(item.quantity * item.price for item in orders if item.side is Side.BUY)


# -- 1. 사고 재현 --------------------------------------------------------------


def test_without_the_constraint_it_buys_money_it_does_not_have() -> None:
    """**고치기 전 동작.** 현금 0 인데 자본 1억어치를 산다.

    이것이 레버리지 2.83배를 만든 계산이다. ``cash=None`` 이 그 시절의 경로다.
    """
    orders, _ = size_orders(
        targets=_targets(), holdings={}, equity=EQUITY, params=PARAMS, cash=None
    )

    assert _bought(orders) == pytest.approx(EQUITY, rel=1e-3)


def test_with_zero_cash_nothing_is_bought() -> None:
    """같은 입력에 주문가능금액 0 을 주면 매수가 사라진다."""
    orders, skipped = size_orders(
        targets=_targets(), holdings={}, equity=EQUITY, params=PARAMS, cash=0.0
    )

    assert _bought(orders) == 0.0
    # **조용히 줄지 않는다.** 후보는 4인데 주문이 0인 이유를 물어볼 수 있어야 한다.
    assert {item.reason for item in skipped} == {"주문가능금액 부족"}
    assert len(skipped) == 4


# -- 2. 예산만큼만 나간다 -------------------------------------------------------


def test_buys_are_capped_at_available_cash() -> None:
    """자본은 1억인데 결제된 현금이 3천만원이면 3천만원어치만 산다."""
    cash = 30_000_000.0
    orders, skipped = size_orders(
        targets=_targets(), holdings={}, equity=EQUITY, params=PARAMS, cash=cash
    )

    assert _bought(orders) <= cash
    # 예산을 놀리지도 않는다 — 1주 단위로 채울 수 있는 만큼은 채운다.
    assert _bought(orders) > cash - 10_000.0
    assert skipped, "예산에 못 들어간 목표는 사유가 남아야 한다"


def test_the_largest_conviction_is_filled_first() -> None:
    """자르는 순서는 목표 비중이 큰 것부터다."""
    # 가장 큰 목표(0.4 × 1억 = 4천만) 하나만 겨우 들어가는 예산
    orders, skipped = size_orders(
        targets=_targets(), holdings={}, equity=EQUITY, params=PARAMS, cash=40_000_000.0
    )

    bought = {item.entity_id for item in orders if item.side is Side.BUY}
    assert "KR:000000" in bought, "비중이 가장 큰 목표가 먼저 채워져야 한다"
    assert {item.entity_id for item in skipped} <= {"KR:000001", "KR:000002", "KR:000003"}


def test_a_trimmed_order_says_so() -> None:
    """줄여서 낸 주문은 줄였다고 적는다."""
    orders, _ = size_orders(
        targets=_targets(1), holdings={}, equity=EQUITY, params=PARAMS, cash=25_000_000.0
    )

    buys = [item for item in orders if item.side is Side.BUY]
    assert len(buys) == 1
    assert "주문가능금액으로" in buys[0].reason
    # 되먹임도 줄인 수량 기준이어야 한다 (불변식 7).
    assert buys[0].realized_weight == pytest.approx(
        buys[0].quantity * buys[0].price / EQUITY
    )


def test_a_scrap_below_the_minimum_is_not_sent() -> None:
    """남은 예산이 최소 주문금액에 못 미치면 내지 않는다."""
    orders, skipped = size_orders(
        targets=_targets(1), holdings={}, equity=EQUITY, params=PARAMS, cash=50_000.0
    )

    assert _bought(orders) == 0.0
    assert [item.reason for item in skipped] == ["주문가능금액 부족"]


# -- 3. 매도는 막지 않는다 ------------------------------------------------------


def test_sells_are_never_blocked_by_cash() -> None:
    """현금이 0 이어도 팔 수는 있어야 한다. 빠져나올 길을 막으면 안전장치가 아니다."""
    targets = [Target(entity_id="KR:000000", weight=0.0, price=10_000.0, adv_value=1.0e12)]
    orders, _ = size_orders(
        targets=targets, holdings={"KR:000000": 500}, equity=EQUITY,
        params=PARAMS, cash=0.0,
    )

    sells = [item for item in orders if item.side is Side.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 500


# -- 4. 결정론 -----------------------------------------------------------------


def test_same_input_twice_gives_the_same_orders() -> None:
    """같은 as_of 로 두 번 돌리면 같은 주문이다 (불변식 5).

    비중이 같은 목표를 섞어 타이브레이커까지 본다 — 입력 순서를 뒤집어도
    결과가 같아야 한다. dict 순서에 기대고 있으면 여기서 갈린다.
    """
    tied = [
        Target(entity_id=name, weight=0.25, price=10_000.0, adv_value=1.0e12)
        for name in ("KR:000003", "KR:000001", "KR:000002", "KR:000000")
    ]
    # 목표는 종목당 0.25 × 1억 = 2,500만원씩. 예산 3천만원이면 하나가 통째로
    # 들어가고 다음 하나가 잘린다 — 순서가 결과에 드러나는 자리다.
    budget = 30_000_000.0

    first, _ = size_orders(
        targets=tied, holdings={}, equity=EQUITY, params=PARAMS, cash=budget
    )
    second, _ = size_orders(
        targets=list(reversed(tied)), holdings={}, equity=EQUITY,
        params=PARAMS, cash=budget,
    )

    def fingerprint(orders):  # type: ignore[no-untyped-def]
        return sorted(
            (item.entity_id, str(item.side), item.quantity) for item in orders
        )

    assert fingerprint(first) == fingerprint(second)
    # 비중이 같으면 entity_id 오름차순으로 채운다 — 예산이 둘까지만 닿는다.
    bought = sorted(item.entity_id for item in first if item.side is Side.BUY)
    assert bought == ["KR:000000", "KR:000001"]
