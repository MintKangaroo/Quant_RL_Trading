"""자기개선 ① 채점지 — 매 결정 세션, Analyst 마다 '그 신호가 실제로 맞았나'(self-improvement.md §4, 2026-10-09).

Phil(자기 채점하는 트레이더)의 첫 단계에 해당한다. 다른 점: 채점만 하고 고치지 않는다. 고치는 일(② 회고 → ③ 진단 →
④ 등록·shadow)은 통계 관문과 사람 승인을 거친다 — 주식은 잡음이 커서 '틀릴 때마다 고치기'가 과적합이다.

규약
- 결정 세션 d 의 신호 = 그 세션이 실제로 본 것: ``signals`` 를 as_of = d 16:00 KST 로(실전 세션과 같은 시점, lookback 5일),
  같은 종목은 가장 늦은 관측(`constraints.constraint_scores` 와 같은 규칙).
- 라벨 = 보정 종가 d+1 → d+1+horizon (다음 세션에 사서 horizon 세션 보유). 이상치 |r| > 50% 는 뺀다(시행 도구와 같은 MAX_MOVE).
- 집합: all · k200(as_of 시점 KOSPI200 구성) · rest. 10분위는 ⌊n/10⌋ 개(최소 5).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from quant_rl_trading.selector.constraints import constraint_scores

KST = ZoneInfo("Asia/Seoul")
MAX_MOVE = 0.5
MIN_N = 50


def decision_as_of(day: date) -> datetime:
    return datetime.combine(day, time(16, 0), tzinfo=KST)


@dataclass(frozen=True)
class Card:
    analyst: str
    universe: str
    n: int
    ic: float
    top_excess: float
    bottom_excess: float
    top_hit: float
    bottom_hit: float


def forward_labels(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """행 d = close[d+1+h]/close[d+1] − 1. 마지막 h+1 행은 라벨이 안 닫혀 NaN."""
    fwd = close.shift(-(horizon + 1)) / close.shift(-1) - 1.0
    return fwd.where(fwd.abs() <= MAX_MOVE)


def score_one(score: pd.Series, label: pd.Series) -> tuple[int, float, float, float, float, float] | None:
    both = pd.concat([score.rename("s"), label.rename("y")], axis=1).dropna()
    n = len(both)
    if n < MIN_N:
        return None
    k = max(n // 10, 5)
    mean = both["y"].mean()
    top = both.nlargest(k, "s")["y"]
    bot = both.nsmallest(k, "s")["y"]
    ic = float(both["s"].rank().corr(both["y"].rank()))
    return n, ic, float(top.mean() - mean), float(bot.mean() - mean), float((top > mean).mean()), float((bot < mean).mean())


def score_session(signals: pd.DataFrame, label: pd.Series, k200: set[str], analysts: Iterable[str] | None = None) -> list[Card]:
    names = sorted(set(signals["analyst"].astype(str))) if analysts is None else list(analysts)
    out = []
    for a in names:
        s = constraint_scores(signals, a)
        if s.empty:
            continue
        for uni, idx in (("all", s.index), ("k200", s.index[s.index.isin(k200)]), ("rest", s.index[~s.index.isin(k200)])):
            r = score_one(s.reindex(idx), label)
            if r is not None:
                out.append(Card(a, uni, *r))
    return out


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    """Analyst × 집합별 평균 — 회고(②)가 읽는 요약. NW 가 아니라 단순 평균·세션 수만(판정 도구가 아니다)."""
    if frame.empty:
        return frame
    g = frame.groupby(["entity_id", "universe"])
    return pd.DataFrame({
        "sessions": g["session"].nunique(),
        "ic": g["ic"].mean(),
        "top_excess": g["top_excess"].mean(),
        "bottom_excess": g["bottom_excess"].mean(),
        "top_hit": g["top_hit"].mean(),
        "bottom_hit": g["bottom_hit"].mean(),
    }).round(4)


def as_records(cards: list[Card], *, day: date, horizon: int, market: str, versions: dict[str, str]) -> list[dict[str, object]]:
    vf = decision_as_of(day)
    return [{
        "entity_id": c.analyst, "valid_from": vf, "market": market, "session": day.isoformat(), "horizon": horizon,
        "universe": c.universe, "n": c.n, "ic": c.ic, "top_excess": c.top_excess, "bottom_excess": c.bottom_excess,
        "top_hit": c.top_hit, "bottom_hit": c.bottom_hit, "signal_version": versions.get(c.analyst, ""),
    } for c in cards]


def is_finite(cards: list[Card]) -> bool:
    return all(np.isfinite([c.ic, c.top_excess, c.bottom_excess]).all() for c in cards)
