"""벤치마크 지수 — **NAV 와 같은 시각·같은 규칙으로 계산한다.**

포트폴리오는 15:40 기준인데 벤치마크가 미장 실시간이면, 그 차이가 통째로
가짜 초과수익·가짜 낙폭이 되어 보상에 들어간다 (accounting.md §2).
그래서 이 모듈은 스냅샷이 쓴 것과 **같은 as_of, 같은 환율**을 받는다.

## ⚠️ 지금 넣는 것은 가격지수(PR)다

목표는 총수익지수(TR)지만 (accounting.md §7) 2026-08-14 실측으로 KRX Open
API·LS `t1511`·FRED 세 곳 다 TR 도 배당수익률 필드도 주지 않는다. 우리는
배당을 받고 벤치마크는 못 받으므로 **국내 대형주 기준 연 2~3%p 만큼 우리가
이긴 것처럼 보인다.** 이 사실은 `config.benchmark` 주석 ·
`nav.blended_benchmark` 독스트링 · `tables.nav_daily.benchmark_index` 설명 ·
화면 배지에도 같이 적혀 있다. 한 곳만 적으면 나머지를 보는 사람이 오독한다.

## 결측일은 null 이다

앞 값으로 채우면 그날 벤치마크가 안 빠진 것이 되어 **벤치마크 낙폭이
지워지고** 우리 낙폭만 깊어 보인다. 대신 왜 null 인지를 ``note`` 로 남겨
화면이 "데이터가 없었다" 와 "벤치마크가 0% 였다" 를 구분하게 한다.

## 휴장과 구멍은 다르다 — 날짜가 아니라 거래일 달력으로 가른다

"직전 종가가 며칠 됐나" 로 재면 설 연휴는 구멍이 되고, 3일짜리 수집 실패는
휴장이 된다. 그래서 **그 지수 자기 시장의 거래일**로 잰다. 종가 다음날부터
오늘까지 그 시장이 장을 연 날이 있었다면 그건 우리가 알았어야 하는 종가이고,
없다면 휴장이다.

미장은 여기서 거래일 하나를 더 봐준다. 우리 스냅샷은 한국시간 15:40 인데
그날 미장은 아직 안 끝났다 — **직전 미장 종가가 그 시각에 알 수 있는
전부다** (accounting.md §2). 이 한 칸을 안 봐주면 미장 벤치마크가 매일 null 이
된다.

## 사슬은 마지막 값이 있는 날에 건다

지수 결측이 며칠 이어져도, 값이 돌아온 날은 **마지막으로 값이 있던 날의
지수 종가**에서 수익률을 잰다. 결측 구간을 건너뛰고 그날 하루치만 재면
그 사이의 시장 등락이 벤치마크에서 사라진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.accounting import ledger
from quant_rl_trading.accounting.nav import BASE_INDEX, blended_benchmark
from quant_rl_trading.collectors.market_hours import SPECS, Market, trading_days

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

INDICES = "indices"

SEOUL = ZoneInfo("Asia/Seoul")

#: 그 시장의 종가가 **마지막으로 마감한 세션** 기준으로 몇 거래일 늦게 도착해도
#: 봐주는가. "마지막으로 마감한 세션" 은 `_expected_close_day` 가 시각으로 가른다 —
#: 예전엔 한국 날짜를 그냥 썼고 미장은 그날 장이 안 끝났으니 1 을 봐줬다. 그러면
#: 미장 시각(05:20 KST)의 스냅샷이 국장에 "오늘(아직 안 연 날) 종가" 를 요구해
#: NaN 이 됐다(2026-09-02 미장 shadow 실측). 이제 둘 다 0 이다.
KNOWABLE_LAG = {Market.KR: 0, Market.US: 0}


def _expected_close_day(market: Market, as_of: datetime) -> date:
    """``as_of`` 에 종가가 있어야 하는 마지막 세션. 그 시장의 정규장 마감 전이면
    오늘 종가는 있을 수 없으므로 직전 거래일이다."""
    spec = SPECS[market]
    local = as_of.astimezone(ZoneInfo(spec.timezone))
    day = local.date()
    if local.time() < spec.regular_close or not trading_days(market, day, day):
        earlier = trading_days(market, day - timedelta(days=20), day - timedelta(days=1))
        return earlier[-1] if earlier else day
    return day


@dataclass(frozen=True)
class BenchmarkSpec:
    """`config.benchmark` 그대로. 임계치는 창고에서 읽는다 (불변식 10)."""

    kr_index: str
    us_index: str
    kr_weight: float
    us_weight: float
    base_value: float
    total_return: bool
    max_staleness_days: int

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> BenchmarkSpec:
        section = store.config("benchmark", as_of=as_of)
        return cls(
            kr_index=str(section["kr_index"]),
            us_index=str(section["us_index"]),
            kr_weight=float(section["kr_weight"]),
            us_weight=float(section["us_weight"]),
            base_value=float(section.get("base_value", BASE_INDEX)),
            total_return=bool(section.get("total_return", False)),
            max_staleness_days=int(section.get("max_staleness_days", 10)),
        )


@dataclass(frozen=True)
class Benchmark:
    """그날의 벤치마크. ``index_value`` 가 None 이면 ``note`` 가 이유를 말한다."""

    index_value: float | None
    note: str | None


def _close(
    store: Store, *, entity: str, market: Market, as_of: datetime, search_days: int
) -> float | None:
    """``as_of`` 에 알 수 있었던 그 지수의 최신 종가. 낡았으면 ``None``.

    휴장으로 지수가 안 움직인 것과 수집이 끊긴 것은 둘 다 "직전 종가" 로
    보이지만, 앞의 것은 벤치마크가 0% 인 날이고 뒤의 것은 **우리가 모르는
    날**이다. 둘을 날짜 수로 가르면 설 연휴가 구멍이 되고 3일짜리 수집 실패가
    휴장이 된다. 그래서 그 시장의 **거래일 달력**으로 가른다.

    ``search_days`` 는 찾아볼 창일 뿐 판정 기준이 아니다. 창이 좁으면 긴
    연휴 뒤에 종가를 못 찾아 없다고 답한다.
    """
    frame = store.get(INDICES, as_of=as_of, entity=entity, lookback=search_days)
    if frame.empty:
        return None
    latest = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    close = latest["close"]
    if pd.isna(close) or float(close) <= 0:
        return None

    close_day = pd.Timestamp(latest["valid_from"]).tz_convert(SEOUL).date()
    today = _expected_close_day(market, as_of)
    # 기대보다 늦은 세션의 종가가 이미 관측돼 있으면(store.get 이 observed_at 으로
    # 걸렀으니 알 수 있었던 값이다) 그것이 최신이다 — 구멍이 아니다.
    if close_day >= today:
        return float(close)
    # 종가 다음날부터 오늘까지 그 시장이 장을 연 날. 있으면 우리가 알았어야
    # 하는 종가가 빠진 것이다 — 미장은 §2 만큼(거래일 1) 봐준다.
    missed = trading_days(market, close_day + timedelta(days=1), today)
    if len(missed) > KNOWABLE_LAG[market]:
        return None
    return float(close)


def level(
    store: Store,
    *,
    as_of: datetime,
    fx_rate: float,
    spec: BenchmarkSpec | None = None,
) -> Benchmark:
    """그 시점의 혼합 벤치마크 지수. 없으면 ``None`` 과 그 이유.

    ``fx_rate`` 는 **스냅샷이 NAV 평가에 쓴 바로 그 환율**이다. 여기서 다시
    조회하면 두 값이 갈릴 수 있고, 갈린 만큼이 그대로 가짜 초과수익이 된다.
    """
    spec = spec or BenchmarkSpec.from_store(store, as_of=as_of)

    def close_at(entity: str, market: Market, moment: datetime) -> float | None:
        return _close(
            store,
            entity=entity,
            market=market,
            as_of=moment,
            search_days=spec.max_staleness_days,
        )

    kr_now = close_at(spec.kr_index, Market.KR, as_of)
    us_now = close_at(spec.us_index, Market.US, as_of)
    if kr_now is None or us_now is None:
        missing = [
            name
            for name, close in ((spec.kr_index, kr_now), (spec.us_index, us_now))
            if close is None
        ]
        return Benchmark(index_value=None, note=f"지수 종가 없음: {', '.join(missing)}")

    anchor = _anchor(store, as_of=as_of)
    if anchor is None:
        # 벤치마크의 첫날. 기준값에서 시작한다 — 없는 어제를 지어내지 않는다.
        return Benchmark(index_value=spec.base_value, note=None)

    anchor_as_of, anchor_level, anchor_fx = anchor
    kr_then = close_at(spec.kr_index, Market.KR, anchor_as_of)
    us_then = close_at(spec.us_index, Market.US, anchor_as_of)
    if kr_then is None or us_then is None or kr_then <= 0 or us_then <= 0:
        # 사슬을 걸 자리가 사라졌다(정정본으로 옛 종가가 물러났다). 지어내지
        # 않는다 — 기준값으로 되돌리면 그날 벤치마크가 100 으로 튄다.
        return Benchmark(
            index_value=None,
            note=(
                f"직전 기준일({anchor_as_of.astimezone(SEOUL).date().isoformat()}) "
                "지수 종가가 없다"
            ),
        )

    kr_return = kr_now / kr_then - 1.0
    # 미장은 **원화환산 후** 넣는다. 환율 변동은 우리 해외분에도 똑같이
    # 작용하므로, 벤치마크만 달러 기준이면 환차익이 통째로 초과수익이 된다.
    us_return_krw = (us_now * fx_rate) / (us_then * anchor_fx) - 1.0

    chained = blended_benchmark(
        kr_returns=[kr_return],
        us_returns_krw=[us_return_krw],
        kr_weight=spec.kr_weight,
        us_weight=spec.us_weight,
        base=anchor_level,
    )
    return Benchmark(index_value=chained[-1], note=None)


def _anchor(store: Store, *, as_of: datetime) -> tuple[datetime, float, float] | None:
    """마지막으로 벤치마크 값이 있던 스냅샷 (시각, 지수, 그날 환율).

    환율은 **그날 NAV 가 쓴 값**을 그대로 쓴다. 다시 조회하면 그 사이 들어온
    정정본을 집어 벤치마크만 다른 환율로 재게 된다.
    """
    frame = store.get(ledger.NAV_DAILY, as_of=as_of, entity=ledger.ACCOUNT)
    if frame.empty:
        return None
    ordered = frame.sort_values(["valid_from", "observed_at"])
    with_benchmark = ordered[ordered["benchmark_index"].notna()]
    if with_benchmark.empty:
        return None
    row = with_benchmark.iloc[-1]
    return (
        pd.Timestamp(row["valid_from"]).to_pydatetime(),
        float(row["benchmark_index"]),
        float(row["fx_rate"]),
    )
