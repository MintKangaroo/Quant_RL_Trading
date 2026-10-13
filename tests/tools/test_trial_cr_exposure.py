"""시행 CR(V6 crisis·bear 배수) — 판정 도구와 금고 배선. 합성 자료만(창고·금고 창을 읽지 않는다).

지키는 것:
 ① 봉인 — 2026-10-01 이후 날짜는 as_of 로도 표로도 못 쓴다, 결정 세션은 t+2 종가가 9/30 안.
 ② 대조 E1 = 실전 설정(config yaml)의 V6 배수, 변형은 넷 이하이고 배수 표만 다르다 — 배수는 실전 `regime_scale`(확인 2세션) 그대로.
 ③ 미고정이면 --run 이 자료를 읽기 전에 거부, 금고 second 창에도 안 붙는다(고정 대기로만 보인다).
 ④ 주 시험(지수) I1~I4 · 해 방지(실제 포트) H1~H3 · 금고 확인 V1·V2 가 각자 기각을 낼 수 있고, 지표가 비면 멈춘다.
 ⑤ 위약용 벡터 배수 = 실전 `regime_scale` 경로와 같은 값.
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


def test_harm_passes_clean_and_flags_bear_loss() -> None:
    clean, spans = _daily({"CR1": 0.05, "CR2": 0.0, "CR3": 0.0}, noise=0.001)
    _, out = cr.judge_harm(clean, spans)
    assert all(out["CR1"]["gates"].values())
    hit, spans = _daily({"CR1": 0.30}, noise=0.001, bear_hit=0.02)
    _, out = cr.judge_harm(hit, spans)
    assert not out["CR1"]["gates"]["H1"]
    sunk, spans = _daily({"CR1": -0.60}, noise=0.001)
    _, out = cr.judge_harm(sunk, spans)
    assert not out["CR1"]["gates"]["H3"]


def test_judge_harm_stops_on_missing() -> None:
    d, spans = _daily({})
    d["CR2"][0] = d["CR2"][0].iloc[:-5]
    with pytest.raises(ValueError):
        cr.judge_harm(d, spans)


def _index(n: int = 3800, seed: int = 2):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    days = list(pd.bdate_range("2011-01-03", periods=n).date)
    r = pd.Series(rng.normal(0.0, 0.012, n), index=days)
    states = pd.Series(rng.choice(["bull", "bear", "volatile", "crisis"], n, p=[0.4, 0.3, 0.22, 0.08]), index=days, dtype=object)
    return r, states


def test_fast_scales_equals_live_scale_path() -> None:
    _, states = _index(400)
    for v in cr.VARIANTS:
        slow = cr.scale_path(states, cr.variant_params(BASE, v))
        assert np.allclose(cr.fast_scales(states, cr.VARIANTS[v], 2).to_numpy(), slow.to_numpy()), v


def test_index_daily_cost_and_five_session_hold() -> None:
    days = list(pd.bdate_range("2020-01-01", periods=6).date)
    r = pd.Series(0.01, index=days)
    k = pd.Series([1.0, 0.5, 0.5, 1.0, 1.0, 0.5], index=days)
    d = cr.index_daily(r, k)
    assert d.iloc[1] == pytest.approx(0.5 * 0.01 - 0.5 * cr.ONE_WAY_COST)
    held = cr.index_daily(r, k, every=5)
    assert held.iloc[:5].tolist() == pytest.approx([0.01] * 5) and held.iloc[5] == pytest.approx(0.005 - 0.5 * cr.ONE_WAY_COST)


def test_judge_index_gates_and_final_verdict() -> None:
    r, states = _index()
    daily = {v: cr.index_daily(r, cr.fast_scales(states, cr.VARIANTS[v])) for v in cr.VARIANTS}
    big = dict(daily)
    big["CR1"] = daily["CR1"] + (states == "crisis").astype(float) * 0.004   # crisis 칸에서 크게, 구간 모두 양
    _, passed, stats = cr.judge_index(big, states, {"CR1": 0.001, "CR2": 0.5, "CR3": 0.5})
    assert passed == ["CR1"] and all(stats["CR1"]["gates"].values())
    _, passed2, stats2 = cr.judge_index(big, states, {"CR1": 0.02, "CR2": 0.5, "CR3": 0.5})
    assert passed2 == [] and not stats2["CR1"]["gates"]["I4"]               # 위약 하나로도 기각
    v, w = cr.final_verdict([], stats, None)
    assert v.startswith("기각 — 주 시험") and w is None
    with pytest.raises(ValueError):
        cr.final_verdict(["CR1"], stats, None)
    ok = {"CR1": {"gates": {"H1": True, "H2": True, "H3": True}}}
    v, w = cr.final_verdict(["CR1"], stats, ok)
    assert v.startswith("주 시험 통과 CR1 · 해 방지 통과") and w == "CR1"
    v, w = cr.final_verdict(["CR1"], stats, {"CR1": {"gates": {"H1": False, "H2": True, "H3": True}}})
    assert v.startswith("기각 — 주 시험 통과 CR1") and "H1" in v and w is None


def test_effect_only_in_covid_fails_special_robustness() -> None:
    r, states = _index()
    daily = {v: cr.index_daily(r, cr.fast_scales(states, cr.VARIANTS[v])) for v in cr.VARIANTS}
    lo, hi = cr.SPECIAL["코로나"]
    covid = pd.Series([lo <= d <= hi for d in r.index], index=r.index)
    noise = pd.Series(np.random.default_rng(5).normal(0, 1e-4, len(r)), index=r.index)
    spiked = {v: daily["E1"] + noise for v in cr.VARIANTS}                            # 구조적 차이 없음(전환 비용 차도 없앤다)
    spiked["E1"] = daily["E1"]
    spiked["CR1"] = spiked["CR1"] + (covid & (states == "crisis")).astype(float) * 0.5   # 손익이 코로나 crisis 칸에만
    lines, passed, stats = cr.judge_index(spiked, states, {"CR1": 0.001, "CR2": 0.5, "CR3": 0.5})
    assert "CR1" not in passed and not stats["CR1"]["gates"]["I5"]
    assert stats["CR1"]["covid_share"]["crisis"] > 0.9
    assert any("특수 제외" in x for x in lines) and any("특수만" in x for x in lines) and any("| 전체 |" in x for x in lines)
    assert {"코로나": (date(2020, 2, 17), date(2020, 6, 30)), "급등장": (date(2025, 1, 2), date(2026, 6, 30))} == cr.SPECIAL


def test_placebo_null_is_not_small() -> None:
    r, states = _index(1200)
    obs = float((cr.index_daily(r, cr.fast_scales(states, cr.VARIANTS["CR2"]))
                 - cr.index_daily(r, cr.fast_scales(states, cr.VARIANTS["E1"]))).mean() * cr.ANN)
    p = cr.placebo_p(r, states, "CR2", obs, draws=60)
    assert 0.0 < p <= 1.0 and p > cr.GATE_PLACEBO        # 무작위 국면에서 관측값은 특별하지 않다


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
    for needle in ("t ≥ 3", "4/5", "−2%p", "−1.645", "0.05/3", "2026-06-30", "2026-09-30", "초안 — 미고정", "V6 −2.7%p", "2020-02-17", "I5"):
        assert needle in body, needle
