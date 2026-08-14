"""NAV 와 수익률. **이 레포에서 NAV 를 계산하는 유일한 곳** (불변식: accounting.md §8).

Executor·Auditor·dashboard·reporting 이 각자 계산하면 반드시 어긋난다. 어긋나면
어느 쪽이 맞는지 판정할 방법이 없고, 보상 함수는 그중 하나를 믿는다.

## 하루의 정의

국장 마감 15:30, 미장 마감 새벽 05:00(한국시간). 두 시장의 하루가 어긋난다.
**일간 NAV 는 한국시간 15:40 하루 한 번**, 그 시점의 미장 값은 **직전 미장
종가**를 쓴다 (accounting.md §2).

벤치마크도 정확히 같은 규칙으로 계산한다. 포트폴리오는 15:40 기준인데
벤치마크가 미장 실시간이면, 그 차이가 통째로 가짜 초과수익·가짜 낙폭이 되어
보상에 들어간다.

## 수익률은 TWR

자본 증액 게이트가 있어 **입금이 발생한다.** 단순 NAV 변화율은 입금일에
수익이 난 것으로 오인한다.

    r_t = (NAV_t − 유입_t) / NAV_{t−1} − 1

**낙폭도 이 누적지수로 계산한다.** NAV 원금액으로 계산하면 입금이 낙폭을
지워서 MDD 예산이 무의미해진다 (accounting.md §6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from quant_rl_trading.accounting.book import KRW, USD, Book

#: 누적지수 초기값. 벤치마크와 같아야 나란히 읽을 수 있다.
BASE_INDEX = 100.0


@dataclass(frozen=True)
class Valuation:
    """한 시점의 NAV 분해. 합계만 들고 다니면 어디가 틀렸는지 못 찾는다."""

    nav: float
    cash_krw: float
    cash_usd: float
    equity_kr: float
    equity_us: float
    accrued_dividend: float
    payable: float
    fx_rate: float
    tax_provision: float

    @property
    def nav_after_tax(self) -> float:
        """세후. **학습·보상은 세전을 쓴다** — 양도세는 연간 정산이라 일간에
        넣으면 매도할 때마다 튄다. 리포트에만 양쪽을 싣는다 (accounting.md §5).

        충당금은 `ledger.build_book` 이 미장 실현손익에서 연도별로 쌓는다
        (§5.1). 미장 매도가 없으면 0 이라 세전과 같은 값이 나오는데, 그건
        정상이다 — 세금이 없는 게 아니라 **낼 일이 아직 없는 것**이다.
        """
        return self.nav - self.tax_provision


def value(
    book: Book,
    *,
    prices: Mapping[str, float],
    fx_rate: float,
) -> Valuation:
    """NAV = 원화현금 + 달러현금×FX + 국내평가 + 해외평가×FX + 미수배당 − 미지급.

    **가격이 없는 보유 종목은 예외다.** 0 으로 치면 그 종목이 사라진 것과
    같아져서, 데이터가 빠진 날 NAV 가 조용히 떨어지고 그게 낙폭으로 기록된다.
    거래정지로 가격이 없으면 직전 가격을 넘겨주는 것은 **호출자의 책임**이다.
    """
    equity_kr = 0.0
    equity_us = 0.0

    for entity_id, position in book.positions.items():
        if position.quantity == 0.0:
            continue
        if entity_id not in prices:
            raise KeyError(
                f"{entity_id}: 가격이 없다. 0 으로 평가하면 NAV 가 조용히 "
                "떨어지고 그것이 낙폭으로 기록된다"
            )
        market_value = position.quantity * prices[entity_id]
        if position.currency == USD:
            equity_us += market_value
        else:
            equity_kr += market_value

    cash_krw = book.cash.get(KRW, 0.0)
    cash_usd = book.cash.get(USD, 0.0)
    accrued = book.accrued_dividend.get(KRW, 0.0) + book.accrued_dividend.get(USD, 0.0) * fx_rate
    payable = book.payable.get(KRW, 0.0) + book.payable.get(USD, 0.0) * fx_rate

    nav = (
        cash_krw
        + cash_usd * fx_rate
        + equity_kr
        + equity_us * fx_rate
        + accrued
        - payable
    )
    return Valuation(
        nav=nav,
        cash_krw=cash_krw,
        cash_usd=cash_usd,
        equity_kr=equity_kr,
        equity_us=equity_us,
        accrued_dividend=accrued,
        payable=payable,
        fx_rate=fx_rate,
        tax_provision=book.tax_provision,
    )


def twr_return(*, nav: float, previous_nav: float, inflow: float) -> float:
    """시간가중 일간수익률. **보상 함수의 r_port 가 이 값이다.**

    첫날(previous_nav = 0)은 0 을 돌려준다 — 비교할 어제가 없는데 수익률을
    지어내면 그 하루가 통째로 가짜다.
    """
    if previous_nav <= 0.0:
        return 0.0
    return (nav - inflow) / previous_nav - 1.0


def compound(returns: Sequence[float], *, base: float = BASE_INDEX) -> list[float]:
    """누적지수. 낙폭·IR·트래킹에러가 전부 이 위에서 계산된다."""
    index = base
    out: list[float] = []
    for value_ in returns:
        index *= 1.0 + value_
        out.append(index)
    return out


def drawdown(index_values: Sequence[float], *, peak: float = float("-inf")) -> list[float]:
    """누적지수 기준 낙폭. **NAV 원금액으로 계산하지 않는다.**

    원금액으로 하면 입금이 낙폭을 지운다 — 30% 빠진 다음 날 큰돈을 넣으면
    장부상 낙폭이 사라지고, MDD 예산이 무의미해진다 (accounting.md §6).

    ``peak`` 은 **창 이전의 고점** 이다. 창만 보고 재면 그 창의 첫날이 늘
    고점이 되어 낙폭이 0 에서 시작한다. 장부의 낙폭은 전 기간 고점 기준이라
    (``snapshot._peak_index``), 창 기준으로 잰 선을 그 옆에 겹쳐 그리면
    **얕은 쪽이 이겨 보인다.** 겹쳐 그릴 선은 같은 고점을 물려받아야 한다.
    """
    out: list[float] = []
    for value_ in index_values:
        peak = max(peak, value_)
        out.append(value_ / peak - 1.0 if peak > 0 else 0.0)
    return out


def blended_benchmark(
    *,
    kr_returns: Sequence[float],
    us_returns_krw: Sequence[float],
    kr_weight: float,
    us_weight: float,
    base: float = BASE_INDEX,
) -> list[float]:
    """혼합 벤치마크 지수. **총수익지수(TR)를 넣어야 한다.**

    가격지수(PR)를 넣으면 우리 포트폴리오는 배당을 받는데 벤치마크는 못 받아,
    배당수익률만큼 **공짜 초과수익**이 생긴다. 연 2~3%p 규모의 가짜 알파다
    (accounting.md §7). 이 함수는 넣어 준 것이 TR 인지 알 수 없다 — 그건
    호출자가 `benchmark.kr_index` 설정으로 보장한다.

    ⚠️ **지금 들어오는 것은 PR 이다.** 2026-08-14 실측으로 KRX Open API·LS
    `t1511`·FRED 세 곳 다 TR 도 배당수익률 필드도 주지 않아, `config.benchmark`
    가 `KR:IDX:KRX 300`·`US:IDX:SP500`(둘 다 가격지수)을 가리키고 있다
    (`total_return: false`). 그래서 이 지수로 잰 초과수익은 **우리에게 유리한
    쪽으로 부풀어 있다.** TR 을 받게 되면 바뀌는 것은 설정 세 값뿐이고 이
    계산은 그대로다.

    미장 수익률은 **원화환산 후** 넣는다. 환율 변동은 우리 포트폴리오의
    해외분에도 똑같이 작용하므로, 벤치마크만 달러 기준이면 환차익이 통째로
    초과수익으로 잡힌다.
    """
    if len(kr_returns) != len(us_returns_krw):
        raise ValueError("두 시장의 수익률 길이가 다르다. 같은 날짜 축이어야 한다")
    total = kr_weight + us_weight
    if total <= 0:
        raise ValueError("벤치마크 가중치 합이 0 이다")

    blended = [
        (kr_weight * kr + us_weight * us) / total
        for kr, us in zip(kr_returns, us_returns_krw, strict=True)
    ]
    return compound(blended, base=base)
