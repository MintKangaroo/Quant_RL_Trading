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

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.dashboard.services import market as market_service
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.reporting.sessions import (
    MISSING,
    UNPUBLISHED,
    Gap,
    SessionRef,
    describe,
    expected_session,
    fx_source_latest,
)
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

#: 지수 **대용 ETF**. FRED 지수를 대체하지 않고 **옆에 세운다.**
#:
#: ## 왜 필요한가
#:
#: FRED 는 하루 늦다. 실측 2026-08-19 08:55 KST(뉴욕 마감 4시간 뒤) 에
#: SP500·NASDAQ·NASDAQ100·SOX·VIX·VXN 이 08-17 에 멈춰 있었고 다우 계열
#: (DJIA·DJTA·DJUA)만 08-18 이었다. 같은 시각 LS 해외는 네 ETF 모두 08-18 을
#: 줬다. 이 메일은 미장 마감 뒤 새벽에 나가므로, 지수만 실으면 **읽는 사람이
#: 방금 끝난 장을 못 본다.**
#:
#: ## 이름을 바꿔치기하지 않는다
#:
#: 라벨에 **ETF 라고 적는다.** "S&P 500" 이라 쓰고 SPY 를 실으면 그 순간
#: 메일이 거짓말을 시작한다 — SPY 종가 767 은 S&P 500 의 7,745 가 아니고,
#: 분배락·운용보수·추적오차만큼 등락도 어긋난다. 마켓 탭이 같은 규칙을 쓴다
#: (``dashboard/services/market.py`` 의 ``US_ETF_PANELS``).
#:
#: 창고 위치도 다르다 — 이쪽은 ``indices`` 가 아니라 ``prices`` 다. 그래서
#: 읽는 함수가 따로 있고, 화면에서도 다른 묶음으로 나간다.
REPORT_PROXY_ETFS: dict[str, tuple[tuple[str, str], ...]] = {
    "US": (
        ("US:SPY", "SPY (S&P 500 추종 ETF)"),
        ("US:QQQ", "QQQ (나스닥 100 추종 ETF)"),
        ("US:DIA", "DIA (다우 추종 ETF)"),
        ("US:SOXX", "SOXX (ICE 반도체 추종 ETF)"),
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


#: 메일에 시장이 나오는 순서. **여기 하나만 고치면 전부 바뀐다.**
#:
#: 순서를 바꿔야 할 때 같은 튜플이 대여섯 군데 흩어져 있으면 반드시 한 곳을
#: 빠뜨린다 — 이 저장소가 반복해서 겪은 결함 계열이다. 섹션·헤드라인·결측
#: 목록·텍스트 대체본이 전부 이 하나를 읽는다.
#:
#: 미장이 앞이다. 이 메일은 **미장 마감 뒤 새벽에 나가서**, 읽는 시점에
#: 방금 끝난 장은 미장이고 국장은 아직 열리지 않았다.
MARKET_ORDER = ("US", "KR")


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
    #: 지수 **대용 ETF**. 지수를 대체하는 것이 아니라 옆에 서는 줄이다 —
    #: 출처(LS 해외)도 창고 표(``prices``)도 위의 ``prices``(FRED·``indices``)와
    #: 다르므로 화면이 둘을 갈라서 보여야 한다.
    #:
    #: 기본값이 빈 목록인 이유는 국장이다 — 국장은 KRX 가 당일 지수를 주므로
    #: 대용치가 없고, 그 자리를 억지로 채우지 않는다.
    proxies: list[IndexRow] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "currency": self.currency,
            "index_session": self.index_session.as_dict(),
            "price_session": self.price_session.as_dict(),
            "prices": [row.as_dict() for row in self.prices],
            "volatility": [row.as_dict() for row in self.volatility],
            "proxies": [row.as_dict() for row in self.proxies],
            "rankings": [rank.as_dict() for rank in self.rankings],
            "floor": self.floor.as_dict(),
            "news": self.news.as_dict(),
        }


@dataclass(frozen=True)
class Briefing:
    as_of: datetime
    fx: dict[str, Any]
    fx_note: str | None
    #: ``fx_note`` 가 두 성격 중 어느 쪽인지. ``None`` 이면 ``fx_note`` 도 없다.
    #: 우리가 못 받은 것(``sessions.MISSING``)과 원본이 아직 안 낸 것
    #: (``sessions.UNPUBLISHED``)은 다른 사실이다 — 후자를 "미수집" 이라
    #: 적으면 매주 없는 결함을 찾게 된다 (실제로 환율이 그랬다).
    fx_gap_kind: str | None
    #: 거시지표는 시장별 칸에 넣지 않는다 — 미장 CPI 가 국장을 흔드는 것이
    #: 정상이라, 좌우로 가르면 그 사실이 안 보인다.
    macro: MacroSection
    markets: dict[str, MarketBrief]

    @property
    def gaps(self) -> list[Gap]:
        """메일 상단에 모아 쓸 "없는 것" 목록. 비어 있으면 다 들어온 날이다.

        **대표 지수는 따로 올린다.** 시장의 세션 상태(``index_session``)는 그
        시장 지수 중 **가장 최신** 하나로 정해지는데, 실측으로 미장은 다우가
        08-14 까지, S&P 500 이 08-13 까지 들어와 있었다. 그러면 시장 단위로는
        "다 들어왔다" 가 되고, 정작 제목 줄에 쓰는 대표 지수가 하루 낡은 채로
        조용히 나간다. 대표가 낡은 것은 표 안쪽이 아니라 맨 위에서 말해야 한다.

        **지수·시세 결측은 늘 ``MISSING``.** 그 시장이 이미 마감·공표된 세션을
        우리 수집기가 못 받은 것이라 늘 고칠 수 있다. 환율만 원본이 늦게
        낼 수 있어 ``fx_gap_kind`` 를 따로 판정한다.
        """
        out: list[Gap] = []
        # 결측 목록도 섹션과 같은 순서다 — 위에서 본 순서와 아래 목록의
        # 순서가 다르면 둘을 짝지어 읽을 수 없다.
        ordered = [code for code in MARKET_ORDER if code in self.markets]
        ordered += [code for code in self.markets if code not in MARKET_ORDER]
        for code in ordered:
            brief = self.markets[code]
            for ref in (brief.index_session, brief.price_session):
                if ref.note:
                    out.append(Gap(MISSING, ref.note))
            headline = brief.prices[0] if brief.prices else None
            if headline is not None and headline.note and not brief.index_session.note:
                out.append(Gap(MISSING, f"{code} {headline.label}: {headline.note}"))
        if self.fx_note:
            out.append(Gap(self.fx_gap_kind or MISSING, self.fx_note))
        return out

    @property
    def notes(self) -> list[str]:
        """``gaps`` 의 문장만. 문구 비교로 어긋난 것을 잡던 옛 호출부 호환용."""
        return [gap.text for gap in self.gaps]

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "fx": self.fx,
            "fx_note": self.fx_note,
            "fx_gap_kind": self.fx_gap_kind,
            "notes": self.notes,
            "gaps": [gap.as_dict() for gap in self.gaps],
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


def _quotes(frame: pd.DataFrame) -> tuple[dict[str, _Quote], date | None]:
    """(entity_id → 마지막 두 세션, 그중 가장 최신 세션).

    지수(``indices``)와 대용 ETF(``prices``)가 **같은 규칙을 쓰게** 하려고
    뽑아 뒀다. 두 곳이 종가 0 을 다르게 다루면 같은 메일 안에서 한쪽만
    -100% 로 보인다.
    """
    latest: dict[str, _Quote] = {}
    observed: date | None = None
    if frame.empty:
        return latest, observed
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
    return latest, observed


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

    latest, observed = _quotes(frame)
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


def proxy_rows(
    store: Store, *, as_of: datetime, market: str, expected: date | None
) -> list[IndexRow]:
    """지수 대용 ETF 줄. **지수 목록에 섞지 않고 따로 돌려준다.**

    창고 위치가 다르다 — ``indices`` 가 아니라 ``prices`` 다(ETF 는 종목이다).
    화면도 따로 묶어야 한다: 같은 표에 넣으면 "S&P 500 7,745" 와 "SPY 767" 이
    나란히 서서 둘 중 하나가 틀린 것처럼 보인다. 다른 것이지 틀린 것이 아니다.

    ``REPORT_PROXY_ETFS`` 에 없는 시장(국장)은 빈 목록이다 — 국장은 KRX 가
    당일 지수를 주므로 대용치가 필요 없다.
    """
    wanted = REPORT_PROXY_ETFS.get(market, ())
    if not wanted:
        return []

    frame = read_prices(
        store,
        as_of=as_of,
        market=market,
        entity=[entity for entity, _ in wanted],
        lookback=INDEX_LOOKBACK_DAYS,
        columns=["entity_id", "close", "valid_from"],
    )
    latest, _ = _quotes(frame)

    rows: list[IndexRow] = []
    for entity, label in wanted:
        found = latest.get(entity)
        if found is None:
            rows.append(
                IndexRow(
                    entity_id=entity,
                    label=label,
                    kind="price",
                    close=None,
                    change=None,
                    session=None,
                    note="창고에 이 ETF 가 없다",
                )
            )
            continue
        rows.append(
            IndexRow(
                entity_id=entity,
                label=label,
                kind="price",
                close=found.close,
                change=found.change,
                session=found.session,
                note=_quote_note(found, market=market, expected=expected),
            )
        )
    return rows


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
    #: 그 세션에 창고가 값을 아는 전체 종목 수. eligible 과의 차이가 걸러진 양이다.
    #:
    #: **언제나 ``eligible`` 이상이어야 한다.** 이 둘이 다른 모집단에서 오면
    #: 비율이 1을 넘고, 그 위에 얹은 "커버리지 xx%" 가 통째로 거짓이 된다
    #: (실측 사고: 43450%). ``rankings`` 의 시가총액 분기 주석 참고.
    universe: int
    note: str | None = None
    #: ``rows[].change`` 를 잰 세션. **``session`` 과 다를 수 있다.**
    #:
    #: 시가총액 순위가 그렇다. 순위는 ``market_stats`` 의 시총으로 매기는데
    #: 그 테이블이 ``prices`` 보다 늦게 들어와서, 실측 2026-08-18 기준으로
    #: 순위는 08-11 시총이고 등락률은 08-14 시세다. **사흘이 한 줄 안에
    #: 섞여 있고, 읽는 사람은 둘 다 08-11 로 읽는다.**
    #:
    #: 값을 맞추지 않고(더 오래된 등락으로 되돌리면 정보가 준다) **어느
    #: 시점의 것인지 말한다.** 화면이 그 일을 한다 — ``render._ranking_rows``.
    change_session: date | None = None

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
            "change_session": (
                self.change_session.isoformat() if self.change_session else None
            ),
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
                    # **분모는 분자를 담는 모집단이어야 한다.** 여기 ``len(panel)``
                    # 을 쓰던 때가 있었다 — 시세 판과 시총 판은 **다른 모집단**이라
                    # 비율이 1을 넘을 수 있고, 실제로 넘었다. 2026-08-18 실측:
                    # 미장 시세가 밀려 panel 이 15행인데 caps 는 4,345행이라
                    # 메일에 **커버리지 43450%** 가 찍혀 나갔다.
                    #
                    # 합집합 = 그 세션에 창고가 **무엇이든 아는** 종목 수다.
                    # caps 는 언제나 그 부분집합이라 비율이 구조적으로 1 이하다.
                    # 정상 데이터에서는 시총을 모르는 종목만 분모에 더해지므로
                    # 원래 뜻(시총을 아는 비율) 그대로다 — 실측 4,345/6,647 = 65%.
                    universe=len(panel.index.union(caps.index)),
                    note=stale("시가총액", cap_session),
                    # 등락률은 시총이 아니라 **시세 판**에서 왔다. 두 날짜가
                    # 다를 수 있다는 사실을 여기서 잃어버리면 화면이 못 되찾는다.
                    change_session=session,
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
                change_session=session,
            )
        )
    return out, filled, panel


