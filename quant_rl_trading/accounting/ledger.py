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

## 해외 양도세 충당

미장 매도로 실현손익이 나면 그때 충당금을 다시 계산한다. **NAV 에서 빼지는
않는다** (accounting.md §5) — `Book.tax_provision` 에만 쌓이고, 세후 NAV 는
`nav.Valuation.nav_after_tax` 가 따로 보고한다.

세 가지를 못 박는다. 설계서(§5)가 "연간 정산·250만원 공제" 까지만 말하므로
나머지는 여기서 정한다:

1. **과세연도는 한국시간 기준 역년(1/1~12/31)** 이다. 체결의 ``valid_from``
   을 서울 시각으로 옮겨 연도를 가른다. UTC 연도로 가르면 12/31 밤 체결이
   다음 해로 새고, 그 해의 공제를 한 번 더 받는다.
2. **공제는 해마다 새로 준다.** 그래서 전 기간 누적이 아니라 연도별 누적
   실현손익에 각각 공제를 적용하고, 연도별 충당액을 더한다.
3. **환산 환율은 체결 시점 환율** 이다 (accounting.md §3 "체결 → 체결 시점
   환율"). 평가일 환율로 소급하면 과거 스냅샷의 충당금이 오늘 환율에 따라
   흔들려서 리플레이가 깨진다.

실제 세법은 취득·양도를 각각 그날의 기준환율로 환산해 환차손익까지 과세하지만,
장부는 평균단가를 달러로 들고 있어 그 분해가 불가능하다. **달러 실현손익을
체결일 환율로 환산**하는 근사이고, 충당금은 신고서가 아니라 추정치다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.accounting.book import KRW, USD, Book, Side, Trade
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.collectors.market_hours import Market, trading_days

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

TRADES = "trades"
CAPITAL_FLOWS = "capital_flows"
DIVIDENDS = "dividends"
NAV_DAILY = "nav_daily"
FX = "fx"

#: 계좌 하나짜리 펀드다. 여러 계좌가 생기면 이 값이 인자로 올라간다.
ACCOUNT = "FUND"

#: 환율 entity_id. accounting.md §3 — 평가는 스냅샷 시각의 매매기준율.
FX_USDKRW = "FX:USDKRW"

#: 과세연도를 가르는 시간대. 양도세는 한국 역년 기준이다.
SEOUL = ZoneInfo("Asia/Seoul")


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
    # **발효 전 입금은 장부에 안 선다.** 게이트는 `observed_at` 만 거르므로
    # "월요일에 넣겠다" 를 오늘 적으면 그 돈이 오늘 현금으로 잡힌다. 실제로
    # 그랬다(2026-08-23 · `cumulative_inflow` 주석에 전말). `daily_inflow` 와
    # 같은 기준으로 자른다 — 세 곳이 같은 표를 다르게 읽으면 반드시 어긋난다.
    if not flows.empty:
        flows = flows[flows["valid_from"] <= pd.Timestamp(as_of)]
    trades = store.get(TRADES, as_of=as_of, lookback=lookback)
    dividends = store.get(DIVIDENDS, as_of=as_of, lookback=lookback)

    # 입출금이 먼저다. 돈이 들어오기 전에 체결이 있을 수는 없다.
    for row in _ordered(flows):
        currency = str(row["currency"])
        rate = 1.0 if currency == KRW else fx_rate(store, as_of=row["valid_from"])
        book = book.with_flow(
            currency=currency, amount=float(row["amount"]), fx_rate=rate
        )

    # 과세연도별 누적 실현손익(원화)과 그 해에 이미 쌓아 둔 충당액.
    # 뒤에 손실이 나면 그 해 충당액이 줄어야 하므로 둘 다 들고 있어야 한다.
    realized_by_year: dict[int, float] = {}
    provisioned_by_year: dict[int, float] = {}
    #: 체결 시각별 환율. 장부는 매 세션 처음부터 다시 접히므로, 같은 체결의
    #: 환율을 창고에 다시 물어보면 백테스트에서 그 조회가 세션 수만큼 곱해진다.
    fx_at: dict[datetime, float] = {}

    for row in _ordered(trades):
        trade = Trade(
            entity_id=str(row["entity_id"]),
            side=Side(str(row["side"])),
            quantity=float(row["quantity"]),
            price=float(row["price"]),
            currency=str(row["currency"]),
            fee=float(row["fee"]),
            tax=float(row["tax"]),
        )
        before = book.realized_pnl.get(USD, 0.0)
        book = book.with_trade(trade)

        # 양도세는 **해외분에만** 붙는다. 국내 주식 양도차익은 대주주가 아닌
        # 한 비과세이고, 국내 매도분에는 이미 증권거래세가 체결 시점에 붙었다.
        if trade.currency != USD:
            continue
        realized_usd = book.realized_pnl.get(USD, 0.0) - before
        if realized_usd == 0.0:
            continue

        moment = pd.Timestamp(row["valid_from"]).to_pydatetime()
        if moment not in fx_at:
            fx_at[moment] = fx_rate(store, as_of=moment)
        year = moment.astimezone(SEOUL).year
        realized_by_year[year] = (
            realized_by_year.get(year, 0.0) + realized_usd * fx_at[moment]
        )
        provision = rates.capital_gains_provision(realized_usd_krw=realized_by_year[year])
        delta = provision - provisioned_by_year.get(year, 0.0)
        provisioned_by_year[year] = provision
        if delta != 0.0:
            book = book.with_tax_provision(delta)

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


def available_cash(
    store: Store,
    *,
    as_of: datetime,
    book: Book,
    settlement_days: int,
    market: str = "KR",
    currency: str = KRW,
) -> float:
    """**주문가능금액.** NAV 도 아니고 장부 현금도 아니다.

    accounting.md §1 이 못 박은 것을 실제로 계산하는 자리다 — "실제 주문가능
    금액은 NAV와 별개로 추적한다. 미결제 대금 때문에 NAV가 있어도 못 산다."
    이게 없던 동안 백테스트는 **없는 돈으로 샀다.** 1억으로 시작한 장부가
    현금 -1억 9,900만원 · 레버리지 2.83배까지 갔고, 시장이 하루 -8.7% 빠진 날
    NAV 는 -27% 빠졌다. 그 -32.6% 는 전략의 낙폭이 아니라 레버리지의 낙폭이다.

    장부 현금은 **발생주의**라 체결 즉시 증감한다(accounting.md §1). 그건
    NAV 를 위해 옳다. 그런데 실제 예수금은 D+2 에 들어오므로, 오늘 판 돈으로
    오늘 사면 그것도 없는 돈으로 사는 것이다. 그래서 **최근
    ``settlement_days`` 거래일 안의 매도 대금을 빼고** 돌려준다.

    빼는 것은 매도 대금뿐이다. 매수 지출은 장부에 이미 반영된 것을 그대로
    둔다 — 양쪽을 다 되돌리면 어제 쓴 돈이 오늘 되살아나 같은 돈을 두 번 쓰게
    된다. **한쪽으로만 보수적인 것이 이 함수의 요점이다.**

    증권사가 매도대금을 담보로 당일 매수를 허용하는 것은 별개의 편의이고,
    설계서에 없다. 없는 것을 가정하면 지금 결함의 축소판이 남는다.
    """
    cash = float(book.cash.get(currency, 0.0))
    if settlement_days <= 0:
        return max(0.0, cash)

    # 결제가 끝난 경계. **거래일로 센다** — 달력일로 세면 금요일 매도가
    # 화요일이 아니라 월요일에 풀려 실제보다 이르게 쓸 수 있게 된다.
    span = timedelta(days=settlement_days * 4 + 14)
    sessions = trading_days(Market(market), (as_of - span).date(), as_of.date())
    recent = [day for day in sessions if day <= as_of.date()][-settlement_days:]
    if not recent:
        return max(0.0, cash)
    cutoff = min(recent)

    trades = store.get(
        TRADES,
        as_of=as_of,
        lookback=settlement_days * 4 + 14,
        columns=["side", "quantity", "price", "currency", "fee", "tax"],
    )
    if trades.empty:
        return max(0.0, cash)

    unsettled = 0.0
    for row in _ordered(trades):
        if str(row["currency"]) != currency:
            continue
        if Side(str(row["side"])) is not Side.SELL:
            continue
        session = pd.Timestamp(row["valid_from"]).tz_convert(SEOUL).date()
        if session < cutoff:
            continue
        # 손에 들어오는 돈은 세금·수수료를 뺀 뒤다.
        unsettled += (
            float(row["quantity"]) * float(row["price"])
            - float(row["fee"])
            - float(row["tax"])
        )

    return max(0.0, cash - unsettled)


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


def principal(store: Store, *, as_of: datetime) -> float:
    """원금 = as_of 까지의 순입출금 누계(원화환산).

    **첫날 NAV 가 아니다.** 첫날 NAV 로 재면 이후 입금이 통째로 수익으로
    잡힌다 (accounting.md §6, TWR 과 같은 이유).

    **통화별로 환산한 뒤 더한다.** 그냥 ``amount.sum()`` 을 하면 달러 입금이
    1달러 = 1원으로 섞인다 — 실전 창고가 정확히 그랬다(2026-08-22): 5,000원 +
    9.49달러가 5,009원으로 세어져 원금이 18,422원 대신 5,009원이 됐고, 총
    수익금이 204원 대신 13,616원(+272%)으로 나갔다. ``daily_inflow`` 는 처음부터
    환산하고 있었으므로 **같은 규칙을 여기서도 쓴다** — 두 벌로 두었던 것이
    갈라진 것이 원인이다.
    """
    frame = store.get(CAPITAL_FLOWS, as_of=as_of, entity=ACCOUNT, lookback=None)
    if frame.empty:
        return 0.0
    # **아직 발효되지 않은 입금은 안 센다.** 게이트는 `observed_at` 만 거른다
    # (불변식 3) — "월요일에 넣겠다" 를 오늘 적어 두면 관측은 오늘이고 발효는
    # 월요일이다. 그 행을 오늘 원금에 넣으면 아직 오지 않은 돈이 장부에 선다.
    #
    # 2026-08-23 실측 사고: shadow 에 8/24 발효 입금 4.902억을 적어 뒀는데
    # 8/22 NAV 가 그것을 이미 더해 5억이 됐다. 그런데 `daily_inflow` 는
    # `valid_from` 을 제대로 걸러 inflow 를 0 으로 봤다 — **같은 행을 두 곳이
    # 다르게 걸러서** TWR 이 (5억-0)/976만-1 = +5022% 를 냈고, 화면의 총
    # 수익률이 4900% 로 찍혔다. 통화 환산을 한 벌로 모은 것과 같은 이유로
    # 날짜 기준도 `daily_inflow` 와 같아야 한다.
    frame = frame[frame["valid_from"] <= pd.Timestamp(as_of)]
    if frame.empty:
        return 0.0
    total = 0.0
    for row in _ordered(frame):
        currency = str(row["currency"])
        rate = 1.0 if currency == KRW else fx_rate(store, as_of=row["valid_from"])
        total += float(row["amount"]) * rate
    return total


def previous_snapshot(store: Store, *, as_of: datetime) -> dict[str, object] | None:
    """**as_of 보다 앞선** 회계 스냅샷. 없으면 None — 첫날이다.

    ``valid_from < as_of`` 로 자른다. ``<=`` 로 두면 **그날 스냅샷을 다시
    계산할 때 자기 자신이 어제로 잡힌다.** 그러면 TWR 이 "오늘 대 오늘" 이
    되어 0 에 가까워지고, 누적지수가 그 가짜 수익률만큼 어긋난다. 낙폭은
    지수로 재므로(snapshot.take) 그 어긋남은 MDD 까지 따라간다.

    재계산은 예외가 아니라 정상 경로다 — 늦게 도착한 체결 때문에 그날 회계가
    정정되면(snapshot.write) ``take`` 가 같은 as_of 로 다시 불린다. 그때
    창고에는 이미 그날 행이 있다. 실제로 shadow 2026-08-14 이 그 모양이었다.
    """
    frame = store.get(NAV_DAILY, as_of=as_of, entity=ACCOUNT)
    if frame.empty:
        return None
    frame = frame[frame["valid_from"] < pd.Timestamp(as_of)]
    if frame.empty:
        return None
    return _ordered(frame)[-1]
