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
    # G5 (2026-09-08 추가, 사용자 승인) — 회계 품질. 현금흐름이 창고에 없어 운전자본 발생액·Altman Z'' 축약형·적자 연속.
    "G5": ("accrual_wc", "z_lite", "loss_streak"),
    # G6 (2026-09-08 추가, 사용자 승인) — 미장 8-K 2.02(실적 발표) 기준 PEAD. 시행 J 는 10-Q 공시일이라 앞 2~3주를 놓쳤다.
    "G6": ("pead_2d", "days_since_earn", "earn_gap"),
    # G7 (2026-09-19 추가, 사용자 승인) — 미장 내부자 Form 4. G4 의 미장 짝. 표는 form4_trades(G4 입력을 안 건드린다).
    "G7": ("form4_sell_60", "form4_sellers_20", "form4_buy_60"),
    # X (시행 X, filing-text-embedding-2026-09.md) — 공시 원문 임베딩의 주성분 3개. 국장만(DART 원문).
    "X": ("text_pc1", "text_pc2", "text_pc3"),
    # G8 (2026-09-23 추가, 사용자 승인) — 국장 잠정실적 서프라이즈(PEAD). G6 의 국장 짝. 표는 prelim_earnings.
    "G8": ("prelim_op_surprise", "prelim_sales_yoy", "prelim_age"),
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
#: X — 공시 임베딩: 최근 60세션 공시의 주성분 평균.
EMBED_LOOKBACK_DAYS = 100
EMBED_SESSIONS = 60
#: G7 — 접수일 기준 60세션(금액)·20세션(보고자 수).
FORM4_LOOKBACK_DAYS = 100
FORM4_SESSIONS = 60
FORM4_CLUSTER_SESSIONS = 20


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

    returns = close.pct_change(fill_method=None).tail(20).abs()
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


# --------------------------------------------------------------------------- G5 회계 품질


FUND_LOOKBACK_DAYS = 1150  # fundamental Analyst 와 같다 — 4분기 전 값과 TTM 이 필요하다


