"""회계 계약 테스트 — `docs/design/accounting.md` §8 이 요구한 다섯 가지.

**보상 함수의 `r_port` 가 여기서 정의된다.** 이게 틀리면 위에 뭘 쌓아도 틀리고,
틀렸다는 사실조차 알 수 없다 — NAV 는 비교할 정답이 없기 때문이다. 그래서
이 테스트들은 "계산이 되는가" 가 아니라 **"틀리는 방식으로 틀리지 않는가"** 를
잡는다.
"""

from __future__ import annotations

import pytest

from quant_rl_trading.accounting import (
    KRW,
    USD,
    Book,
    Rates,
    Side,
    Trade,
    blended_benchmark,
    compound,
    drawdown,
    twr_return,
    value,
)

FX = 1350.0

RATES = Rates(
    fee_kr=0.00015,
    fee_us=0.0025,
    transaction_tax_kr=0.0018,
    dividend_tax_kr=0.154,
    dividend_tax_us=0.15,
    capital_gains_us=0.22,
)


def funded(krw: float = 10_000_000.0) -> Book:
    return Book(cash={KRW: krw, USD: 0.0})


# -- §6 TWR -----------------------------------------------------------------------


def test_입금일에는_수익률이_0이다() -> None:
    """NAV 는 늘었지만 수익은 없다.

    단순 NAV 변화율을 쓰면 입금일이 대박난 날로 기록된다. 자본 증액 게이트가
    있는 이 프로젝트에서 입금은 **반드시 일어나는 일**이라, 이 하나가 틀리면
    성과 곡선 전체가 거짓이 된다.
    """
    previous = 10_000_000.0
    deposit = 5_000_000.0

    assert twr_return(nav=previous + deposit, previous_nav=previous, inflow=deposit) == 0.0


def test_첫날은_수익률을_지어내지_않는다() -> None:
    """비교할 어제가 없다. 0 을 돌려주는 것이 정답이다."""
    assert twr_return(nav=10_000_000.0, previous_nav=0.0, inflow=10_000_000.0) == 0.0


def test_낙폭은_누적지수로_잰다_입금이_지우지_못한다() -> None:
    """**NAV 원금액으로 낙폭을 재면 입금이 낙폭을 지운다.**

    30% 빠진 다음 날 큰돈을 넣으면 장부상 NAV 는 회복된 것처럼 보인다. 그러면
    MDD 예산(reward-and-risk.md)이 무의미해지고, 킬스위치도 안 걸린다.
    """
    # −30% 뒤 입금이 있었지만 수익률 축에는 입금이 없다.
    returns = [0.0, -0.30, 0.0]
    index = compound(returns)
    worst = min(drawdown(index))

    assert worst == pytest.approx(-0.30)


# -- §4 배당 ----------------------------------------------------------------------


def test_배당락일에_낙폭이_생기지_않는다() -> None:
    """배당락으로 주가가 빠진 만큼 미수배당이 들어와 NAV 가 유지된다.

    입금일 인식으로 바꾸면 이 균형이 깨지고, 배당락 하락이 그대로 낙폭이 되어
    보상 함수가 **배당을 손실로 오인**한다.
    """
    book = funded(0.0).with_trade(Trade("KR:005930", Side.BUY, 100, 10_000.0))
    before = value(book, prices={"KR:005930": 10_000.0}, fx_rate=FX).nav

    # 주당 1,000원 배당. 배당락으로 주가가 정확히 그만큼 빠진다.
    gross = 100 * 1_000.0
    net = RATES.dividend_net(gross=gross, currency=KRW)
    after_book = book.with_dividend(currency=KRW, net_amount=net)
    after = value(after_book, prices={"KR:005930": 9_000.0}, fx_rate=FX).nav

    # 세금분만 줄어든다. 그 이상 줄면 배당을 손실로 잡은 것이다.
    assert before - after == pytest.approx(gross * RATES.dividend_tax_kr)
    assert twr_return(nav=after, previous_nav=before, inflow=0.0) > -0.02


def test_배당_입금은_NAV를_바꾸지_않는다() -> None:
    """미수배당 → 현금. 자리만 옮기는 것이다."""
    book = funded(0.0).with_dividend(currency=KRW, net_amount=84_600.0)
    before = value(book, prices={}, fx_rate=FX).nav

    paid = book.with_dividend_paid(currency=KRW, net_amount=84_600.0)
    after = value(paid, prices={}, fx_rate=FX).nav

    assert after == pytest.approx(before)
    assert paid.cash[KRW] == pytest.approx(84_600.0)


# -- §3 환율 ----------------------------------------------------------------------


