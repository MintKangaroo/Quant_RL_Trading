"""리스크 패리티 룰 베이스라인 — 포트폴리오 구성(§3)을 allocator 에 끼운다.

`baseline.py` 의 순수 `allocate` 는 스코어만 본다. 이 자리는 **위험 구조**를
본다 — 팩터 공분산(§2)으로 섹터 리스크 기여를 균등화하고(§3-1) 스코어로
틸트한 뒤(§3-2) 제약에 투영한다(§3-3).

## 왜 별도 함수인가 — 순수성을 지킨다

`baseline.allocate` 는 창고를 모르는 순수 함수다(scores·params·volatility 만
받는다). 리스크 패리티는 세션 시점의 공분산·섹터·하방 베타가 있어야 하므로
창고를 탄다. 둘을 한 함수에 섞으면 순수 경로까지 창고에 묶인다. 그래서
호출부(session/daily.py)가 baseline 값을 보고 갈라 부른다.

## 현금은 여기서 빼지 않는다

§3-3 의 "현금 하한" 을 여기서 걸면 뒤의 `exposure.apply`(레짐·추세·변동성으로
노출을 줄이는 단계)와 **이중으로** 현금을 뺀다. 레짐 현금은 그 한 곳
(exposure)이 정한다 — 여기서는 `cash_floor=0` 으로 순수 투자 비중만 낸다.

## 데이터가 모자라면 스코어 비례로 물러선다

팩터 모델이 None(가격·섹터·지수 중 하나가 빔)이면 빈 비중을 내는 대신
스코어 비례로 물러선다. 세션이 통째로 현금이 되는 것보다, 위험 구조 없이
나눈 비중이라도 내는 것이 낫다 — 물러섰다는 사실은 호출부가 로그로 남긴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from quant_rl_trading.allocator.baseline import AllocatorParams, allocate
from quant_rl_trading.portfolio import constraints, factor_model, risk_parity
from quant_rl_trading.portfolio.sector_beta import DEFAULT_WINDOW, estimate as estimate_betas
from quant_rl_trading.selector import ksic
from quant_rl_trading.selector.candidates import sector_map
from quant_rl_trading.store import Store

#: 섹터 분류 — DART 표준산업분류(candidates.sector_map 규칙).
SECTOR_SOURCE = "dart_company"


@dataclass(frozen=True)
class RiskParityParams:
    """리스크 패리티 베이스라인 손잡이. RL 할인율 gamma 와 무관하다.

    **이 키들은 RL 캐시 지문에 넣지 않는다** — RL env(allocator/env.py)가 안
    읽는다. 넣으면 룰 손잡이 하나 바꿀 때마다 RL 캐시가 깨진다.
    """

    score_tilt: float
    name_rc_cap: float
    sector_rc_cap: float
    downside_beta_cap: float
    window: int

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> RiskParityParams:
        return cls(
            score_tilt=float(store.config("allocator.score_tilt", as_of=as_of)),
            name_rc_cap=float(store.config("allocator.name_risk_cap", as_of=as_of)),
            sector_rc_cap=float(store.config("allocator.sector_risk_cap", as_of=as_of)),
            downside_beta_cap=float(
                store.config("allocator.downside_beta_cap", as_of=as_of)
            ),
            window=int(store.config("allocator.risk_window", as_of=as_of)),
        )


def _downside_betas(
    store: Store, *, as_of: datetime, market: str, sectors: Mapping[str, str],
    entities: list[str], window: int,
) -> pd.Series:
    """종목별 하방 베타 — 그 종목 섹터의 값(§1). 모르면 NaN(투영이 안 누른다)."""
    betas = estimate_betas(store, as_of=as_of, market=market, window=window)
    values = {
        entity: betas[sectors[entity]].down_beta
        for entity in entities
        if entity in sectors and sectors[entity] in betas
    }
    return pd.Series(values, dtype=float)


def allocate_risk_parity(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    scores: Mapping[str, float],
    entities: list[str],
    params: RiskParityParams,
    fallback: AllocatorParams,
    volatility: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], str]:
    """리스크 패리티 목표 비중과 **어느 경로로 났는지**.

    돌려주는 문자열이 경로다: ``"risk_parity"`` 이면 위험 구조로, ``"fallback"``
    이면 스코어 비례로 물러섰다는 뜻이다. 호출부가 이것을 로그에 남긴다 —
    비중이 왜 그 모양인지 사후에 못 대면 운영할 수 없다.
    """
    model = factor_model.estimate(
        store, as_of=as_of, entities=entities, market=market, window=params.window
    )
    if model is None or model.covariance.empty:
        return allocate(scores=scores, params=fallback, volatility=volatility), "fallback"

    cov = model.covariance
    sectors = ksic.roll_up_map(
        sector_map(
            store, as_of=as_of, entities=list(cov.index),
            market=market, source=SECTOR_SOURCE,
        )
    )
    base = risk_parity.construct_baseline(
        scores=scores, cov=cov, sectors=sectors, tilt=params.score_tilt
    )
    if base.empty:
        return allocate(scores=scores, params=fallback, volatility=volatility), "fallback"

    downside = _downside_betas(
        store, as_of=as_of, market=market, sectors=sectors,
        entities=list(base.index), window=params.window,
    )
    projected = constraints.project(
        base,
        cov=cov,
        sectors=sectors,
        downside_beta=downside,
        name_rc_cap=params.name_rc_cap,
        sector_rc_cap=params.sector_rc_cap,
        downside_beta_cap=params.downside_beta_cap,
        cash_floor=0.0,  # 레짐 현금은 exposure.apply 가 정한다
    )
    weights = {
        entity: round(float(value), 10)
        for entity, value in projected.items()
        if value > 0.0
    }
    return weights, "risk_parity"
