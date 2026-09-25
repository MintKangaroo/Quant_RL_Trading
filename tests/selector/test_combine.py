"""합성 점수 — 결측 Analyst 처리 (selector.md §1, 시행 AT 2026-09-25)."""

from __future__ import annotations

import pandas as pd
import pytest

from quant_rl_trading.selector.combine import combined_scores

W = {"fundamental": 0.84, "ranker": 1.0}


def _signals() -> pd.DataFrame:
    rows = [
        # 두 점수가 다 있는 국내 종목 — 랭커 최고, fundamental 도 좋다
        ("US:DOM", "ranker", 0.69, 0.094), ("US:DOM", "fundamental", 0.30, 0.072),
        # fundamental 이 없는 외국 발행사 — 랭커만 조금 낮다
        ("US:FOR", "ranker", 0.60, 0.094),
    ]
    return pd.DataFrame(rows, columns=["entity_id", "analyst", "score", "confidence"])


def test_기본은_결측을_분모에서_뺀다() -> None:
    """flow_kr 규칙(의견 없음 ≠ 중립) — 그래서 랭커 하나뿐인 종목이 위로 간다(미장 24/24 의 기제)."""
    s = combined_scores(_signals(), W)
    assert s["US:FOR"] == pytest.approx(0.60)
    assert s.index[0] == "US:FOR"


def test_결측을_0_으로_두면_두_점수_종목이_앞선다() -> None:
    s = combined_scores(_signals(), W, missing_as_zero=("fundamental",))
    a, b = 0.84 * 0.072, 1.0 * 0.094
    assert s["US:FOR"] == pytest.approx(b * 0.60 / (a + b))
    assert s.index[0] == "US:DOM"


def test_그날_아무도_점수를_안_냈으면_채우지_않는다() -> None:
    """Analyst 가 통째로 죽은 날 0 으로 채우면 고장이 중립 의견으로 둔갑한다."""
    only_rank = _signals()[lambda f: f["analyst"] == "ranker"]
    s = combined_scores(only_rank, W, missing_as_zero=("fundamental",))
    assert s["US:FOR"] == pytest.approx(0.60)
