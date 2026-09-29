"""FA(전 피처) 76열을 **세션 하나씩, 그 시점 as_of 로** 만든다 — BE2 shadow 의 매일 입력.

## 왜 새로 짜지 않고 부르기만 하나 (불변식 5)

판정 때의 FA 패널(`tools/final_round_kit.load_full_panel`)은 연구 캐시를 조립한 것이다 — 그 캐시들이 **이미
패키지 함수로** 구워졌다. 그래서 여기는 같은 함수를 그 세션의 같은 as_of 로 부를 뿐이다:

| 묶음 | 연구 캐시가 부른 것 | as_of |
|---|---|---|
| 점수 6 | 창고 `signals`(일일 실행기가 쓴 값, `tools/bake_long_panel.py`) | 세션 공표 시각 |
| 원피처 35 | `Analyst.features(as_of)` (`tools/diagnose_ic.py cache-extra`) | 세션 공표 시각 |
| G1~G7 | `analysts/ranker_sources.build(묶음, RiskAnalyst, as_of)` (`trial_ranker_sources.build_panel`) | 세션 **개장** 시각 |
| BA 5 | `analysts/valueup.features` (시행 BA) | 개장 전 관측만 센다 |

정규화(시장·세션 안 rank-gauss, 결측 표지, BA 개수 0 채우기)는 kit 의 `attach_block`·`finalize` 규칙을 그대로 옮겼다.
**같은지 테스트가 지킨다**(`tests/analysts/test_fa_features.py` — kit 의 `finalize` 와 정확 비교).

## 행(종목) 집합

연구 패널의 국장 행 = 여섯 Analyst 점수가 있는 종목 ∪ 그 세션 시세가 있는 종목(타깃 행). 실전도 같다 —
타깃은 미래라 못 보지만 타깃 행의 뿌리는 **그 세션 시세가 있는 종목**이다. 행 집합은 트랜스포머의 횡단면
주의(같은 날 종목끼리)와 순위 백분위에 들어가므로 좁히지 않는다.

## 연구 패널과 다른 한 곳 — 밸류업 결측 표지(`miss_ba`)

연구 패널은 공시를 판정 창 **끝(2026-06-30)까지** 한 번에 읽고 "그 종목에 해당 공시가 한 건이라도 있었나" 로
표지를 세웠다 — 2022 년 세션의 표지가 2025 년 첫 배당 공시를 안다(미래). 실전은 그럴 수 없으므로 같은 시작점
(`BA_PANEL_START` 에서 365+30일 앞)부터 **그 세션 as_of 까지** 본다. 판정 창 끝 무렵 세션에서는 둘이 거의 같고
(모델이 가장 최근에 배운 구간), 개수·경과일 피처 자체는 개장 전 관측만 세므로 같다.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from quant_rl_trading.analysts import ranker_sources, valueup
from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.chart import ChartAnalyst
from quant_rl_trading.analysts.event import EventAnalyst
from quant_rl_trading.analysts.flow_kr import FlowKrAnalyst
from quant_rl_trading.analysts.flow_us import FlowUsAnalyst
from quant_rl_trading.analysts.fundamental import FundamentalAnalyst
from quant_rl_trading.analysts.ranker import BASE_ANALYSTS
from quant_rl_trading.analysts.regime import RegimeAnalyst
from quant_rl_trading.analysts.risk import RiskAnalyst
from quant_rl_trading.collectors.market_hours import Market, local_time
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.fa import (
    BA_COLUMNS,
    BA_ZERO_FIRST,
    BLOCK_COLUMNS,
    BLOCK_MARKETS,
    BLOCK_ORDER,
    FA_FEATURES,
    FEATURE_SET,
    FLAG_OF,
    FLAGS,
    RAW_ANALYSTS,
    SCALED,
    SCORE_COLUMNS,
)
from quant_rl_trading.store.memo import MemoStore
from quant_rl_trading.store.prices import read_prices

logger = logging.getLogger(__name__)

TABLE = "fa_features"
SIGNALS = "signals"

#: 원피처를 내는 Analyst 클래스 — `tools/measure_ic.ANALYSTS` 의 같은 이름.
ANALYST_CLASSES: dict[str, type[Analyst]] = {
    "chart": ChartAnalyst, "event": EventAnalyst, "flow_kr": FlowKrAnalyst, "flow_us": FlowUsAnalyst,
    "fundamental": FundamentalAnalyst, "regime": RegimeAnalyst, "risk": RiskAnalyst,
}

#: G 묶음의 as_of — 세션 **개장 직전**(UTC). `tools/trial_ranker_sources.OPEN_UTC` 와 같다.
OPEN_UTC: dict[str, timedelta] = {"KR": timedelta(hours=0), "US": timedelta(hours=13, minutes=30)}

#: 판정 때 **실제로 자료가 있던** 묶음(시장별). 국장 G3·G6·G7 은 미장 자료라 월 조각이 0행이었다 —
#: 빌더가 언젠가 국장 행을 내더라도 모델이 본 적 없는 값을 넣지 않는다(표지 1 로 남는다).
LIVE_GROUPS: dict[str, tuple[str, ...]] = {
    "KR": ("G1", "G2", "G4", "G5"),
    "US": ("G1", "G2", "G3", "G4", "G5", "G6", "G7"),
}

#: 연구 패널(판정 창)의 첫 세션 — 밸류업 표지의 공시 읽기 시작점이 여기서 나온다(모듈 독스트링).
BA_PANEL_START = date(2022, 7, 1)

#: 점수 한 세션을 찾는 창(달력일). 주말·연휴를 넘겨도 그 세션 하나는 들어온다.
SCORE_LOOKBACK_DAYS = 4
#: 그 세션 시세가 있는 종목(타깃 행의 뿌리) — 하루치면 되지만 연휴를 넘길 여유.
PRICE_LOOKBACK_DAYS = 7


# --------------------------------------------------------------------------- 정규화 (kit 규칙 그대로)


def rank_gauss(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """세션×시장 안에서 순위 → 정규분위. `tools/trial_pooled_rank.rank_gauss` 와 **같은 연산**이다.

    결측은 순위 중앙(0). 값은 float32. 순위는 입력 dtype(float32) 그대로 매긴다 — float64 로 올려 매기면
    float32 에서 같던 두 값이 갈라져 동점 처리가 달라진다.
    """
    out = frame.copy()
    g = frame.groupby(["market", "session"])
    for c in columns:
        r = g[c].rank(pct=True)
        cnt = g[c].transform("count")
        out[c] = pd.Series(norm.ppf((r * cnt - 0.5) / cnt), index=frame.index).fillna(0.0).astype(np.float32)
    return out


def attach(panel: pd.DataFrame, frame: pd.DataFrame | None, block: str) -> pd.DataFrame:
    """묶음 하나를 왼쪽 병합하고 결측 표지를 세운다 — kit `attach_block` 과 같은 규칙.

    표지는 개별 열의 NaN 이 아니라 **그 (종목, 세션) 키가 묶음 자료에 있었는가** 다.
    """
    cols = list(BLOCK_COLUMNS[block])
    flag = FLAG_OF.get(block)
    for c in cols:
        if c not in panel.columns:
            panel[c] = np.nan
    if frame is None or frame.empty:
        if flag:
            panel[flag] = np.float32(1.0)
        return panel
    have = [c for c in cols if c in frame.columns]
    right = frame[["entity_id", "session", *have]].drop_duplicates(["entity_id", "session"], keep="last")
    right["_hit"] = np.float32(1.0)
    merged = panel.merge(right, on=["entity_id", "session"], how="left", suffixes=("", "_new"))
    for c in have:
        new = f"{c}_new" if f"{c}_new" in merged.columns else c
        merged[c] = merged[new].astype(np.float32)
        if new != c:
            merged = merged.drop(columns=[new])
    if flag:
        merged[flag] = (1.0 - merged["_hit"].fillna(0.0)).astype(np.float32)
    return merged.drop(columns=["_hit"])


def finalize(panel: pd.DataFrame) -> pd.DataFrame:
    """묶음별 채우기 → 시장별 rank-gauss → 표지 → is_us → 빠진 열 채우기. kit `finalize` + `load_full_panel` 뒷정리.

    돌려주는 표: entity_id · session · market · FA 76열(float32). 결측이 남지 않는다.
    """
    zero_first = [c for c in BA_ZERO_FIRST if c in panel.columns]
    if zero_first:
        panel[zero_first] = panel[zero_first].fillna(0.0)
    scaled = [c for c in SCALED if c in panel.columns]
    panel = rank_gauss(panel, scaled)
    for f in FLAGS:
        if f not in panel.columns:
            panel[f] = np.float32(1.0)
        panel[f] = panel[f].fillna(1.0).astype(np.float32)
    panel["is_us"] = (panel["market"] == "US").astype(np.float32)
    for c in FA_FEATURES:
        if c not in panel.columns:
            panel[c] = np.float32(1.0 if c.startswith("miss_") else 0.0)
        elif panel[c].isna().any():
            panel[c] = panel[c].fillna(1.0 if c.startswith("miss_") else 0.0).astype(np.float32)
    return panel[["entity_id", "session", "market", *FA_FEATURES]].reset_index(drop=True)


# --------------------------------------------------------------------------- 묶음별 원값


def session_of(market: str, as_of: datetime) -> date:
    """as_of 가 가리키는 그 시장의 세션 날짜(현지). 국장 16:00 KST → 그날."""
    return local_time(Market(market), as_of).date()


def _scores(store, market: str, session: date, as_of: datetime) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """점수 6 — 창고 `signals` 의 그 세션 값. 종목·Analyst 마다 가장 늦은 관측(정정본은 창고가 이미 골랐다)."""
    names = [name for name, _col in BASE_ANALYSTS.items()
             if not (market == "KR" and name == "flow_us") and not (market == "US" and name == "flow_kr")]
    frame = store.get(SIGNALS, as_of=as_of, lookback=SCORE_LOOKBACK_DAYS, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "analyst", "score"])
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", *SCORE_COLUMNS])
    frame = frame[frame["analyst"].astype(str).isin(names)
                  & frame["entity_id"].astype(str).str.startswith(f"{market}:")]
    days = pd.to_datetime(frame["valid_from"]).map(lambda t: session_of(market, t.to_pydatetime()))
    frame = frame[days == session]
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", *SCORE_COLUMNS])
    frame = frame.sort_values("observed_at").groupby(["entity_id", "analyst"], as_index=False).tail(1)
    frame = frame.assign(feature=frame["analyst"].map(BASE_ANALYSTS))
    wide = frame.pivot_table(index="entity_id", columns="feature", values="score", aggfunc="last")
    for c in SCORE_COLUMNS:
        if c not in wide.columns:
            wide[c] = np.nan
    wide = wide[list(SCORE_COLUMNS)].astype(np.float32)
    wide.columns.name = None
    return wide.reset_index().assign(session=session)


def score_counts(store, market: str, session: date, as_of: datetime) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """점수 6 열마다 그 세션에 점수가 있는 종목 수. 하나라도 0 이면 일일 실행기가 아직 안 돌았거나 그 Analyst 가 죽었다.

    그 상태로 FA 를 적으면 빈 열이 "순위 중앙" 으로 굳어 append-only 창고에 남는다 — 호출자(score_be2)는 적지 않고 멈춘다.
    """
    wide = _scores(store, market, session, as_of)
    return {c: int(wide[c].notna().sum()) if c in wide.columns else 0 for c in SCORE_COLUMNS}


def _priced(store, market: str, session: date, as_of: datetime) -> set[str]:  # type: ignore[no-untyped-def]
    """그 세션 종가가 있는 종목 — 연구 패널의 타깃 행."""
    prices = read_prices(store, as_of=as_of, lookback=PRICE_LOOKBACK_DAYS, market=market, columns=["close"])
    if prices.empty:
        return set()
    days = pd.to_datetime(prices["valid_from"]).map(lambda t: session_of(market, t.to_pydatetime()))
    return set(prices.loc[days == session, "entity_id"].astype(str))


def _raw(store, market: str, session: date, as_of: datetime, keys: set[str]) -> pd.DataFrame | None:  # type: ignore[no-untyped-def]
    """원피처 — 여섯 Analyst 의 `features(as_of)`, 열 이름 `raw_{analyst}_{feature}`. 행 집합 안만 남긴다."""
    merged: pd.DataFrame | None = None
    for name in RAW_ANALYSTS[market]:
        analyst = ANALYST_CLASSES[name](store, ReplayClock(as_of), market=Market(market))
        frame = analyst.features(as_of)
        if frame is None or frame.empty:
            continue
        frame = frame.reset_index(names="entity_id")
        frame = frame[frame["entity_id"].astype(str).isin(keys)]
        feats = [c for c in frame.columns if c != "entity_id"]
        frame[feats] = frame[feats].astype("float32")
        frame = frame.rename(columns={c: f"raw_{name}_{c}" for c in feats})
        merged = frame if merged is None else merged.merge(frame, on="entity_id", how="outer")
    if merged is None:
        return None
    return merged.assign(session=session)


def _group(store, group: str, market: str, session: date) -> pd.DataFrame | None:  # type: ignore[no-untyped-def]
    """묶음 G* — `ranker_sources.build` 를 세션 **개장** 시각으로. 연구 월 조각과 같은 호출이다."""
    moment = datetime(session.year, session.month, session.day, tzinfo=UTC) + OPEN_UTC[market]
    analyst = RiskAnalyst(store, ReplayClock(moment), market=Market(market))
    raw = ranker_sources.build(group, analyst, moment)
    if raw.empty:
        return None
    raw = raw.reset_index().rename(columns={"index": "entity_id"})
    if "entity_id" not in raw.columns:
        raw = raw.rename(columns={raw.columns[0]: "entity_id"})
    return raw.assign(session=session)


def _ba(store, session: date, as_of: datetime, entities: set[str]) -> pd.DataFrame | None:  # type: ignore[no-untyped-def]
    """밸류업 5 — 시행 BA 함수. 표지의 공시 읽기 시작점을 연구 패널과 맞춘다(모듈 독스트링)."""
    sessions = [BA_PANEL_START, session] if session > BA_PANEL_START else [session]
    frame = valueup.features(store, sessions, entities, as_of=as_of)
    if frame.empty:
        return None
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    frame = frame[frame["session"] == session]
    for c in BA_COLUMNS:
        if c not in frame.columns:
            frame[c] = np.nan
    return frame


# --------------------------------------------------------------------------- 세션 하나


def build_session(store, market: str, session: date, as_of: datetime, *,  # type: ignore[no-untyped-def]
                  groups: Sequence[str] | None = None) -> pd.DataFrame:
    """그 세션의 FA 행(정규화 끝) — entity_id · session · market · FA 76열.

    ``as_of`` 는 그 세션 공표 시각이다(일일 실행기의 `last_published`, 백필은 `policy.for_session`).
    창고는 전부 `store.get(as_of=...)` 로 읽는다 — as_of 뒤에 관측된 행은 어떤 경로로도 안 들어온다(불변식 1).
    """
    if market not in RAW_ANALYSTS:
        raise ValueError(f"시장 {market!r} 은 FA 정의가 없다")
    groups = tuple(LIVE_GROUPS[market] if groups is None else groups)
    cached = store if isinstance(store, MemoStore) else MemoStore(store)

    scores = _scores(cached, market, session, as_of)
    entities = set(scores["entity_id"].astype(str)) | _priced(cached, market, session, as_of)
    if not entities:
        return pd.DataFrame(columns=["entity_id", "session", "market", *FA_FEATURES])
    panel = pd.DataFrame({"entity_id": sorted(entities)})
    panel = panel.merge(scores.drop(columns=["session"]), on="entity_id", how="left")
    panel["session"] = session
    panel["market"] = market
    for c in SCORE_COLUMNS:
        panel[c] = panel[c].astype(np.float32)

    for block in BLOCK_ORDER:
        if block == "score":
            continue
        if market not in BLOCK_MARKETS[block]:
            panel = attach(panel, None, block)
            continue
        if block == "raw":
            frame = _raw(cached, market, session, as_of, entities)
        elif block == "ba":
            frame = _ba(cached, session, as_of, entities)
        elif block in groups:
            frame = _group(cached, block, market, session)
        else:
            frame = None
        panel = attach(panel, frame, block)
    return finalize(panel)


# --------------------------------------------------------------------------- 창고


def run_id(market: str, session: date) -> str:
    """결정론적 적재 ID — 같은 세션을 두 번 넣으면 창고가 거부한다."""
    return f"fa-features-{market}-{session:%Y%m%d}-{FEATURE_SET}"


def records(frame: pd.DataFrame, *, as_of: datetime, source: str = "fa_features") -> list[dict[str, object]]:
    """창고 행. valid_from = observed_at = as_of — 그 시각까지의 자료로 계산한 파생값(signals 와 같은 규약)."""
    out: list[dict[str, object]] = []
    values = frame[list(FA_FEATURES)].to_numpy(np.float64)
    for i, (entity, session, market) in enumerate(frame[["entity_id", "session", "market"]].itertuples(index=False)):
        row: dict[str, object] = {
            "entity_id": str(entity), "valid_from": as_of, "observed_at": as_of, "source": source,
            "market": str(market), "session": f"{session:%Y-%m-%d}", "feature_set": FEATURE_SET,
        }
        row.update(zip(FA_FEATURES, values[i].tolist(), strict=True))
        out.append(row)
    return out


def write_session(store, frame: pd.DataFrame, *, market: str, session: date, as_of: datetime) -> int:  # type: ignore[no-untyped-def]
    """한 세션을 적는다. 이미 있으면 0 — append-only 라 다시 쓰지 않는다."""
    ident = run_id(market, session)
    if store.ingest_run_recorded(TABLE, ident):
        return 0
    if frame.empty:
        return 0
    return int(store.append(TABLE, records(frame, as_of=as_of), ingest_run_id=ident))


def read_window(store, *, as_of: datetime, market: str, lookback_days: int) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """as_of 에 알 수 있었던 FA 행 — entity_id · session(date) · FA 76열(float32). 세션·종목마다 최신 관측 하나."""
    frame = store.get(TABLE, as_of=as_of, lookback=lookback_days, market=market)
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", *FA_FEATURES])
    frame = frame[frame["feature_set"].astype(str) == FEATURE_SET]
    frame = frame.sort_values("observed_at").groupby(["entity_id", "session"], as_index=False).tail(1)
    out = frame[["entity_id", "session", *FA_FEATURES]].copy()
    out["session"] = pd.to_datetime(out["session"]).dt.date
    out[list(FA_FEATURES)] = out[list(FA_FEATURES)].astype(np.float32)
    return out.reset_index(drop=True)


__all__ = [
    "BA_PANEL_START", "LIVE_GROUPS", "OPEN_UTC", "TABLE", "attach", "build_session", "finalize", "rank_gauss",
    "read_window", "records", "run_id", "score_counts", "session_of", "write_session",
]
