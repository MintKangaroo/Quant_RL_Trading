"""시행 CR(V6 crisis·bear 배수) — 판정 도구와 금고 배선. 합성 자료만(창고·금고 창을 읽지 않는다).

지키는 것:
 ① 봉인 — 2026-10-01 이후 날짜는 as_of 로도 표로도 못 쓴다, 결정 세션은 t+2 종가가 9/30 안.
 ② 대조 E1 = 실전 설정(config yaml)의 V6 배수, 변형은 넷 이하이고 배수 표만 다르다 — 배수는 실전 `regime_scale`(확인 2세션) 그대로.
 ③ 미고정이면 --run 이 자료를 읽기 전에 거부, 금고 second 창에도 안 붙는다(고정 대기로만 보인다).
 ④ 주 시험 기준 A1~A6 · 금고 확인 V1·V2 가 각자 기각을 낼 수 있고, 지표가 비면 멈춘다.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from quant_rl_trading.selector.exposure import ExposureParams
from tools import trial_cr_exposure as cr
from tools import vault_judge as vj

BASE = ExposureParams(below_trend=1.0, regime_scale=dict(cr.V6), squeezed=1.0, squeeze_quantile=0.2,
                      regime_confirm_sessions=2, deadband=0.10, deadband_asymmetric=False)


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


# --------------------------------------------------------------------------- ① 봉인


def test_seal_constants_and_refusals() -> None:
    assert date(2026, 9, 30) == cr.LAST_CLOSE and date(2026, 10, 1) == cr.SEALED_FROM
    assert cr.sealed_as_of().date() == date(2026, 9, 30)
    with pytest.raises(SystemExit):
        cr.sealed_as_of(date(2026, 10, 1))
    with pytest.raises(SystemExit):
        cr.guard("x", [date(2026, 9, 30), date(2026, 10, 2)])
    cr.guard("x", [date(2026, 9, 30)])


def test_usable_days_needs_t_plus_2_inside_cap() -> None:
    cal = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
    assert cr.usable_days(cal, cal) == [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28)]


# --------------------------------------------------------------------------- ② 변형·배수


def test_control_is_live_v6_and_variants_differ_only_in_scale_table() -> None:
    conf = yaml.safe_load(Path("config/quant_rl_trading.yaml").read_text())["exposure"]
    assert {k: float(v) for k, v in conf["regime_scale"].items()} == cr.VARIANTS[cr.CONTROL]
    assert conf["regime_confirm_sessions"] == 2
    assert len(cr.VARIANTS) <= 4 and set(cr.TREATMENTS) == set(cr.VARIANTS) - {cr.CONTROL}
    assert cr.VARIANTS["CR1"] == {**cr.V6, "crisis": 1.0}
    assert set(cr.VARIANTS["CR2"].values()) == {1.0}
    assert cr.VARIANTS["CR3"] == {**cr.V6, "bear": 1.0}
    p = cr.variant_params(BASE, "CR1")
    assert p.regime_confirm_sessions == BASE.regime_confirm_sessions and p.regime_scale["crisis"] == 1.0


def test_scale_path_uses_confirm_two_min() -> None:
    days = pd.date_range("2025-01-01", periods=5).date
    states = pd.Series(["bull", "crisis", "bull", "bull", "bear"], index=days, dtype=object)
    e1 = cr.scale_path(states, cr.variant_params(BASE, "E1")).tolist()
    assert e1 == [1.0, 0.5, 0.5, 1.0, 0.7]          # 낮추기는 즉시, 올리기는 2세션 확인
    assert cr.scale_path(states, cr.variant_params(BASE, "CR1")).tolist() == [1.0, 1.0, 1.0, 1.0, 0.7]
    assert cr.scale_path(states, cr.variant_params(BASE, "CR2")).tolist() == [1.0] * 5
    assert cr.scale_path(states, cr.variant_params(BASE, "CR3")).tolist() == [1.0, 0.5, 0.5, 1.0, 1.0]


def test_regime_states_use_prior_close_only() -> None:
    days = pd.bdate_range("2024-01-01", periods=300).date
    closes = pd.Series(np.linspace(100, 130, 300), index=days)
    st = cr.regime_states(closes, list(days[-3:]), -0.03)
    assert set(st) <= {"bull", "bear", "volatile", "crisis", "unknown"}
    shocked = closes.copy()
    shocked.iloc[-1] = 1.0                            # 그날 종가는 그날 국면에 안 들어간다
    assert cr.regime_states(shocked, [days[-1]], -0.03).iloc[0] == cr.regime_states(closes, [days[-1]], -0.03).iloc[0]


def _market(n: int = 60, names: int = 40, seed: int = 0):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    days = list(pd.bdate_range("2025-01-01", periods=n).date)
    ent = [f"E{i}" for i in range(names)]
    ret = pd.DataFrame(rng.normal(0.0005, 0.02, (n, names)), index=days, columns=ent)
    pred = pd.DataFrame([(e, d, float(rng.normal())) for d in days for e in ent], columns=["entity_id", "session", "pred"])
    trad = {d: set(ent) for d in days}
    return days, ent, ret, pred, trad


def test_targets_rebalance_every_ten_and_same_targets_across_variants() -> None:
    days, _, ret, pred, trad = _market()
    tgt, rebal = cr.targets_from_scores(pred, ret, trad, days, cr.equal_weigh)
    assert rebal == [days[i] for i in range(0, len(days), 10)]
    assert (tgt.gt(0).sum(axis=1) == cr.N_TOP).all()
    paths = {"E1": pd.Series(1.0, index=days), "CR2": pd.Series(1.0, index=days)}
    out = cr.books(tgt, ret, ret.mean(axis=1), paths)
    assert np.allclose(out["E1"].to_numpy(), out["CR2"].to_numpy())
    half = cr.books(tgt, ret, ret.mean(axis=1), {"k": pd.Series(0.5, index=days)})["k"]
    assert not np.allclose(half.to_numpy(), out["E1"].to_numpy())


# --------------------------------------------------------------------------- ③ 미고정 거부·금고 배선


def test_run_refused_while_unpinned(capsys) -> None:  # type: ignore[no-untyped-def]
    if cr.PROTOCOL_HASH is not None:
        pytest.skip("이미 고정됐다")
    assert cr.main(["--run", "--data", "/nonexistent-never-opened"]) == 2
    assert "초안" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cr.main(["--plan", "--save"])
    assert cr.main(["--plan"]) == 0


def test_cr_pending_on_second_window_until_pinned() -> None:
    second = vj.windows()["second"]
    assert vj.protocol_path("CR") == vj.CR_PROTOCOL and vj.CR_PROTOCOL.exists()
    assert vj.ENTITY["CR"] == cr.ENTITY and "CR" in vj.RUNNERS and "CR" in vj.PREREQ
    if vj.CR_PROTOCOL_HASH is None:
        assert "CR" not in vj.trials_of(second) and "CR" in vj.pending_trials(second)
        assert vj.registration_problems(second) == []
    assert vj.CR_PROTOCOL_HASH == cr.PROTOCOL_HASH      # 두 자리를 같이 고정한다


def test_vault_prereq_skips_without_passing_main(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert "없다" in str(cr.vault_prereq(tmp_path, "abc"))
    out = tmp_path / "_diag/cr-exposure"
    out.mkdir(parents=True)
    (out / "main.json").write_text('{"protocol_hash": "abc", "verdict": "기각 — 주 시험"}')
    assert "통과가 아니다" in str(cr.vault_prereq(tmp_path, "abc"))
    assert "해시" in str(cr.vault_prereq(tmp_path, "zzz"))
    (out / "main.json").write_text('{"protocol_hash": "abc", "verdict": "주 시험 통과 CR1 — 금고"}')
    assert cr.vault_prereq(tmp_path, "abc") is None


# --------------------------------------------------------------------------- ④ 판정


def _daily(effect: dict[str, float], *, n: int = 1159, noise: float = 0.002, bear_hit: float = 0.0):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(1)
    days = list(pd.bdate_range("2021-12-29", periods=n).date)
    base = pd.Series(rng.normal(0.0004, 0.012, n), index=days)
    out = {}
    for arm in cr.VARIANTS:
        out[arm] = {}
        for s in cr.SEEDS:
            extra = effect.get(arm, 0.0) / cr.ANN + rng.normal(0, noise, n) * (arm != cr.CONTROL)
            r = base + extra + rng.normal(0, 1e-4, n)
            if arm != cr.CONTROL and bear_hit:
                r[(r.index >= date(2022, 2, 1)) & (r.index <= date(2022, 3, 1))] -= bear_hit
            out[arm][s] = r
    spans = {d: (d + timedelta(days=1), d + timedelta(days=2)) for d in days}
    return out, spans


def test_judge_main_passes_large_effect_and_rejects_null() -> None:
    big, spans = _daily({"CR1": 0.30, "CR2": 0.0, "CR3": 0.0}, noise=0.001)
    _, verdict, stats = cr.judge_main(big, spans)
    assert verdict.startswith("주 시험 통과 CR1") and all(stats["CR1"]["gates"].values())
    null, spans = _daily({}, noise=0.002)
    _, verdict, stats = cr.judge_main(null, spans)
    assert verdict.startswith("기각")


def test_bear_loss_cap_rejects_alone() -> None:
    d, spans = _daily({"CR1": 0.30}, noise=0.001, bear_hit=0.02)
    _, verdict, stats = cr.judge_main(d, spans)
    assert not stats["CR1"]["gates"]["A5"] and stats["CR1"]["gates"]["A1"]
    assert not verdict.startswith("주 시험 통과 CR1")


def test_judge_main_stops_on_missing() -> None:
    d, spans = _daily({})
    d["CR2"][0] = d["CR2"][0].iloc[:-5]
    with pytest.raises(ValueError):
        cr.judge_main(d, spans)


def test_vault_margins_and_judge() -> None:
    d, _ = _daily({"CR1": 0.05})
    ctrl = pd.DataFrame(d["E1"]).mean(axis=1)
    m = cr.block_margins(pd.DataFrame(d["CR1"]).mean(axis=1), ctrl)
    assert m["gate_ann"] < 0 and m["gate_mdd"] < 0 and m["blocks"] > 10
    win = {s: r.iloc[:30] for s, r in d["E1"].items()}
    _, ok = cr.judge_vault({s: r + 0.0001 for s, r in win.items()}, win, m, "CR1")
    assert ok.startswith("확인 —")
    _, bad = cr.judge_vault({s: r - 0.02 for s, r in win.items()}, win, m, "CR1")
    assert bad.startswith("기각")
    with pytest.raises(ValueError):
        cr.judge_vault({}, win, m, "CR1")


def test_gates_written_in_protocol() -> None:
    body = cr.PROTOCOL.read_text()
    for needle in ("t ≥ 3", "5/5", "4/5", "−2%p", "2026-09-30", "초안 — 미고정"):
        assert needle in body, needle