#: 지표 한글 이름. 키는 ``entity_id``(= ``market:indicator``) 로, 창고의
#: 자연키 그대로다.
#:
#: **원제를 버리지 않는다.** 한글 이름만 남기면 그 숫자가 어느 발표에서 온
#: 것인지 확인할 길이 사라진다 — 메일은 원제를 부제로 함께 싣는다.
#: 여기 없는 지표는 원제를 그대로 쓴다. 창고에 새 지표가 생겨도 조용히
#: 빠지지 않고 영문으로라도 나간다.
MACRO_LABELS: dict[str, str] = {
    "KR:BASE_RATE": "한국은행 기준금리",
    "KR:CPI": "소비자물가",
    "KR:GDP": "GDP 성장률",
    "KR:PPI": "생산자물가",
    "KR:TREASURY_3Y": "국고채 3년",
    "KR:UNEMPLOYMENT": "실업률",
    "US:CPI": "소비자물가",
    "US:EMPLOYMENT": "실업률",
    "US:EMPLOYMENT_COST": "고용비용지수",
    "US:FED_FUNDS": "연방기금금리",
    "US:GDP": "GDP",
    "US:HOUSING_STARTS": "주택착공",
    "US:INDUSTRIAL_PRODUCTION": "산업생산",
    "US:JOBLESS_CLAIMS": "신규 실업수당 청구",
    "US:JOLTS": "구인건수",
    "US:PCE": "개인소비지출",
    "US:PPI": "생산자물가",
    "US:RETAIL_ADVANCE": "소매판매 (속보)",
    "US:RETAIL_SALES": "소매판매",
    "US:TRADE_BALANCE": "무역수지",
}

