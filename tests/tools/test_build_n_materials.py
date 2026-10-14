"""N 재료 굽기 — 시각(결정 시각 이후 관측은 다음 세션)·감쇠·정정 차이·재료 파일 읽기."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from tools import build_n_materials as bn
from tools import eval_ladder as el

SESS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_arrival_respects_decision_cutoff() -> None:
    # 결정 시각 = 세션 16:00 UTC(= 다음날 01:00 KST). 18:00 KST 관측은 그 세션에, 16:00 UTC 1초 뒤는 다음 세션에.
    obs = pd.Series([datetime(2024, 1, 2, 9, 0, tzinfo=UTC), datetime(2024, 1, 2, 16, 0, 1, tzinfo=UTC),
                     datetime(2024, 1, 9, tzinfo=UTC)])
    assert list(bn.arrival_index(obs, SESS)) == [0, 1, 4]


def test_event_wide_and_decay() -> None:
    ev = pd.DataFrame({"entity_id": ["KR:A", "KR:A", "KR:B"], "v": [1.0, 2.0, np.nan],
                       "observed_at": [datetime(2024, 1, 2, 9, tzinfo=UTC)] * 3})
    w = bn.event_wide(ev, "v", SESS, pd.Index(["KR:A", "KR:B"]))
    assert w.loc[SESS[0], "KR:A"] == 3.0 and w["KR:B"].sum() == 0.0
    d = bn._decay(w, half_life=1.0)
    assert d.loc[SESS[1], "KR:A"] == pytest.approx(1.5)


def test_last_state_expires() -> None:
    ev = pd.DataFrame({"entity_id": ["KR:A"], "v": [5.0], "observed_at": [datetime(2024, 1, 2, 9, tzinfo=UTC)]})
    val, age = bn._last_state(ev, "v", SESS, pd.Index(["KR:A"]), max_age=1)
    assert list(val["KR:A"].fillna(-1)) == [5.0, 5.0, -1, -1] and age.iloc[1, 0] == 1


def test_amend_delta_keeps_sum_right() -> None:
    t = pd.Timestamp("2024-01-02 09:00", tz="UTC")
    ev = pd.DataFrame({"entity_id": ["KR:A", "KR:A"], "kind": ["cb", "cb"], "doc_id": ["1", "2"],
                       "observed_at": [t, t + pd.Timedelta(days=3)], "amended": [False, True], "parsed": [True, True],
                       "amount": [100.0, 120.0], "shares": [10.0, 10.0], "ratio": [5.0, 6.0]})
    out = bn.amend_deltas(ev)
    assert list(out["amount"]) == [100.0, 20.0] and list(out["shares"]) == [10.0, 0.0]


def test_feature_frame_keeps_extra_columns(tmp_path) -> None:
    f = pd.DataFrame({"entity_id": ["KR:A"], "session": [date(2024, 1, 2)], "pred": [1.0], "x_a": [2.0], "m_a": [0.0],
                      "junk": [9], "seed": [0]})
    p = tmp_path / "N9.parquet"
    f.to_parquet(p, index=False)
    out = el.read_feature_frame(p)
    assert list(out.columns) == ["entity_id", "session", "pred", "x_a", "m_a"]


def test_feature_frame_rejects_sealed(tmp_path) -> None:
    f = pd.DataFrame({"entity_id": ["KR:A"], "session": [date(2026, 10, 2)], "pred": [1.0], "seed": [0]})
    p = tmp_path / "N9.parquet"
    f.to_parquet(p, index=False)
    with pytest.raises(SystemExit):
        el.read_feature_frame(p)


def test_sparse_gauss_keeps_zero_mass_at_zero() -> None:
    v = pd.Series([0.0, 0.0, 0.0, 2.0, 5.0, -1.0, 0.0, 0.0, 1.0, np.nan])
    s = pd.Series([1, 1, 1, 1, 1, 1, 2, 2, 2, 2])
    out = el.sparse_gauss(v, s)
    assert list(out[[0, 1, 2, 6, 7, 9]]) == [0.0] * 6
    assert out[4] > out[3] > 0 > out[5] and out[8] > 0


def test_decile_sparse_uses_three_groups() -> None:
    days = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    ents = [f"KR:{i:03d}" for i in range(10)]
    close = pd.DataFrame(100.0, index=days, columns=ents)
    close.loc[days[2], ents[:2]] = 110.0              # 앞 두 종목만 t+1→t+2 에 오른다
    ret = close.shift(-2) / close.shift(-1) - 1.0
    ctx = el.Context(close=close, ret=ret, bench=None, k200_close=None, trad={d: set(ents) for d in days},  # type: ignore[arg-type]
                     uni=None, scale=None)  # type: ignore[arg-type]
    pred = pd.DataFrame({"entity_id": ents, "session": days[0], "pred": [1.0, 2.0] + [0.0] * 8})
    d = el.decile_frame(pred, ctx)
    assert d.loc[days[0], "d10"] == pytest.approx(0.1) and d.loc[days[0], "d1"] == pytest.approx(0.0)
