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


class _FakeStore:
    """`store.get(table, as_of, lookback, until, …)` 흉내 — until 이하 valid_from 만, as_of 이하 observed_at 만."""

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self.tables = tables

    def get(self, table, *, as_of, lookback, until, market, columns):  # type: ignore[no-untyped-def]
        f = self.tables[table]
        f = f[(f["valid_from"] <= until) & (f["observed_at"] <= as_of)]
        return f[columns].copy()


def test_n6_is_one_day_late_and_marks_missing() -> None:
    # 기준일 d 잔고는 d+1 13:00 KST 공표 → 세션 d+1 부터. 잔고 행이 없는 종목은 결측 + m_lend=1, 반환 없는 날은 0.
    ents = ["KR:A", "KR:B"]
    base = {"shares": pd.DataFrame(100.0, index=SESS, columns=ents), "close_adj": pd.DataFrame(1.0, index=SESS, columns=ents)}

    def at(d: date, kst_hour: int) -> datetime:
        return datetime(d.year, d.month, d.day, kst_hour - 9, tzinfo=UTC)

    lend = pd.DataFrame({"entity_id": ["KR:A", "KR:A"], "valid_from": [at(SESS[0], 9), at(SESS[1], 9)],
                         "observed_at": [at(SESS[1], 13), at(SESS[2], 13)], "lend_balance": [10.0, 30.0]})
    lock = pd.DataFrame({"entity_id": ["KR:B"], "valid_from": [at(SESS[0], 9)], "observed_at": [at(SESS[1], 13)],
                         "returned_shares": [5.0]})
    out = bn.n6(base, _FakeStore({"stock_lending": lend, "lockup_returns": lock}))
    assert np.isnan(out["x_lend_ratio"].loc[SESS[0], "KR:A"])
    assert out["x_lend_ratio"].loc[SESS[1], "KR:A"] == pytest.approx(0.10)
    assert out["x_lend_ratio"].loc[SESS[2], "KR:A"] == pytest.approx(0.30)
    assert list(out["m_lend"]["KR:B"]) == [1.0] * 4
    assert np.isnan(out["x_lockup20"].loc[SESS[0], "KR:B"])      # 첫 세션은 전날 주식수가 없다
    assert out["x_lockup20"].loc[SESS[1], "KR:B"] == pytest.approx(0.05)
    assert "N6" in bn.SIGNS and bn.BUILDERS["N6"][0] == "store"


def test_arrival_handles_microsecond_unit() -> None:
    # 창고는 datetime64[us] 로 돌려준다 — 단위를 안 맞추면 µs 정수가 ns 결정 시각보다 작아 전부 세션 0 이 됐다(N6 첫 굽기).
    obs = pd.Series(pd.to_datetime([datetime(2024, 1, 3, 4, tzinfo=UTC), datetime(2024, 1, 4, 4, tzinfo=UTC)])).astype("datetime64[us, UTC]")
    assert list(bn.arrival_index(obs, SESS)) == [1, 2]


def _n7_events(n_te: int, n_te_text: int, n_te_parsed: int) -> pd.DataFrame:
    t = pd.Timestamp("2024-01-02 09:00", tz="UTC")
    rows = [{"entity_id": "KR:A", "kind": "bb_trust", "observed_at": t, "has_text": True, "parsed": True, "amount": 50.0, "ratio": np.nan}]
    for i in range(n_te):
        rows.append({"entity_id": "KR:A" if i == 0 else "KR:B", "kind": "bb_trust_end", "observed_at": t + pd.Timedelta(days=1),
                     "has_text": i < n_te_text, "parsed": i < n_te_parsed, "amount": 40.0 if i < n_te_parsed else np.nan,
                     "ratio": 80.0 if i < n_te_parsed else np.nan})
    return pd.DataFrame(rows)


def _n7_base() -> dict[str, pd.DataFrame]:
    ents = ["KR:A", "KR:B"]
    return {"cap": pd.DataFrame(1000.0, index=SESS, columns=ents), "close_adj": pd.DataFrame(1.0, index=SESS, columns=ents)}


def test_n7_refuses_without_trust_end_texts() -> None:
    # 원문 없는 신탁 해지결과를 '이행 0' 으로 읽으면 안 된다 — 80% 미만이면 굽기를 거부한다.
    with pytest.raises(SystemExit):
        bn.n7(_n7_base(), _n7_events(n_te=5, n_te_text=0, n_te_parsed=0))


def test_n7_marks_unparsed_trust_end_as_unknown() -> None:
    # 원문 80% 이상이어도 파싱 못 한 해지결과가 온 종목은 금액 열이 결측(모름) + m_trust_gap=1, 0 으로 읽지 않는다.
    out = bn.n7(_n7_base(), _n7_events(n_te=5, n_te_text=5, n_te_parsed=1))
    assert out["pred"].loc[SESS[1], "KR:A"] > 0 and out["m_trust_gap"].loc[SESS[1], "KR:A"] == 0
    assert np.isnan(out["x_unfilled_h60"].loc[SESS[1], "KR:B"]) and out["m_trust_gap"].loc[SESS[1], "KR:B"] == 1
    assert np.isnan(out["pred"].loc[SESS[1], "KR:B"])


def test_backfill_takes_trust_end_first() -> None:
    from tools import backfill_n_texts as bt

    assert bt.TARGETS[0][0] == "bb_trust_end"
