"""세션 피처 캐시 — RL 학습이 매 스텝 창고를 다시 읽지 않게 한다.

M4 의 병목은 정책도 보상도 아니라 **읽기**였다. `allocator/env.py` 의 한 스텝이
0.5~3.6초인데(실측, 2026-08-18) 그 대부분이 후보 선정·시세·지수·환율을 창고에서
다시 퍼오는 시간이다. 250스텝 에피소드가 몇 분이면 PPO 가 요구하는 32환경 ×
20M 스텝은 계산상 불가능하다.

## 경계가 이 파일의 전부다 — 무엇을 캐시하면 안 되는가

이 파일에 들어오는 값은 **세션 하나(as_of)로 완전히 결정되고 에이전트의 액션과
무관한 것**뿐이다. 그 경계는 아래 두 목록이고, 이 목록이 곧 학습의 정합성이다.

**캐시해도 되는 것 — 세션만으로 결정된다**

- 후보 선정(`selector.pipeline.run`) · 합성점수 · Analyst별 점수/신뢰도
- 종가 · 20일 평균 거래대금 · 변동성 · 베타
- 체결 시점의 시장 상태(`backtest.market.states`) — 그 종목의 그날 봉이다
- 레짐 · 환율 · 지수 수익률 · 한미 금리차 · Analyst 슬롯

**절대 캐시하면 안 되는 것 — 액션에 의존한다**

- 포트폴리오 피처(`_portfolio_features`) · 실현비중 · 주문가능금액 · 장부
- 체결 결과(`replay.fills.simulate_fill`) — 주문에 의존한다
- NAV · 낙폭 · 회전율 · 액션 반영률 — 전부 직전 스텝의 결과다

**후자를 여기 넣는 순간 학습은 조용히 틀린다.** 같은 세션의 캐시를 32개 환경이
공유하는데 그 안에 한 환경의 포트폴리오 상태가 들어 있으면, 다른 31개는 자기가
하지 않은 거래의 결과로 보상을 받는다. 그 오류는 예외를 내지 않고 학습 곡선에도
안 보인다 — 승격한 뒤 실계좌에서만 보인다.

⚠️ 위 경계를 넓히고 싶어졌다면, 넓히기 전에 "이 값이 액션이 달랐어도 같은가" 를
먼저 답하라. 답이 "대개 같다" 면 캐시하면 안 된다.

## 캐시 유무가 값을 바꾸지 않는 이유

builder 와 env 가 **같은 함수**를 쓴다. 아래 `SessionReader` 가 창고 경로이고,
`CachedSessionReader` 는 그 위에 파일을 얹은 것뿐이다. builder 는 `SessionReader`
를 그대로 불러 결과를 굽는다 — 굽는 쪽과 읽는 쪽에 같은 계산이 두 벌 있으면
언젠가 갈라지고, 갈라진 순간 성적표는 아무 말도 해주지 않는다.

여기에 얹힌 값들이 **종목 단위로 독립**이라는 점이 두 경로가 같은 값을 내는
근거다. `market_stats` · `states` · `_betas` · `combined_scores` 는 전부
``groupby(entity_id)`` 이므로, 어떤 종목 목록으로 물어도 한 종목의 답이 같다.
후보 선정만은 횡단면(상관 페널티·상위 N)이라 통째로 굽는다.

## 자본(equity) 의존 — 유일한 예외를 표지로 막는다

`selector.pipeline.run` 은 `equity` 를 받는다. 쓰이는 곳은 딱 하나,
`filters.tradable_universe` 의 "1주 가격이 자본의 15% 를 넘으면 뺀다" 다.
그래서 캐시는 **그 이유로 탈락한 종목이 0개일 때만** 자본과 무관해진다 — 굽는
자본에서 아무도 안 걸렸다면, 그보다 **큰** 자본에서도 아무도 안 걸린다(문턱이
올라가므로). 그 사실을 ``equity_floor`` 로 적어 두고, env 는 자기 자본이 그
아래면 캐시를 쓰지 않고 창고로 간다.

굽는 자본은 `allocator.env.cache_equity` 다. 에피소드가 낙폭 상한에서 끝나므로
NAV 가 그 아래로 내려갈 일이 없게 잡는 값이다.

## 표지(stamp)

파일마다 창고 경로·시장·세션·설정 지문을 적는다. **다른 창고의 캐시를 읽는
사고**가 이 저장소에서 가장 조용한 사고이기 때문이다 — 값이 그럴듯해서 아무도
안 묻는다. 표지가 어긋나면 무시하지 않고 `CacheStampMismatch` 로 멈춘다.

## 배치

    <cache_root>/v1/<MARKET>/<YYYY-MM-DD>.pq   (실제 확장자는 `cache_path` 참조)

세션당 파일 하나다. 파일 개수가 곧 읽기 비용인 창고와 달리(flows 조각 109만 개)
여기는 한 스텝이 파일 **하나**만 열므로 세션 축으로 쪼개는 것이 맞다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pyarrow as pa

# **불변식 1 의 예외가 아니라 적용 범위 밖이다.** 게이트가 지키는 것은 *창고*
# 이고(``observed_at <= as_of``, 정정본 선택), 여기서 여닫는 파일은 창고가
# 아니라 **창고에서 게이트를 거쳐 읽은 값을 접어 둔 파생물**이다. 이 파일의
# 모든 입력은 위 `SessionReader` 를 통해 ``store.get(as_of=...)`` 로 들어온다 —
# 캐시를 읽는다고 게이트를 건너뛰는 경로가 생기지 않는다.
#
# 창고 테이블로 만들지 않은 이유: 캐시는 append-only 이중시간 사실이 아니라
# **언제든 지우고 다시 구울 수 있는 계산 결과**다. 창고에 넣으면 학습을 다시
# 돌릴 때마다 지울 수 없는 행이 쌓이고, 그 행을 다른 질의가 사실로 읽는다.
import pyarrow.parquet as pq  # invariant-allow: data-access

from quant_rl_trading.accounting.ledger import FX_USDKRW
from quant_rl_trading.analysts.regime import RegimeAnalyst
from quant_rl_trading.backtest import market as market_module
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.replay.fills import MarketState
from quant_rl_trading.selector import pipeline as selector_pipeline
from quant_rl_trading.selector.combine import combined_scores
from quant_rl_trading.selector.weights import analyst_weights, measured_weights
from quant_rl_trading.session.daily import market_stats

# **패키지의 ``config`` 함수가 같은 이름의 서브모듈을 가린다.** 모듈로 받으면
# ``store.config(name, as_of=...)`` 함수가 잡혀 조용히 AttributeError 가 난다.
from quant_rl_trading.store.config import CONFIG_TABLE, current_values

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

logger = logging.getLogger(__name__)

SIGNALS = "signals"
INDICES = "indices"
FX = "fx"
MACRO = "macro_releases"

#: 캐시 판번호. **의미가 바뀌면 올린다.** 컬럼을 더하거나 계산 규칙을 고쳤는데
#: 이 값을 안 올리면 옛 캐시가 새 코드에 조용히 섞인다.
BUILDER_VERSION = 1

CACHE_DIRNAME = "rl_cache"
METADATA_KEY = b"quant_rl_trading.rl_cache"

#: 베타·상관을 재는 창(거래일). `analysts/regime.py` 의 BETA_WINDOW 와 같다 —
#: 같은 개념을 두 창으로 재면 화면과 학습이 다른 베타를 말한다.
BETA_WINDOW = 60

#: 환율 변동성 창(거래일).
FX_WINDOW = 20

#: 종목 축에서 Analyst 가 차지하는 슬롯 수. env 의 관측 규격과 같은 값이고,
#: 여기서는 "슬롯에 누굴 앉히나" 를 굽기 위해 쓴다.
ANALYST_SLOTS = 9

#: 신호 컬럼 이름 규칙. Analyst 이름이 동적이라 컬럼도 동적이다.
SIGNAL_PREFIX = "sig::"


class CacheStampMismatch(RuntimeError):
    """캐시 파일의 표지가 지금 창고·설정과 다르다. **조용히 무시하지 않는다.**"""


# -- 세션 스칼라 ---------------------------------------------------------------


@dataclass(frozen=True)
class SessionScalars:
    """그 세션의 시장 스칼라. 종목과 무관하고, 액션과도 무관하다."""

    #: 원달러. 없으면 None — **1.0 으로 때우지 않는다** (`ledger.fx_rate`).
    fx_rate: float | None
    #: 최근 20거래일 환율 수익률 표준편차. 표본이 5개 미만이면 0.
    fx_volatility: float
    #: 벤치마크 지수의 (1일, 5일, 20일) 수익률. 표본이 모자라면 0 — 지어내지 않는다.
    index_returns: tuple[float, float, float]
    #: 한미 정책금리차(%p). 못 찾으면 0.
    rate_differential: float
    #: 레짐 판정. `analysts/regime.py` 의 값 그대로.
    regime_state: str


@dataclass(frozen=True)
class SelectionView:
    """후보 선정의 결과 중 **env 가 실제로 쓰는 것만**.

    `Selection` 을 통째로 담지 않는 이유는 캐시가 안 쓰는 것까지 굽기 시작하면
    무엇이 계약인지 흐려지기 때문이다. env 가 보는 것은 (종목, 점수) 순서와
    흔적 노트 다섯 줄뿐이다.
    """

    candidates: tuple[tuple[str, float], ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class EntityStats:
    """`session.daily.market_stats` 의 세 사전. 이름을 붙여 자리를 못 바꾸게 한다."""

    prices: dict[str, float]
    adv: dict[str, float]
    volatility: dict[str, float]


# -- 창고 경로 ------------------------------------------------------------------


class SessionReader:
    """세션 하나로 결정되는 값을 **창고에서** 읽는다.

    env 와 builder 가 같이 쓰는 유일한 구현이다. 여기에 없는 것(포트폴리오·
    체결 결과)은 캐시 대상이 아니다 — 모듈 독스트링의 경계를 코드로 옮긴 것이
    이 클래스의 메서드 목록이다.

    ## as_of 는 인자다, 상태가 아니다

    리더 하나가 여러 세션을 본다. as_of 를 필드로 들면 "지금 몇 시인가" 가
    두 곳(리더·시계)에 생기고, 둘이 어긋나는 순간 미래를 읽는다(불변식 1).

    ## 같은 as_of 를 두 번 사지 않는다

    스칼라는 한 스텝에서 서너 번 필요하다(`_fx_rate`·`_portfolio_features`·
    `_benchmark_return`). 매번 창고로 가면 같은 답을 세 번 산다. as_of 하나짜리
    메모를 든다 — **게이트를 우회하는 것이 아니라 같은 질의를 접는 것이다.**
    """

    def __init__(self, store: Store, market: str) -> None:
        self.store = store
        self.market = str(market)
        self._regime = RegimeAnalyst(store, ReplayClock(_EPOCH))
        self._scalars_at: datetime | None = None
        self._scalars: SessionScalars | None = None
        self._index_at: datetime | None = None
        self._index: pd.Series | None = None

    # -- 후보 --------------------------------------------------------------

    def selection(self, as_of: datetime, *, equity: float) -> SelectionView:
        selection = selector_pipeline.run(
            self.store, as_of=as_of, market=self.market, equity=equity
        )
        return SelectionView(
            candidates=tuple(
                (str(item.entity_id), float(item.score)) for item in selection.candidates
            ),
            notes=tuple(str(note) for note in selection.trace.notes[:5]),
        )

    # -- 종목 축 ------------------------------------------------------------

    def stats(self, as_of: datetime, entities: Sequence[str]) -> EntityStats:
        prices, adv, volatility = market_stats(
            self.store, as_of=as_of, entities=list(entities), market=self.market
        )
        return EntityStats(prices=prices, adv=adv, volatility=volatility)

    def signals(
        self, as_of: datetime, entities: Sequence[str]
    ) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, float]]]:
        """(합성점수, (종목,Analyst)→(score,confidence)).

        합성은 `analyst_weights` — **제약 Analyst 가 빠진 알파 가중치**다
        (태스크 #32). Analyst별 칸에는 `risk` 도 그대로 들어간다: RL 이 위험을
        보는 것은 옳고, 그것이 알파 점수인 척 섞이는 것은 틀리다.
        """
        if not entities:
            return {}, {}
        frame = self.store.get(
            SIGNALS, as_of=as_of, entity=list(entities), lookback=5
        )
        alpha = analyst_weights(self.store, as_of=as_of, market=self.market)
        combined = combined_scores(frame, alpha)
        return (
            {str(key): float(value) for key, value in combined.items()},
            latest_signals(frame),
        )

    def betas(self, as_of: datetime, entities: Sequence[str]) -> dict[str, float]:
        """지수 대비 베타. 공분산/분산 — 회귀와 같은 값이고 훨씬 싸다."""
        if not entities:
            return {}
        index = self.index_series(as_of)
        if len(index) < BETA_WINDOW + 1:
            return {}
        index_returns = index.pct_change(fill_method=None).dropna().tail(BETA_WINDOW)
        variance = float(index_returns.var())
        if not math.isfinite(variance) or variance == 0.0:
            return {}

        from quant_rl_trading.store.prices import read_prices

        frame = read_prices(
            self.store,
            as_of=as_of,
            entity=list(entities),
            lookback=BETA_WINDOW * 3,
            market=self.market,
            columns=["close", "adj_factor"],
            # 베타는 수익률로 재므로 **보정가**다. 분할 하루가 -90% 로 남으면
            # 그 종목의 베타가 통째로 가짜가 된다.
            adjusted=True,
        )
        if frame.empty:
            return {}
        out: dict[str, float] = {}
        centered = index_returns - float(index_returns.mean())
        for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
            closes = group.set_index(group["valid_from"].dt.date)["close"].astype(float)
            returns = (
                closes.pct_change(fill_method=None).dropna().reindex(index_returns.index)
            )
            returns = returns.dropna()
            if len(returns) < BETA_WINDOW // 2:
                continue
            aligned = centered.reindex(returns.index)
            covariance = float(((returns - returns.mean()) * aligned).mean())
            out[str(entity)] = covariance / variance
        return out

    def fill_states(
        self, as_of: datetime, entities: Sequence[str]
    ) -> dict[str, MarketState]:
        """체결일의 종목별 시장 상태. **체결 자체가 아니라 그 입력이다.**

        `simulate_fill` 은 순수 함수라 (주문, 시장상태) → 체결이다. 앞의 것은
        액션이라 캐시 밖이고, 뒤의 것은 그날 봉이라 캐시 안이다.
        """
        return market_module.states(
            self.store, as_of=as_of, entities=list(entities), market=self.market
        )

    # -- 세션 스칼라 --------------------------------------------------------

    def scalars(self, as_of: datetime) -> SessionScalars:
        if self._scalars_at == as_of and self._scalars is not None:
            return self._scalars
        fx = self._fx_series(as_of)
        fx_rate = float(fx.iloc[-1]) if not fx.empty else None
        returns = fx.pct_change(fill_method=None).dropna().tail(FX_WINDOW)
        index = self.index_series(as_of)
        scalars = SessionScalars(
            fx_rate=fx_rate,
            fx_volatility=float(returns.std()) if len(returns) >= 5 else 0.0,
            index_returns=(
                window_return(index, 1),
                window_return(index, 5),
                window_return(index, 20),
            ),
            rate_differential=self._rate_differential(as_of),
            regime_state=self._regime_state(as_of),
        )
        self._scalars_at, self._scalars = as_of, scalars
        return scalars

    def analyst_slots(self, as_of: datetime) -> tuple[str, ...]:
        """관측에 앉힐 Analyst. **이름을 코드에 적지 않는다.**

        `measured_weights` 를 쓰는 이유는 여기가 **관측**이기 때문이다 —
        `risk` 는 알파에서 빠졌지만 RL 이 위험을 보는 것은 옳다(태스크 #32).
        """
        names = set(measured_weights(self.store, as_of=as_of, market=self.market))
        frame = self.store.get(
            SIGNALS, as_of=as_of, lookback=5, columns=["analyst"]
        )
        if not frame.empty:
            # 가중치 0(관찰 모드)인 Analyst 도 점수는 낸다. 그 점수를 관측에서
            # 빼면 RL 은 "아직 못 미더운 신호" 를 아예 못 본다.
            names |= {str(value) for value in frame["analyst"].unique()}
        ordered = sorted(names)[:ANALYST_SLOTS]
        if len(names) > ANALYST_SLOTS:
            logger.warning(
                "Analyst %d명 중 %d명만 관측에 실린다: %s",
                len(names),
                ANALYST_SLOTS,
                ", ".join(sorted(names - set(ordered))),
            )
        return tuple(ordered)

    # -- 내부 --------------------------------------------------------------

    def index_series(self, as_of: datetime) -> pd.Series:
        """벤치마크 KR 지수의 일별 종가. 이름은 `config.benchmark` 에서 온다."""
        if self._index_at == as_of and self._index is not None:
            return self._index
        entity = str(self.store.config("benchmark.kr_index", as_of=as_of))
        frame = self.store.get(INDICES, as_of=as_of, entity=entity, lookback=180)
        if frame.empty:
            series = pd.Series(dtype=float)
        else:
            ordered = frame.sort_values(["valid_from", "observed_at"])
            series = (
                ordered.groupby(ordered["valid_from"].dt.date)["close"]
                .last()
                .astype(float)
            )
        self._index_at, self._index = as_of, series
        return series

    def _fx_series(self, as_of: datetime) -> pd.Series:
        frame = self.store.get(
            FX, as_of=as_of, entity=FX_USDKRW, lookback=FX_WINDOW * 3
        )
        if frame.empty:
            return pd.Series(dtype=float)
        ordered = frame.sort_values(["valid_from", "observed_at"])
        return (
            ordered.groupby(ordered["valid_from"].dt.date)["rate"].last().astype(float)
        )

    def _regime_state(self, as_of: datetime) -> str:
        """레짐. `analysts/regime.py` 의 판정을 그대로 쓴다.

        화면·신호와 다른 판정을 여기서 새로 만들면, 같은 날의 시장 상태를
        시스템이 두 개 갖게 된다.
        """
        self._regime.clock = ReplayClock(as_of)
        return str(self._regime.state(as_of))

    def _rate_differential(self, as_of: datetime) -> float:
        """한미 정책금리차(%p). 계열 이름은 `store.config` 에서 온다.

        없으면 0 이다 — **0 은 "차이가 없다" 가 아니라 "모른다" 인데**, 관측
        텐서에는 결측을 표현할 자리가 없다. 발표가 월 단위라 최근 400일을 본다.
        """
        frame = self.store.get(
            MACRO, as_of=as_of, lookback=400, columns=["entity_id", "actual"]
        )
        if frame.empty:
            return 0.0
        kr_name = str(self.store.config("allocator.env.kr_policy_rate_series", as_of=as_of))
        us_name = str(self.store.config("allocator.env.us_policy_rate_series", as_of=as_of))

        def latest(entity: str) -> float | None:
            rows = frame[frame["entity_id"] == entity].dropna(subset=["actual"])
            return float(rows["actual"].iloc[-1]) if not rows.empty else None

        kr, us = latest(kr_name), latest(us_name)
        if kr is None or us is None:
            logger.debug("정책금리를 못 찾았다: kr=%s us=%s", kr, us)
            return 0.0
        return kr - us


#: RegimeAnalyst 는 생성 시 Clock 을 요구하는데, 판정할 때마다 as_of 로 갈아
#: 끼운다(`_regime_state`). 그래서 이 값은 **한 번도 쓰이지 않는다** — 벽시계를
#: 부르지 않으려고 두는 자리표시다 (불변식 2).
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


# -- 순수 도우미 ----------------------------------------------------------------


def latest_signals(signals: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    """(종목, Analyst) → (score, confidence). **가장 늦은 관측** 하나만.

    `combine.combined_scores` 와 같은 규칙이다 — 여기서 다른 규칙을 쓰면
    관측의 Analyst 칸과 합성점수 칸이 서로 다른 날의 신호를 가리킨다.
    """
    if signals.empty:
        return {}
    frame = signals
    if "observed_at" in frame.columns:
        frame = (
            frame.sort_values("observed_at")
            .groupby(["entity_id", "analyst"], as_index=False)
            .tail(1)
        )
    return {
        (str(row["entity_id"]), str(row["analyst"])): (
            float(row["score"]),
            float(row["confidence"]),
        )
        for row in frame.to_dict(orient="records")
    }


def window_return(series: pd.Series, span: int) -> float:
    """``span`` 세션 전 대비 수익률. 표본이 모자라면 0 — 지어내지 않는다."""
    if len(series) <= span:
        return 0.0
    previous = float(series.iloc[-span - 1])
    if previous <= 0:
        return 0.0
    return float(series.iloc[-1]) / previous - 1.0


# -- 구운 결과 ------------------------------------------------------------------


@dataclass(frozen=True)
class Stamp:
    """이 캐시가 **어느 창고·어느 설정**에서 나왔는지.

    다른 창고의 캐시를 읽는 사고는 예외를 내지 않는다 — 값이 그럴듯하기
    때문이다. 그래서 표지를 파일에 박고 읽을 때마다 대조한다.
    """

    builder_version: int
    store_root: str
    market: str
    session: str
    as_of: str
    config_fingerprint: str

    def to_json(self) -> dict[str, Any]:
        return {
            "builder_version": self.builder_version,
            "store_root": self.store_root,
            "market": self.market,
            "session": self.session,
            "as_of": self.as_of,
            "config_fingerprint": self.config_fingerprint,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Stamp:
        return cls(
            builder_version=int(raw["builder_version"]),
            store_root=str(raw["store_root"]),
            market=str(raw["market"]),
            session=str(raw["session"]),
            as_of=str(raw["as_of"]),
            config_fingerprint=str(raw["config_fingerprint"]),
        )


def config_fingerprint(store: Store, *, as_of: datetime) -> str:
    """그 시점 설정 전체의 지문.

    임계치 하나가 바뀌면 후보도 점수도 달라진다. 어느 키가 캐시에 영향을
    주는지 손으로 열거하면 **키가 늘 때마다 그 목록이 낡는다** — 낡은 목록은
    "영향 없음" 이라고 거짓말한다. 그래서 전부 센다.
    """
    values = current_values(store.get(CONFIG_TABLE, as_of=as_of))
    payload = json.dumps(
        {name: value for name, (value, _revision) in sorted(values.items())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SessionCache:
    """한 세션의 구운 피처. 파일 하나와 1:1 이다."""

    stamp: Stamp
    scalars: SessionScalars
    selection: SelectionView
    analyst_slots: tuple[str, ...]
    #: 이 캐시가 유효한 최소 자본. 이보다 작은 자본으로 물으면 후보 선정이
    #: 달라질 수 있다(모듈 독스트링의 "자본 의존"). env 는 창고로 되돌아간다.
    equity_floor: float
    #: **행이 있는 종목 집합.** 여기 있는 종목의 결측은 "창고에도 없다" 는
    #: 뜻이고, 여기 없는 종목은 "안 구웠다" 는 뜻이다. 둘을 못 가르면 캐시가
    #: 조용히 0 을 만들어 낸다.
    covered: frozenset[str]
    combined: dict[str, float]
    stats: EntityStats
    betas: dict[str, float]
    signals: dict[tuple[str, str], tuple[float, float]]
    fill_states: dict[str, MarketState]

    def misses(self, entities: Iterable[str]) -> list[str]:
        return [entity for entity in entities if entity not in self.covered]


# -- 굽기 ----------------------------------------------------------------------


def cache_path(root: Path, market: str, session: date) -> Path:
    directory = Path(root) / f"v{BUILDER_VERSION}" / str(market).upper()
    return directory / f"{session.isoformat()}.parquet"  # invariant-allow: data-access


def default_cache_root(store: Store) -> Path:
    """창고 옆에 둔다. **캐시가 창고를 따라다녀야** 표지 대조가 뜻을 갖는다."""
    return Path(store.root) / CACHE_DIRNAME


def build_session(
    reader: SessionReader,
    *,
    as_of: datetime,
    session: date,
    equity: float,
    extra_entities: Sequence[str] = (),
) -> SessionCache:
    """세션 하나를 굽는다. **읽기는 전부 이 as_of 로만 한다** (불변식 1).

    ``extra_entities`` 는 후보 밖인데도 행을 남겨 둘 종목이다. 학습 중 보유
    종목이 그날 후보에서 빠지는 일이 흔한데, 그때마다 창고로 되돌아가면
    캐시의 뜻이 없어진다. **값에는 영향이 없다** — 덜 구우면 느려질 뿐이고,
    더 구워도 종목별 계산이 독립이라 답이 달라지지 않는다.
    """
    store = reader.store
    selection = reader.selection(as_of, equity=equity)
    entities = list(
        dict.fromkeys([entity for entity, _score in selection.candidates] + list(extra_entities))
    )

    combined, signals = reader.signals(as_of, entities)
    stats = reader.stats(as_of, entities)
    betas = reader.betas(as_of, entities)
    fill_states = reader.fill_states(as_of, entities)
    scalars = reader.scalars(as_of)

    return SessionCache(
        stamp=Stamp(
            builder_version=BUILDER_VERSION,
            store_root=str(Path(store.root).resolve()),
            market=reader.market.upper(),
            session=session.isoformat(),
            as_of=as_of.isoformat(),
            config_fingerprint=config_fingerprint(store, as_of=as_of),
        ),
        scalars=scalars,
        selection=selection,
        analyst_slots=reader.analyst_slots(as_of),
        equity_floor=equity,
        covered=frozenset(entities),
        combined=combined,
        stats=stats,
        betas=betas,
        signals=signals,
        fill_states=fill_states,
    )


def price_capped(store: Store, *, as_of: datetime, market: str, equity: float) -> int:
    """"1주 가격이 자본의 상한 초과" 로 탈락한 종목 수.

    0 이어야 ``equity_floor`` 규칙이 성립한다(모듈 독스트링). 0 이 아니면 그
    세션의 캐시는 굽는 자본에서만 유효하므로 경고하고 굽지 않는다 — 자본이
    다른 채로 쓰면 후보 목록이 조용히 달라진다.
    """
    from quant_rl_trading.selector import filters as filters_module

    params = filters_module.FilterParams.from_store(store, as_of=as_of, market=market)
    result = filters_module.tradable_universe(
        store, as_of=as_of, market=market, params=params, equity=equity
    )
    return sum(
        1 for reason in result.dropped.values() if reason == filters_module.PRICE_CAP_REASON
    )


def write(cache: SessionCache, path: Path) -> Path:
    """원자적으로 쓴다. **중단은 예외가 아니라 기본값이다** — 몇 시간짜리
    작업이고 이 기계는 램을 다른 작업과 나눠 쓴다. 반쯤 쓰인 parquet 이 남으면
    다음 실행이 그걸 "이미 구웠다" 로 읽는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = _to_table(cache)
    temporary = path.with_suffix(".parquet.tmp")  # invariant-allow: data-access
    pq.write_table(table, temporary, compression="zstd")  # invariant-allow: data-access
    os.replace(temporary, path)
    return path


def read(path: Path) -> SessionCache:
    table = pq.read_table(path)  # invariant-allow: data-access
    raw = table.schema.metadata or {}
    if METADATA_KEY not in raw:
        raise CacheStampMismatch(f"{path} 에 표지가 없다. v1 이전 파일이거나 다른 도구가 썼다")
    meta = json.loads(raw[METADATA_KEY].decode("utf-8"))
    frame = table.to_pandas()
    return _from_frame(meta, frame)


def verify(
    cache: SessionCache, *, store: Store, market: str, session: date, as_of: datetime
) -> None:
    """표지 대조. **어긋나면 멈춘다.**

    무시하고 창고로 되돌아가는 선택지도 있는데, 그러면 "캐시가 안 먹는다" 가
    성능 문제로만 보이고 원인(다른 창고를 가리킨 채로 몇 시간 학습)은 안
    보인다. 조용한 실패보다 시끄러운 실패가 낫다.
    """
    stamp = cache.stamp
    expected_root = str(Path(store.root).resolve())
    problems: list[str] = []
    if stamp.builder_version != BUILDER_VERSION:
        problems.append(f"판번호 {stamp.builder_version} != {BUILDER_VERSION}")
    if stamp.store_root != expected_root:
        problems.append(f"창고 {stamp.store_root} != {expected_root}")
    if stamp.market != str(market).upper():
        problems.append(f"시장 {stamp.market} != {str(market).upper()}")
    if stamp.session != session.isoformat():
        problems.append(f"세션 {stamp.session} != {session.isoformat()}")
    fingerprint = config_fingerprint(store, as_of=as_of)
    if stamp.config_fingerprint != fingerprint:
        problems.append(f"설정 지문 {stamp.config_fingerprint} != {fingerprint}")
    if problems:
        raise CacheStampMismatch(
            "캐시 표지가 지금 창고·설정과 다르다: " + " · ".join(problems)
        )


# -- 직렬화 --------------------------------------------------------------------


def _to_table(cache: SessionCache) -> pa.Table:
    entities = sorted(cache.covered)
    rank = {entity: index for index, (entity, _score) in enumerate(cache.selection.candidates)}
    score = {entity: value for entity, value in cache.selection.candidates}
    analysts = sorted({analyst for _entity, analyst in cache.signals})

    columns: dict[str, list[Any]] = {
        "entity_id": entities,
        "candidate_rank": [rank.get(entity, -1) for entity in entities],
        "candidate_score": [score.get(entity, np.nan) for entity in entities],
        "combined_score": [cache.combined.get(entity, np.nan) for entity in entities],
        "price": [cache.stats.prices.get(entity, np.nan) for entity in entities],
        "adv_value": [cache.stats.adv.get(entity, np.nan) for entity in entities],
        "volatility": [cache.stats.volatility.get(entity, np.nan) for entity in entities],
        "beta": [cache.betas.get(entity, np.nan) for entity in entities],
    }
    # 체결 입력. `has_fill_state` 를 따로 두는 이유는 low·high 가 **정당하게**
    # 없을 수 있어서다(결측 저가). NaN 하나로는 "봉이 없다" 와 "저가만 없다" 를
    # 못 가른다.
    states = cache.fill_states
    columns["has_fill_state"] = [entity in states for entity in entities]
    for name, getter in (
        ("fill_close", lambda s: s.close),
        ("fill_volume", lambda s: s.volume),
        ("fill_adv", lambda s: s.adv),
        ("fill_volatility", lambda s: s.volatility),
        ("fill_low", lambda s: s.low),
        ("fill_high", lambda s: s.high),
        ("fill_tick_size", lambda s: s.tick_size),
    ):
        columns[name] = [
            _as_float(getter(states[entity])) if entity in states else np.nan
            for entity in entities
        ]
    for analyst in analysts:
        columns[f"{SIGNAL_PREFIX}{analyst}::score"] = [
            cache.signals.get((entity, analyst), (np.nan, np.nan))[0] for entity in entities
        ]
        columns[f"{SIGNAL_PREFIX}{analyst}::confidence"] = [
            cache.signals.get((entity, analyst), (np.nan, np.nan))[1] for entity in entities
        ]

    metadata = {
        **cache.stamp.to_json(),
        "analysts": analysts,
        "analyst_slots": list(cache.analyst_slots),
        "selection_notes": list(cache.selection.notes),
        "equity_floor": cache.equity_floor,
        "fx_rate": cache.scalars.fx_rate,
        "fx_volatility": cache.scalars.fx_volatility,
        "index_returns": list(cache.scalars.index_returns),
        "rate_differential": cache.scalars.rate_differential,
        "regime_state": cache.scalars.regime_state,
    }
    table = pa.table(columns)
    return table.replace_schema_metadata(
        {METADATA_KEY: json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")}
    )


def _from_frame(meta: Mapping[str, Any], frame: pd.DataFrame) -> SessionCache:
    entities = [str(value) for value in frame["entity_id"]]
    ranked = frame[frame["candidate_rank"] >= 0].sort_values("candidate_rank")
    candidates = tuple(
        (str(row["entity_id"]), float(row["candidate_score"]))
        for row in ranked.to_dict(orient="records")
    )

    def column(name: str) -> dict[str, float]:
        if name not in frame.columns:
            return {}
        return {
            entity: float(value)
            for entity, value in zip(entities, frame[name], strict=True)
            if not _missing(value)
        }

    fill_states: dict[str, MarketState] = {}
    for index, entity in enumerate(entities):
        if not bool(frame["has_fill_state"].iloc[index]):
            continue
        fill_states[entity] = MarketState(
            entity_id=entity,
            close=float(frame["fill_close"].iloc[index]),
            volume=float(frame["fill_volume"].iloc[index]),
            adv=float(frame["fill_adv"].iloc[index]),
            volatility=float(frame["fill_volatility"].iloc[index]),
            tick_size=float(frame["fill_tick_size"].iloc[index]),
            low=_or_none(frame["fill_low"].iloc[index]),
            high=_or_none(frame["fill_high"].iloc[index]),
        )

    signals: dict[tuple[str, str], tuple[float, float]] = {}
    for analyst in meta.get("analysts", []):
        score_column = f"{SIGNAL_PREFIX}{analyst}::score"
        confidence_column = f"{SIGNAL_PREFIX}{analyst}::confidence"
        if score_column not in frame.columns:
            continue
        for index, entity in enumerate(entities):
            score = frame[score_column].iloc[index]
            if _missing(score):
                continue
            signals[(entity, str(analyst))] = (
                float(score),
                float(frame[confidence_column].iloc[index]),
            )

    returns = list(meta["index_returns"])
    return SessionCache(
        stamp=Stamp.from_json(meta),
        scalars=SessionScalars(
            fx_rate=None if meta["fx_rate"] is None else float(meta["fx_rate"]),
            fx_volatility=float(meta["fx_volatility"]),
            index_returns=(float(returns[0]), float(returns[1]), float(returns[2])),
            rate_differential=float(meta["rate_differential"]),
            regime_state=str(meta["regime_state"]),
        ),
        selection=SelectionView(
            candidates=candidates,
            notes=tuple(str(note) for note in meta.get("selection_notes", [])),
        ),
        analyst_slots=tuple(str(name) for name in meta.get("analyst_slots", [])),
        equity_floor=float(meta["equity_floor"]),
        covered=frozenset(entities),
        combined=column("combined_score"),
        stats=EntityStats(
            prices=column("price"), adv=column("adv_value"), volatility=column("volatility")
        ),
        betas=column("beta"),
        signals=signals,
        fill_states=fill_states,
    )


def _missing(value: Any) -> bool:
    """결측인가. **NaN 은 "창고에도 없다" 는 뜻이다** — 0.0 과 다르다.

    가격 0 은 데이터 사고이고 점수 0 은 중립 의견이다. 둘을 NaN 과 같게 다루면
    캐시가 없는 값을 지어내게 된다.
    """
    return value is None or bool(pd.isna(value))


def _or_none(value: Any) -> float | None:
    return None if _missing(value) else float(value)


def _as_float(value: Any) -> float:
    return np.nan if value is None else float(value)


# -- 캐시를 얹은 리더 ------------------------------------------------------------


class CachedSessionReader(SessionReader):
    """캐시가 있으면 읽고, 없으면 창고로 간다.

    **캐시 유무가 값을 바꾸지 않는다.** 캐시에 없는 종목은 부모(창고 경로)에게
    그 종목만 다시 묻고 합친다 — 종목별 계산이 독립이라 합친 결과가 전부
    창고에서 읽은 것과 같다(모듈 독스트링).

    자본이 ``equity_floor`` 아래면 후보 선정만 창고로 되돌아간다. 종목 축 값은
    자본과 무관하므로 그대로 쓴다.
    """

    #: 메모리에 들고 있을 세션 수. 에피소드는 세션을 순서대로 지나므로 하나면
    #: 충분하지만, 체결(D+1)이 다음 세션의 상태를 물을 수 있어 여유를 둔다.
    LRU = 4

    def __init__(self, store: Store, market: str, root: Path) -> None:
        super().__init__(store, market)
        self.root = Path(root)
        self.hits = 0
        self.misses = 0
        self._loaded: OrderedDict[date, SessionCache | None] = OrderedDict()

    def session_cache(self, as_of: datetime, session: date) -> SessionCache | None:
        if session in self._loaded:
            self._loaded.move_to_end(session)
            return self._loaded[session]
        path = cache_path(self.root, self.market, session)
        cache: SessionCache | None = None
        if path.exists():
            cache = read(path)
            verify(cache, store=self.store, market=self.market, session=session, as_of=as_of)
            self.hits += 1
        else:
            self.misses += 1
        self._loaded[session] = cache
        while len(self._loaded) > self.LRU:
            self._loaded.popitem(last=False)
        return cache

    # -- 덮어쓰는 메서드 ----------------------------------------------------

    def selection(self, as_of: datetime, *, equity: float) -> SelectionView:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().selection(as_of, equity=equity)
        if equity < cache.equity_floor:
            # 자본이 굽던 때보다 작다 — 1주 가격 상한이 다르게 걸릴 수 있다.
            # 조용히 캐시를 쓰면 후보 목록이 달라지고, 그 차이는 로그에 안 뜬다.
            logger.debug(
                "%s 자본 %.0f < equity_floor %.0f — 후보만 창고에서 다시 읽는다",
                as_of.date(),
                equity,
                cache.equity_floor,
            )
            return super().selection(as_of, equity=equity)
        return cache.selection

    def stats(self, as_of: datetime, entities: Sequence[str]) -> EntityStats:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().stats(as_of, entities)
        missing = cache.misses(entities)
        prices = {e: v for e, v in cache.stats.prices.items() if e in set(entities)}
        adv = {e: v for e, v in cache.stats.adv.items() if e in set(entities)}
        volatility = {e: v for e, v in cache.stats.volatility.items() if e in set(entities)}
        if missing:
            extra = super().stats(as_of, missing)
            prices.update(extra.prices)
            adv.update(extra.adv)
            volatility.update(extra.volatility)
        return EntityStats(prices=prices, adv=adv, volatility=volatility)

    def signals(
        self, as_of: datetime, entities: Sequence[str]
    ) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, float]]]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().signals(as_of, entities)
        wanted = set(entities)
        missing = cache.misses(entities)
        combined = {e: v for e, v in cache.combined.items() if e in wanted}
        latest = {key: value for key, value in cache.signals.items() if key[0] in wanted}
        if missing:
            extra_combined, extra_latest = super().signals(as_of, missing)
            combined.update(extra_combined)
            latest.update(extra_latest)
        return combined, latest

    def betas(self, as_of: datetime, entities: Sequence[str]) -> dict[str, float]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().betas(as_of, entities)
        wanted = set(entities)
        out = {e: v for e, v in cache.betas.items() if e in wanted}
        missing = cache.misses(entities)
        if missing:
            out.update(super().betas(as_of, missing))
        return out

    def fill_states(
        self, as_of: datetime, entities: Sequence[str]
    ) -> dict[str, MarketState]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().fill_states(as_of, entities)
        wanted = set(entities)
        out = {e: v for e, v in cache.fill_states.items() if e in wanted}
        missing = cache.misses(entities)
        if missing:
            out.update(super().fill_states(as_of, missing))
        return out

    def scalars(self, as_of: datetime) -> SessionScalars:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().scalars(as_of)
        return cache.scalars

    def analyst_slots(self, as_of: datetime) -> tuple[str, ...]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().analyst_slots(as_of)
        return cache.analyst_slots


def build_reader(
    store: Store, market: str, *, cache_root: Path | None = None, use_cache: bool = True
) -> SessionReader:
    """env 가 부르는 진입점. **캐시가 없으면 그냥 창고 경로다.**"""
    if not use_cache:
        return SessionReader(store, market)
    root = Path(cache_root) if cache_root is not None else default_cache_root(store)
    if not root.exists():
        return SessionReader(store, market)
    return CachedSessionReader(store, market, root)
