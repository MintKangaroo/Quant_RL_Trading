"""시행 WL 러너 — 합성 자료만. 목표 라벨 · 패자 제외 포트 · 해시 잠금."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import trial_wl_heads as wl


def test_labels_are_tails_per_session() -> None:
    days = [date(2026, 1, 1), date(2026, 1, 2)]
    panel = pd.DataFrame({"session": np.repeat(days, 20), "y5": np.tile(np.arange(20, dtype=float), 2)})
    w, l = wl.label(panel, "W"), wl.label(panel, "L")
    assert w.sum() == 4 and l.sum() == 4                      # 세션마다 20 × 10% = 2
    assert set(panel.loc[w == 1, "y5"]) == {18.0, 19.0} and set(panel.loc[l == 1, "y5"]) == {0.0, 1.0}
    assert wl.label(panel, "C1").equals(panel["y5"])
    panel.loc[0, "y5"] = np.nan
    assert np.isnan(wl.label(panel, "W").iloc[0])           # 결측은 0 이 아니라 결측


def test_exclusion_book_drops_worst_by_direction() -> None:
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    names = [f"KR:{i:06d}" for i in range(20)]
    pred = pd.DataFrame([(e, d, float(i)) for d in days for i, e in enumerate(names)], columns=["entity_id", "session", "pred"])
    ret = pd.DataFrame(0.0, index=days, columns=names)
    ret.loc[:, names[:2]] = -0.5                               # 점수 최하 둘이 망한다
    trad = {d: set(names) for d in days}
    low, _ = wl.excl_book(pred, days, ret, trad, worst_is_high=False)
    high, _ = wl.excl_book(pred, days, ret, trad, worst_is_high=True)
    assert low.iloc[0] > high.iloc[0]                          # 낮은 점수를 뺀 쪽이 패자를 피했다


def test_gates_and_hash() -> None:
    assert (wl.W_GATE_ANN, wl.W_GATE_T, wl.W_GATE_SHARE, wl.W_GATE_REGIME, wl.W_GATE_MDD, wl.W_GATE_TURN) == (
        0.01, 2.0, 4, -0.01, 0.02, 1.2)
    assert (wl.L_GATE_ANN, wl.L_GATE_T, wl.L_GATE_SHARE, wl.L_GATE_REGIME) == (0.005, 2.0, 4, -0.005)
    assert wl.PROTOCOL_HASH == hashlib.sha256(wl.PROTOCOL.read_bytes()).hexdigest()[:16]


def test_run_refuses_on_hash_mismatch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(wl, "PROTOCOL_HASH", "0" * 16)
    assert wl.cmd_run(object(), save=False) == 2  # type: ignore[arg-type]
    assert "판정 거부" in capsys.readouterr().out