def accounting_quality(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G5. 이익의 질과 부도 거리 — "무너질 종목" 의 고전적 재료.

    - ``accrual_wc``  = (운전자본 − 4분기 전 운전자본) / 총자산. 운전자본 = 유동자산 − 유동부채.
                        현금흐름표가 없어 Sloan 발생액의 대용이다(현금 변화가 섞인다 — 한계로 적는다).
    - ``z_lite``      = 6.56·WC/TA + 6.72·EBIT_ttm/TA + 1.05·자본/부채. Altman Z'' 에서 이익잉여금 항을 뺀 것.
    - ``loss_streak`` = 최근 분기부터 거슬러 순이익 < 0 인 연속 분기 수(최대 8).
    분기 복원·TTM 은 fundamental Analyst 와 **같은 함수**를 쓴다(공시 전 재무는 store.get 이 막는다).
    """
    from quant_rl_trading.analysts.fundamental import (
        FLOW_METRICS,
        FUNDAMENTALS,
        SOURCE_BY_MARKET,
        STOCK_METRICS,
        to_quarterly,
        trailing_twelve_months,
    )

    raw = analyst.store.get(FUNDAMENTALS, as_of=as_of, lookback=FUND_LOOKBACK_DAYS, market=str(analyst.market))
    if raw.empty:
        return pd.DataFrame()
    raw = raw[(raw["source"] == SOURCE_BY_MARKET.get(str(analyst.market), "dart")) & (raw["observed_at"] >= raw["valid_from"])]
    if raw.empty:
        return pd.DataFrame()
    quarterly = to_quarterly(raw)
    if quarterly.empty:
        return pd.DataFrame()
    series = trailing_twelve_months(quarterly).sort_values(["entity_id", "metric", "fiscal_year", "quarter"])
    latest = series.groupby(["entity_id", "metric"]).tail(1)
    prev4 = series.groupby(["entity_id", "metric"]).nth(-5)  # 4분기 전 (최근 것이 -1)
    entities = pd.Index(sorted(set(latest["entity_id"])), name="entity_id")
    columns = list(dict.fromkeys((*FLOW_METRICS, *STOCK_METRICS)))

    def spread(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(index=entities, columns=columns, dtype=float)
        return frame.pivot_table(index="entity_id", columns="metric", values=value_column).reindex(index=entities, columns=columns)

    point, ttm, point_prev = spread(latest, "value"), spread(latest, "ttm"), spread(prev4, "value")
    ta = point["total_assets"].where(point["total_assets"] > 0)
    wc = point["current_assets"] - point["current_liabilities"]
    wc_prev = point_prev["current_assets"] - point_prev["current_liabilities"]
    raw_f = pd.DataFrame(index=entities)
    raw_f["accrual_wc"] = (wc - wc_prev) / ta
    liabilities = point["total_liabilities"].where(point["total_liabilities"] > 0)
    raw_f["z_lite"] = 6.56 * wc / ta + 6.72 * ttm["operating_income"] / ta + 1.05 * point["total_equity"] / liabilities
    ni = series[series["metric"] == "net_income"].sort_values(["entity_id", "fiscal_year", "quarter"])
    streak = ni.groupby("entity_id")["value"].apply(
        lambda v: int(next((i for i, x in enumerate(reversed(v.tolist()[-8:])) if not (x < 0)), min(len(v), 8)))
    )
    raw_f["loss_streak"] = streak.reindex(entities).astype(float)
    tradable = analyst.tradable_entities(as_of, lookback=FUND_LOOKBACK_DAYS)
    if tradable is not None:
        raw_f = raw_f.loc[raw_f.index.intersection(pd.Index(sorted(tradable)))]
    return raw_f.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G6 미장 8-K 실적 발표 PEAD


EARN_LOOKBACK_DAYS = 120
EARN_MAX_SESSIONS = 60
EARN_ITEM = "2.02"


def earnings_drift(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G6. 최근 60세션 안 마지막 8-K 2.02(실적 발표) 뒤의 반응. 미장만(국장 documents 엔 8-K 가 없다).

    - ``pead_2d``        = 발표일 t 와 t+1 의 2일 누적 초과수익(종목 − 명단 동일가중). 발표 시각(장 전/후)을 모르므로
                           [t, t+1] 로 잡는다(시행 C 와 같은 규칙).
    - ``days_since_earn``= 마지막 발표 뒤 지난 세션 수(0~60). 없으면 결측.
    - ``earn_gap``       = 발표일 t 의 하루 초과수익(반응의 첫날만).
    발표일은 `documents` 의 8-K 제목에 2.02 항목이 있는 행의 접수일. 60세션이 넘은 발표는 결측(드리프트가 끝났다).
    """
    docs = analyst.store.get(
        "documents", as_of=as_of, lookback=EARN_LOOKBACK_DAYS, columns=["entity_id", "valid_from", "doc_type", "title"],
    )
    if docs.empty:
        return pd.DataFrame()
    prefix = f"{analyst.market}:"
    docs = docs[docs["entity_id"].astype(str).str.startswith(prefix) & (docs["doc_type"] == "earnings")
                & docs["title"].astype(str).str.contains(EARN_ITEM, regex=False)].copy()
    if docs.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=EARN_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    close = analyst.wide(prices, "close")
    sessions = list(close.index)
    if len(sessions) < 5:
        return pd.DataFrame()
    returns = close.pct_change(fill_method=None)
    excess = returns.sub(returns.mean(axis=1), axis=0)
    docs["day"] = docs["valid_from"].dt.date
    last = docs.sort_values("valid_from").groupby("entity_id")["day"].last()
    day_pos = {d: i for i, d in enumerate(sessions)}
    raw = pd.DataFrame(index=pd.Index(sorted(close.columns), name="entity_id"))
    pead, since, gap = {}, {}, {}
    for entity, day in last.items():
        if entity not in raw.index:
            continue
        # 접수일 그 날 또는 다음 세션
        pos = next((day_pos[s_] for s_ in sessions if s_ >= day), None)
        if pos is None:
            continue
        ago = len(sessions) - 1 - pos
        if ago > EARN_MAX_SESSIONS:
            continue
        since[entity] = float(ago)
        e0 = excess.iloc[pos][entity] if pos < len(sessions) else np.nan
        e1 = excess.iloc[pos + 1][entity] if pos + 1 < len(sessions) else np.nan
        gap[entity] = float(e0)
        pead[entity] = float(np.nansum([e0, e1])) if not (np.isnan(e0) and np.isnan(e1)) else np.nan
    raw["pead_2d"] = pd.Series(pead)
    raw["days_since_earn"] = pd.Series(since)
    raw["earn_gap"] = pd.Series(gap)
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G7 미장 내부자 Form 4


def form4_trading(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G7. 계획 외 장내매도 금액·매도 보고자 수·장내매수 금액. **창은 접수일(valid_from)로 센다** —
    거래일로 세면 아직 공시 안 된 거래를 본다. 10b5-1 계획매매(plan_10b5_1 == 1)는 매도에서 뺀다."""
    trades = analyst.store.get(
        "form4_trades", as_of=as_of, lookback=FORM4_LOOKBACK_DAYS, market=str(analyst.market),
        columns=["entity_id", "valid_from", "owner_cik", "trans_code", "shares", "price", "plan_10b5_1"],
    )
    if trades.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=FORM4_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    close = analyst.wide(prices, "close")
    volume = analyst.wide(prices, "volume").reindex(columns=close.columns)
    value = analyst.wide(prices, "value").reindex(columns=close.columns)
    value = value.where(value.notna(), close * volume)  # G1 과 같은 대체
    sessions = _session_index(prices)
    if len(sessions) < FORM4_CLUSTER_SESSIONS:
        return pd.DataFrame()
    trades = trades.copy()
    trades["day"] = trades["valid_from"].dt.date
    trades["usd"] = trades["shares"].astype(float) * trades["price"].astype(float)
    trades = trades[trades["usd"] > 0]
    start_60 = sessions[-min(FORM4_SESSIONS, len(sessions))]
    start_20 = sessions[-FORM4_CLUSTER_SESSIONS]
    recent = trades[trades["day"] >= start_60]
    sells = recent[(recent["trans_code"] == "S") & (recent["plan_10b5_1"].fillna(0.0) != 1.0)]
    buys = recent[recent["trans_code"] == "P"]
    adv20 = value.tail(20).mean().replace(0.0, np.nan)
    raw = pd.DataFrame(index=adv20.index)
    raw["form4_sell_60"] = sells.groupby("entity_id")["usd"].sum().reindex(adv20.index).fillna(0.0) / adv20
    cluster = sells[sells["day"] >= start_20]
    raw["form4_sellers_20"] = (
        cluster[cluster["owner_cik"].astype(str) != ""].groupby("entity_id")["owner_cik"].nunique()
        .reindex(adv20.index).fillna(0.0).astype(float)
    )
    raw["form4_buy_60"] = buys.groupby("entity_id")["usd"].sum().reindex(adv20.index).fillna(0.0) / adv20
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- X 공시 원문 임베딩


def filing_embedding(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """시행 X. 최근 60세션 공시 임베딩 주성분의 **종목별 평균**. 공시가 없으면 결측(0 이 아니다 —
    '공시가 없었다' 와 '주성분이 0 이었다' 는 다른 사실이고, rank-gauss 가 결측을 중앙으로 보낸다)."""
    rows = analyst.store.get(
        "document_embeddings", as_of=as_of, lookback=EMBED_LOOKBACK_DAYS, market=str(analyst.market),
        columns=["entity_id", "valid_from", "pc1", "pc2", "pc3"],
    )
    if rows.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=EMBED_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    sessions = _session_index(prices)
    if len(sessions) < 20:
        return pd.DataFrame()
    start = sessions[-min(EMBED_SESSIONS, len(sessions))]
    rows = rows.copy()
    rows["day"] = rows["valid_from"].dt.date
    rows = rows[rows["day"] >= start]
    if rows.empty:
        return pd.DataFrame()
    mean = rows.groupby("entity_id")[["pc1", "pc2", "pc3"]].mean()
    mean.columns = ["text_pc1", "text_pc2", "text_pc3"]
    return mean.replace([np.inf, -np.inf], np.nan).dropna(how="all")


# --------------------------------------------------------------------------- G8 국장 잠정실적 서프라이즈

#: 60세션 + 여유. 발표 효과의 창을 넘은 공시는 결측이다(등록).
PRELIM_LOOKBACK_DAYS = 120
PRELIM_MAX_SESSIONS = 60
#: 같은 분기의 연결·별도가 며칠 차로 따로 나오는 경우 — 이 안이면 연결을 쓴다.
PRELIM_SAME_PERIOD_DAYS = 5


def prelim_surprise(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G8. 개장 전까지 공시된 가장 최근 잠정실적 한 건(연결 우선, 정정은 정정 시각에 갈아끼움).

    - ``prelim_op_surprise`` = (당기 − 전년동기 영업이익) / 공시 전 마지막 세션의 시가총액
    - ``prelim_sales_yoy``   = 매출액 전년동기대비 증감률(%) — 원문 칸이 아니라 두 값으로 계산(형식마다 자리가 다르다)
    - ``prelim_age``         = 공시 뒤 지난 세션 수(0~60). 넘으면 세 피처 모두 결측.
    """
    rows = analyst.store.get("prelim_earnings", as_of=as_of, lookback=PRELIM_LOOKBACK_DAYS, market=str(analyst.market))
    if rows.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=PRELIM_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    sessions = _session_index(prices)
    if len(sessions) < 5:
        return pd.DataFrame()
    rows = rows.sort_values(["observed_at", "valid_from"])
    picked = []
    for _entity, g in rows.groupby("entity_id"):
        latest = g["valid_from"].max()
        recent = g[g["valid_from"] >= latest - pd.Timedelta(days=PRELIM_SAME_PERIOD_DAYS)]
        cons = recent[recent["basis"] == "consolidated"]
        picked.append((cons if not cons.empty else recent).iloc[-1])
    last = pd.DataFrame(picked)
    last["day"] = pd.to_datetime(last["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    pos = np.searchsorted(np.array(sessions), last["day"].to_numpy(), side="left")
    last["ago"] = len(sessions) - 1 - pos
    last = last[(pos < len(sessions)) & (last["ago"] <= PRELIM_MAX_SESSIONS)].copy()
    if last.empty:
        return pd.DataFrame()
    caps = analyst.store.get("market_stats", as_of=as_of, lookback=PRELIM_LOOKBACK_DAYS, market=str(analyst.market),
                             columns=["entity_id", "valid_from", "metric", "value"])
    caps = caps[caps["metric"] == "market_cap"] if not caps.empty else caps
    cap_before = {}
    if not caps.empty:
        caps = caps.assign(day=pd.to_datetime(caps["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date).sort_values("day")
        by_entity = {e: g for e, g in caps.groupby("entity_id")}
        for r in last.itertuples():
            g = by_entity.get(r.entity_id)
            before = g[g["day"] < r.day] if g is not None else None
            if before is not None and not before.empty:
                cap_before[r.entity_id] = float(before["value"].iloc[-1])
    last = last.set_index("entity_id")
    raw = pd.DataFrame(index=last.index)
    cap = pd.Series(cap_before).reindex(raw.index)
    raw["prelim_op_surprise"] = (last["op_cur"] - last["op_base"]) / cap.where(cap > 0)
    base = last["sales_base"].abs()
    raw["prelim_sales_yoy"] = (last["sales_cur"] - last["sales_base"]) / base.where(base > 0) * 100.0
    raw["prelim_age"] = last["ago"].astype(float)
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")


BUILDERS = {
    "G1": liquidity_decay, "G2": filing_distress, "G3": short_flow, "G4": insider_selling,
    "G5": accounting_quality, "G6": earnings_drift, "G7": form4_trading, "G8": prelim_surprise, "X": filing_embedding,
}


def build(group: str, analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """묶음 이름 → 원값 표. 등록된 열만, 등록된 순서로 돌려준다(없는 열은 결측)."""
    raw = BUILDERS[group](analyst, as_of)
    columns = list(GROUPS[group])
    if raw.empty:
        return pd.DataFrame(columns=columns)
    return raw.reindex(columns=columns)
