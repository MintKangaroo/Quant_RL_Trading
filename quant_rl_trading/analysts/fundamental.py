"""Fundamental Analyst — DART 재무를 직접 계산한다.

## 왜 직접 계산하나

남이 계산해 준 PER/PBR 을 받아 쓰면 **그 값이 어느 시점 재무로 계산됐는지 알
수 없다.** 실적은 회계기간 종료 후 45~69일 뒤에 공시되는데, 벤더 값은 보통
"현재 알려진 최신 재무" 로 계산돼 있어서 과거 어느 날짜로 되감아도 그날 몰랐던
재무가 섞여 들어온다. 재무를 원자료로 받아 두면 게이트가 그 문제를 대신 막아
준다 — ``store.get(as_of=...)`` 가 공시 전 재무를 애초에 안 보여준다.

## DART 손익은 Q4 만 누적값이다

실측 (삼성전자 2024, 조원)::

    revenue   Q1=71.9  Q2=74.1  Q3=79.1  Q4=300.9
                                          └ 연간 누적 (71.9+74.1+79.1+75.8)

Q1~Q3 는 3개월 값이고 **사업보고서(Q4)만 연간 누적**이다. 이걸 그대로 쓰면
매년 4분기마다 매출이 4배로 뛰고 성장률이 폭발한다 — 전 종목에 같은 시기에
같은 방향으로 생기는 가짜 신호라 IC 가 그럴듯하게 나오기까지 한다.

실제 4분기 = 연간 - (Q1+Q2+Q3) 로 복원한다.

## 밸류 (v0.2)

v0.1 은 시가총액이 없어 퀄리티와 성장만 봤다. KRX Open API 가 열리면서
``market_stats`` 에 상장주식수·시가총액이 들어왔고, 밸류를 켰다.

**비율이 아니라 수익률(역수)로 쓴다.** PER 이 아니라 이익수익률
(net_income / market_cap) 이다. PER 은 이익이 0 에 가까워지면 발산하고 적자
기업에서는 음수가 되는데, 그 음수는 "아주 싸다"와 부호가 같아 횡단면 정렬이
뒤집힌다. 역수는 적자가 그냥 음의 수익률이 되어 순서가 유지된다.

## 커버리지가 모자라면 밸류를 **뺀다**

시가총액이 없는 날은 밸류 z 가 전부 NaN 이 되고, ``fillna(0.0)`` 이 그걸
중앙값 자리로 덮는다. 그러면 밸류 가중치 0.35 가 **아무 정보도 없는 0 에**
실려서 나머지 피처의 영향력만 희석시킨다. 점수는 멀쩡히 나오므로 아무도
눈치채지 못한다 — ``LOOKBACK_DAYS`` 주석의 실패와 같은 모양이다.

그래서 횡단면 커버리지가 ``MIN_VALUE_COVERAGE`` 에 못 미치면 밸류 열을 아예
만들지 않는다. ``combine`` 이 남은 가중치로 정규화하므로 퀄리티·성장만으로
정직하게 되돌아간다. 조용히 희석되는 것보다 대놓고 빠지는 편이 낫다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

FUNDAMENTALS = "fundamentals"

#: 전년 동기 대비(YoY)를 계산하려면 **8분기**가 필요하다. TTM 이 4분기를
#: 먹고, 그 전 TTM 이 또 4분기를 먹는다.
#:
#: 게다가 Q4 누적 복원은 **같은 해 Q1~Q3** 를 요구한다. 창이 달력연도를 온전히
#: 품지 못하면 매년 Q4 가 통째로 버려져 8분기가 채워지지 않는다.
#: 실측: 800일이면 종목당 7분기만 남아 revenue_growth 의 횡단면 표준편차가
#: 0이 됐다 — **피처가 조용히 죽어 있었고 점수는 계속 나왔다.**
#: 창을 줄여 속도를 얻으면 이렇게 티 안 나게 신호가 사라진다.
LOOKBACK_DAYS = 1150

#: 기간에 걸쳐 발생하는 값. Q4 누적 복원이 필요하다.
#: 시장별 재무 소스. 미장은 tools/backfill_fundamentals_us.py 가 SEC companyfacts 로 채운다.
SOURCE_BY_MARKET = {"KR": "dart", "US": "edgar"}
FLOW_METRICS = ("revenue", "operating_income", "net_income")

#: 특정 시점의 잔액. 누적 개념이 없다.
STOCK_METRICS = (
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities",
)

WEIGHTS = {
    # 밸류 0.35 — 국내 횡단면에서 가장 오래 살아남은 팩터다.
    "earnings_yield": 0.15,   # TTM 순이익 / 시가총액 (PER 의 역수)
    "book_to_market": 0.12,   # 자본 / 시가총액 (PBR 의 역수)
    "sales_to_price": 0.08,   # TTM 매출 / 시가총액 (PSR 의 역수)
    # 퀄리티 0.47
    "roe": 0.20,              # 자본 대비 이익 — 퀄리티의 대표
    "operating_margin": 0.13,
    "low_leverage": 0.09,     # 부채비율의 역 (낮을수록 +)
    "current_ratio": 0.05,
    # 성장 0.18
    "revenue_growth": 0.11,   # TTM 매출 전년 대비
    "profit_growth": 0.07,    # TTM 영업이익 전년 대비
}

#: 시가총액이 있어야 계산되는 것들. 커버리지가 모자라면 통째로 빠진다.
VALUE_FEATURES = ("earnings_yield", "book_to_market", "sales_to_price")

MARKET_STATS = "market_stats"

#: 시가총액을 찾으러 거슬러 올라가는 달력일. 거래일 기준 한 달 남짓이다.
#: 재무(1150일)만큼 길 필요가 없다 — 시가총액은 매일 관측되므로, 길게 잡으면
#: 거래정지 종목의 **한참 묵은** 시가총액이 최신인 척 섞여 든다.
MARKET_CAP_LOOKBACK_DAYS = 45

#: 밸류를 켜기 위해 시가총액이 있어야 하는 종목 비율. 절반을 밑돌면 그날의
#: 밸류 횡단면은 표본이라 부르기 어렵다.
MIN_VALUE_COVERAGE = 0.5


def _quarter_index(period: str) -> tuple[int, int]:
    """``2024Q3`` → (2024, 3). 정렬용."""
    year, quarter = period.split("Q")
    return int(year), int(quarter)


def to_quarterly(frame: pd.DataFrame) -> pd.DataFrame:
    """DART 손익을 **진짜 분기값**으로 되돌린다.

    사업보고서(Q4)는 연간 누적이므로 같은 해 Q1~Q3 를 빼야 그 분기 값이 된다.
    Q1~Q3 가 없으면(신규 상장 등) Q4 를 버린다 — 4배 부풀린 값을 남기는 것보다
    없는 편이 낫다.
    """
    if frame.empty:
        return frame

    out = frame.copy()
    out[["fiscal_year", "quarter"]] = pd.DataFrame(
        [_quarter_index(period) for period in out["fiscal_period"]], index=out.index
    )

    flows = out["metric"].isin(FLOW_METRICS)
    annual = out[flows & (out["quarter"] == 4)]
    if annual.empty:
        return out

    partial = (
        out[flows & (out["quarter"] < 4)]
        .groupby(["entity_id", "metric", "fiscal_year"])
        .agg(partial_sum=("value", "sum"), quarters=("quarter", "nunique"))
    )

    joined = annual.join(
        partial, on=["entity_id", "metric", "fiscal_year"], how="left"
    )
    # 앞 세 분기가 다 있어야 복원할 수 있다.
    restorable = joined["quarters"] == 3
    out.loc[joined.index[restorable], "value"] = (
        joined.loc[restorable, "value"] - joined.loc[restorable, "partial_sum"]
    )
    out = out.drop(index=joined.index[~restorable.fillna(False)])
    return out


def trailing_twelve_months(frame: pd.DataFrame) -> pd.DataFrame:
    """종목·지표별 최근 4분기 합.

    분기 하나로 보면 계절성이 신호를 덮는다. 유통·게임처럼 4분기에 몰리는
    업종은 분기 비교만으로 판단할 수 없다.
    """
    ordered = frame.sort_values(["entity_id", "metric", "fiscal_year", "quarter"])
    grouped = ordered.groupby(["entity_id", "metric"])["value"]
    ordered["ttm"] = grouped.transform(lambda s: s.rolling(4).sum())
    ordered["ttm_prev"] = grouped.transform(lambda s: s.rolling(4).sum().shift(4))
    return ordered


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """0 나눗셈과 음수 분모를 막는다.

    자본잠식 기업은 total_equity 가 음수다. ROE 를 그대로 계산하면 적자 기업이
    양수 ROE 를 받는다 — 부호가 두 번 뒤집히기 때문이다. 그런 종목은 값을
    비운다.
    """
    safe = denominator.where(denominator > 0)
    return (numerator / safe).replace([np.inf, -np.inf], np.nan)


class FundamentalAnalyst(Analyst):
    name = "fundamental"
    version = "fundamental-v0.2.0"

    def market_cap(self, as_of: datetime) -> pd.Series:
        """종목별 최신 시가총액. 없으면 빈 Series.

        게이트가 ``observed_at <= as_of`` 를 막아 주므로, 여기서는 **그 시점까지
        관측된 것 중 가장 최근 것**을 고르기만 하면 된다.
        """
        raw = self.store.get(
            MARKET_STATS, as_of=as_of, lookback=MARKET_CAP_LOOKBACK_DAYS
        )
        if raw.empty:
            return pd.Series(dtype=float)
        raw = raw[(raw["market"] == str(self.market)) & (raw["metric"] == "market_cap")]
        if raw.empty:
            return pd.Series(dtype=float)
        latest = raw.sort_values("valid_from").groupby("entity_id").tail(1)
        caps = latest.set_index("entity_id")["value"].astype(float)
        # 0 이나 음수 시가총액은 관측 오류다. 나누면 부호가 뒤집힌다.
        return caps[caps > 0]

    def features(self, as_of: datetime) -> pd.DataFrame:
        # 게이트가 공시 전 재무를 걸러 준다. 45~69일 지연은 여기서 자동으로 지켜진다.
        raw = self.store.get(FUNDAMENTALS, as_of=as_of, lookback=LOOKBACK_DAYS)
        if raw.empty:
            return pd.DataFrame()
        # 시장별 재무 소스 — 국장 DART, 미장 EDGAR(companyfacts). 둘 다 같은 metric 이름·
        # 같은 분기 규약(Q4 = 연간 누적)으로 적혀 있어 아래 계산은 시장을 모른다.
        raw = raw[(raw["source"] == SOURCE_BY_MARKET.get(str(self.market), "dart"))
                  & (raw["market"] == str(self.market))]
        # 회계기간이 끝나기 전에 공시될 수는 없다. 이 조건이 깨진 행은 12월
        # 결산이 아닌 회사인데, 수집기가 fiscal_period 를 **요청값**으로 붙여
        # 오라벨한 것이다. 실측에서 2026Q3 의 관측시각이 2026-03-30 으로
        # 찍혀 있었다. 고칠 수 없는 라벨이므로 쓰지 않는다.
        raw = raw[raw["observed_at"] >= raw["valid_from"]]
        if raw.empty:
            return pd.DataFrame()

        quarterly = to_quarterly(raw)
        if quarterly.empty:
            return pd.DataFrame()
        series = trailing_twelve_months(quarterly)

        # 지표별 최신 관측 하나씩.
        latest = series.groupby(["entity_id", "metric"]).tail(1)

        # 관측된 종목 전체가 기준 축이다. pivot_table 은 값이 전부 NaN 인 표를
        # 통째로 비워 버리므로(전년 자료가 아직 없는 초기 구간이 그렇다),
        # 표마다 인덱스가 달라지지 않게 공통 축으로 맞춘다.
        entities = pd.Index(sorted(set(latest["entity_id"])), name="entity_id")
        columns = list(dict.fromkeys((*FLOW_METRICS, *STOCK_METRICS)))

        def spread(value_column: str) -> pd.DataFrame:
            table = latest.pivot_table(
                index="entity_id", columns="metric", values=value_column
            )
            return table.reindex(index=entities, columns=columns)

        ttm, ttm_prev, point = spread("ttm"), spread("ttm_prev"), spread("value")

        tradable = self.tradable_entities(as_of, lookback=LOOKBACK_DAYS)
        if tradable is not None:
            keep = entities.intersection(pd.Index(sorted(tradable)))
            ttm, ttm_prev, point = ttm.loc[keep], ttm_prev.loc[keep], point.loc[keep]
        if ttm.empty:
            return pd.DataFrame()

        raw_features = pd.DataFrame(index=ttm.index)
        raw_features["roe"] = _safe_ratio(ttm["net_income"], point["total_equity"])
        raw_features["operating_margin"] = _safe_ratio(
            ttm["operating_income"], ttm["revenue"]
        )
        raw_features["low_leverage"] = -_safe_ratio(
            point["total_liabilities"], point["total_equity"]
        )
        raw_features["current_ratio"] = _safe_ratio(
            point["current_assets"], point["current_liabilities"]
        )
        raw_features["revenue_growth"] = _safe_ratio(ttm["revenue"], ttm_prev["revenue"]) - 1.0
        raw_features["profit_growth"] = (
            _safe_ratio(ttm["operating_income"], ttm_prev["operating_income"]) - 1.0
        )

        # 밸류. 시가총액이 충분히 깔린 날만 만든다 — 모자라면 열 자체가 없고,
        # combine 이 남은 가중치로 정규화한다.
        caps = self.market_cap(as_of).reindex(ttm.index)
        if caps.notna().mean() >= MIN_VALUE_COVERAGE:
            raw_features["earnings_yield"] = ttm["net_income"] / caps
            raw_features["book_to_market"] = point["total_equity"] / caps
            raw_features["sales_to_price"] = ttm["revenue"] / caps

        raw_features = raw_features.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw_features.empty:
            return pd.DataFrame()
        # 결측은 횡단면 순위 중앙(0). 앞뒤로 채우면 미래를 본다.
        return raw_features.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        # 밸류가 빠진 날은 그 가중치를 빼고 정규화한다. 없는 피처를 0 으로
        # 채워 넣으면 나머지 피처의 영향력만 줄어든다.
        present = {
            name: weight for name, weight in WEIGHTS.items() if name in features.columns
        }
        return combine(features, present)
