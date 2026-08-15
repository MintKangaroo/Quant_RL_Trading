"""브리핑 데이터 — 숫자를 모으는 계층. 문장은 여기서 만들지 않는다.

모든 조회는 ``store.get(table, as_of=...)`` 를 경유한다 (불변식 1). 마켓 탭이
이미 같은 숫자를 만들고 있으므로 **그 함수들을 그대로 쓴다** — 시세를 읽어
직전 세션 대비 등락을 내는 규칙이 두 벌이 되면, 언젠가 화면과 메일이 서로
다른 등락률을 말한다.

## 여기서 지키는 세 가지

1. **없는 것은 없다고 적는다.** 요청한 지수가 창고에 없으면 목록에서 빼지
   않는다. ``close=None`` 인 줄로 남기고 ``note`` 에 이유를 적는다. 빠진 줄은
   아무 말도 안 하지만, 빈칸에 이유가 붙으면 사람이 고칠 수 있다.

2. **변동성 지수는 가격지수와 갈린다.** VIX 가 +12% 인 것과 나스닥이 +12%
   인 것은 반대 사건이다. 같은 표에 두면 손익 색이 붙는다
   (``dashboard.services.market.VOLATILITY_INDICES`` 가 유일한 명단이다).

3. **상승 종목에는 거래대금·주가 하한이 걸린다.** 하한이 없으면 이 섹션은
   동전주 목록이 된다 — 1주 100원짜리가 30% 오른 것은 시황이 아니다.
   하한값은 ``store.config`` 에서 읽는다 (불변식 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.dashboard.services import market as market_service
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.reporting.sessions import SessionRef, describe, expected_session
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

DOCUMENTS = "documents"
MARKET_STATS = "market_stats"
MACRO_RELEASES = "macro_releases"

#: 브리핑이 싣는 지수. **창고의 entity_id 그대로다.**
#:
#: 마켓 탭은 그 시장의 지수를 전부 나열하지만(국장만 44종) 메일은 그럴 수
#: 없다 — Gmail 은 본문이 크면 잘라낸다 (reporting.md §3). 사람이 아침에
#: 한 번 훑는 목록은 이 정도가 상한이다.
#:
#: config 가 정한 대표 지수(``benchmark.kr_index``·``us_index``)는 여기 없어도
#: 자동으로 들어간다 — 대표를 화면이 고르면 안 되기 때문이다 (불변식 10).
REPORT_INDICES: dict[str, tuple[tuple[str, str], ...]] = {
    "KR": (
        ("KR:IDX:KOSPI", "코스피"),
        ("KR:IDX:KOSDAQ", "코스닥"),
    ),
    "US": (
        ("US:IDX:SP500", "S&P 500"),
        ("US:IDX:NASDAQ", "나스닥"),
        ("US:IDX:DJIA", "다우"),
        ("US:IDX:SOX", "필라델피아 반도체"),
        ("US:IDX:VIX", "VIX (S&P 변동성)"),
        ("US:IDX:VXN", "VXN (나스닥 변동성)"),
    ),
}

#: 지수 종가를 찾을 때 여는 창(일). 직전 세션 대비만 재면 되므로 짧다.
#: 연휴를 넘겨도 두 세션이 잡히도록 여유를 둔다.
INDEX_LOOKBACK_DAYS = 10

#: 공시를 찾을 때 여는 창(일). 하루치만 열면 주말·연휴 뒤에 아무것도 안 잡힌다.
FILING_LOOKBACK_DAYS = 3

#: 시세·시총 판을 만들 때 여는 창(일). 마지막 두 세션만 쓰지만, 창고에 구멍이
#: 있으면 두 세션이 열흘 안에 흩어져 있다 — 실제로 미장 시세가 08-12 에서
#: 멈춰 있는 동안 그랬다.
PANEL_LOOKBACK_DAYS = 12

#: 거시지표 발표를 찾을 때 여는 창(일). 일정은 몇 주 전에 공표되므로 넉넉히 연다.
MACRO_LOOKBACK_DAYS = 45

#: 리포트가 "이번 구간" 으로 치는 거래일 수. 금요일 리포트가 목·금 발표를
#: 함께 담는다 — 하루만 보면 전날 저녁 발표가 어느 리포트에도 안 실린다.
MACRO_WINDOW_SESSIONS = 2


@dataclass(frozen=True)
class IndexRow:
    entity_id: str
    label: str
    #: ``"price"`` | ``"volatility"``. 화면이 손익 색을 칠할지 가르는 유일한 표시.
    kind: str
    close: float | None
    change: float | None
    session: date | None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "label": self.label,
            "kind": self.kind,
            "close": self.close,
            "change": self.change,
            "session": self.session.isoformat() if self.session else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class Floor:
    """상승 종목에 적용한 하한. **메일에 그대로 적는다.**

    하한을 숨기면 "왜 저 종목이 없냐" 에 답할 수 없고, 답할 수 없는 필터는
    언젠가 아무도 못 믿는 필터가 된다.
    """

    currency: str
    min_turnover: float
    min_price: float
    pool: int
    #: 하한 위에 남은 종목 수. 0 이면 목록이 비는 이유가 이것이다.
    eligible: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "min_turnover": self.min_turnover,
            "min_price": self.min_price,
            "pool": self.pool,
            "eligible": self.eligible,
        }


@dataclass(frozen=True)
class MarketBrief:
    market: str
    currency: str
    index_session: SessionRef
    price_session: SessionRef
    prices: list[IndexRow]
    volatility: list[IndexRow]
    rankings: list[Ranking]
    floor: Floor
    news: NewsSection

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "currency": self.currency,
            "index_session": self.index_session.as_dict(),
            "price_session": self.price_session.as_dict(),
            "prices": [row.as_dict() for row in self.prices],
            "volatility": [row.as_dict() for row in self.volatility],
            "rankings": [rank.as_dict() for rank in self.rankings],
            "floor": self.floor.as_dict(),
            "news": self.news.as_dict(),
        }


@dataclass(frozen=True)
class Briefing:
    as_of: datetime
    fx: dict[str, Any]
    fx_note: str | None
    #: 거시지표는 시장별 칸에 넣지 않는다 — 미장 CPI 가 국장을 흔드는 것이
    #: 정상이라, 좌우로 가르면 그 사실이 안 보인다.
    macro: MacroSection
    markets: dict[str, MarketBrief]

    @property
    def notes(self) -> list[str]:
        """메일 상단에 모아 쓸 "없는 것" 목록. 비어 있으면 다 들어온 날이다.

        **대표 지수는 따로 올린다.** 시장의 세션 상태(``index_session``)는 그
        시장 지수 중 **가장 최신** 하나로 정해지는데, 실측으로 미장은 다우가
        08-14 까지, S&P 500 이 08-13 까지 들어와 있었다. 그러면 시장 단위로는
        "다 들어왔다" 가 되고, 정작 제목 줄에 쓰는 대표 지수가 하루 낡은 채로
        조용히 나간다. 대표가 낡은 것은 표 안쪽이 아니라 맨 위에서 말해야 한다.
        """
        out: list[str] = []
        for code, brief in self.markets.items():
            for ref in (brief.index_session, brief.price_session):
                if ref.note:
                    out.append(ref.note)
            headline = brief.prices[0] if brief.prices else None
            if headline is not None and headline.note and not brief.index_session.note:
                out.append(f"{code} {headline.label}: {headline.note}")
        if self.fx_note:
            out.append(self.fx_note)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "fx": self.fx,
            "fx_note": self.fx_note,
            "notes": self.notes,
            "macro": self.macro.as_dict(),
            "markets": {code: brief.as_dict() for code, brief in self.markets.items()},
        }


def _session_of(value: Any) -> date | None:
    stamp = pd.Timestamp(value)
    return None if pd.isna(stamp) else stamp.date()


def _wanted(market: str, headline: str) -> list[tuple[str, str]]:
    """이 시장에서 실을 지수 목록. config 대표 지수를 맨 앞에 세운다."""
    rows = list(REPORT_INDICES.get(market, ()))
    if headline and headline not in {entity for entity, _ in rows}:
        # 이름을 모르면 entity_id 를 그대로 쓴다. 대용치로 바꿔치기하지 않는다.
        rows.insert(0, (headline, headline.rsplit(":", 1)[-1]))
    return rows


@dataclass(frozen=True)
class _Quote:
    """창고에서 찾아낸 지수 한 종의 마지막 두 세션."""

    close: float
    change: float | None
    session: date | None
    #: 등락을 잰 기준 세션. **직전 거래일이 아닐 수 있다.**
    prior: date | None


def _quote_note(quote: _Quote, *, market: str, expected: date | None) -> str | None:
    """이 숫자를 곧이곧대로 읽으면 안 되는 이유. 없으면 ``None``.

    두 가지를 잡는다.

    1. **낡은 종가** — 창고의 마지막 세션이 기대 세션보다 이르다
    2. **여러 날치 등락** — 사이에 빠진 세션이 있으면 "하루 등락" 이 아니다.
       08-07 대비 08-13 을 +3.5% 라 적고 아무 말도 안 하면, 읽는 사람은 그것을
       하루 사이에 벌어진 일로 읽는다. 창고에 구멍이 있는 동안 실제로 그런
       줄이 나온다
    """
    if quote.session is None:
        return "종가가 없다"
    if quote.change is None:
        return "직전 세션 종가가 없어 등락을 못 잰다"

    notes: list[str] = []
    if expected is not None and quote.session < expected:
        # 괄호를 겹치지 않는다 — 이 문장이 다시 괄호 안에 들어가는 자리가
        # 있어서 "(08-13 종가 (08-14 미수집))" 가 됐다. 390px 에서 특히 길다.
        notes.append(f"{quote.session.isoformat()} 종가 · {expected.isoformat()} 미수집")
    if quote.prior is not None:
        # 달력이 말하는 세션 수. 둘을 포함해 2면 붙어 있는 두 거래일이다.
        span = len(trading_days(Market(market), quote.prior, quote.session))
        if span > 2:
            notes.append(
                f"{quote.prior.isoformat()} 대비 — 사이 {span - 2}개 세션이 창고에 없다"
            )
    return " · ".join(notes) if notes else None


def index_rows(
    store: Store, *, as_of: datetime, market: str, headline: str, expected: date | None
) -> tuple[list[IndexRow], list[IndexRow], SessionRef]:
    """(가격지수, 변동성지수, 세션 상태).

    요청한 지수가 창고에 없어도 줄은 남는다 — 그게 "없는 것은 없다고 적는다" 다.
    """
    wanted = _wanted(market, headline)
    entities = [entity for entity, _ in wanted]
    frame = store.get(
        market_service.INDICES,
        as_of=as_of,
        lookback=INDEX_LOOKBACK_DAYS,
        market=market,
        entity=entities,
        columns=["entity_id", "close", "valid_from"],
    )

    latest: dict[str, _Quote] = {}
    observed: date | None = None
    if not frame.empty:
        for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
            # 휴장·미수집 세션은 종가가 0 이나 NaN 으로 들어온다. 섞으면 지수가
            # 하루 만에 -100% 로 보인다 (market_service.indices 와 같은 규칙).
            closes = group["close"].astype(float)
            live = group[closes > 0]
            if live.empty:
                continue
            values = live["close"].astype(float)
            last = float(values.iloc[-1])
            previous = float(values.iloc[-2]) if len(values) >= 2 else None
            session = _session_of(live["valid_from"].iloc[-1])
            prior = _session_of(live["valid_from"].iloc[-2]) if len(values) >= 2 else None
            latest[str(entity)] = _Quote(
                close=last,
                change=(last / previous - 1.0) if previous else None,
                session=session,
                prior=prior,
            )
            if session and (observed is None or session > observed):
                observed = session

    ref = describe(Market(market), "지수", expected=expected, observed=observed)

    prices: list[IndexRow] = []
    volatility: list[IndexRow] = []
    for entity, label in wanted:
        kind = (
            "volatility"
            if entity in market_service.VOLATILITY_INDICES
            else "price"
        )
        found = latest.get(entity)
        if found is None:
            row = IndexRow(
                entity_id=entity,
                label=label,
                kind=kind,
                close=None,
                change=None,
                session=None,
                note="창고에 이 지수가 없다",
            )
        else:
            row = IndexRow(
                entity_id=entity,
                label=label,
                kind=kind,
                close=found.close,
                change=found.change,
                session=found.session,
                note=_quote_note(found, market=market, expected=expected),
            )
        (volatility if kind == "volatility" else prices).append(row)
    return prices, volatility, ref


#: 순위표 2종. 키는 창고의 컬럼 이름, 값은 (제목, 정렬 기준 설명).
#:
#: **무엇으로 줄 세웠는지 표마다 적는다.** 어느 컬럼으로 줄 세웠는지 안 보이면
#: 읽는 사람이 무엇을 본 건지 모른다.
#:
#: **거래량(주식 수) 상위는 뺐다.** 주식 수로 세면 싼 주식이 늘 이긴다 —
#: 2026-08-12 미장에서 1·5위가 $1.52·$1.36 짜리였다. 그게 틀린 계산은
#: 아니지만 "시장이 어디로 움직였나" 라는 이 메일의 질문에는 답하지 못한다.
#: 같은 자리를 거래대금이 훨씬 잘 채운다(그날 MU·SPY·NVDA).
#:
#: **하한을 안 걸어서 생긴 일이 아니다.** 하한은 거래량 순위에도 걸려 있었고
#: ($1 · $5M) 저 둘은 그걸 통과했다. 하한을 올려서 풀 문제도 아니다 —
#: 주식 수로 세는 한 싼 쪽이 유리한 건 척도 자체의 성질이라, 하한은 바닥만
#: 잘라낼 뿐 순위의 기울기를 못 바꾼다. 그래서 표를 없앴다.
RANKINGS: tuple[tuple[str, str, str], ...] = (
    ("value", "거래대금 상위", "prices.value — 체결 금액"),
    ("market_cap", "시가총액 상위", "market_stats.market_cap"),
)


@dataclass(frozen=True)
class RankRow:
    entity_id: str
    name: str
    #: 줄 세운 값. 어느 컬럼인지는 ``Ranking.sort_by`` 가 안다.
    metric: float
    close: float | None
    #: 직전 거래일 대비. **못 재면 None 이고, 0.0 으로 채우지 않는다** —
    #: 0% 는 "보합" 이라는 다른 사실이다.
    change: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "metric": self.metric,
            "close": self.close,
            "change": self.change,
        }


@dataclass(frozen=True)
class Ranking:
    key: str
    label: str
    #: 무엇으로 줄 세웠는가. 표 아래 각주로 그대로 나간다.
    sort_by: str
    session: date | None
    prior: date | None
    rows: list[RankRow]
    #: 하한을 통과해 순위 경쟁에 들어간 종목 수.
    eligible: int
    #: 그 세션에 값이 있던 전체 종목 수. eligible 과의 차이가 하한이 걸러낸 양이다.
    universe: int
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "sort_by": self.sort_by,
            "session": self.session.isoformat() if self.session else None,
            "prior": self.prior.isoformat() if self.prior else None,
            "rows": [row.as_dict() for row in self.rows],
            "eligible": self.eligible,
            "universe": self.universe,
            "note": self.note,
        }


def _price_panel(
    store: Store, *, as_of: datetime, market: str
) -> tuple[pd.DataFrame, date | None, date | None]:
    """그 시장의 **마지막 두 세션** 시세. ``(표, 최신 세션, 직전 세션)``.

    표는 entity_id 색인에 ``close``·``volume``·``value``·``prev``·``change``.

    직전 종가가 없는 종목의 ``change`` 는 NaN 으로 남긴다. 0 으로 채우면
    "보합" 이라는 다른 사실이 되고, 그 거짓은 순위표에서 조용히 퍼진다.
    """
    frame = read_prices(
        store,
        as_of=as_of,
        lookback=PANEL_LOOKBACK_DAYS,
        market=market,
        columns=["entity_id", "close", "volume", "value", "valid_from"],
    )
    empty = pd.DataFrame(columns=["close", "volume", "value", "prev", "change"])
    if frame.empty:
        return empty, None, None

    frame = frame.copy()
    frame["session"] = [_session_of(v) for v in frame["valid_from"]]
    sessions = sorted({s for s in frame["session"] if s is not None})
    if not sessions:
        return empty, None, None
    session = sessions[-1]
    prior = sessions[-2] if len(sessions) >= 2 else None

    last = (
        frame[frame["session"] == session]
        .drop_duplicates("entity_id", keep="last")
        .set_index("entity_id")
    )
    panel = pd.DataFrame(index=last.index)
    panel["close"] = last["close"].astype(float)
    panel["volume"] = last["volume"].astype(float)
    panel["value"] = last["value"].astype(float)

    if prior is None:
        panel["prev"] = float("nan")
    else:
        before = (
            frame[frame["session"] == prior]
            .drop_duplicates("entity_id", keep="last")
            .set_index("entity_id")
        )
        panel["prev"] = before["close"].astype(float)
    panel["change"] = panel["close"] / panel["prev"] - 1.0
    return panel, session, prior


def _liquid(panel: pd.DataFrame, floor: Floor) -> pd.DataFrame:
    """하한을 통과한 종목만.

    **거래량 순위에도 하한이 필요하다.** 100원짜리가 거래량 1위인 것은 시황이
    아니라 액면가의 부작용이다 — 같은 돈으로 주식 수가 100배 잡히기 때문이다.
    그래서 거래량 순위에도 거래대금·주가 하한을 똑같이 건다.
    """
    if panel.empty:
        return panel
    return panel[
        (panel["value"] >= floor.min_turnover) & (panel["close"] >= floor.min_price)
    ]


def _rank_rows(
    frame: pd.DataFrame, column: str, names: dict[str, str], limit: int
) -> list[RankRow]:
    top = frame.sort_values(column, ascending=False).head(limit)
    rows = []
    for entity, row in top.iterrows():
        key = str(entity)
        change = row.get("change")
        close = row.get("close")
        rows.append(
            RankRow(
                entity_id=key,
                name=names.get(key, key),
                metric=float(row[column]),
                close=None if close is None or pd.isna(close) else float(close),
                change=None if change is None or pd.isna(change) else float(change),
            )
        )
    return rows


def market_caps(
    store: Store, *, as_of: datetime, market: str
) -> tuple[pd.Series, date | None]:
    """종목별 최신 시가총액과 그 세션.

    ``until=as_of`` 를 반드시 준다. ``lookback`` 은 valid_from 의 **하한**만
    자르므로, 표지 날짜가 미래인 행이 있으면 그것이 "최신" 으로 뽑힌다.
    실제로 창고의 미장 ``shares`` 에 2028-08-01 짜리 행이 있다 — 그 옆에
    사는 테이블을 창 없이 읽으면 2년 뒤 값으로 순위를 매기게 된다.
    """
    frame = store.get(
        MARKET_STATS,
        as_of=as_of,
        lookback=PANEL_LOOKBACK_DAYS,
        until=as_of,
        market=market,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    if frame.empty:
        return pd.Series(dtype=float), None
    caps = frame[frame["metric"] == "market_cap"]
    if caps.empty:
        return pd.Series(dtype=float), None
    caps = caps.copy()
    caps["session"] = [_session_of(v) for v in caps["valid_from"]]
    sessions = sorted({s for s in caps["session"] if s is not None})
    if not sessions:
        return pd.Series(dtype=float), None
    session = sessions[-1]
    latest = caps[caps["session"] == session].drop_duplicates("entity_id", keep="last")
    return latest.set_index("entity_id")["value"].astype(float), session


def rankings(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    floor: Floor,
    expected: date | None,
    limit: int,
) -> tuple[list[Ranking], Floor, pd.DataFrame]:
    """순위 3종. ``(순위표들, 통과 수가 채워진 하한, 시세 판)``.

    시세는 **한 번만 읽는다** — 세 표가 같은 판을 나눠 쓴다.
    """
    panel, session, prior = _price_panel(store, as_of=as_of, market=market)
    liquid = _liquid(panel, floor)
    filled = Floor(
        currency=floor.currency,
        min_turnover=floor.min_turnover,
        min_price=floor.min_price,
        pool=floor.pool,
        eligible=len(liquid),
    )

    caps, cap_session = market_caps(store, as_of=as_of, market=market)
    entities = set()
    for column, source in (("volume", liquid), ("value", liquid)):
        if not source.empty:
            entities |= {
                str(e) for e in source.sort_values(column, ascending=False).head(limit).index
            }
    if not caps.empty:
        entities |= {str(e) for e in caps.sort_values(ascending=False).head(limit).index}
    names = market_service.entity_names(store, as_of=as_of, entities=sorted(entities))

    def stale(kind: str, when: date | None) -> str | None:
        if when is None:
            return f"{kind}가 창고에 없다"
        if expected is not None and when < expected:
            missing = len(trading_days(Market(market), when, expected)) - 1
            return f"{when.isoformat()} 기준 — {expected.isoformat()} 까지 {missing}개 세션 미수집"
        return None

    out: list[Ranking] = []
    for key, label, sort_by in RANKINGS:
        if key == "market_cap":
            if caps.empty:
                out.append(
                    Ranking(key, label, sort_by, None, None, [], 0, 0, stale("시가총액", None))
                )
                continue
            frame = pd.DataFrame({"market_cap": caps})
            frame = frame.join(panel[["close", "change"]], how="left")
            rows = _rank_rows(frame, "market_cap", names, limit)
            out.append(
                Ranking(
                    key=key,
                    label=label,
                    sort_by=sort_by,
                    session=cap_session,
                    prior=prior,
                    rows=rows,
                    eligible=len(frame),
                    universe=len(panel),
                    note=stale("시가총액", cap_session),
                )
            )
            continue
        rows = _rank_rows(liquid, key, names, limit) if not liquid.empty else []
        out.append(
            Ranking(
                key=key,
                label=label,
                sort_by=sort_by,
                session=session,
                prior=prior,
                rows=rows,
                eligible=len(liquid),
                universe=len(panel),
                note=stale("시세", session),
            )
        )
    return out, filled, panel


@dataclass(frozen=True)
class MacroRow:
    market: str
    label: str
    actual: float
    previous: float | None
    unit: str
    #: **발표 시각.** ``valid_from``(우리가 안 시각)이 아니다.
    released_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "label": self.label,
            "actual": self.actual,
            "previous": self.previous,
            "unit": self.unit,
            "released_at": self.released_at.isoformat(),
        }


@dataclass(frozen=True)
class MacroSection:
    released: list[MacroRow]
    upcoming: list[MacroRow]
    #: 시장별 한 줄. 그 시장에서 발표가 없었으면 이유가 여기 있다.
    notes: list[str]
    since: date | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "released": [row.as_dict() for row in self.released],
            "upcoming": [row.as_dict() for row in self.upcoming],
            "notes": self.notes,
            "since": self.since.isoformat() if self.since else None,
        }


def macro_section(
    store: Store, *, as_of: datetime, sessions: dict[str, date | None], limit: int
) -> MacroSection:
    """그 구간에 **실제로 발표된** 거시지표. 예정은 따로 담는다.

    ## scheduled 와 released 를 섞지 않는다

    이 테이블은 일정이 먼저 들어오고(``actual`` 없음) 발표 후 실측값이 같은
    자연키로 덮는다. **``actual`` 이 없는 것은 발표된 것이 아니다.** 아직 안
    나온 지표를 나온 것처럼 적는 것이 이 섹션이 할 수 있는 가장 나쁜 거짓말이다.

    ## 컨센서스는 없다

    우리는 시장 예상치를 수집하지 않는다. 그래서 "예상 대비 서프라이즈" 를
    쓸 수 없고, **직전값 대비만** 적는다. 없는 열을 만들지 않는다.

    ## 시각은 scheduled_at 이다

    ``valid_from`` 은 우리가 그 사실을 안 시각이고 발표 시각은 ``scheduled_at``
    이다. 헷갈리면 새벽에 나온 지표가 엉뚱한 시각으로 찍힌다.
    """
    frame = store.get(
        MACRO_RELEASES,
        as_of=as_of,
        lookback=MACRO_LOOKBACK_DAYS,
        columns=[
            "entity_id", "market", "indicator", "release_name", "scheduled_at",
            "actual", "previous", "unit", "status", "observed_at",
        ],
    )
    # 구간의 시작 — 가장 이른 시장 세션에서 MACRO_WINDOW_SESSIONS 만큼 되돌린다.
    since: date | None = None
    for code, day in sessions.items():
        if day is None:
            continue
        window = trading_days(
            Market(code), day - timedelta(days=14), day
        )[-MACRO_WINDOW_SESSIONS:]
        start = window[0] if window else day
        since = start if since is None else min(since, start)

    if frame.empty:
        return MacroSection([], [], ["거시지표 테이블이 비어 있다"], since)

    # 같은 자연키의 최신 관측만. 정정본은 새 행으로 쌓인다.
    frame = frame.sort_values("observed_at").groupby(
        ["entity_id", "scheduled_at"], as_index=False
    ).last()

    def row_of(record: dict[str, Any]) -> MacroRow:
        previous = record.get("previous")
        return MacroRow(
            market=str(record.get("market") or ""),
            label=str(record.get("release_name") or record.get("indicator") or ""),
            actual=float(record["actual"]) if pd.notna(record.get("actual")) else 0.0,
            previous=None if previous is None or pd.isna(previous) else float(previous),
            unit=str(record.get("unit") or ""),
            released_at=record["scheduled_at"].to_pydatetime(),
        )

    # **actual 이 있는 것만 발표다.** status 만 믿지 않는다 — 둘 다 본다.
    done = frame[
        (frame["status"] == "released")
        & frame["actual"].notna()
        & (frame["scheduled_at"] <= as_of)
    ]
    if since is not None:
        edge = pd.Timestamp(since, tz="UTC")
        done = done[done["scheduled_at"] >= edge]
    ordered = done.sort_values("scheduled_at", ascending=False)
    released = [row_of(r) for r in ordered.to_dict(orient="records")]

    ahead = frame[(frame["status"] == "scheduled") & (frame["scheduled_at"] > as_of)]
    upcoming = [
        row_of(r) for r in ahead.sort_values("scheduled_at").head(limit).to_dict(orient="records")
    ]

    notes: list[str] = []
    for code in market_service.MARKETS:
        if not any(row.market == code for row in released):
            where = "국내" if code == "KR" else "미국"
            notes.append(f"{where} 지표: 이 구간에 발표된 것이 없다")
    return MacroSection(released[:limit], upcoming, notes, since)


#: 뉴스 선별 기준. **규칙이지 LLM 이 아니다** — 재현 가능해야 하기 때문이다.
#: 같은 as_of 로 두 번 만들면 같은 목록이 나오고, "왜 이건 있고 저건 없나" 에
#: 코드를 읽지 않고도 답할 수 있다.
NEWS_ALWAYS = ("distress",)
NEWS_IF_MAJOR = ("earnings", "dilution", "buyback", "split", "contract")

#: "주요 종목" 의 정의 — 그 세션 거래대금 상위 이만큼.
NEWS_MAJOR_POOL = 100


@dataclass(frozen=True)
class NewsRow:
    entity_id: str
    name: str
    doc_type: str
    title: str
    url: str
    filed_on: date | None
    #: 왜 뽑혔는가. 표에 그대로 나간다.
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "doc_type": self.doc_type,
            "title": self.title,
            "url": self.url,
            "filed_on": self.filed_on.isoformat() if self.filed_on else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class NewsSection:
    rows: list[NewsRow]
    #: 창고에 있던 전체 건수. 몇 건에서 몇 건을 골랐는지 밝히는 데 쓴다.
    total: int
    criteria: str
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.as_dict() for row in self.rows],
            "total": self.total,
            "criteria": self.criteria,
            "note": self.note,
        }


def news_section(
    store: Store,
    panel: pd.DataFrame,
    *,
    as_of: datetime,
    market: str,
    limit: int,
) -> NewsSection:
    """공시·뉴스에서 **중요한 것만**. 규칙으로 고르고, 규칙을 밝힌다.

    하루 공시가 수천 건이다(2026-08-14 국장 2,990건). 전부 실으면 메일이
    잘리고 아무도 안 읽는다. 자르는 것 자체는 피할 수 없으므로, **자른
    사실과 기준을 함께 싣는다.**

    두 갈래로 고른다.

    1. ``distress`` — 상장폐지·거래정지·관리종목·회생. **규모와 무관하게**
       중요하다. 작은 회사의 거래정지가 큰 회사의 배당 공시보다 급하다
    2. 그 밖의 사건성 공시(실적·증자·자사주·분할·계약)는 **그날 거래대금 상위
       종목의 것만**. 규모 기준을 여기에만 거는 이유가 1번이다

    **LLM 을 쓰지 않는다.** 규칙이라 같은 as_of 면 같은 목록이 나오고, 요약이
    사실을 덧칠할 여지가 없다. 제목·공시일·종목·URL 을 그대로 옮긴다.
    """
    frame = store.get(
        DOCUMENTS,
        as_of=as_of,
        lookback=FILING_LOOKBACK_DAYS,
        columns=["entity_id", "doc_id", "doc_type", "title", "url", "valid_from"],
    )
    criteria = (
        f"distress(관리·정지·회생)는 전부 · 그 밖은 거래대금 상위 "
        f"{NEWS_MAJOR_POOL}위 종목의 실적·증자·자사주·분할·계약만"
    )
    if frame.empty:
        return NewsSection([], 0, criteria, "공시 테이블이 비어 있다")

    mine = frame[frame["entity_id"].astype(str).str.startswith(f"{market}:")]
    total = len(mine)
    if mine.empty:
        return NewsSection([], 0, criteria, "이 시장 공시가 창고에 없다")

    major: set[str] = set()
    if not panel.empty:
        major = {
            str(e)
            for e in panel.sort_values("value", ascending=False).head(NEWS_MAJOR_POOL).index
        }

    mine = mine.sort_values("valid_from", ascending=False).drop_duplicates("doc_id")
    picked: list[dict[str, Any]] = []
    for record in mine.to_dict(orient="records"):
        kind = str(record.get("doc_type") or "")
        entity = str(record.get("entity_id") or "")
        if kind in NEWS_ALWAYS:
            picked.append({**record, "reason": "관리·정지·회생"})
        elif kind in NEWS_IF_MAJOR and entity in major:
            picked.append({**record, "reason": "거래대금 상위 종목"})

    names = market_service.entity_names(
        store, as_of=as_of, entities=sorted({str(r["entity_id"]) for r in picked[:limit]})
    )
    rows = [
        NewsRow(
            entity_id=str(record["entity_id"]),
            name=names.get(str(record["entity_id"]), str(record["entity_id"])),
            doc_type=str(record.get("doc_type") or ""),
            # DART 제목에는 정렬용 공백이 길게 들어 있다. 메일에서 줄바꿈을 망친다.
            title=" ".join(str(record.get("title") or "").split()),
            url=str(record.get("url") or ""),
            filed_on=_session_of(record["valid_from"]),
            reason=str(record["reason"]),
        )
        for record in picked[:limit]
    ]
    note = None if rows else "기준에 걸린 공시가 없다"
    return NewsSection(rows, total, criteria, note)


def market_brief(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    headline: str,
    expected: date | None,
    settings: dict[str, Any],
) -> MarketBrief:
    """한 시장의 브리핑 한 칸. KR·US 가 같은 함수, 같은 모양이다.

    시세는 ``rankings`` 안에서 **한 번만** 읽고, 그 판을 뉴스 선별(거래대금
    상위 종목 판정)이 이어 쓴다.
    """
    prices, volatility, index_ref = index_rows(
        store, as_of=as_of, market=market, headline=headline, expected=expected
    )
    currency = market_service.CURRENCY.get(market, "")
    suffix = currency.lower()
    floor = Floor(
        currency=currency,
        min_turnover=float(settings[f"min_turnover_{suffix}"]),
        min_price=float(settings[f"min_price_{suffix}"]),
        pool=int(settings["gainer_pool"]),
    )
    ranks, filled, panel = rankings(
        store,
        as_of=as_of,
        market=market,
        floor=floor,
        expected=expected,
        limit=int(settings["ranking_rows"]),
    )
    observed = next((rank.session for rank in ranks if rank.key == "value"), None)
    price_ref = describe(Market(market), "시세", expected=expected, observed=observed)
    return MarketBrief(
        market=market,
        currency=currency,
        index_session=index_ref,
        price_session=price_ref,
        prices=prices,
        volatility=volatility,
        rankings=ranks,
        floor=filled,
        news=news_section(
            store,
            panel,
            as_of=as_of,
            market=market,
            limit=int(settings["filings_rows"]),
        ),
    )


def build_briefing(
    store: Store, *, as_of: datetime, clock: Clock | None = None
) -> Briefing:
    """``as_of`` 시점의 시황 브리핑.

    같은 ``as_of`` 로 두 번 부르면 같은 것이 나온다 — 벽시계를 읽는 곳이
    한 군데도 없기 때문이다 (불변식 2). 그것이 리포트의 결정론 테스트다
    (reporting.md §5).
    """
    settings = store.config("reporting", as_of=as_of)
    benchmark = store.config("benchmark", as_of=as_of)
    expected = {
        code: expected_session(store, Market(code), as_of=as_of, clock=clock)
        for code in market_service.MARKETS
    }

    rate = market_service.fx(store, as_of=as_of, lookback=INDEX_LOOKBACK_DAYS)
    fx_note = None
    if rate["rate"] is None:
        fx_note = f"환율: 최근 {INDEX_LOOKBACK_DAYS}일 안에 USD/KRW 가 없다"
    elif rate["sessions"]:
        last = date.fromisoformat(rate["sessions"][-1])
        want = expected["KR"]
        if want is not None and last < want:
            missing = len(trading_days(Market.KR, last, want)) - 1
            fx_note = (
                f"환율: 창고가 {last.isoformat()} 까지다 "
                f"({want.isoformat()} 까지 {missing}개 국장 세션 미수집)"
            )

    return Briefing(
        as_of=as_of,
        fx=rate,
        fx_note=fx_note,
        macro=macro_section(
            store, as_of=as_of, sessions=expected, limit=int(settings["macro_rows"])
        ),
        markets={
            code: market_brief(
                store,
                as_of=as_of,
                market=code,
                headline=str(benchmark[market_service.BENCHMARK_KEY[code]]),
                expected=expected[code],
                settings=settings,
            )
            for code in market_service.MARKETS
        },
    )