#: **값 자체가 퍼센트인 단위.** 이런 지표는 변화를 %가 아니라 %p 로 잰다.
#:
#: 금리가 3.50 에서 3.63 으로 올랐을 때 "+3.7%" 라고 쓰면 금리가 3.7% 올랐다는
#: 뜻으로 읽힌다. 실제로는 **0.13%p** 다. 둘은 완전히 다른 크기이고, 금리·
#: 실업률처럼 정책이 걸린 숫자에서 이 혼동은 그냥 오보다.
PERCENT_UNITS = frozenset({"%", "percent", "연%"})

#: 같은 값을 두 번 싣지 않을 때, 남길 쪽을 먼저 적는다.
#: 소매판매는 속보치(ADVANCE)와 확정치가 같은 숫자를 각각 발표한다 —
#: 시장이 보고 움직이는 것은 속보치라 그쪽을 남긴다.
MACRO_PREFERRED = ("US:RETAIL_ADVANCE",)


@dataclass(frozen=True)
class MacroRow:
    entity_id: str
    market: str
    #: 한글 이름. 모르면 원제.
    label: str
    #: 원제. 가공한 숫자를 검증할 수 있게 함께 싣는다.
    source_name: str
    actual: float
    previous: float | None
    unit: str
    #: **발표 시각.** ``valid_from``(우리가 안 시각)이 아니다.
    released_at: datetime

    @property
    def is_percent(self) -> bool:
        return self.unit in PERCENT_UNITS

    @property
    def change(self) -> float | None:
        """직전 대비. 퍼센트 지표는 **%p 차이**, 나머지는 **비율 변화**다.

        단위가 뭔지에 따라 뜻이 달라지므로 ``change_unit`` 과 늘 같이 읽어야
        한다. 하나만 보면 금리 +0.13%p 가 +0.13% 로 읽힌다.
        """
        if self.previous is None:
            return None
        if self.is_percent:
            return self.actual - self.previous
        if self.previous == 0:
            return None
        return self.actual / self.previous - 1.0

    @property
    def change_unit(self) -> str:
        return "%p" if self.is_percent else "%"

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "market": self.market,
            "label": self.label,
            "source_name": self.source_name,
            "actual": self.actual,
            "previous": self.previous,
            "unit": self.unit,
            "change": self.change,
            "change_unit": self.change_unit,
            "released_at": self.released_at.isoformat(),
        }


