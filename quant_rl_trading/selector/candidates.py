"""후보 선정 — selector.md §5 의 여섯 단계. **순서가 곧 설계다.**

    1. 유니버스 필터        살 수 있는 것만
    2. 합성 점수            죽은 Analyst 는 자동으로 빠진다
    3. News·SNS 거부 제외   거부 상한 30%
    4. 상관 페널티          이미 뽑은 것과 겹치면 감점
    5. 섹터 상한
    6. 상위 N개

**3번이 4번보다 먼저다.** 상관 계산은 비싸고, 거부될 종목까지 계산할 이유가
없다.

## 거부에 상한을 두는 이유

News·SNS 는 매수 금지만 할 수 있고 매도 권한은 없다(불변식: 금지 사항).
그래도 하루에 후보 전부를 막아 버리면 그날 포트폴리오가 통째로 현금이 된다.
LLM 판정 하나가 펀드를 멈추게 두지 않는다 — **상한을 넘으면 심각도 높은
것부터** 자르고 나머지는 통과시키되, 잘린 사실을 흔적에 남긴다.

## 흔적을 남긴다

각 단계에서 무엇이 왜 빠졌는지 ``SelectionTrace`` 에 쌓는다. Decision Trace
화면이 이걸 읽는다. **후보가 0개인 날 이유를 말할 수 없으면 그 시스템은
운영할 수 없다.**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

VERDICTS = "verdicts"
PRICES = "prices"
SECTORS = "sectors"

#: 섹터를 읽는 창(거래일). 며칠 안 받아진 날이 있어도 직전 관측을 찾을 만큼.
SECTOR_LOOKBACK_DAYS = 30

#: 상관을 재는 창(거래일). selector.md §5-4.
CORRELATION_WINDOW = 60

#: 거부가 후보의 이 비율을 넘으면 심각도 낮은 것부터 살린다.
DEFAULT_REJECTION_CAP = 0.30


@dataclass(frozen=True)
class SelectionParams:
    n_candidates: int
    corr_threshold: float
    corr_penalty: float
    sector_cap: float
    rejection_cap: float = DEFAULT_REJECTION_CAP

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> SelectionParams:
        return cls(
            n_candidates=int(store.config("selector.n_candidates", as_of=as_of)),
            corr_threshold=float(store.config("selector.corr_threshold", as_of=as_of)),
            corr_penalty=float(store.config("selector.corr_penalty", as_of=as_of)),
            sector_cap=float(store.config("selector.sector_cap", as_of=as_of)),
        )


@dataclass
class SelectionTrace:
    """왜 이 후보들인가. 단계마다 남긴다."""

    counts: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def stage(self, name: str, remaining: int) -> None:
        self.counts[name] = remaining

    def drop(self, entity_id: str, reason: str) -> None:
        self.dropped.setdefault(entity_id, reason)

    def note(self, message: str) -> None:
        self.notes.append(message)


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    score: float
    raw_score: float
    sector: str | None = None

    @property
    def penalized(self) -> bool:
        return self.score != self.raw_score


def rejected_entities(
    store: Store, *, as_of: datetime, universe: Sequence[str], cap: float, trace: SelectionTrace
) -> set[str]:
    """News·SNS 가 매수를 막은 종목. **상한까지만 반영한다.**

    ``expires_at`` 이 지난 거부는 효력이 없다 — 영구 차단은 존재할 수 없다는
    것이 verdicts 스키마의 규칙이다.
    """
    if not universe:
        return set()
    frame = store.get(VERDICTS, as_of=as_of, lookback=30)
    if frame.empty:
        return set()

    live = frame[
        frame["entity_id"].isin(list(universe))
        & (frame["decision"] == "reject")
        & (frame["expires_at"].isna() | (frame["expires_at"] > pd.Timestamp(as_of)))
    ]
    if live.empty:
        return set()

    ranked = live.sort_values("severity", ascending=False)
    limit = max(1, int(len(universe) * cap))
    entities = list(dict.fromkeys(str(value) for value in ranked["entity_id"]))
    if len(entities) > limit:
        trace.note(
            f"거부 {len(entities)}건이 상한({limit}건)을 넘어 심각도 높은 순으로 "
            f"{limit}건만 반영했다. LLM 판정 하나가 펀드를 멈추게 두지 않는다"
        )
        entities = entities[:limit]
    for entity in entities:
        trace.drop(entity, "News·SNS 매수 금지")
    return set(entities)


def correlation_matrix(
    store: Store, *, as_of: datetime, entities: Sequence[str], market: str
) -> pd.DataFrame:
    """일간 수익률 상관. 관측이 모자란 종목은 행렬에서 빠진다.

    빠진 종목은 페널티를 못 받는데, 그건 **상관이 낮다는 뜻이 아니라 모른다는
    뜻이다.** 모르는 것을 0 으로 채우면 신규 상장주가 항상 분산 효과가 큰
    것처럼 보인다 — 그래서 여기서는 채우지 않고, 호출부가 '모름' 을 흔적에 남긴다.
    """
    if not entities:
        return pd.DataFrame()
    prices = store.get(
        PRICES,
        as_of=as_of,
        entity=list(entities),
        lookback=CORRELATION_WINDOW * 2,
        market=market,
        columns=["close"],
    )
    if prices.empty:
        return pd.DataFrame()
    wide = (
        prices.sort_values("valid_from")
        .pivot_table(index="valid_from", columns="entity_id", values="close")
        .tail(CORRELATION_WINDOW + 1)
    )
    returns = wide.pct_change(fill_method=None).dropna(how="all")
    # 관측이 절반도 없는 종목의 상관은 우연이다.
    enough = returns.columns[returns.notna().sum() >= CORRELATION_WINDOW // 2]
    return returns[enough].corr()


def sector_map(
    store: Store,
    *,
    as_of: datetime,
    entities: Sequence[str],
    market: str,
    source: str,
    lookback: int = SECTOR_LOOKBACK_DAYS,
) -> dict[str, str]:
    """{entity_id: 섹터}. 종목마다 **as_of 이전 가장 최근** 관측 하나.

    ``source`` 는 기본값이 없다. `sectors` 에는 서로 다른 분류체계가 함께
    산다 — KRX 소속부(`krx_openapi`)는 업종이 아니라 시장 세부 구분이고,
    DART 표준산업분류(`dart_company`)가 진짜 업종이다. 섞어서 접으면 종목마다
    어느 체계의 값이 나왔는지 알 수 없는 dict 이 만들어지고, 그걸로 건 섹터
    상한은 무엇을 분산시킨 것인지 아무도 말할 수 없다. 그래서 고르게 한다.

    이중시간이 핵심이다 — ``store.get`` 이 ``observed_at <= as_of`` 를 이미
    걸러 주므로, 남은 것 중 ``valid_from`` 이 가장 늦은 행을 고르면 그
    시점에 알 수 있었던 섹터가 나온다. 종목이 업종을 옮긴 뒤에 조회하면
    새 섹터가, 옮기기 전 시점을 조회하면 옛 섹터가 나온다.

    섹터를 모르는 종목은 dict 에 아예 없다. **None 으로도, "기타" 로도
    채우지 않는다** — 채우면 그 종목들이 selector 에서 한 섹터로 묶여 섹터
    상한이 엉뚱한 종목을 걸러내게 된다 (candidates.select 는 sectors.get 이
    None 이면 그 종목엔 상한을 적용하지 않는다).
    """
    if not entities:
        return {}
    frame = store.get(SECTORS, as_of=as_of, entity=list(entities), lookback=lookback)
    if frame.empty:
        return {}
    frame = frame[(frame["market"] == market) & (frame["source"] == source)]
    if frame.empty:
        return {}
    latest = frame.sort_values("valid_from").groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): str(row["sector"])
        for row in latest.to_dict(orient="records")
        if row["sector"]
    }


def select(
    *,
    scores: pd.Series,
    params: SelectionParams,
    correlations: pd.DataFrame | None = None,
    sectors: Mapping[str, str] | None = None,
    trace: SelectionTrace | None = None,
) -> list[Candidate]:
    """4~6단계. 점수 높은 순으로 훑으며 상관 감점과 섹터 상한을 적용한다.

    **감점은 순위를 바꾼다.** 감점 후 점수가 다음 종목보다 낮아지면 그 종목이
    먼저 뽑혀야 하므로, 매번 남은 것 중 가장 높은 것을 다시 고른다.
    """
    trace = trace or SelectionTrace()
    remaining = {entity: float(score) for entity, score in scores.items()}
    raw = dict(remaining)
    chosen: list[Candidate] = []
    sector_counts: dict[str, int] = {}
    sector_limit = max(1, int(params.n_candidates * params.sector_cap))

    while remaining and len(chosen) < params.n_candidates:
        entity = max(remaining, key=lambda key: remaining[key])
        score = remaining.pop(entity)
        sector = sectors.get(entity) if sectors else None

        if sector is not None and sector_counts.get(sector, 0) >= sector_limit:
            trace.drop(entity, f"섹터 상한({sector} {sector_limit}종목)")
            continue

        chosen.append(Candidate(entity, score=score, raw_score=raw[entity], sector=sector))
        if sector is not None:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        if correlations is None or entity not in correlations.columns:
            continue
        # 이미 뽑힌 것과 겹치는 종목을 감점한다. 분산되지 않은 포트폴리오는
        # 분산이 아니라 레버리지다.
        column = correlations[entity]
        for other in list(remaining):
            if other not in column.index:
                continue
            value = column[other]
            if pd.notna(value) and float(value) > params.corr_threshold:
                remaining[other] -= params.corr_penalty
                trace.note(f"{other}: {entity} 와 상관 {float(value):.2f} — 감점")

    trace.stage("selected", len(chosen))
    return chosen