def test_환율만_변한_날_해외분이_정확히_그만큼_움직인다() -> None:
    book = Book(cash={KRW: 0.0, USD: 1_000.0})
    book = book.with_trade(
        Trade("US:AAPL", Side.BUY, 10, 50.0, currency=USD)
    )

    before = value(book, prices={"US:AAPL": 50.0}, fx_rate=1_000.0).nav
    after = value(book, prices={"US:AAPL": 50.0}, fx_rate=1_100.0).nav

    # 달러 자산 총액(현금 500 + 주식 500)이 1,000달러. 환율 10% 상승분 그대로.
    assert after - before == pytest.approx(1_000.0 * 100.0)


# -- §1 발생주의 ------------------------------------------------------------------


def test_매수_직후_수수료만큼_NAV가_준다() -> None:
    """체결 시점에 비용을 인식한다(발생주의). 결제일이 아니다.

    같은 가격에 사서 같은 가격으로 평가하면 NAV 는 **정확히 수수료만큼** 줄어야
    한다. 안 줄면 비용이 어딘가로 사라진 것이고, 백테스트가 실제보다 좋아진다.
    """
    book = funded()
    before = value(book, prices={}, fx_rate=FX).nav

    trade = RATES.priced(
        entity_id="KR:005930", side=Side.BUY, quantity=10, price=70_000.0
    )
    after = value(
        book.with_trade(trade), prices={"KR:005930": 70_000.0}, fx_rate=FX
    ).nav

    assert before - after == pytest.approx(trade.fee + trade.tax)
    assert trade.fee > 0.0


def test_증권거래세는_매도에만_붙는다() -> None:
    """매수에도 붙이면 왕복 비용이 두 배가 된다. 비관적인 백테스트도 틀렸다."""
    buy_fee, buy_tax = RATES.costs(side=Side.BUY, gross=1_000_000.0, currency=KRW)
    sell_fee, sell_tax = RATES.costs(side=Side.SELL, gross=1_000_000.0, currency=KRW)

    assert buy_tax == 0.0
    assert sell_tax == pytest.approx(1_000_000.0 * RATES.transaction_tax_kr)
    assert buy_fee == sell_fee


# -- §5 세금 ----------------------------------------------------------------------


def test_양도세는_NAV에_없고_세후에만_있다() -> None:
    """연간 정산이라 일간 NAV 에 넣으면 매도할 때마다 NAV 가 튄다.

    학습·보상은 세전을 쓰고, 리포트에만 양쪽을 싣는다.
    """
    book = funded().with_tax_provision(1_000_000.0)
    valuation = value(book, prices={}, fx_rate=FX)

    assert valuation.nav == pytest.approx(10_000_000.0)
    assert valuation.nav_after_tax == pytest.approx(9_000_000.0)


def test_손실난_해에는_충당금이_0이다() -> None:
    assert RATES.capital_gains_provision(realized_usd_krw=-500.0, allowance=2_500_000.0) == 0.0


# -- 평가 안전장치 ----------------------------------------------------------------


def test_가격이_없으면_0으로_평가하지_않고_멈춘다() -> None:
    """**0 으로 치면 그 종목이 사라진 것과 같다.**

    데이터가 빠진 날 NAV 가 조용히 떨어지고, 그게 낙폭으로 기록되고, 킬스위치가
    걸린다. 거래정지로 가격이 없으면 직전 가격을 넘기는 것은 호출자 책임이다.
    """
    book = funded().with_trade(Trade("KR:005930", Side.BUY, 10, 70_000.0))

    with pytest.raises(KeyError, match="가격이 없다"):
        value(book, prices={}, fx_rate=FX)


def test_보유보다_많이_팔_수_없다() -> None:
    book = funded().with_trade(Trade("KR:005930", Side.BUY, 10, 70_000.0))

    with pytest.raises(ValueError, match="매도"):
        book.with_trade(Trade("KR:005930", Side.SELL, 11, 70_000.0))


# -- §7 벤치마크 ------------------------------------------------------------------


def test_벤치마크는_같은_날짜축에서만_섞인다() -> None:
    with pytest.raises(ValueError, match="길이가 다르다"):
        blended_benchmark(
            kr_returns=[0.01, 0.02], us_returns_krw=[0.01],
            kr_weight=0.5, us_weight=0.5,
        )


def test_혼합_벤치마크는_가중평균이다() -> None:
    index = blended_benchmark(
        kr_returns=[0.10], us_returns_krw=[0.00], kr_weight=0.5, us_weight=0.5
    )
    assert index[0] == pytest.approx(105.0)