@dataclass(frozen=True)
class MacroSection:
    released: list[MacroRow]
    #: 시장별 한 줄. 그 시장에서 발표가 없었으면 이유가 여기 있다.
    notes: list[str]
    since: date | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "released": [row.as_dict() for row in self.released],
            "notes": self.notes,
            "since": self.since.isoformat() if self.since else None,
        }


def macro_section(
    store: Store, *, as_of: datetime, sessions: dict[str, date | None], limit: int
) -> MacroSection:
    """그 구간에 **실제로 발표된** 거시지표.

    ## scheduled 와 released 를 섞지 않는다

    이 테이블은 일정이 먼저 들어오고(``actual`` 없음) 발표 후 실측값이 같은
    자연키로 덮는다. **``actual`` 이 없는 것은 발표된 것이 아니다.** 아직 안
    나온 지표를 나온 것처럼 적는 것이 이 섹션이 할 수 있는 가장 나쁜 거짓말이다.

    **예정 목록은 싣지 않는다.** 좁은 화면에서 "발표됨" 과 "예정" 을 확실히
    가르려면 자리가 드는데, 못 가를 바에는 안 싣는 편이 안전하다.

    ## 컨센서스는 없다

    우리는 시장 예상치를 수집하지 않는다. "예상 대비 서프라이즈" 를 쓸 수
    없고 **직전값 대비만** 적는다. 없는 열을 만들지 않는다.

    ## 접는 규칙 둘

    1. **지표당 최신 하나.** 연방기금금리는 매일 같은 값으로 다시 발표된다 —
       접지 않으면 3.63 짜리 네 줄이 다른 지표를 통째로 밀어낸다
    2. **같은 값의 중복 발표는 하나로.** 소매판매 속보치와 확정치가 같은
       763,602 를 각각 든다. 둘 다 실으면 한 사건이 두 사건으로 보인다
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
        return MacroSection([], ["거시지표 테이블이 비어 있다"], since)

    # 같은 자연키의 최신 관측만. 정정본은 새 행으로 쌓인다.
    frame = frame.sort_values("observed_at").groupby(
        ["entity_id", "scheduled_at"], as_index=False
    ).last()

    # **actual 이 있는 것만 발표다.** status 만 믿지 않는다 — 둘 다 본다.
    done = frame[
        (frame["status"] == "released")
        & frame["actual"].notna()
        & (frame["scheduled_at"] <= as_of)
    ]
    if since is not None:
        done = done[done["scheduled_at"] >= pd.Timestamp(since, tz="UTC")]
    if done.empty:
        return MacroSection([], _macro_notes([]), since)

    # 지표당 최신 하나.
    done = done.sort_values("scheduled_at").groupby("entity_id", as_index=False).last()

    def row_of(record: dict[str, Any]) -> MacroRow:
        previous = record.get("previous")
        entity = str(record.get("entity_id") or "")
        source_name = str(record.get("release_name") or record.get("indicator") or "")
        return MacroRow(
            entity_id=entity,
            market=str(record.get("market") or ""),
            label=MACRO_LABELS.get(entity, source_name),
            source_name=source_name,
            actual=float(record["actual"]),
            previous=None if previous is None or pd.isna(previous) else float(previous),
            unit=str(record.get("unit") or ""),
            released_at=record["scheduled_at"].to_pydatetime(),
        )

    rows = [row_of(r) for r in done.to_dict(orient="records")]
    rows.sort(key=lambda row: (row.released_at, row.entity_id), reverse=True)

    # 같은 값의 중복 발표 접기. 선호 목록에 있는 쪽을 남긴다.
    seen: dict[tuple[str, float, float | None, datetime], MacroRow] = {}
    for row in rows:
        key = (row.market, row.actual, row.previous, row.released_at)
        kept = seen.get(key)
        if kept is None or (
            row.entity_id in MACRO_PREFERRED and kept.entity_id not in MACRO_PREFERRED
        ):
            seen[key] = row
    deduped = sorted(
        seen.values(), key=lambda row: (row.released_at, row.entity_id), reverse=True
    )
    return MacroSection(deduped[:limit], _macro_notes(deduped), since)


def _macro_notes(rows: list[MacroRow]) -> list[str]:
    """발표가 없는 시장을 이름으로 부른다. **섹션을 지우지 않는다** —
    섹션이 사라지면 읽는 사람이 "원래 없는 항목" 으로 안다."""
    notes: list[str] = []
    for code in market_service.MARKETS:
        if not any(row.market == code for row in rows):
            where = "국내" if code == "KR" else "미국"
            notes.append(f"{where} 지표: 이 구간에 발표된 것이 없다")
    return notes


#: 뉴스 소스. **공시(dart)가 아니라 기사(newsapi)다.**
#:
#: 창고의 ``documents`` 에는 둘이 같이 산다 — 2026-08-14 기준 dart 5,492건 ·
#: newsapi 107건. 공시는 뺐다(사용자 요청). 공시는 "무슨 일이 처리됐나" 고
#: 기사는 "무슨 일이 벌어지고 있나" 라, 아침에 읽고 싶은 쪽은 후자다.
NEWS_SOURCE = "newsapi"

#: 뉴스를 찾을 때 여는 창(일). 기사는 공시보다 듬성듬성해서 조금 넓게 연다.
NEWS_LOOKBACK_DAYS = 5

#: "주요 종목" 의 정의 — 그 세션 거래대금 상위 이만큼.
NEWS_MAJOR_POOL = 100


@dataclass(frozen=True)
class NewsRow:
    entity_id: str
    name: str
    title: str
    url: str
    published_on: date | None
    #: 왜 뽑혔는가. **화면에 그대로 나간다** — 근거를 못 대는 선별은
    #: "왜 이건 있고 저건 없나" 에 답할 수 없다.
    reason: str
    #: 그 종목에 이 구간 기사가 몇 건이었나. 관심이 몰린 정도다.
    volume: int
    #: 한국어 번역(미장 전용). ``translate.NewsTitleTranslate`` 가 채운다.
    #: **``title`` 은 지우지 않는다** — 번역이 틀렸을 때 검증할 원문이 없으면
    #: 번역은 못 믿을 필터가 된다. ``None`` 이면 번역이 없었다는 뜻이고,
    #: 렌더러는 원문으로 폴백한다.
    title_ko: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "title": self.title,
            "title_ko": self.title_ko,
            "url": self.url,
            "published_on": self.published_on.isoformat() if self.published_on else None,
            "reason": self.reason,
            "volume": self.volume,
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
    """기사에서 **중요한 것만**. 규칙으로 고르고, 규칙을 밝힌다.

    ## 무엇을 "중요" 로 보나

    기사는 종목별로 흩어져 있고 한 종목에 수십 건이 몰린다(삼성전자 34건).
    그래서 **종목을 먼저 고르고, 그 종목의 최신 기사 하나씩** 싣는다.
    종목을 고르는 기준이 둘이다.

    1. **그날 거래대금 상위 종목** — 돈이 실제로 몰린 곳이다. 시황이라는
       말의 뜻에 가장 가깝다
    2. **기사가 몰린 종목** — 거래대금 상위가 아니어도 그날 화제가 된 곳이다.
       1번만 쓰면 대형주 목록이 되고, 그건 뉴스가 아니라 시가총액 순위다

    한 종목에 한 줄만 준다. 안 그러면 삼성전자 기사 5건이 목록을 다 먹는다.

    **LLM 을 쓰지 않는다.** 규칙이라 같은 as_of 면 같은 목록이 나오고, 요약이
    사실을 덧칠할 여지가 없다. 제목·URL·날짜를 창고 원문 그대로 옮긴다.

    ## 없으면 없다고 적는다

    미장 기사는 창고에 **한 건도 없다** — newsapi 수집이 국장 종목만 돈다.
    그때 이 섹션은 사라지지 않고 그 사실을 적는다. 섹션이 없어지면 읽는
    사람이 "원래 없는 항목" 으로 알고, 아무도 수집기를 고치지 않는다.
    """
    criteria = (
        f"거래대금 상위 {NEWS_MAJOR_POOL}위 종목 · 기사가 몰린 종목 · 종목당 최신 1건"
    )
    frame = store.get(
        DOCUMENTS,
        as_of=as_of,
        lookback=NEWS_LOOKBACK_DAYS,
        columns=["entity_id", "doc_id", "title", "url", "source", "valid_from"],
    )
    if frame.empty:
        return NewsSection([], 0, criteria, "뉴스 테이블이 비어 있다")

    mine = frame[
        (frame["source"] == NEWS_SOURCE)
        & frame["entity_id"].astype(str).str.startswith(f"{market}:")
    ]
    total = len(mine)
    if mine.empty:
        where = "국장" if market == "KR" else "미장"
        return NewsSection(
            [], 0, criteria, f"창고에 {where} 뉴스가 없다 — 수집기가 이 시장을 아직 안 돈다"
        )

    mine = mine.sort_values("valid_from", ascending=False).drop_duplicates("doc_id")
    counts = mine["entity_id"].astype(str).value_counts()

    major: list[str] = []
    if not panel.empty:
        major = [
            str(e)
            for e in panel.sort_values("value", ascending=False).head(NEWS_MAJOR_POOL).index
        ]
    major_set = set(major)

    # 거래대금 상위 종목 먼저, 그 다음 기사가 몰린 종목. 같은 순위 안에서는
    # 기사 건수 순 — 순서가 정해져 있어야 같은 as_of 가 같은 목록을 낸다.
    ordered = sorted(
        counts.index,
        key=lambda entity: (
            0 if entity in major_set else 1,
            -int(counts[entity]),
            str(entity),
        ),
    )

    picked: list[NewsRow] = []
    names = market_service.entity_names(
        store, as_of=as_of, entities=sorted(ordered[:limit])
    )
    for entity in ordered[:limit]:
        head = mine[mine["entity_id"].astype(str) == entity].iloc[0]
        published = _session_of(head["valid_from"])
        volume = int(counts[entity])
        picked.append(
            NewsRow(
                entity_id=str(entity),
                name=names.get(str(entity), str(entity)),
                title=" ".join(str(head.get("title") or "").split()),
                url=str(head.get("url") or ""),
                published_on=published,
                reason="거래대금 상위" if entity in major_set else f"기사 {volume}건",
                volume=volume,
            )
        )
    return NewsSection(picked, total, criteria, None)


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
    proxies = proxy_rows(store, as_of=as_of, market=market, expected=expected)
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
        proxies=proxies,
        rankings=ranks,
        floor=filled,
        news=news_section(
            store,
            panel,
            as_of=as_of,
            market=market,
            limit=int(settings["news_rows"]),
        ),
    )


def _fx_gap(
    rate: dict[str, Any], *, as_of: datetime, kr_expected: date | None
) -> tuple[str | None, str | None]:
    """환율 결측 문장과 그 성격. ``(문장, MISSING|UNPUBLISHED|None)``.

    **"미수집" 이라고 다 같은 미수집이 아니다.** ``fx_source_latest`` 가
    "FRED 가 이 시점에 이미 내놓았을 마지막 관측일" 을 계산해 준다
    (``sessions.py`` — H.10 은 월요일 주간 발행이라 일간 계열인데도 관측이
    최대 한 주 늦다). 창고가 그 날짜보다 이르면 **우리가 못 받은 것**이고,
    같으면 **원본이 아직 안 낸 것**이다 — 후자를 "미수집" 이라 적으면 매주
    없는 결함을 찾게 된다(실측: 2026-08-15 기준 DEXKOUS 갱신 08-10, 관측끝
    08-07 — 국장 08-14 세션 대비로는 "미수집" 처럼 보이지만 FRED 자체가
    거기까지다).
    """
    if rate["rate"] is None:
        return f"환율: 최근 {INDEX_LOOKBACK_DAYS}일 안에 USD/KRW 가 없다", MISSING
    if not rate["sessions"]:
        return None, None
    last = date.fromisoformat(rate["sessions"][-1])
    source_latest = fx_source_latest(as_of)
    if last < source_latest:
        # FRED 는 이미 냈는데 창고가 못 따라갔다 — 우리 수집이 밀린 것이다.
        note = (
            f"환율: 창고가 {last.isoformat()} 까지다 — FRED 원본은 "
            f"{source_latest.isoformat()} 까지 이미 냈다 (수집 확인 필요)"
        )
        return note, MISSING
    if kr_expected is not None and last < kr_expected:
        # 창고는 FRED 가 가진 걸 다 갖고 있다. 늦은 것은 원본이다.
        note = (
            f"환율: FRED 원본이 아직 {last.isoformat()} 까지다 "
            "(H.10 은 월요일 주간 발행 — 다음 값은 다음 발행일에 나온다)"
        )
        return note, UNPUBLISHED
    return None, None


def _translate_us_news(
    markets: dict[str, MarketBrief], *, translate: Any, as_of: datetime
) -> dict[str, MarketBrief]:
    """미장 뉴스 제목에 ``title_ko`` 를 채운다. 국장은 손대지 않는다 — 이미 한국어다.

    ``translate`` 가 ``None`` 이면(기본값) 아무것도 안 한다 — 테스트·백테스트가
    API 키 없이도 결정론을 지킨다 (``translate.py`` 모듈 독스트링).
    """
    us = markets.get("US")
    if translate is None or us is None or not us.news.rows:
        return markets

    from quant_rl_trading.reporting.translate import Headline

    headlines = [Headline(entity_id=row.entity_id, title=row.title) for row in us.news.rows]
    translated = translate.translate(headlines, as_of=as_of)
    if not translated:
        return markets

    new_rows = [
        NewsRow(
            entity_id=row.entity_id,
            name=row.name,
            title=row.title,
            url=row.url,
            published_on=row.published_on,
            reason=row.reason,
            volume=row.volume,
            title_ko=translated.get(Headline(row.entity_id, row.title).fingerprint),
        )
        for row in us.news.rows
    ]
    # **필드를 손으로 옮겨 적지 않는다.** 여기는 뉴스만 바꾸는 자리인데
    # 전부 나열해 두면, 나중에 필드가 하나 늘 때 이 함수만 빠뜨린다 —
    # 실제로 그랬다(대용 ETF 줄이 미장에서만 조용히 사라졌다. 국장은 이
    # 함수를 안 지나서 멀쩡했고, 그래서 더 안 보였다).
    new_us = replace(
        us,
        news=NewsSection(
            rows=new_rows, total=us.news.total, criteria=us.news.criteria, note=us.news.note
        ),
    )
    return {**markets, "US": new_us}


def build_briefing(
    store: Store, *, as_of: datetime, clock: Clock | None = None, translate: Any = None
) -> Briefing:
    """``as_of`` 시점의 시황 브리핑.

    같은 ``as_of`` 로 두 번 부르면 같은 것이 나온다 — 벽시계를 읽는 곳이
    한 군데도 없기 때문이다 (불변식 2). 그것이 리포트의 결정론 테스트다
    (reporting.md §5).

    ``translate`` 는 ``reporting.translate.NewsTitleTranslate`` (또는 같은
    인터페이스). 기본 ``None`` 이면 미장 뉴스 제목은 영어 그대로 나간다 —
    이 함수 자체는 번역기를 몰라도 되고, 호출부(``tools/send_briefing.py``)만
    명시적으로 넘긴다.
    """
    settings = store.config("reporting", as_of=as_of)
    benchmark = store.config("benchmark", as_of=as_of)
    expected = {
        code: expected_session(store, Market(code), as_of=as_of, clock=clock)
        for code in market_service.MARKETS
    }

    rate = market_service.fx(store, as_of=as_of, lookback=INDEX_LOOKBACK_DAYS)
    fx_note, fx_gap_kind = _fx_gap(rate, as_of=as_of, kr_expected=expected["KR"])

    markets = {
        code: market_brief(
            store,
            as_of=as_of,
            market=code,
            headline=str(benchmark[market_service.BENCHMARK_KEY[code]]),
            expected=expected[code],
            settings=settings,
        )
        for code in market_service.MARKETS
    }
    markets = _translate_us_news(markets, translate=translate, as_of=as_of)

    return Briefing(
        as_of=as_of,
        fx=rate,
        fx_note=fx_note,
        fx_gap_kind=fx_gap_kind,
        macro=macro_section(
            store, as_of=as_of, sessions=expected, limit=int(settings["macro_rows"])
        ),
        markets=markets,
    )
