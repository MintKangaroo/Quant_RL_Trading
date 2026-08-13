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

if TYPE_CHECKING:
    from lattice.store import Store

UNIVERSE = "universe"
PRICES = "prices"
DOCUMENTS = "documents"

#: 거래대금 평균을 낼 창(거래일).
TURNOVER_WINDOW = 20

#: 부실 공시를 이 기간 안에 냈으면 매매 대상에서 뺀다. 관리종목 지정·불성실
#: 공시는 한 번 나면 한동안 유효한 사실이다.
DISTRESS_WINDOW_DAYS = 120


@dataclass(frozen=True)
class FilterParams:
    min_turnover: float
    min_listed_days: int
    max_price_ratio: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime, market: str) -> FilterParams:
        key = "min_turnover_20d_kr" if market == "KR" else "min_turnover_20d_us"
        return cls(
            min_turnover=float(store.config(f"universe.{key}", as_of=as_of)),
            min_listed_days=int(store.config("universe.min_listed_days", as_of=as_of)),
            max_price_ratio=float(store.config("universe.max_price_ratio", as_of=as_of)),
        )


@dataclass(frozen=True)
class FilterResult:
    """남은 종목과 **왜 빠졌는지**. 이유를 안 남기면 후보가 준 날 설명할 수 없다."""

    kept: tuple[str, ...]
    dropped: dict[str, str]

    def __len__(self) -> int:
        return len(self.kept)


def tradable_universe(
    store: Store, *, as_of: datetime, market: str, params: FilterParams, equity: float
) -> FilterResult:
    """살 수 있는 종목.

    ``equity`` 는 자본(NAV)이다. **1주 가격이 자본의 15% 를 넘는 종목은 뺀다** —
    한 주도 제대로 못 담는 종목을 후보에 두면 목표 비중이 라운딩에서 통째로
    사라지고, 그 자리는 현금으로 남는다.
    """
    lookback = max(params.min_listed_days + 30, 400)
    universe = store.get(UNIVERSE, as_of=as_of, lookback=lookback, market=market)
    if universe.empty:
        return FilterResult(kept=(), dropped={})

    universe = universe.copy()
    universe["session"] = universe["valid_from"].dt.date
    latest = universe.sort_values("valid_from").groupby("entity_id").tail(1)

    dropped: dict[str, str] = {}
    alive: list[str] = []
    for row in latest.to_dict(orient="records"):
        entity = str(row["entity_id"])
        if not (bool(row["is_listed"]) and bool(row["is_tradable"])):
            dropped[entity] = "상장폐지·거래불가"
            continue
        alive.append(entity)

    # 상장 경과일. 창 안에서 처음 보인 날을 상장일로 본다.
    first_seen = universe.groupby("entity_id")["session"].min()
    latest_session = max(universe["session"])
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

    prices = store.get(
        PRICES, as_of=as_of, entity=alive, lookback=TURNOVER_WINDOW * 2, market=market
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

    kept: list[str] = []
    for entity in alive:
        if entity not in turnover.index or pd.isna(turnover[entity]):
            dropped[entity] = "거래대금 관측 없음"
            continue
        if float(turnover[entity]) < params.min_turnover:
            dropped[entity] = "거래대금 하한 미달"
            continue
        price = float(last_close.get(entity, 0.0))
        if price <= 0:
            dropped[entity] = "종가 없음"
            continue
        if equity > 0 and price > equity * params.max_price_ratio:
            dropped[entity] = "1주 가격이 자본의 상한 초과"
            continue
        kept.append(entity)

    return FilterResult(kept=tuple(sorted(kept)), dropped=dropped)


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
