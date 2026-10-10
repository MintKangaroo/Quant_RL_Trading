"""순차 검정 — 합성 자료. 효과 0 이면 매일 봐도 거의 '모름', 큰 효과면 '우세 확정', 구간은 표본이 늘수록 좁아진다."""
from __future__ import annotations

import numpy as np

from quant_rl_trading.modelops.sequential import confidence_sequence


def test_null_rarely_rejects_even_when_peeking_daily() -> None:
    rng = np.random.default_rng(0)
    false = 0
    for _rep in range(200):
        x = rng.normal(0, 0.01, 500)
        seen = any(confidence_sequence(x[:t], alpha=0.05, t_star=60).status != "모름" for t in range(10, 501, 5))
        false += seen
    assert false / 200 <= 0.07        # 매 5세션 엿봐도 5% 언저리


def test_real_effect_detected_and_width_shrinks() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(0.003, 0.01, 600)          # 일 IR 0.3(연 약 4.8) — 600세션이면 확정되어야 한다
    assert confidence_sequence(x, alpha=0.05, t_star=60).status == "우세 확정"
    w1 = confidence_sequence(x[:60], alpha=0.05, t_star=60)
    w2 = confidence_sequence(x[:600], alpha=0.05, t_star=60)
    assert (w2.upper - w2.lower) < (w1.upper - w1.lower)
    assert confidence_sequence(x[:5], alpha=0.05, t_star=60).status == "표본 부족"


def test_dashboard_pairs_have_no_nan_and_known_books() -> None:
    import json
    import math

    from quant_rl_trading.dashboard.services import alpha_ir as svc

    keys = {b["key"] for b in svc.BOOKS}
    assert all(t in keys and c in keys for t, c, _ in svc.PAIRS)
    seq = confidence_sequence(np.array([0.01, 0.02]), alpha=0.05, t_star=60)
    assert seq.status == "표본 부족" and math.isnan(seq.lower)
    vals = [v if v == v else None for v in seq.annual]
    json.loads(json.dumps({"x": vals}, allow_nan=False))
