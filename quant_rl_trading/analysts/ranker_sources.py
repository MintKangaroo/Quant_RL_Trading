"""6차 학습 — 랭커 정보원 후보 피처 4묶음 ("무너질 종목" 재료).

사전등록 `docs/protocols/ranker-sources-round6-2026-09.md` 의 정의를 코드로 옮긴 것.
**시행 도구와 실전 랭커가 같은 함수를 쓴다**(불변식 5) — 채택되면 `ranker.features()` 가
여기 함수를 부르고, 시행 도구도 처음부터 여기 함수만 부른다.

모든 함수는 `(analyst, as_of)` 를 받아 index = entity_id, 열 = 피처인 원값 표를 돌려준다.
순위 정규화(rank-gauss)는 호출자가 세션×시장 안에서 한다 — 여기서는 하지 않는다.
관측 시각은 `store.get(as_of=)` 이 거른다. 이 파일 안에서 시각을 당기거나 미루지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst

GROUPS: dict[str, tuple[str, ...]] = {
    "G1": ("turnover_decay", "amihud_20", "zero_volume_20"),
    "G2": ("distress_120", "dilution_60", "filing_burst_20"),
    "G3": ("short_intensity_20", "short_intensity_chg", "days_to_cover"),
    "G4": ("insider_sell_60", "insider_net_60"),
}

#: G1 — ADV120 이 필요하므로 달력일로 넉넉히.
PRICE_LOOKBACK_DAYS = 200
#: G2 — 250세션 평균(분모)까지 보려면 1년 남짓.
DOC_LOOKBACK_DAYS = 400
DOC_SESSION_WINDOWS = {"distress_120": 120, "dilution_60": 60, "filing_burst_20": 20}
DOC_BASELINE_SESSIONS = 250
DOC_BASELINE_MIN_SESSIONS = 120
#: G3 — 일별 20세션 + 격주 잔고 하나(최대 30일 지연).
SHORT_LOOKBACK_DAYS = 60
#: G4 — 60세션 ≈ 90달력일.
INSIDER_LOOKBACK_DAYS = 100
INSIDER_SESSIONS = 60


def _session_index(prices: pd.DataFrame) -> list[date]:
    return sorted(prices["session"].unique())


# --------------------------------------------------------------------------- G1 유동성 고갈


def liquidity_decay(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G1. 수준(risk.liquidity)이 아니라 **변화** — 거래가 마르고 있는가."""
    prices = analyst.price_panel(as_of, lookback=PRICE_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    close = analyst.wide(prices, "close")
    volume = analyst.wide(prices, "volume").reindex(columns=close.columns)
    value = analyst.wide(prices, "value").reindex(columns=close.columns)
    # 옛 미장 수집기는 value 를 비웠다 — close × volume 으로 메우되 결측 비율은 --coverage 가 적는다.
    value = value.where(value.notna(), close * volume)
    if len(close) < 120:
        return pd.DataFrame()

    raw = pd.DataFrame(index=close.columns)
    adv20 = value.tail(20).mean()
    adv120 = value.tail(120).mean()
    raw["turnover_decay"] = np.log(adv20.clip(lower=1.0)) - np.log(adv120.clip(lower=1.0))
    raw.loc[adv120.isna() | (adv120 <= 0), "turnover_decay"] = np.nan

    returns = close.pct_change().tail(20).abs()
    illiq = (returns / value.tail(20).replace(0.0, np.nan)).mean()
    raw["amihud_20"] = np.log(illiq.where(illiq > 0))

    vol20 = volume.tail(20)
    raw["zero_volume_20"] = (vol20.fillna(0.0) <= 0).sum() / vol20.notna().sum().clip(lower=1)
    raw.loc[vol20.notna().sum() < 10, "zero_volume_20"] = np.nan
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G2 부실·희석 공시


def filing_distress(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G2. event 가 가중합으로 눌러 놓은 distress·dilution 원자료와 공시 폭주."""
    documents = analyst.store.get(
        "documents", as_of=as_of, lookback=DOC_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "doc_type"],
    )
    if documents.empty:
        return pd.DataFrame()
    prefix = f"{analyst.market}:"
    documents = documents[documents["entity_id"].astype(str).str.startswith(prefix)].copy()
    if documents.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=DOC_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    sessions = _session_index(prices)
    documents["day"] = documents["valid_from"].dt.date
    # 공시 접수일을 그 날 또는 다음 세션에 붙인다 — "최근 N 세션" 을 세션 축으로 세기 위해.
    pos = np.searchsorted(np.array(sessions), documents["day"].to_numpy(), side="left")
    documents = documents[pos < len(sessions)].copy()
    documents["ago"] = len(sessions) - 1 - pos[pos < len(sessions)]

    raw = pd.DataFrame(index=pd.Index(sorted(prices["entity_id"].unique()), name="entity_id"))
    for column, window in DOC_SESSION_WINDOWS.items():
        recent = documents[documents["ago"] < window]
        if column == "filing_burst_20":
            counts = recent.groupby("entity_id").size()
            baseline = documents[documents["ago"] < DOC_BASELINE_SESSIONS].groupby("entity_id").size()
            covered = min(len(sessions), DOC_BASELINE_SESSIONS)
            if covered < DOC_BASELINE_MIN_SESSIONS:
                raw[column] = np.nan
                continue
            per_session = baseline.reindex(raw.index).fillna(0.0) / covered
            raw[column] = counts.reindex(raw.index).fillna(0.0) / (per_session * window).replace(0.0, np.nan)
        else:
            kind = "distress" if column.startswith("distress") else "dilution"
            counts = recent[recent["doc_type"] == kind].groupby("entity_id").size()
            raw[column] = counts.reindex(raw.index).fillna(0.0)
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G3 미장 공매도 흐름


def short_flow(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G3. FINRA 일별 공매도 거래량 비율(20·5−20)과 최신 격주 잔고 days_to_cover. 미장만 값이 있다."""
    frame = analyst.store.get(
        "short_flow", as_of=as_of, lookback=SHORT_LOOKBACK_DAYS, market=str(analyst.market),
        columns=["entity_id", "valid_from", "kind", "short_volume", "total_volume", "days_to_cover"],
    )
    if frame.empty:
        return pd.DataFrame()
    # FINRA 는 매매 대상보다 훨씬 넓은 명단(ETF·우선주·ADR)을 준다 — 그 시점 매매 가능 종목만 남긴다.
    tradable = analyst.tradable_entities(as_of, lookback=SHORT_LOOKBACK_DAYS)
    if tradable is not None:
        frame = frame[frame["entity_id"].isin(tradable)]
    if frame.empty:
        return pd.DataFrame()
    frame = frame.sort_values("valid_from")
    daily = frame[frame["kind"].astype(str) == "volume"]
    raw = pd.DataFrame(index=pd.Index(sorted(frame["entity_id"].unique()), name="entity_id"))
    if not daily.empty:
        last20 = daily.groupby("entity_id").tail(20)
        last5 = daily.groupby("entity_id").tail(5)
        n20 = last20.groupby("entity_id").size()

        def ratio(part: pd.DataFrame) -> pd.Series:
            g = part.groupby("entity_id")
            return g["short_volume"].sum() / g["total_volume"].sum().replace(0.0, np.nan)

        r20 = ratio(last20).where(n20 >= 10)
        raw["short_intensity_20"] = r20
        raw["short_intensity_chg"] = ratio(last5) - r20
    interest = frame[frame["kind"].astype(str) == "interest"]
    if not interest.empty:
        raw["days_to_cover"] = interest.groupby("entity_id")["days_to_cover"].last().astype(float)
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G4 내부자 처분


def insider_selling(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G4. 최근 60세션 임원·주요주주 처분(그날 종가 × 주수) ÷ ADV20, 그리고 시행 D 의 순매수."""
    trades = analyst.store.get(
        "insider_trades", as_of=as_of, lookback=INSIDER_LOOKBACK_DAYS, market=str(analyst.market),
        columns=["entity_id", "valid_from", "change"],
    )
    if trades.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=INSIDER_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    close = analyst.wide(prices, "close")
    value = analyst.wide(prices, "value").reindex(columns=close.columns)
    sessions = _session_index(prices)
    if len(sessions) < 20:
        return pd.DataFrame()
    window_start = sessions[-min(INSIDER_SESSIONS, len(sessions))]
    trades = trades[trades["change"].fillna(0.0) != 0].copy()
    trades["day"] = trades["valid_from"].dt.date
    trades = trades[trades["day"] >= window_start]
    if trades.empty:
        return pd.DataFrame()
    # 보고일의 종가(없으면 직전 세션 종가)로 금액을 매긴다.
    idx = np.searchsorted(np.array(sessions), trades["day"].to_numpy(), side="right") - 1
    trades = trades[idx >= 0].copy()
    idx = idx[idx >= 0]
    px = close.reindex(index=[sessions[i] for i in idx]).to_numpy()
    cols = {c: j for j, c in enumerate(close.columns)}
    col_idx = trades["entity_id"].map(cols)
    trades = trades[col_idx.notna()].copy()
    if trades.empty:
        return pd.DataFrame()
    prices_at = px[np.arange(len(px))[col_idx.notna().to_numpy()], col_idx.dropna().astype(int).to_numpy()]
    trades["krw"] = trades["change"].astype(float) * prices_at
    adv20 = value.tail(20).mean().replace(0.0, np.nan)
    g = trades.groupby("entity_id")["krw"]
    raw = pd.DataFrame(index=adv20.index)
    raw["insider_sell_60"] = (-g.apply(lambda s: s[s < 0].sum())).reindex(adv20.index).fillna(0.0) / adv20
    raw["insider_net_60"] = g.sum().reindex(adv20.index).fillna(0.0) / adv20
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


BUILDERS = {"G1": liquidity_decay, "G2": filing_distress, "G3": short_flow, "G4": insider_selling}


def build(group: str, analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """묶음 이름 → 원값 표. 등록된 열만, 등록된 순서로 돌려준다(없는 열은 결측)."""
    raw = BUILDERS[group](analyst, as_of)
    columns = list(GROUPS[group])
    if raw.empty:
        return pd.DataFrame(columns=columns)
    return raw.reindex(columns=columns)
