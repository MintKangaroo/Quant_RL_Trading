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

## 지금 없는 것: 밸류

PER·PBR 은 시가총액이 필요하고, 시가총액은 **상장주식수**가 필요하다. pykrx 는
약관 문제로 막혔고, DART 주식총수는 분기보고서에서 비어 있으며, KRX Open API 는
아직 인증이 열리지 않았다. 없는 것을 지어내지 않는다 — 주식수가 들어오면
``VALUE_FEATURES`` 를 켠다.

그래서 v0.1 은 **퀄리티와 성장**만 본다. 둘 다 시가총액이 필요 없다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from lattice.analysts.base import Analyst, combine, zscore

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
FLOW_METRICS = ("revenue", "operating_income", "net_income")

#: 특정 시점의 잔액. 누적 개념이 없다.
STOCK_METRICS = (
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities",
)

WEIGHTS = {
    "roe": 0.30,             # 자본 대비 이익 — 퀄리티의 대표
    "operating_margin": 0.20,
    "low_leverage": 0.15,    # 부채비율의 역 (낮을수록 +)
    "current_ratio": 0.10,
    "revenue_growth": 0.15,  # TTM 매출 전년 대비
    "profit_growth": 0.10,   # TTM 영업이익 전년 대비
}

#: 상장주식수가 들어오면 켠다. 지금은 계산할 수 없다.
VALUE_FEATURES = ("per", "pbr", "psr")


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
    version = "fundamental-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        # 게이트가 공시 전 재무를 걸러 준다. 45~69일 지연은 여기서 자동으로 지켜진다.
        raw = self.store.get(FUNDAMENTALS, as_of=as_of, lookback=LOOKBACK_DAYS)
        if raw.empty:
            return pd.DataFrame()
        raw = raw[(raw["source"] == "dart") & (raw["market"] == str(self.market))]
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

        raw_features = raw_features.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw_features.empty:
            return pd.DataFrame()
        # 결측은 횡단면 중앙값 자리(z=0). 앞뒤로 채우면 미래를 본다.
        return raw_features.apply(zscore).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)
