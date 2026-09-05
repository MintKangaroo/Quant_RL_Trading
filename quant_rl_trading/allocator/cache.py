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

설정 지문은 **이 경로가 읽는 키 위에서만** 계산한다(`CONFIG_DEPENDENCIES`).
전체 설정 위에서 재던 시절에는 브로커 계좌 두 줄을 심은 것만으로 구워 둔
캐시가 통째로 무효가 됐다 — 그러면 사람은 설정을 안 고치거나 캐시를 안
굽는다. 좁히다 빠뜨리는 쪽이 훨씬 위험하므로, 그 목록은 손이 아니라 시험이
지킨다(`tests/allocator/test_cache_config_scope.py`).

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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
        #: as_of → 지수 시계열. 한 칸짜리 메모였는데, 학습은 32개 환경이 서로
        #: 다른 세션을 같은 리더로 두드려서 매 호출이 미스가 됐다(2026-08-26).
        #: 세션 하나가 시리즈 180개라 수천 세션을 다 들어도 램은 몇 MB 다.
        self._index_memo: dict[datetime, pd.Series] = {}

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
        memo = self._index_memo.get(as_of)
        if memo is not None:
            return memo
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
        self._index_memo[as_of] = series
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

        **`macro_releases` 가 아니라 `indices` 를 읽는다.** 금리가 저 표에도
        있어서 원래 저기서 읽었는데, 그 표는 "언제 무엇이 발표되나" 를 담는
        **일정** 표라 `valid_from` 이 설계상 수집 시각이다. 그래서 과거 as_of
        로 조회하면 한 행도 안 걸리고, 이 칸이 200k 스텝 내내 0 이었다
        (2026-08-19 실측). 시계열로 읽어야 하는 값은 `indices` 에 온다.

        섹터·USD 칸이 비어 있는 것은 설계지만 이건 아니었다 — **비어 있는
        칸을 볼 때 "원래 그런 것" 과 "읽는 표가 틀린 것" 은 겉모습이 같다.**
        """
        frame = self.store.get(
            INDICES, as_of=as_of, lookback=400, columns=["entity_id", "close"]
        )
        if frame.empty:
            return 0.0
        kr_name = str(self.store.config("allocator.env.kr_policy_rate_series", as_of=as_of))
        us_name = str(self.store.config("allocator.env.us_policy_rate_series", as_of=as_of))

        def latest(entity: str) -> float | None:
            rows = frame[frame["entity_id"] == entity].dropna(subset=["close"])
            return float(rows["close"].iloc[-1]) if not rows.empty else None

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
    #: 지문을 만든 설정 값 그대로(`CONFIG_DEPENDENCIES` 범위). **지문만으로는
    #: "무엇이 달라졌나" 를 답할 수 없다** — 어긋난 사람이 보는 것은
    #: ``9317fab8 != b56e4b15`` 하나뿐이고, 거기서 다음 행동이 안 나온다.
    #: 옛 형식 파일에는 없다(None).
    config_values: dict[str, str] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "builder_version": self.builder_version,
            "store_root": self.store_root,
            "market": self.market,
            "session": self.session,
            "as_of": self.as_of,
            "config_fingerprint": self.config_fingerprint,
            "config_values": self.config_values,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Stamp:
        values = raw.get("config_values")
        return cls(
            builder_version=int(raw["builder_version"]),
            store_root=str(raw["store_root"]),
            market=str(raw["market"]),
            session=str(raw["session"]),
            as_of=str(raw["as_of"]),
            config_fingerprint=str(raw["config_fingerprint"]),
            config_values=(
                None if values is None else {str(k): str(v) for k, v in values.items()}
            ),
        )


#: 지문이 세는 설정. **이름 하나는 그 키와 그 아래 섹션 전부**를 가리킨다 —
#: ``"universe"`` 는 ``universe.min_listed_days`` 를 포함한다.
#:
#: ## 왜 전체가 아닌가 (2026-08-22)
#:
#: 지문은 원래 설정 **전체** 위에서 계산했다. "어느 키가 캐시에 영향을 주는지
#: 손으로 열거하면 그 목록이 낡는다" 는 이유였는데, 그 대가가 실제로 나왔다:
#: 브로커 계좌 설정 두 줄(``execution.account_mode`` ·
#: ``execution.live_account_fingerprint_paper``)을 창고에 심었더니 구워 둔 캐시가
#: 통째로 무효가 됐다. 이 경로는 그 키들을 읽지 않는다. 그러면 사람은 둘 중
#: 하나를 안 하게 된다 — **설정을 안 고치거나 캐시를 안 굽거나.** 둘 다 나쁘다.
#:
#: ## 좁히기의 위험과 그것을 막는 방법
#:
#: 반대 방향의 사고가 훨씬 나쁘다. 진짜 영향을 주는 키를 빠뜨리면 환경이
#: 달라졌는데 캐시는 유효하다고 말하고, 그 위에서 나온 학습 결과는 아무 데도
#: 안 뜨는 거짓이 된다. **과잉 포함은 시끄럽고 안전하며, 잘못된 좁히기는
#: 조용하고 위험하다.**
#:
#: 그래서 이 목록을 손으로 지키지 않는다. `tests/allocator/test_cache_config_scope.py`
#: 가 ``allocator/env.py`` · ``allocator/cache.py`` · ``tools/build_rl_cache.py``
#: 에서 시작하는 import 닫힘을 훑어 **거기서 읽는 모든 설정 이름**을 뽑고, 이
#: 목록이 그것을 전부 덮는지 본다. 새 키를 읽는 코드가 들어오면 그 시험이 깨진다.
#:
#: 목록에는 import 로 닿지만 이 경로에서 실제로는 안 도는 코드가 읽는 키도
#: 들어 있다(``executor.orders`` 의 슬라이스 설정, ``executor.guards`` 의
#: 킬스위치). 더 좁히려면 "저 코드는 안 돈다" 를 사람이 매번 판단해야 하고,
#: 그 판단이 한 번 틀리면 위의 조용한 사고가 된다. 시끄러운 쪽에 남긴다.
CONFIG_DEPENDENCIES: tuple[str, ...] = (
    # 수수료·세금·환산 — 체결 손익과 NAV 를 바꾼다.
    "accounting.capital_gains_allowance_krw",
    "accounting.capital_gains_us",
    "accounting.dividend_tax_kr",
    "accounting.dividend_tax_us",
    "accounting.fee_kr",
    "accounting.fee_us",
    "accounting.snapshot_time",
    "accounting.transaction_tax_kr",
    # 환경 규격 — 슬롯 수·에피소드 길이·비중 상한·자본·정책금리 계열.
    "allocator.baseline",
    "allocator.cash_buffer",
    "allocator.env.cache_carry_sessions",
    "allocator.env.cache_equity",
    "allocator.env.initial_capital",
    "allocator.env.kr_policy_rate_series",
    "allocator.env.max_entry_delay_days",
    "allocator.env.us_policy_rate_series",
    "allocator.episode_days",
    "allocator.max_position_weight",
    "allocator.n_max_candidates",
    # 리스크 패리티 룰 베이스라인(§3) 손잡이. **RL env 가 직접 읽지는 않지만**
    # cache.py 가 session.daily 를 import 하고 그 모듈이 risk_parity_baseline 을
    # 물어, import 닫힘이 이 키들에 닿는다. baseline: risk_parity 로 학습하면
    # 이 값들이 베이스라인을 바꾸므로 지문에 있어야 맞다 — score_tilt 는 진화
    # 유전자가 될 값이라 특히 그렇다 (test_cache_config_scope 가 강제한다).
    "allocator.downside_beta_cap",
    "allocator.name_risk_cap",
    "allocator.risk_window",
    "allocator.score_tilt",
    "allocator.sector_risk_cap",
    # Analyst 가중치가 IC 문턱에서 나온다 — 문턱이 바뀌면 합성점수가 바뀐다.
    "analyst.ic_min_samples",
    "analyst.ic_t_min",
    "analyst.ic_threshold",
    # 섹션째 읽는다(`accounting/benchmark.py`), 이름을 만들어 읽기도 한다
    # (`session/daily.py` 의 f"benchmark.{market}_index").
    "benchmark",
    "data_quality.missing_warn",
    # **execution 은 섹션째 넣지 않는다.** 계좌 모드·실계좌 지문이 여기 있고,
    # 그것이 이 목록을 만든 이유다. 읽는 키만 이름으로 적는다.
    "execution.defer_minutes",
    "execution.impact_k",
    "execution.max_adv_ratio",
    "execution.max_liquidation_days",
    "execution.max_slippage",
    "execution.min_order_value",
    "execution.settlement_days",
    "execution.slice_count",
    "execution.slice_interval_sec",
    "exposure",
    "killswitch.drawdown_trigger",
    "killswitch.liquidate_on_trigger",
    "reward",
    "selector.corr_penalty",
    "selector.corr_threshold",
    "selector.n_candidates",
    "selector.risk_floor_percentile",
    "selector.sector_cap",
    # 유니버스 문턱. 시장별 이름을 만들어 읽으므로(`f"universe.{key}"`) 섹션째다.
    "universe",
)


#: 접두는 맞지만 캐시가 **안 쓰는** 키. `exposure.regime_confirm_sessions` 는 세션의 노출 배수
#: 확인 기간(selector/exposure.py)이라 구운 피처와 무관한데, 2026-08-28 에 새 키로 심기며
#: 에포크 시점부터 발효된 것으로 들어와 930개 세션 캐시의 지문을 전부 깨뜨렸다(학습 재개 실패).
#: 새 키를 심을 땐 이 목록·접두를 먼저 본다 — 지문은 "캐시 내용이 바뀌는 키" 만 세야 한다.
#: `selector.exit_rank` 도 같다 — 완충 구간은 **보유 종목**이 있어야 서는데 캐시는 보유를 모르고
#: 굽는다(`selection(... held=None)`), 그래서 구운 후보는 이 키와 무관하다.
CONFIG_INDEPENDENT = frozenset(
    {
        "exposure.regime_confirm_sessions",
        "selector.exit_rank",
        # 정책을 어느 장부에 끼우나 — 세션(allocator/live.py)만 읽는다. 환경은 모른다.
        "allocator.rl.checkpoint",
        "allocator.rl.modes",
    }
)


def depends_on(name: str) -> bool:
    """설정 이름 하나가 이 경로에 영향을 주는가. `CONFIG_DEPENDENCIES` 가 정의다."""
    if name in CONFIG_INDEPENDENT:
        return False
    return any(name == dep or name.startswith(f"{dep}.") for dep in CONFIG_DEPENDENCIES)


def dependent_config(
    store: Store, *, as_of: datetime, prefetched: pd.DataFrame | None = None
) -> dict[str, str]:
    """그 시점에 **발효돼 있던** 의존 설정. 이름 → 값(JSON 문자열).

    ``valid_from <= as_of`` 를 한 번 더 거는 이유는 `store.config` 가 그렇게
    읽기 때문이다(`store/config.read_value`). 게이트가 걸러주는 것은
    ``observed_at`` 뿐이라, "다음 달부터 올린다" 를 오늘 적어 두면 관측은
    오늘이지만 발효는 다음 달이다. 그 행을 지문이 세면 **아무도 아직 안 읽는
    값 때문에 캐시가 깨진다.**

    ``prefetched`` 는 **더 늦은 as_of 로 미리 읽어 둔 config 프레임**이다.
    학습은 스텝마다 세션 캐시를 열고, 열 때마다 여기가 창고를 두드리면 그
    왕복이 스텝 시간을 지배한다(2회차 발사에서 실측). 상위집합을 게이트와
    같은 조건(observed_at ≤ as_of)으로 거르면 창고를 직접 읽은 것과 같다.
    """
    if prefetched is not None:
        frame = prefetched[prefetched["observed_at"] <= as_of]
    else:
        frame = store.get(CONFIG_TABLE, as_of=as_of)
    if not frame.empty:
        frame = frame[frame["valid_from"] <= as_of]
    values = current_values(frame)
    return {
        name: value
        for name, (value, _revision) in sorted(values.items())
        if depends_on(name)
    }


def fingerprint_of(values: Mapping[str, str]) -> str:
    """의존 설정 한 벌의 지문. 표지에는 값도 같이 적지만(`Stamp`), 짧은 식별자가
    로그·학습 기록(`tools/train_rl.py`)에 필요하다."""
    payload = json.dumps(dict(sorted(values.items())), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def config_fingerprint(store: Store, *, as_of: datetime) -> str:
    """그 시점 **의존 설정**의 지문. 무관한 키가 바뀌어도 그대로다."""
    return fingerprint_of(dependent_config(store, as_of=as_of))


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

    settings = dependent_config(store, as_of=as_of)
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
            config_fingerprint=fingerprint_of(settings),
            config_values=settings,
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
    cache: SessionCache,
    *,
    store: Store,
    market: str,
    session: date,
    as_of: datetime,
    config_frame: pd.DataFrame | None = None,
    settings: Mapping[str, str] | None = None,
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
    if settings is None:
        settings = dependent_config(store, as_of=as_of, prefetched=config_frame)
    fingerprint = fingerprint_of(settings)
    if stamp.config_fingerprint != fingerprint:
        problems.append(_config_problem(stamp, settings, fingerprint))
    if problems:
        raise CacheStampMismatch(
            "캐시 표지가 지금 창고·설정과 다르다: " + " · ".join(problems)
        )


def _config_problem(stamp: Stamp, settings: Mapping[str, str], fingerprint: str) -> str:
    """어느 설정이 어떻게 달라졌는지까지 적는다.

    **지문 두 개만 적으면 다음 행동이 안 나온다.** 실제로 2026-08-22 에 캐시가
    통째로 무효가 됐을 때, 원인이 브로커 계좌 두 줄이었다는 것을 알아내는 데
    사람이 창고를 따로 뒤져야 했다. 이제는 메시지가 이름을 말한다.
    """
    head = f"설정 지문 {stamp.config_fingerprint} != {fingerprint}"
    if stamp.config_values is None:
        # v1 초기 파일: 지문이 설정 **전체** 위에서 계산돼 있어 지금 값과 맞을
        # 수 없다. 다시 구워야 한다는 말을 그대로 적는다.
        return f"{head} (표지에 의존 설정 값이 없다 — 옛 형식이라 다시 구워야 한다)"
    before = stamp.config_values
    names = sorted(set(before) | set(settings))
    changes = [
        f"{name}: {before.get(name, '(없음)')} → {settings.get(name, '(없음)')}"
        for name in names
        if before.get(name) != settings.get(name)
    ]
    shown = " · ".join(changes[:5])
    if len(changes) > 5:
        shown += f" 외 {len(changes) - 5}건"
    return f"{head} — {shown}"


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
        (str(entity), float(score))
        for entity, score in zip(
            ranked["entity_id"].to_numpy(), ranked["candidate_score"].to_numpy(), strict=True
        )
    )

    def column(name: str) -> dict[str, float]:
        if name not in frame.columns:
            return {}
        return {
            entity: float(value)
            for entity, value in zip(entities, frame[name], strict=True)
            if not _missing(value)
        }

    # **행 루프에서 .iloc 을 부르지 않는다.** 학습은 스텝마다 세션 파일을 새로
    # 여는데, 2,800종목 × 7컬럼 × Analyst 수만큼 .iloc 을 부르면 그 오버헤드가
    # 스텝 시간을 지배한다(2회차 발사에서 실측 — 로드당 .iloc 2,300회).
    # 컬럼을 numpy 로 한 번 뽑아 두고 인덱싱만 한다.
    fill_states: dict[str, MarketState] = {}
    has_fill = frame["has_fill_state"].to_numpy()
    fill_columns = {
        name: frame[f"fill_{name}"].to_numpy()
        for name in ("close", "volume", "adv", "volatility", "tick_size", "low", "high")
    }
    for index, entity in enumerate(entities):
        if not bool(has_fill[index]):
            continue
        fill_states[entity] = MarketState(
            entity_id=entity,
            close=float(fill_columns["close"][index]),
            volume=float(fill_columns["volume"][index]),
            adv=float(fill_columns["adv"][index]),
            volatility=float(fill_columns["volatility"][index]),
            tick_size=float(fill_columns["tick_size"][index]),
            low=_or_none(fill_columns["low"][index]),
            high=_or_none(fill_columns["high"][index]),
        )

    signals: dict[tuple[str, str], tuple[float, float]] = {}
    for analyst in meta.get("analysts", []):
        score_column = f"{SIGNAL_PREFIX}{analyst}::score"
        confidence_column = f"{SIGNAL_PREFIX}{analyst}::confidence"
        if score_column not in frame.columns:
            continue
        scores = frame[score_column].to_numpy()
        confidences = frame[confidence_column].to_numpy()
        name = str(analyst)
        for index, entity in enumerate(entities):
            score = scores[index]
            if _missing(score):
                continue
            signals[(entity, name)] = (float(score), float(confidences[index]))

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
    #:
    #: **읽는 쪽이 미래를 앞질러 보면 이 값으로는 모자란다.** 오라클 카나리는
    #: 5세션 앞을 같이 읽는데(§0), 그러면 오늘 것이 5스텝 만에 밀려나 같은
    #: 파일을 두 번 판다 — 스텝 비용이 그대로 두 배다. 그래서 값을 생성자에서
    #: 받는다. 늘리는 쪽 비용은 세션 하나당 수백 KB 다.
    LRU = 4

    def __init__(self, store: Store, market: str, root: Path, *, lru: int = LRU) -> None:
        super().__init__(store, market)
        self.lru = max(1, int(lru))
        self.root = Path(root)
        self.hits = 0
        self.misses = 0
        self._loaded: OrderedDict[date, SessionCache | None] = OrderedDict()
        #: verify 용 config 선인출. **로드마다 창고를 두드리지 않기 위해서다** —
        #: 2회차 학습 발사에서 이 왕복이 스텝 시간을 지배했다(12일 페이스).
        #: 관측시각이 단조라 더 늦은 as_of 가 오면 그때 한 번 다시 읽는다.
        self._config_frame: pd.DataFrame | None = None
        self._config_as_of: datetime | None = None
        self._config_memo: dict[datetime, dict[str, str]] = {}
        #: 캐시 밖 종목을 창고에서 읽어 기억해 둔 것. 세션→종류→종목→값.
        #: **세션 캐시와 같이 산다** — 창에서 밀려난 세션의 기억만 남으면
        #: 그것이 곧 새는 자리가 된다.
        self._extras: dict[date, dict[str, dict[str, Any]]] = {}

    def _config_settings(self, as_of: datetime) -> dict[str, str]:
        """그 시점 의존 설정 — 세션별 메모.

        상위집합을 **10년 지평선으로 한 번만** 읽는다. as_of 를 그대로 쓰면
        학습의 세션 시계가 단조 전진이라 스텝마다 "더 늦다 → 다시 읽자" 가
        발화한다(2회차 발사에서 실측 — 이것 하나가 스텝당 15ms 였다).
        dependent_config 가 observed_at ≤ as_of 로 거르므로 결과는 같다.
        """
        memo = self._config_memo.get(as_of)
        if memo is not None:
            return memo
        if self._config_frame is None or self._config_as_of is None or as_of > self._config_as_of:
            horizon = as_of + timedelta(days=3650)
            frame = self.store.get(CONFIG_TABLE, as_of=horizon)
            if not frame.empty:
                # 의존 이름 판정(depends_on)을 여기서 한 번만 한다 — 로드마다
                # 전 행을 다시 재면 그것만 스텝당 수 ms 다(2회차 발사 실측).
                frame = frame[frame["entity_id"].map(depends_on)]
            self._config_frame = frame
            self._config_as_of = horizon
        settings = dependent_config(self.store, as_of=as_of, prefetched=self._config_frame)
        self._config_memo[as_of] = settings
        return settings

    def session_cache(self, as_of: datetime, session: date) -> SessionCache | None:
        if session in self._loaded:
            self._loaded.move_to_end(session)
            return self._loaded[session]
        path = cache_path(self.root, self.market, session)
        cache: SessionCache | None = None
        if path.exists():
            cache = read(path)
            verify(
                cache, store=self.store, market=self.market, session=session,
                as_of=as_of, settings=self._config_settings(as_of),
            )
            self.hits += 1
        else:
            self.misses += 1
        self._loaded[session] = cache
        while len(self._loaded) > self.lru:
            evicted, _ = self._loaded.popitem(last=False)
            self._extras.pop(evicted, None)
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

    def _absorb(
        self,
        as_of: datetime,
        cache: SessionCache,
        entities: Sequence[str],
        *,
        kind: str,
        fetch: Callable[[list[str]], dict[str, Any]],
    ) -> dict[str, Any]:
        """캐시에 없는 종목을 **창고에서 한 번만** 읽어 그 세션에 붙여 둔다.

        ## 왜 필요한가 — 보유는 후보를 벗어난다

        캐시는 그날 후보(24종목 남짓)만 굽는다. 그런데 학습 중 장부는 그날
        후보가 아닌 종목을 계속 들고 있고(어제 산 것이 오늘 후보에서 빠진다),
        그 종목의 시세·베타·체결상태는 매 스텝 창고로 되돌아갔다. 그래서
        **에피소드가 길어질수록 스텝이 느려졌다** — 실측 12.6ms → 33ms(64스텝),
        원인은 장부에 쌓인 종목 수였다.

        ## 값이 달라지지 않는 이유

        같은 as_of 로 창고에서 읽은 것을 기억할 뿐이다. 종목별 계산은 서로
        독립이라(모듈 독스트링) 합친 결과는 전부 창고에서 읽은 것과 같다.
        **없는 종목은 없다고 기억한다** — 그래야 "그날 봉이 없다"(거래정지·
        상장폐지)가 그대로 없음으로 남고, 매 스텝 다시 묻지도 않는다.

        ## 종류별로 따로 읽는다

        넷을 한꺼번에 구우면 시세만 필요한 자리(`_plan_orders`·평가)에서도
        베타까지 읽는다. 베타는 종목당 180세션을 훑어서, 보유가 30종목을
        넘어가면 그 눈치 없는 선읽기가 스텝을 두 배로 만든다(실측 51.6ms).
        """
        memo: dict[str, Any] = self._extras.setdefault(as_of.date(), {}).setdefault(
            kind, {}
        )
        missing = [entity for entity in cache.misses(entities) if entity not in memo]
        if missing:
            fetched = fetch(missing)
            for entity in missing:
                memo[entity] = fetched.get(entity)
        return memo

    def stats(self, as_of: datetime, entities: Sequence[str]) -> EntityStats:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().stats(as_of, entities)
        wanted = set(entities)
        prices = {e: v for e, v in cache.stats.prices.items() if e in wanted}
        adv = {e: v for e, v in cache.stats.adv.items() if e in wanted}
        volatility = {e: v for e, v in cache.stats.volatility.items() if e in wanted}

        def fetch(missing: list[str]) -> dict[str, Any]:
            extra = super(CachedSessionReader, self).stats(as_of, missing)
            return {
                entity: (
                    extra.prices.get(entity),
                    extra.adv.get(entity),
                    extra.volatility.get(entity),
                )
                for entity in missing
                if entity in extra.prices or entity in extra.adv
            }

        memo = self._absorb(as_of, cache, entities, kind="stats", fetch=fetch)
        for entity in wanted:
            row = memo.get(entity)
            if row is None:
                continue
            price, daily, vol = row
            if price is not None:
                prices[entity] = price
            if daily is not None:
                adv[entity] = daily
            if vol is not None:
                volatility[entity] = vol
        return EntityStats(prices=prices, adv=adv, volatility=volatility)

    def signals(
        self, as_of: datetime, entities: Sequence[str]
    ) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, float]]]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().signals(as_of, entities)
        wanted = set(entities)
        combined = {e: v for e, v in cache.combined.items() if e in wanted}
        latest = {key: value for key, value in cache.signals.items() if key[0] in wanted}

        def fetch(missing: list[str]) -> dict[str, Any]:
            extra_combined, extra_latest = super(CachedSessionReader, self).signals(
                as_of, missing
            )
            rows: dict[str, Any] = {}
            for entity in missing:
                pairs = {
                    key[1]: value
                    for key, value in extra_latest.items()
                    if key[0] == entity
                }
                if entity in extra_combined or pairs:
                    rows[entity] = (extra_combined.get(entity), pairs)
            return rows

        memo = self._absorb(as_of, cache, entities, kind="signals", fetch=fetch)
        for entity in wanted:
            row = memo.get(entity)
            if row is None:
                continue
            score, pairs = row
            if score is not None:
                combined[entity] = score
            for analyst, pair in pairs.items():
                latest[(entity, analyst)] = pair
        return combined, latest

    def betas(self, as_of: datetime, entities: Sequence[str]) -> dict[str, float]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().betas(as_of, entities)
        wanted = set(entities)
        out = {e: v for e, v in cache.betas.items() if e in wanted}
        memo = self._absorb(
            as_of,
            cache,
            entities,
            kind="betas",
            fetch=lambda missing: dict(
                super(CachedSessionReader, self).betas(as_of, missing)
            ),
        )
        out.update(
            {e: v for e, v in memo.items() if e in wanted and v is not None}
        )
        return out

    def fill_states(
        self, as_of: datetime, entities: Sequence[str]
    ) -> dict[str, MarketState]:
        cache = self.session_cache(as_of, as_of.date())
        if cache is None:
            return super().fill_states(as_of, entities)
        wanted = set(entities)
        out = {e: v for e, v in cache.fill_states.items() if e in wanted}
        memo = self._absorb(
            as_of,
            cache,
            entities,
            kind="fill_states",
            fetch=lambda missing: dict(
                super(CachedSessionReader, self).fill_states(as_of, missing)
            ),
        )
        out.update(
            {e: v for e, v in memo.items() if e in wanted and v is not None}
        )
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
    store: Store,
    market: str,
    *,
    cache_root: Path | None = None,
    use_cache: bool = True,
    lru: int = CachedSessionReader.LRU,
) -> SessionReader:
    """env 가 부르는 진입점. **캐시가 없으면 그냥 창고 경로다.**"""
    if not use_cache:
        return SessionReader(store, market)
    root = Path(cache_root) if cache_root is not None else default_cache_root(store)
    if not root.exists():
        return SessionReader(store, market)
    return CachedSessionReader(store, market, root, lru=lru)
