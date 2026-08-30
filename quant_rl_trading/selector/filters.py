"""유니버스 필터 — **살 수 있는 종목만 남긴다** (selector.md §5-1).

점수를 매기기 전에 거른다. 순서가 뒤바뀌면 못 사는 종목에 계산을 쓰고,
더 나쁘게는 **못 사는 종목이 백테스트 수익률을 만든다.**

임계치는 전부 `store.config` 에서 온다 (불변식 10).

## 데이터 유니버스와 매매 유니버스는 다르다

창고는 상장폐지 종목까지 품는다 — 그래야 생존편향이 없다. 하지만 그것을
사지는 않는다. 이 모듈이 그 경계다 (data-contract §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.store.errors import ConfigNotFound
from quant_rl_trading.store.prices import read_prices

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

UNIVERSE = "universe"
DOCUMENTS = "documents"

#: 거래대금 평균을 낼 창(거래일).
TURNOVER_WINDOW = 20
#: 시가총액이 사는 표 (reporting.briefing 과 같은 이름을 쓴다)
MARKET_STATS = "market_stats"

#: 부실 공시를 이 기간 안에 냈으면 매매 대상에서 뺀다. 관리종목 지정·불성실
#: 공시는 한 번 나면 한동안 유효한 사실이다.
DISTRESS_WINDOW_DAYS = 120


@dataclass(frozen=True)
class FilterParams:
    #: **절대 하한.** 자본과 무관하게 이보다 안 거래되는 종목은 안 산다.
    min_turnover: float
    min_listed_days: int
    max_price_ratio: float
    #: 자본에서 유도하는 유동성 하한의 배수. 실효 하한은
    #: ``max(min_turnover, capacity_multiple × 자본)`` 이다 (KR 만).
    #: 0 이면 배수를 끄고 절대 하한만 쓴다.
    capacity_multiple: float
    #: 순위 하한 — 절대 하한을 통과한 뒤 상위 N 만 남긴다. 0 이면 안 쓴다
    #: (config `universe.top_*_rank`, 사전등록 시행으로 켠다).
    top_turnover_rank: int = 0
    top_volume_rank: int = 0
    top_market_cap_rank: int = 0

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime, market: str) -> FilterParams:
        key = "min_turnover_20d_kr" if market == "KR" else "min_turnover_20d_us"
        return cls(
            min_turnover=float(store.config(f"universe.{key}", as_of=as_of)),
            min_listed_days=int(store.config("universe.min_listed_days", as_of=as_of)),
            max_price_ratio=float(store.config("universe.max_price_ratio", as_of=as_of)),
            capacity_multiple=float(
                store.config("universe.turnover_capacity_multiple", as_of=as_of)
            ),
            top_turnover_rank=_rank_config(store, "top_turnover_rank", as_of=as_of),
            top_volume_rank=_rank_config(store, "top_volume_rank", as_of=as_of),
            top_market_cap_rank=_rank_config(store, "top_market_cap_rank", as_of=as_of),
        )

    def effective_floor(self, *, market: str, equity: float) -> float:
        """자본을 반영한 실효 거래대금 하한.

        **KR 에만 배수를 건다.** 자본(NAV)은 원화이고 KR 거래대금도 원화라 바로
        비교되지만, US 거래대금은 달러라 환산 없이는 못 건다 — 미장 자본·환율
        처리가 설계되기 전까지 US 는 절대 하한만 쓴다 (config 주석 참고).
        """
        if market != "KR" or equity <= 0 or self.capacity_multiple <= 0:
            return self.min_turnover
        return max(self.min_turnover, self.capacity_multiple * equity)


def _rank_config(store: Store, name: str, *, as_of: datetime) -> int:
    """순위 하한 설정. **없으면 0(끔)** — 이 키가 없던 시점의 as_of 조회도 돌아야 한다."""
    try:
        return int(store.config(f"universe.{name}", as_of=as_of))
    except ConfigNotFound:
        return 0


@dataclass(frozen=True)
class FilterResult:
    """남은 종목과 **왜 빠졌는지**. 이유를 안 남기면 후보가 준 날 설명할 수 없다."""

    kept: tuple[str, ...]
    dropped: dict[str, str]

    def __len__(self) -> int:
        return len(self.kept)


#: 1주 가격이 자본의 상한을 넘어 탈락한 이유. **상수로 두는 이유가 있다** —
#: RL 피처 캐시(`allocator/cache.py`)가 "이 세션의 캐시가 자본과 무관한가" 를
#: 이 이유의 개수로 판단한다. 문자열을 두 곳에 적으면 한쪽만 고쳐지고, 그러면
#: 캐시가 조용히 다른 후보 목록을 내준다.
PRICE_CAP_REASON = "1주 가격이 자본의 상한 초과"


def tradable_universe(
    store: Store, *, as_of: datetime, market: str, params: FilterParams, equity: float
) -> FilterResult:
    """살 수 있는 종목.

    ``equity`` 는 자본(NAV)이다. **1주 가격이 자본의 15% 를 넘는 종목은 뺀다** —
    한 주도 제대로 못 담는 종목을 후보에 두면 목표 비중이 라운딩에서 통째로
    사라지고, 그 자리는 현금으로 남는다.

    거래대금 하한도 자본에서 유도한다. 상수 하한을 통과해도 **목표금액이
    ``max_adv_ratio`` 상한에 잘리는** 종목은 실효적으로 못 담는다 — 자본이
    커지면 상수 하한은 "못 파는 종목" 이 아니라 "못 사는 종목" 을 걸러야 한다
    (portfolio-construction.md §부록). ``FilterParams.effective_floor`` 참고.
    """
    lookback = max(params.min_listed_days + 30, 400)
    # **창은 400일이지만 쓰는 것은 종목당 두 값뿐이다** — 마지막 상태와 창 안
    # 최초 등장일. 창 전체를 ``store.get`` 으로 퍼오면 2,877종목 × 400세션 =
    # 115만 행을 복사해 2,877 행을 남기게 된다. 그 복사가 백테스트의 세션당
    # 비용을 지배했다 (실측 5.2초 → 0.8초, RSS 982MB → 366MB). 접기는 창고
    # 안에서 끝낸다 — 남는 행은 같다 (store.latest_by_entity).
    universe = store.latest_by_entity(
        UNIVERSE,
        as_of=as_of,
        lookback=lookback,
        market=market,
        columns=["valid_from", "is_listed", "is_tradable"],
    )
    if universe.empty:
        return FilterResult(kept=(), dropped={})

    dropped: dict[str, str] = {}
    alive: list[str] = []
    for row in universe.to_dict(orient="records"):
        entity = str(row["entity_id"])
        # 2026-08-15 사고: KR 백필의 상폐 감지가 시장을 안 가려서 US 종목
        # 6,648개에 market="KR" 이 잘못 찍혔다. 위 latest_by_entity 의
        # market=market 필터는 **저장된 market 컬럼값**을 보므로, 이미
        # 오염된 행은 그 필터를 그대로 통과한다. entity_id 는 이 사고로
        # 안 깨졌으므로("US:AA" 그대로) 접두어로 다시 확인한다 — 쓰기 시점
        # 방어(schema.validate_batch)는 이후 재발만 막을 뿐 이미 들어간
        # 행은 append-only 라 지울 수 없다.
        if not entity.startswith(f"{market}:"):
            dropped[entity] = "시장 불일치"
            continue
        if not (bool(row["is_listed"]) and bool(row["is_tradable"])):
            dropped[entity] = "상장폐지·거래불가"
            continue
        alive.append(entity)

    # 상장 경과일. 창 안에서 처음 보인 날을 상장일로 본다.
    first_seen = universe.set_index("entity_id")["first_valid_from"].dt.date
    latest_session = max(universe["valid_from"]).date()
    young = {
        entity
        for entity in alive
        if (latest_session - first_seen[entity]).days < params.min_listed_days
        # 창 자체가 짧으면 전 종목이 신규주로 보인다. 창 끝에 닿은 종목은
        # "적어도 창만큼 오래됐다" 로 본다 — 없는 과거를 신규 상장으로 읽지 않는다.
        and (latest_session - first_seen[entity]).days < lookback - 1
    }
    for entity in young:
        dropped[entity] = "상장 6개월 미만"
    alive = [entity for entity in alive if entity not in young]

    # 휴장일 행이 섞이면 그날이 창의 마지막이 되어 ``last_close`` 가 0 이 되고,
    # 살아 있는 종목이 통째로 "종가 없음" 으로 탈락한다.
    prices = read_prices(
        store, as_of=as_of, entity=alive, lookback=TURNOVER_WINDOW * 2, market=market
    )
    if prices.empty:
        for entity in alive:
            dropped[entity] = "가격 없음"
        return FilterResult(kept=(), dropped=dropped)

    recent = prices.sort_values("valid_from")
    turnover = recent.groupby("entity_id")["value"].tail(TURNOVER_WINDOW)
    turnover = recent.loc[turnover.index].groupby("entity_id")["value"].mean()
    last_close = recent.groupby("entity_id")["close"].tail(1)
    last_close = recent.loc[last_close.index].set_index("entity_id")["close"]

    floor = params.effective_floor(market=market, equity=equity)
    # 하한이 상수를 넘었나 — 자본이 유동성을 조이기 시작한 자리다. 이유를
    # 두 개로 가른다: 상수 미달은 "못 파는 종목", 배수 미달은 "못 사는 종목".
    capital_floor = floor > params.min_turnover

    kept: list[str] = []
    for entity in alive:
        if entity not in turnover.index or pd.isna(turnover[entity]):
            dropped[entity] = "거래대금 관측 없음"
            continue
        value = float(turnover[entity])
        if value < floor:
            dropped[entity] = (
                "거래대금 용량 미달"
                if capital_floor and value >= params.min_turnover
                else "거래대금 하한 미달"
            )
            continue
        price = float(last_close.get(entity, 0.0))
        if price <= 0:
            dropped[entity] = "종가 없음"
            continue
        if equity > 0 and price > equity * params.max_price_ratio:
            dropped[entity] = PRICE_CAP_REASON
            continue
        kept.append(entity)

    kept = _apply_rank_caps(
        store, kept, dropped,
        as_of=as_of, market=market, params=params, turnover=turnover, prices=recent,
    )
    return FilterResult(kept=tuple(sorted(kept)), dropped=dropped)


def _apply_rank_caps(
    store: Store,
    kept: list[str],
    dropped: dict[str, str],
    *,
    as_of: datetime,
    market: str,
    params: FilterParams,
    turnover: pd.Series,
    prices: pd.DataFrame,
) -> list[str]:
    """순위 하한 — 절대 하한을 통과한 것들 중 **상위 N** 만 남긴다.

    절대 하한(원)은 시장이 통째로 얼어붙은 날에는 의미가 없고, 자본이 커지면
    "못 사는 종목" 을 거르는 쪽으로 성격이 바뀐다. 순위는 **그날의 시장 안에서**
    자른다. 셋 다 0 이면 아무것도 안 한다(기본값).

    **셋은 교집합이다** — 거래대금 상위 300 ∧ 시총 상위 300 이면 둘 다 드는 것만
    남는다. 합집합으로 두면 "상위" 라는 말이 무의미해진다.
    """
    if not kept:
        return kept
    caps: list[tuple[str, int, pd.Series]] = []
    if params.top_turnover_rank > 0:
        caps.append(("거래대금 순위 밖", params.top_turnover_rank, turnover))
    if params.top_volume_rank > 0:
        volume = prices.groupby("entity_id")["volume"].tail(TURNOVER_WINDOW)
        volume = prices.loc[volume.index].groupby("entity_id")["volume"].mean()
        caps.append(("거래량 순위 밖", params.top_volume_rank, volume))
    if params.top_market_cap_rank > 0:
        caps.append(("시총 순위 밖", params.top_market_cap_rank, _market_caps(store, as_of=as_of, market=market)))
    for reason, limit, series in caps:
        ranked = series.reindex(kept).dropna()
        if ranked.empty:
            # 잴 값이 없으면 **거르지 않는다** — 관측이 없는 것을 "순위 밖" 으로
            # 적으면 수집 사고가 조용히 유니버스를 비운다.
            continue
        survivors = set(ranked.nlargest(limit).index)
        for entity in kept:
            if entity in ranked.index and entity not in survivors:
                dropped[entity] = reason
        kept = [entity for entity in kept if entity not in ranked.index or entity in survivors]
    return kept


def _market_caps(store: Store, *, as_of: datetime, market: str) -> pd.Series:
    """종목별 최신 시가총액. 없으면 빈 시리즈 — 호출자가 "거르지 않는다" 로 받는다."""
    frame = store.get(
        MARKET_STATS, as_of=as_of, lookback=20, until=as_of, market=market,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    if frame.empty:
        return pd.Series(dtype=float)
    caps = frame[frame["metric"] == "market_cap"]
    if caps.empty:
        return pd.Series(dtype=float)
    caps = caps.sort_values("valid_from").groupby("entity_id")["value"].last()
    return caps.astype(float)


def distressed(store: Store, *, as_of: datetime, market: str) -> set[str]:
    """부실 공시를 낸 종목 — 관리종목·불성실공시·거래정지·회생절차.

    설정의 ``universe.exclude_flags`` 가 요구하는 것을 **우리가 실제로 가진
    데이터로** 구현한 것이다. 거래소 지정 플래그를 따로 받지 않으므로 DART
    공시 분류를 쓴다 (`collectors/dart_filings.py`).

    event Analyst 도 같은 공시를 점수 피처로 쓴다. 여기서는 감점이 아니라
    **배제**다 — 감점으로만 두면 다른 장점이 상쇄해서 관리종목이 후보에
    남는다. 상장폐지 종목을 배제하는 것과 같은 이유다.
    """
    documents = store.get(DOCUMENTS, as_of=as_of, lookback=DISTRESS_WINDOW_DAYS)
    if documents.empty:
        return set()
    hits = documents[
        documents["entity_id"].astype(str).str.startswith(f"{market}:")
        & (documents["doc_type"] == "distress")
    ]
    return {str(value) for value in hits["entity_id"]}
