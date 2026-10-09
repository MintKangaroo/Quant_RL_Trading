"""① 채점지 — 합성 자료만. 라벨 정렬(다음 세션 진입) · 10분위 초과·적중 부호 · 집합 분리 · 최소 표본."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from quant_rl_trading.modelops import scorecard as sc


def _signals(scores: dict[str, float], analyst: str = "x") -> pd.DataFrame:
    return pd.DataFrame({"entity_id": list(scores), "analyst": analyst, "score": list(scores.values()),
                         "observed_at": pd.Timestamp("2026-10-15", tz="UTC")})


def test_label_enters_next_session() -> None:
    days = [date(2026, 10, 15) + timedelta(days=i) for i in range(8)]
    close = pd.DataFrame({"a": [100, 110, 110, 110, 110, 110, 121, 121]}, index=days, dtype=float)
    lab = sc.forward_labels(close, 5)
    assert abs(lab.loc[days[0], "a"] - 0.10) < 1e-12          # 110(d+1) → 121(d+6)
    assert lab.loc[days[2:], "a"].isna().all()                  # 라벨이 안 닫힌 세션


def test_perfect_signal_scores_positive() -> None:
    names = [f"KR:{i:03d}" for i in range(100)]
    label = pd.Series(np.linspace(-0.05, 0.05, 100), index=names)
    sig = _signals({n: float(i) for i, n in enumerate(names)})
    cards = sc.score_session(sig, label, k200=set(names[:60]))
    allc = next(c for c in cards if c.universe == "all")
    assert allc.n == 100 and allc.ic > 0.99
    assert allc.top_excess > 0 and allc.bottom_excess < 0 and allc.top_hit == 1.0 and allc.bottom_hit == 1.0
    assert {c.universe for c in cards} == {"all", "k200"}        # rest 는 40종목 < 최소 50
    assert sc.is_finite(cards)


def test_records_shape() -> None:
    names = [f"KR:{i:03d}" for i in range(60)]
    cards = sc.score_session(_signals({n: float(i) for i, n in enumerate(names)}), pd.Series(0.01, index=names) + np.arange(60) * 1e-4, set())
    recs = sc.as_records(cards, day=date(2026, 10, 15), horizon=5, market="KR", versions={"x": "v1"})
    assert recs[0]["entity_id"] == "x" and recs[0]["session"] == "2026-10-15" and recs[0]["signal_version"] == "v1"
    assert recs[0]["valid_from"].hour == 16
