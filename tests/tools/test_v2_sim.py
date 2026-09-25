"""v2 S1 시뮬레이터 — 실전 보유일 규칙과 같은가."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tools.v2_sim import fixed, simulate

DAYS = pd.date_range("2025-01-01", periods=30).date
NAMES = ["A", "B", "C"]


def _data(seed: int = 0):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    targets = pd.DataFrame(1 / 3, index=DAYS, columns=NAMES)
    ret = pd.DataFrame(rng.normal(0, 0.01, (30, 3)), index=DAYS, columns=NAMES)
    bench = ret.mean(axis=1)
    return targets, ret, bench


def test_노출_1_매일_재조정은_동일가중과_같다() -> None:
    t, r, b = _data()
    res = simulate(t, r, b, 0.0, fixed(every=1))
    assert res.daily.to_numpy() == pytest.approx(r.mean(axis=1).to_numpy())


def test_보유일엔_주문이_없다() -> None:
    t, r, b = _data()
    res = simulate(t, r, b, 0.01, fixed(every=10))
    # 비용 0 인 날 = 보유일. 재조정일(0·10·20)에만 비용이 붙는다.
    gross = simulate(t, r, b, 0.0, fixed(every=10)).daily
    paid = (gross - res.daily).abs() > 1e-12
    assert list(np.flatnonzero(paid.to_numpy())) == [0, 10, 20]


def test_노출을_줄이면_줄인_몫은_현금이다() -> None:
    t, r, b = _data()
    half = simulate(t, r, b, 0.0, lambda s: (0.5, True))
    assert half.daily.to_numpy() == pytest.approx(0.5 * r.mean(axis=1).to_numpy())
    assert (half.exposure == 0.5).all()


def test_노출_하한을_지킨다() -> None:
    t, r, b = _data()
    res = simulate(t, r, b, 0.0, lambda s: (0.0, True), k_min=0.3)
    assert (res.exposure == 0.3).all()
