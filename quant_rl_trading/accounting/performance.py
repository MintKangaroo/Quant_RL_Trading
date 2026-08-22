"""성과 요약 — **화면과 메일이 같은 숫자를 읽는 자리.**

NAV 산출은 이 패키지 한 곳에서만 한다 (accounting.md §8). 그런데 "오늘
얼마 벌었나" 를 화면이 따로 접고 메일이 또 따로 접으면, NAV 를 한 곳에서
계산한 보람이 그 다음 칸에서 사라진다. 이 모듈은 **이미 계산돼 창고에 있는
것**(``nav_daily``·``trades``·``capital_flows``)을 읽어 한 덩어리로 묶을 뿐,
NAV 도 TWR 도 여기서 다시 계산하지 않는다.

## 수익률과 자산 증감은 다른 사실이다 ⭐

    자산 증감 = NAV_t − NAV_{t−1}          ← 입금이 들어오면 같이 커진다
    손익      = NAV_t − NAV_{t−1} − 유입_t  ← 그중 우리가 번 것
    수익률    = TWR (nav_daily.twr_return)  ← 손익을 어제 자산으로 나눈 것

셋을 같이 싣되 **입출금 금액을 반드시 함께 적는다.** 안 적으면 490,238,209원
입금이 들어온 날 "자산 +5,000%" 와 "수익률 +0.0%" 가 한 화면에서 서로를
거짓말쟁이로 만든다 (accounting.md §6).

## 없는 것과 0 은 다르다

- ``session is None`` — 회계 스냅샷이 아직 없다. **못 쟀다.**
- ``fills == []`` 이고 ``session`` 이 있다 — 그날 **매매가 없었다.**
- ``daily_return == 0.0`` — 잰 결과가 보합이다.

셋을 한 문구로 뭉치면 읽는 사람이 "손실 0" 으로 읽는다. 호출부가 가를 수
있도록 사실을 갈라 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from quant_rl_trading.accounting import ledger
from quant_rl_trading.accounting import snapshot as snapshot_module
from quant_rl_trading.accounting.book import KRW, Book, Side, Trade
from quant_rl_trading.accounting.nav import BASE_INDEX
from quant_rl_trading.store import names as names_module

if TYPE_CHECKING:  # pragma: no cover - 타입 전용
    from quant_rl_trading.store import Store

SEOUL = ledger.SEOUL

#: 메일 한 통에 싣는 체결 줄 수의 상한. Gmail 은 본문이 크면 잘라낸다
#: (reporting.md §3). 넘치는 줄은 지우지 않고 "외 N건" 으로 센다.
MAIL_FILL_ROWS = 12


@dataclass(frozen=True)
class Fill:
    """체결 한 줄. **실현손익은 매도에만 있다.**

    매수에 0 을 넣으면 "본전" 으로 읽힌다 — 매수는 아직 아무것도 실현하지
    않은 것이지 0원을 번 것이 아니다.
    """

    entity_id: str
    name: str
    side: str
    quantity: float
    price: float
    currency: str
    fee: float
    tax: float
    realized_pnl: float | None = None
    realized_rate: float | None = None

    @property
    def amount(self) -> float:
        """체결대금. 비용은 녹이지 않는다 — 녹이면 비용만 따로 못 본다."""
        return self.quantity * self.price

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "amount": self.amount,
            "currency": self.currency,
            "fee": self.fee,
            "tax": self.tax,
            "realized_pnl": self.realized_pnl,
            "realized_rate": self.realized_rate,
        }


@dataclass(frozen=True)
class Performance:
    """한 세션의 성과. 못 잰 자리는 ``None`` 이고, 이유는 ``note`` 에 있다."""

    #: 운용 모드(``store.mode``). 배지에 그대로 쓴다 — 모의 운용 숫자를
    #: 실전으로 읽는 것이 여기서 가능한 가장 비싼 오해다.
    mode: str
    mode_note: str
    #: 이 성과가 어느 창고에서 나왔는지. 화면·메일이 섞이면 이 값이 다르다.
    store_root: str

    session: date | None
    previous_session: date | None
    #: 첫 회계 스냅샷 날짜. 누적수익률의 "시작" 이 언제인지 말한다.
    since: date | None

    nav: float | None
    previous_nav: float | None
    #: NAV_t − NAV_{t−1}. **수익이 아니다** — 입금이 들어오면 같이 커진다.
    nav_change: float | None
    #: (since, session] 순입출금. 원화환산.
    inflow: float | None
    #: nav_change − inflow. 그중 우리가 번 것.
    pnl: float | None

    #: 시간가중 일간수익률. ``nav_daily.twr_return`` 을 그대로 읽는다.
    daily_return: float | None
    #: 누적지수 기준. ``index_value / 100 − 1``.
    cumulative_return: float | None
    index_value: float | None
    drawdown: float | None

    #: 입출금 누계 = 원금. **첫날 NAV 가 아니다** — 첫날 NAV 로 재면 이후
    #: 입금이 통째로 수익으로 잡힌다.
    principal: float | None
    total_pnl: float | None

    #: 그 세션의 체결. **상한에 잘릴 수 있다** — 아래 집계는 자르기 **전**의
    #: 전수라, 목록 길이로 건수를 세면 안 된다. 실제로 그렇게 세어서
    #: "매매 16건 (매수 0 · 매도 12)" 이 나갔다.
    fills: list[Fill]
    #: 상한(``MAIL_FILL_ROWS``)에 잘려 ``fills`` 에 안 담긴 체결 수.
    fills_omitted: int = 0
    #: 자르기 전 전수. 목록이 잘려도 이 숫자는 안 변한다.
    buy_count: int = 0
    sell_count: int = 0
    #: 그날 매도로 실현한 손익 합. **원화 체결만 센다** — 달러 실현손익을
    #: 여기 더하려면 체결 시점 환율이 필요한데(accounting.md §3), 평가일
    #: 환율로 소급하면 과거 스냅샷이 오늘 환율에 흔들린다. 미장 매도가 섞인
    #: 날의 정확한 합은 체결별 ``Fill.realized_pnl`` 에 통화와 함께 있다.
    #: 매도가 없으면 ``None`` — **0 이 아니다.**
    realized_pnl: float | None = None
    #: 성과를 못 잰 이유. **``None`` 이면 잰 것이다** — 매매 0건은 잰 결과이지
    #: 못 잰 것이 아니다.
    note: str | None = None

    @property
    def measured(self) -> bool:
        return self.session is not None

    @property
    def fill_count(self) -> int:
        """그 세션의 전체 체결 건수. 목록이 잘려도 안 변한다."""
        return self.buy_count + self.sell_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_note": self.mode_note,
            "store_root": self.store_root,
            "session": self.session.isoformat() if self.session else None,
            "previous_session": (
                self.previous_session.isoformat() if self.previous_session else None
            ),
            "since": self.since.isoformat() if self.since else None,
            "nav": self.nav,
            "previous_nav": self.previous_nav,
            "nav_change": self.nav_change,
            "inflow": self.inflow,
            "pnl": self.pnl,
            "daily_return": self.daily_return,
            "cumulative_return": self.cumulative_return,
            "index_value": self.index_value,
            "drawdown": self.drawdown,
            "principal": self.principal,
            "total_pnl": self.total_pnl,
            "realized_pnl": self.realized_pnl,
            "fills": [fill.as_dict() for fill in self.fills],
            "fills_omitted": self.fills_omitted,
            "fill_count": self.fill_count,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "note": self.note,
        }


def _kst_date(value: Any) -> date | None:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        return stamp.date()
    return stamp.tz_convert(SEOUL).date()


def realized_by_trade(store: Store, *, as_of: datetime) -> dict[str, dict[str, Any]]:
    """체결별 실현손익. **매도에만 값이 있다.**

    평균단가는 그 종목의 **모든 과거 매수**에 달려 있어서 최근 며칠만 봐서는
    못 구한다 — 그래서 전 기간을 접는다.

    **계산을 다시 구현하지 않는다.** ``Book.with_trade`` 가 이미
    ``(체결가 − 평단) × 수량, 비용 차감`` 을 하고 있고(이동평균법, 수수료
    포함), 회계와 화면이 다른 식을 쓰면 어느 쪽이 맞는지 판정할 방법이 없다.
    여기서는 그 장부를 재생하며 매도 직전·직후 누적 실현손익의 **차이**를
    꺼낼 뿐이다.

    수익률의 분모는 **취득원가**(평단 × 수량)다. 매도대금으로 나누면 손실이
    난 거래에서 분모가 작아져 손실률이 실제보다 작아 보인다.

    키는 ``"{주문번호 앞머리}|{종목}"`` 이다 — 백테스트 체결의 ``order_id``
    는 ``"{세션}|{종목}|{방향}"`` 이라 앞머리가 세션이 된다.
    """
    frame = store.get(ledger.TRADES, as_of=as_of)
    if frame.empty:
        return {}

    book = Book()
    out: dict[str, dict[str, Any]] = {}
    for row in ledger._ordered(frame):
        entity = str(row["entity_id"])
        currency = str(row["currency"])
        side = Side(str(row["side"]))
        quantity = float(row["quantity"])
        held = book.positions.get(entity)
        basis = (held.avg_cost * quantity) if held else 0.0
        before = book.realized_pnl.get(currency, 0.0)
        try:
            book = book.with_trade(
                Trade(
                    entity_id=entity,
                    side=side,
                    quantity=quantity,
                    price=float(row["price"]),
                    currency=currency,
                    fee=float(row["fee"]),
                    tax=float(row["tax"]),
                )
            )
        except ValueError:
            # 보유보다 많이 판 기록이다. 장부가 아니라 데이터의 문제이므로
            # 화면을 죽이지 않고 그 건만 건너뛴다.
            continue
        if side is not Side.SELL:
            continue
        realized = book.realized_pnl.get(currency, 0.0) - before
        key = f"{str(row['order_id']).split('|')[0]}|{entity}"
        out[key] = {
            "realized_pnl": realized,
            "realized_rate": (realized / basis) if basis else None,
            "currency": currency,
        }
    return out


def fills(store: Store, *, as_of: datetime, session: date) -> list[Fill]:
    """그 세션의 체결과, 상한에 잘려 빠진 건수.

    세션은 **한국시간 역일**로 가른다 (``ledger`` 의 과세연도와 같은 시간대).
    UTC 날짜로 가르면 16:00 KST 체결이 같은 날 07:00 UTC 라 우연히 맞다가,
    새벽에 도는 미장 체결에서 하루씩 밀린다.

    **전수를 돌려준다.** 자르는 것은 호출부의 일이다 — 여기서 자르면 건수·
    실현손익 합계가 잘린 뒤의 것이 되어 조용히 틀린다.

    큰 것부터 세운다 — 상한에 걸려 잘릴 때 사라져야 할 것은 작은 쪽이다.
    """
    frame = store.get(ledger.TRADES, as_of=as_of, lookback=10)
    if frame.empty:
        return []
    frame = frame[frame["valid_from"].map(_kst_date) == session]
    if frame.empty:
        return []

    realized = realized_by_trade(store, as_of=as_of)
    entities = sorted({str(row) for row in frame["entity_id"]})
    labels = names_module.of(store, as_of=as_of, entities=entities)

    rows: list[Fill] = []
    for row in ledger._ordered(frame):
        entity = str(row["entity_id"])
        key = f"{str(row['order_id']).split('|')[0]}|{entity}"
        match = realized.get(key) or {}
        rows.append(
            Fill(
                entity_id=entity,
                name=labels.get(entity, entity),
                side=str(row["side"]),
                quantity=float(row["quantity"]),
                price=float(row["price"]),
                currency=str(row["currency"]),
                fee=float(row["fee"]),
                tax=float(row["tax"]),
                realized_pnl=match.get("realized_pnl"),
                realized_rate=match.get("realized_rate"),
            )
        )

    rows.sort(key=lambda fill: fill.amount, reverse=True)
    return rows


def daily(
    store: Store,
    *,
    as_of: datetime,
    snapshot: snapshot_module.Snapshot | None = None,
    fill_limit: int | None = MAIL_FILL_ROWS,
) -> Performance:
    """``as_of`` 시점에서 본 마지막 회계 세션의 성과.

    ## ``snapshot`` 을 왜 받나

    화면은 요청 시각에 장부를 **다시 접는다**(``dashboard`` 의 ``build_context``)
    — 회계 크론(23:20)이 아직 안 돈 시각에도 오늘 값을 보여줘야 하기 때문이다.
    그때 이 함수가 창고의 ``nav_daily`` 만 읽으면 같은 화면에서 KPI 는 오늘을,
    성과 칸은 어제를 말한다. **이미 접어 둔 스냅샷을 넘기면 그것을 오늘로
    쓴다** — 같은 계산을 두 번 하지 않고, 두 숫자가 갈릴 자리도 없앤다.

    안 넘기면 창고의 마지막 ``nav_daily`` 행이 오늘이다. 메일(``reporting``)이
    그 경로다 — 메일은 확정된 종가 회계만 싣는다.

    어느 경로든 **어제는 ``ledger.previous_snapshot``** 이 고른다. 그 함수가
    ``valid_from < as_of`` 로 잘라서 "오늘을 다시 계산할 때 자기 자신이 어제로
    잡히는" 사고를 막는다.

    ``nav_daily`` 가 비어 있고 스냅샷도 없으면 숫자를 지어내지 않는다 —
    ``note`` 에 이유를 적고 전부 ``None`` 으로 돌려준다. 0 으로 채우면
    "손실 0" 으로 읽힌다.
    """
    from quant_rl_trading.store import mode as mode_module

    mode = mode_module.of(store.root)
    blank = {
        "mode": mode.code,
        "mode_note": mode.note,
        "store_root": str(store.root),
    }

    curve = store.get(ledger.NAV_DAILY, as_of=as_of, entity=ledger.ACCOUNT, lookback=None)
    if snapshot is None and curve.empty:
        return Performance(
            **blank,
            session=None,
            previous_session=None,
            since=None,
            nav=None,
            previous_nav=None,
            nav_change=None,
            inflow=None,
            pnl=None,
            daily_return=None,
            cumulative_return=None,
            index_value=None,
            drawdown=None,
            principal=None,
            total_pnl=None,
            fills=[],
            note="회계 스냅샷이 아직 없다 — 성과를 잴 수 없다",
        )

    if snapshot is not None:
        moment = snapshot.as_of
        nav = snapshot.valuation.nav
        inflow = snapshot.inflow
        daily_return = snapshot.twr_return
        index_value = snapshot.index_value
        drawdown = snapshot.drawdown
    else:
        ordered = curve.sort_values(["valid_from", "observed_at"])
        # 같은 세션의 정정본이 여러 행 있을 수 있다. 세션마다 마지막 관측만.
        last = ordered.groupby("valid_from", as_index=False).tail(1).iloc[-1].to_dict()
        moment = pd.Timestamp(last["valid_from"]).to_pydatetime()
        nav = float(last["nav"])
        inflow = float(last["inflow"] or 0.0)
        daily_return = None if pd.isna(last["twr_return"]) else float(last["twr_return"])
        index_value = None if pd.isna(last["index_value"]) else float(last["index_value"])
        drawdown = None if pd.isna(last["drawdown"]) else float(last["drawdown"])

    session = _kst_date(moment)
    previous = ledger.previous_snapshot(store, as_of=moment)
    previous_nav = float(previous["nav"]) if previous is not None else None
    previous_session = _kst_date(previous["valid_from"]) if previous is not None else None
    since = _kst_date(curve["valid_from"].min()) if not curve.empty else session

    nav_change = (nav - previous_nav) if previous_nav is not None else None
    pnl = (nav_change - inflow) if nav_change is not None else None
    # **어제가 없으면 누적수익률도 없다.** 첫날의 지수는 기준값 100 이라
    # 0.00% 가 나오는데, 그건 "안 벌었다" 가 아니라 "아직 잰 구간이 없다" 다.
    # KPI 스트립도 같은 규칙을 쓴다 (dashboard/services/trading.kpis).
    cumulative = (
        None
        if index_value is None or previous is None
        else index_value / BASE_INDEX - 1.0
    )

    # **원금은 입출금의 합이지 첫날 NAV 가 아니다** (accounting.md §6).
    # 환산 규칙은 ``ledger`` 한 곳에 있다 — 달러 입금을 1원으로 세면 원금이
    # 조용히 줄고 총 수익금이 그만큼 부풀어 오른다.
    principal = ledger.principal(store, as_of=as_of)

    all_fills = [] if session is None else fills(store, as_of=as_of, session=session)
    realized = [
        fill.realized_pnl
        for fill in all_fills
        if fill.realized_pnl is not None and fill.currency == KRW
    ]
    shown = all_fills if fill_limit is None else all_fills[:fill_limit]

    return Performance(
        **blank,
        session=session,
        previous_session=previous_session,
        since=since,
        nav=nav,
        previous_nav=previous_nav,
        nav_change=nav_change,
        inflow=inflow,
        pnl=pnl,
        daily_return=daily_return,
        cumulative_return=cumulative,
        index_value=index_value,
        drawdown=drawdown,
        principal=principal or None,
        total_pnl=(nav - principal) if principal > 0 else None,
        fills=shown,
        fills_omitted=len(all_fills) - len(shown),
        buy_count=sum(1 for fill in all_fills if fill.side == Side.BUY),
        sell_count=sum(1 for fill in all_fills if fill.side == Side.SELL),
        realized_pnl=sum(realized) if realized else None,
        # 첫날은 비교할 어제가 없다. 그건 못 잰 것이 아니라 **없는 것**이라
        # 숫자 자리를 None 으로 두고 이유를 적는다.
        note=(
            None
            if previous is not None
            else "첫 회계 세션 — 비교할 직전 스냅샷이 없다"
        ),
    )
