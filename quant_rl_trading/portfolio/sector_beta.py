"""섹터 상·하방 베타 — 방어 관계를 **가정하지 말고 측정한다** (§1).

"통신·유틸은 방어주" 는 통념이지 사실이 아니다. 2022년 금리 급등기에는
유틸·리츠가 오히려 더 빠졌다. 그래서 고정 테이블을 박지 않고, 시장이 하락한
날만 골라 하방 베타를 직접 잰다.

    하방 베타 = cov(r_sector, r_mkt | r_mkt < 0) / var(r_mkt | r_mkt < 0)
    상방 베타 = cov(r_sector, r_mkt | r_mkt > 0) / var(r_mkt | r_mkt > 0)

| 하방 베타 | 의미 |
|---|---|
| < 0.7 | 하락장에서 실제로 방어 |
| ≈ 1.0 | 시장과 같이 빠짐 |
| > 1.3 | 하락장에서 더 크게 빠짐 |

이상적인 방어 섹터는 **하방 낮고 상방 높은** 비대칭 섹터다. 둘 다 낮으면
그냥 현금과 비슷하고, 그럴 바엔 현금을 드는 게 낫다.

## 왜 시가총액 가중인가

섹터 수익률을 등가중으로 접으면 그 섹터의 소형주가 대형주와 같은 목소리를
낸다. 우리가 실제로 담는 것은 지수처럼 움직이는 대형주 쪽이므로, 섹터가
시장에 어떻게 반응하는지도 **시총 가중**으로 봐야 실제 노출과 맞는다.

## 왜 롤링인가

베타는 국면에 따라 변한다. as_of 마다 **직전 window 세션**으로 다시 잰다 —
한 번 재서 박아두면 §1 이 경계한 고정 테이블이 된다. `rolling_betas` 가 그
시계열을 내서, 낮게 측정된 섹터가 실제 하락장에서 덜 빠졌는지 사후 확인할
수 있게 한다 (§7 검증).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from quant_rl_trading.accounting.benchmark import INDICES
from quant_rl_trading.selector import ksic
from quant_rl_trading.selector.candidates import sector_map
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

#: 베타를 재는 창(거래일). §1 이 "롤링 250일" 로 못박았다.
DEFAULT_WINDOW = 250

#: DART 표준산업분류만 쓴다 — KRX 소속부는 업종이 아니라 시장 세부 구분이다
#: (`candidates.sector_map` 독스트링). ksic.roll_up_map 이 24섹터로 접는다.
SECTOR_SOURCE = "dart_company"

#: 시가총액 관측이 이만큼 안에 있어야 가중에 쓴다. fundamental 과 같은 값.
MARKET_STATS = "market_stats"
MARKET_CAP_LOOKBACK_DAYS = 45

#: 베타를 내기 위한 최소 하락일/상승일 수. 이보다 적으면 표본이 얇아
#: 공분산이 잡음이다 — 그 섹터는 NaN 으로 남기고 채택하지 않는다.
MIN_SIGN_DAYS = 20


@dataclass(frozen=True)
class SectorBeta:
    """한 섹터의 상·하방 베타와 그것을 받친 표본 수."""

    down_beta: float
    up_beta: float
    n_down: int
    n_up: int
    n_members: int

    @property
    def is_defensive(self) -> bool:
        """하방 낮고(<0.7) 상방은 그만큼 안 낮은(비대칭) 섹터."""
        return (
            np.isfinite(self.down_beta)
            and self.down_beta < 0.7
            and self.up_beta > self.down_beta
        )


def _daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """가격 롱포맷 → 세션×종목 일간수익률.

    휴장·결측으로 빠진 날은 그 종목만 NaN 이 되고, 수익률은 **관측된 이웃
    세션 사이**로 계산된다. 종목마다 상장·상폐 시점이 달라 판이 성기다.
    """
    if prices.empty:
        return pd.DataFrame()
    wide = prices.pivot_table(
        index="valid_from", columns="entity_id", values="close", aggfunc="last"
    ).sort_index()
    # **결측을 앞값으로 채우지 않는다.** pad 로 채우면 거래가 끊긴 종목이
    # 수익률 0 을 내서 변동성과 베타가 아래로 눌린다. NaN 으로 두면 그날은
    # sector_composite_returns 의 가중에서 빠진다.
    return wide.pct_change(fill_method=None)


def sector_composite_returns(
    stock_returns: pd.DataFrame,
    *,
    caps: pd.Series,
    sectors: dict[str, str],
) -> pd.DataFrame:
    """종목 수익률 → 세션×섹터 **시총가중** 수익률.

    가중치는 종목의 (그 시점) 시가총액이다 — 하루 안에서는 고정으로 본다.
    가중은 그날 실제로 관측된 종목들 사이에서만 정규화한다: 한 종목이 그날
    수익률이 없으면(NaN) 그 종목을 빼고 나머지 시총으로 다시 정규화한다.
    안 그러면 결측 하나가 섹터 수익률을 통째로 NaN 으로 만든다.
    """
    if stock_returns.empty:
        return pd.DataFrame()
    # 섹터를 아는 종목만. 모르는 종목은 섞지 않는다(candidates.sector_map 규칙).
    known = [e for e in stock_returns.columns if e in sectors and e in caps.index]
    if not known:
        return pd.DataFrame()
    returns = stock_returns[known]
    weight = caps.reindex(known).astype(float)

    frames: dict[str, pd.Series] = {}
    for sector in sorted(set(sectors[e] for e in known)):
        members = [e for e in known if sectors[e] == sector]
        r = returns[members]
        w = weight.reindex(members).to_numpy()
        # 관측된 종목만으로 가중평균. 결측은 가중에서 빠지고 분모가 준다.
        mask = r.notna().to_numpy()
        w_row = mask * w  # 세션×종목
        denom = w_row.sum(axis=1)
        num = np.nansum(np.where(mask, r.to_numpy(), 0.0) * w, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            composite = np.where(denom > 0, num / denom, np.nan)
        frames[sector] = pd.Series(composite, index=r.index)
    return pd.DataFrame(frames)


def _one_sided_beta(sector_ret: pd.Series, market_ret: pd.Series, *, downside: bool):
    """한쪽(하락/상승) 시장일만 골라 베타와 그 표본 수를 낸다."""
    both = pd.concat([sector_ret, market_ret], axis=1, keys=["s", "m"]).dropna()
    side = both[both["m"] < 0] if downside else both[both["m"] > 0]
    n = len(side)
    if n < MIN_SIGN_DAYS:
        return float("nan"), n
    var = float(side["m"].var(ddof=1))
    if not np.isfinite(var) or var <= 0:
        return float("nan"), n
    cov = float(side["s"].cov(side["m"]))
    return cov / var, n


def betas_from_returns(
    sector_returns: pd.DataFrame,
    market_returns: pd.Series,
    *,
    sectors: dict[str, str],
) -> dict[str, SectorBeta]:
    """섹터 수익률 + 시장 수익률 → 섹터별 상·하방 베타.

    시장 수익률의 부호로 날을 가른다. 같은 창 안에서 하락일로 하방을,
    상승일로 상방을 잰다. 창은 이미 호출자가 잘라서 넘긴다.
    """
    if sector_returns.empty or market_returns.empty:
        return {}
    member_count = pd.Series(sectors).value_counts()
    out: dict[str, SectorBeta] = {}
    for sector in sector_returns.columns:
        down, n_down = _one_sided_beta(
            sector_returns[sector], market_returns, downside=True
        )
        up, n_up = _one_sided_beta(
            sector_returns[sector], market_returns, downside=False
        )
        out[sector] = SectorBeta(
            down_beta=down,
            up_beta=up,
            n_down=n_down,
            n_up=n_up,
            n_members=int(member_count.get(sector, 0)),
        )
    return out


def _market_cap(store: Store, *, as_of: datetime, market: str) -> pd.Series:
    """종목별 최신 시가총액. `analysts/fundamental.market_cap` 과 같은 읽기."""
    raw = store.get(MARKET_STATS, as_of=as_of, lookback=MARKET_CAP_LOOKBACK_DAYS)
    if raw.empty:
        return pd.Series(dtype=float)
    raw = raw[(raw["market"] == market) & (raw["metric"] == "market_cap")]
    if raw.empty:
        return pd.Series(dtype=float)
    latest = raw.sort_values("valid_from").groupby("entity_id").tail(1)
    caps = latest.set_index("entity_id")["value"].astype(float)
    return caps[caps > 0]


def _panels(
    store: Store, *, as_of: datetime, market: str, window: int
) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict[str, str]]:
    """창고에서 필요한 네 판을 읽어 (종목수익률, 시장수익률, 시총, 섹터) 로.

    창은 ``window`` 거래일이지만 달력 여백을 둬서 휴장·결측으로 실효 세션이
    모자라지 않게 한다 — 수익률은 첫 세션을 하나 잃으므로 +5 도 더 준다.
    """
    span = int((window + 5) * 1.6) + 10  # 거래일 → 달력일 여백
    # **보정 종가로 수익률을 낸다.** 원주가면 액면분할·무상증자가 그대로
    # 수익률이 되고, 그 하루가 섹터 베타를 통째로 흔든다 (corporate_actions.py).
    prices = read_prices(
        store,
        as_of=as_of,
        lookback=span,
        market=market,
        columns=["entity_id", "valid_from", "close"],
        adjusted=True,
    )
    stock_ret = _daily_returns(prices)

    index_raw = store.get(
        INDICES, as_of=as_of, entity=_index_name(market), lookback=span
    )
    market_ret = _index_returns(index_raw)

    caps = _market_cap(store, as_of=as_of, market=market)
    entities = list(stock_ret.columns)
    sectors_raw = sector_map(
        store, as_of=as_of, entities=entities, market=market, source=SECTOR_SOURCE
    )
    sectors = ksic.roll_up_map(sectors_raw)
    return stock_ret, market_ret, caps, sectors


def _index_name(market: str) -> str:
    return "KR:IDX:KOSPI" if market == "KR" else "US:IDX:SP500"


def _index_returns(index_raw: pd.DataFrame) -> pd.Series:
    if index_raw.empty:
        return pd.Series(dtype=float)
    series = (
        index_raw.sort_values(["valid_from", "observed_at"])
        .groupby("valid_from")["close"]
        .last()
    )
    return series.pct_change(fill_method=None)


def _trim(frame, window: int):
    """수익률 판을 마지막 ``window`` 세션으로 자른다."""
    if frame.empty:
        return frame
    return frame.tail(window)


def estimate(
    store: Store,
    *,
    as_of: datetime,
    market: str = "KR",
    window: int = DEFAULT_WINDOW,
) -> dict[str, SectorBeta]:
    """as_of 시점의 섹터 상·하방 베타. **직전 ``window`` 세션**으로 잰다.

    §3 리스크 패리티·§4 위기 공분산의 입력이자, §5 에서 Risk Analyst 가
    RL 상태값으로 내보내는 값이기도 하다.
    """
    stock_ret, market_ret, caps, sectors = _panels(
        store, as_of=as_of, market=market, window=window
    )
    if stock_ret.empty or market_ret.empty or not sectors:
        return {}
    sector_ret = sector_composite_returns(stock_ret, caps=caps, sectors=sectors)
    # 시장 수익률의 세션에 맞춰 자른다. 둘의 세션이 어긋나면 교집합만 남는다.
    sector_ret = _trim(sector_ret, window)
    market_ret = market_ret.reindex(sector_ret.index)
    return betas_from_returns(sector_ret, market_ret, sectors=sectors)


def rolling_betas(
    store: Store,
    *,
    as_of: datetime,
    market: str = "KR",
    window: int = DEFAULT_WINDOW,
    step: int = 20,
    points: int = 12,
) -> pd.DataFrame:
    """베타 시계열 — 검증용(§7).

    ``step`` 세션 간격으로 ``points`` 개의 as_of 를 잡아 각 시점의 하방 베타를
    낸다. 낮게 측정된 섹터가 실제 하락장에서 덜 빠졌는지 사후로 보게 한다.
    한 번에 넓은 창을 읽어 시점마다 잘라 쓰므로 창고 조회는 한 번뿐이다.
    """
    span_sessions = window + step * points
    span = int((span_sessions + 5) * 1.6) + 10
    prices = read_prices(
        store, as_of=as_of, lookback=span, market=market,
        columns=["entity_id", "valid_from", "close"],
        adjusted=True,
    )
    stock_ret = _daily_returns(prices)
    index_raw = store.get(INDICES, as_of=as_of, entity=_index_name(market), lookback=span)
    market_ret = _index_returns(index_raw)
    caps = _market_cap(store, as_of=as_of, market=market)
    sectors_raw = sector_map(
        store, as_of=as_of, entities=list(stock_ret.columns),
        market=market, source=SECTOR_SOURCE,
    )
    sectors = ksic.roll_up_map(sectors_raw)
    if stock_ret.empty or not sectors:
        return pd.DataFrame()
    sector_ret = sector_composite_returns(stock_ret, caps=caps, sectors=sectors)

    rows = []
    sessions = sector_ret.index
    for k in range(points):
        end = len(sessions) - k * step
        if end < window:
            break
        idx = sessions[end - window : end]
        window_ret = sector_ret.loc[idx]
        window_mkt = market_ret.reindex(idx)
        betas = betas_from_returns(window_ret, window_mkt, sectors=sectors)
        for sector, beta in betas.items():
            rows.append({
                "as_of_session": idx[-1],
                "sector": sector,
                "down_beta": beta.down_beta,
                "up_beta": beta.up_beta,
            })
    return pd.DataFrame(rows)
