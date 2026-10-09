"""K200 동일가중 계산 지수 — 전날 스냅샷 종목 · 결측·과대 이동 제외 · 첫날 1.0."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from quant_rl_trading.accounting import relative


class _Fake:
    def __init__(self, members: pd.DataFrame) -> None:
        self.members = members

    def get(self, table, **kw):  # type: ignore[no-untyped-def]
        assert table == "index_members"
        return self.members


def _ts(day: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} 09:00", tz="Asia/Seoul").tz_convert("UTC")


def test_equal_weight_uses_previous_snapshot_and_drops_bad_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    # 스냅샷은 매번 전체 구성이다 — 1/2 에 A·B, 1/5 에 A·B·C(C 편입).
    members = pd.DataFrame({"entity_id": ["A", "B", "A", "B", "C"],
                            "valid_from": [_ts("2026-01-02")] * 2 + [_ts("2026-01-05")] * 3, "index_id": "KR:IDX:KOSPI200"})
    rows = []
    for e, closes in {"A": [100, 110, 121], "B": [100, 90, 300], "C": [100, 200, 200]}.items():
        for d, c in zip(["2026-01-02", "2026-01-05", "2026-01-06"], closes, strict=True):
            rows.append({"entity_id": e, "valid_from": _ts(d), "close": float(c)})
    monkeypatch.setattr("quant_rl_trading.store.prices.read_prices", lambda *a, **k: pd.DataFrame(rows))
    idx = relative.k200_equal_weight_index(_Fake(members), as_of=datetime(2026, 1, 7, tzinfo=UTC), lookback=10)  # type: ignore[arg-type]
    days = [d.isoformat() for d in idx.index]
    assert days == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert idx.iloc[0] == 1.0
    # 1/5: 전날(1/2) 스냅샷 A·B → (+10% − 10%)/2 = 0 (C 는 1/5 에 들어와 그날은 안 센다)
    assert idx.iloc[1] == pytest.approx(1.0)
    # 1/6: 1/5 스냅샷 A·B·C → A +10%, B +233%(과대 이동 → 제외), C 0% → 평균 +5%
    assert idx.iloc[2] == pytest.approx(1.05)
