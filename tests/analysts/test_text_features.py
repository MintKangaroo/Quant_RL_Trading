"""시행 X — 공시 임베딩 피처와 PCA 적합.

무효가 되는 길 둘: 판정 창에서 PCA 를 적합하는 것, 공시 없는 종목을 0(중앙)으로 채우는 것.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.ranker_sources import GROUPS, filing_embedding
from tools.build_text_features import COMPONENTS, fit_pca, load_pca


class FakeStore:
    def __init__(self, rows: pd.DataFrame) -> None:
        self.rows = rows

    def get(self, table: str, **_: object) -> pd.DataFrame:
        assert table == "document_embeddings"
        return self.rows


class FakeAnalyst:
    market = "KR"
    wide = staticmethod(Analyst.wide)

    def __init__(self, rows: pd.DataFrame, sessions: list[date]) -> None:
        self.store = FakeStore(rows)
        self.sessions = sessions

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        return pd.DataFrame([
            {"entity_id": e, "session": s, "close": 10.0, "volume": 100.0, "value": 1000.0}
            for s in self.sessions for e in ("KR:A", "KR:B", "KR:C")
        ])


def test_창_안_공시만_평균하고_없는_종목은_결측이다() -> None:
    sessions = list(pd.bdate_range("2025-01-02", periods=70).date)
    def moment(day: date) -> datetime:
        return datetime.combine(day, datetime.min.time(), tzinfo=UTC)

    rows = pd.DataFrame([
        {"entity_id": "KR:A", "valid_from": moment(sessions[-3]), "pc1": 1.0, "pc2": 0.0, "pc3": -1.0},
        {"entity_id": "KR:A", "valid_from": moment(sessions[-2]), "pc1": 3.0, "pc2": 2.0, "pc3": 1.0},
        # 60세션 창 밖 — 평균에 들어가면 안 된다.
        {"entity_id": "KR:B", "valid_from": moment(sessions[0]), "pc1": 9.0, "pc2": 9.0, "pc3": 9.0},
    ])
    raw = filing_embedding(FakeAnalyst(rows, sessions), datetime(2025, 4, 30, tzinfo=UTC))  # type: ignore[arg-type]
    assert raw.loc["KR:A", "text_pc1"] == 2.0 and raw.loc["KR:A", "text_pc3"] == 0.0
    # 공시가 없는 종목(B·C)은 행이 없다 — 0 으로 채우면 "공시 없음" 이 "주성분 0" 으로 둔갑한다.
    assert "KR:B" not in raw.index and "KR:C" not in raw.index
    assert list(raw.columns) == list(GROUPS["X"])


def test_PCA_는_표본의_주성분을_그대로_낸다(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(size=(200, 2)) @ np.array([[1.0, 0.0, 0.0, 0.5], [0.0, 1.0, 0.0, -0.5]])
    sample = tmp_path / "sample.npz"
    np.savez_compressed(sample, vectors=base + 5.0)
    fit_pca(sample, tmp_path / "pca.npz")
    mean, components = load_pca(tmp_path / "pca.npz")
    assert components.shape == (COMPONENTS, 4)
    np.testing.assert_allclose(mean, (base + 5.0).mean(axis=0))
    # 2차원 부분공간이므로 셋째 주성분은 거의 아무것도 설명하지 못한다.
    scores = (base + 5.0 - mean) @ components.T
    assert scores[:, 2].std() < 1e-6 < scores[:, 0].std()
