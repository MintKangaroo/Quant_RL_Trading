"""v2 U2 군집 — 시장 요인을 빼고 숨은 업종 묶음을 되찾는가."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools.v2_clusters import cluster_month


def test_시장_요인이_세도_업종_묶음을_되찾는다() -> None:
    rng = np.random.default_rng(0)
    t, groups, per = 60, 4, 10
    market = rng.normal(0, 0.02, t)                       # 모두를 끄는 강한 시장 요인
    cols, data = [], []
    for g in range(groups):
        sector = rng.normal(0, 0.01, t)
        for i in range(per):
            cols.append(f"G{g}_{i}")
            data.append(market + sector + rng.normal(0, 0.004, t))
    r = pd.DataFrame(np.array(data).T, columns=cols)
    c = cluster_month(r, groups)
    truth = pd.Series([int(n[1]) for n in cols], index=cols)
    # 같은 업종은 같은 군집으로, 군집 수는 업종 수와 같다
    assert c.groupby(truth).nunique().max() == 1
    assert c.nunique() == groups
