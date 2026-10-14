"""11/23 등록 넷(BLT·UIR·T2P·TK) — 판정 도구와 금고 배선. 합성 자료만(창고·점수 캐시·금고 창을 읽지 않는다).

지키는 것:
 ① 초안(해시 None)은 주 시험이 자료를 읽기 전에 거부하고, 금고 second 창에 붙지 않는다(`--plan` 은 "고정 대기").
 ② 해시는 도구 한 곳(`PROTOCOL_HASH`)에만 — vault_judge 가 거기서 읽는다.
 ③ 구성 표는 P1-b′ 등록(`vault_judge.P1B_ARMS`)과 같은 값이다.
 ④ 판정 함수는 칸마다 혼자서도 기각을 내고, 지표가 nan 이면 멈춘다(조용히 기각하지 않는다).
 ⑤ 스타일 잔차는 세션마다 스타일 열과 직교한다.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools import eval_ladder as el
from tools import trial_sprint_1123 as sp
from tools import vault_judge as vj


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


# --------------------------------------------------------------------------- ① ② 등록·배선


def test_docs_exist_and_hash_lives_in_one_place() -> None:
    assert "T2P" not in sp.VAULT_TRIALS and sp.PROTOCOL_HASH["T2P"] is None and "T2P" not in vj.RUNNERS
    for t in sp.VAULT_TRIALS:
        assert (sp.REPO_ROOT / sp.DOCS[t]).exists(), t
        assert vj.EXTRA_PROTOCOLS[t] == (sp.DOCS[t], sp.PROTOCOL_HASH[t])
        assert vj.ENTITY[t] == sp.ENTITY[t] and vj.FAMILY[t] == sp.FAMILY[t]
        assert t in vj.RUNNERS and t in vj.PREREQ


def test_unpinned_trials_do_not_attach_to_second() -> None:
    second = vj.windows()["second"]
    for t in sp.VAULT_TRIALS:
        if sp.PROTOCOL_HASH[t] is None:
            assert t not in vj.trials_of(second)
            assert t in vj.pending_trials(second)
        else:
            assert t in vj.trials_of(second)
    assert vj.registration_problems(second) == []


def test_main_test_refuses_draft_before_reading_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(sp.PROTOCOL_HASH, "BLT", None)
    with pytest.raises(SystemExit, match="초안"):
        sp.main(["run", "--trial", "BLT", "--root", "/nonexistent-never-opened"])
    monkeypatch.setitem(sp.PROTOCOL_HASH, "BLT", "0" * 16)
    with pytest.raises(SystemExit, match="바뀌었다"):
        sp.require_pinned("BLT")


def test_walk_refuses_draft_before_reading_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for t in ("TK", "UIR"):
        monkeypatch.setitem(sp.PROTOCOL_HASH, t, None)
        with pytest.raises(SystemExit, match="초안"):
            sp.main(["walk", "--trial", t, "--root", "/nonexistent-never-opened"])


def test_arms_match_p1b_registration() -> None:
    for mine, theirs in (("B0", "B0"), ("B1p", "B1′"), ("B2p", "B2′")):
        n, mult, every, theta = sp.ARMS[mine]
        assert vj.P1B_ARMS[theirs] == (n, every, mult, theta)


def test_prereq_skips_unless_main_passed(tmp_path: Path) -> None:
    assert "없다" in str(sp.vault_prereq("BLT", tmp_path, "abc"))
    d = sp.out_dir("BLT", tmp_path)
    d.mkdir(parents=True)
    (d / "main.json").write_text(json.dumps({"protocol_hash": "abc", "verdict": "기각 — ③ 실제 포트"}))
    assert "통과가 아니다" in str(sp.vault_prereq("BLT", tmp_path, "abc"))
    assert "등록 해시" in str(sp.vault_prereq("BLT", tmp_path, "xyz"))
    (d / "main.json").write_text(json.dumps({"protocol_hash": "abc", "verdict": "주 시험 통과 V1 — 금고 second 확인으로"}))
    assert sp.vault_prereq("BLT", tmp_path, "abc") is None


def test_model_hash_section_is_required(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("# x\n")
    with pytest.raises(SystemExit, match="모델 해시"):
        sp._model_hashes("T2P", doc)
    doc.write_text("## 모델 해시\n\n- `T2P-T2-s0.txt` 0123456789abcdef\n")
    assert sp._model_hashes("T2P", doc) == {"T2P-T2-s0.txt": "0123456789abcdef"}


# --------------------------------------------------------------------------- ④ 판정 함수


def _d(ann: float, t: float, ex: float, ex_t: float) -> dict:
    return {"ann": ann, "t": t, "n": 500, "ex_special": {"ann": ex, "t": ex_t, "n": 300}, "special_only": {"ann": ann, "t": 1.0, "n": 200}}


def _variant(**kw: float) -> dict:
    base = {"vs_current": _d(0.05, 1.5, 0.06, 1.2), "seed_pos_current": 5, "vs_same": _d(0.02, 0.8, 0.01, 0.4),
            "vs_uni": _d(0.08, 1.4, 0.05, 1.0), "vs_bench": _d(-0.20, -1.5, 0.03, 0.4),
            "mdd": -0.25, "mdd_current": -0.26, "bench_mdd": -0.35, "turn": 5.0, "turn_current": 22.0, "turn_pair_t": -4.0}
    base.update(kw)
    return base


def _res(**over: object) -> dict:
    res = {"principle": {"ic": _d(0.10, 17.0, 0.09, 13.0), "d10_d1": _d(0.6, 7.0, 0.6, 6.0)},
           "marginal": {"dic": _d(0.017, 5.7, 0.019, 4.5), "seed_pos": 5},
           "variants": {"V1": _variant(), "V2": _variant(vs_current=_d(0.04, 2.0, 0.05, 1.5), turn=15.0)}}
    res.update(over)
    return res


def test_blt_passes_and_picks_larger_t() -> None:
    g = sp.gates("BLT", _res())
    assert g["passed"] == ["V1", "V2"] and g["winner"] == "V2"
    assert g["verdict"].startswith("주 시험 통과 V2")


@pytest.mark.parametrize(("over", "why"), [
    ({"marginal": {"dic": _d(0.017, 2.9, 0.019, 4.5), "seed_pos": 5}}, "②"),             # t < 3
    ({"marginal": {"dic": _d(0.017, 5.7, 0.019, 1.9), "seed_pos": 5}}, "②"),             # 특수 뺀 쪽 t < 2
    ({"marginal": {"dic": _d(0.017, 5.7, 0.019, 4.5), "seed_pos": 3}}, "②"),             # 시드 3/5
    ({"principle": {"ic": _d(0.10, 17.0, -0.01, -1.0), "d10_d1": _d(0.6, 7.0, 0.6, 6.0)}}, "①"),
])
def test_each_principle_cell_rejects_alone(over: dict, why: str) -> None:
    assert sp.gates("BLT", _res(**over))["verdict"] == {"①": "기각 — ① 원리", "②": "기각 — ② 한계기여"}[why]


@pytest.mark.parametrize("kw", [
    {"vs_current": _d(0.05, 1.5, -0.01, -0.1)},          # 특수 뺀 쪽 음
    {"seed_pos_current": 3},
    {"vs_same": _d(-0.01, -0.3, 0.01, 0.2)},
    {"vs_uni": _d(0.01, 0.2, -0.02, -0.3)},
    {"vs_bench": _d(0.01, 0.1, -0.01, -0.1)},             # 대 벤치 특수 뺀 쪽 음
    {"mdd": -0.29},                                      # 현행보다 3%p 깊음
    {"turn": 18.0},                                      # 현행 × 0.75 초과
])
def test_each_port_cell_rejects_alone(kw: dict) -> None:
    res = _res(variants={"V1": _variant(**kw)})
    assert sp.gates("BLT", res)["verdict"] == "기각 — ③ 실제 포트"


def test_full_window_bench_loss_is_record_only() -> None:
    """대 K200 전체 손실(급등장 스타일)은 기준이 아니라 기록 — 특수 뺀 쪽만 본다(등록 문서 '확인할 것 1')."""
    res = _res(variants={"V1": _variant(vs_bench=_d(-0.25, -2.2, 0.02, 0.3))})
    assert sp.gates("BLT", res)["passed"] == ["V1"]


def test_t2p_noninferiority_and_turnover_claim() -> None:
    res = _res(marginal={"dic": _d(0.0043, 1.6, 0.0067, 2.0), "seed_pos": 5}, variants={"V1": _variant()})
    assert sp.gates("T2P", res)["passed"] == ["V1"]
    worse = _res(marginal={"dic": _d(-0.003, -1.0, -0.003, -1.0), "seed_pos": 0}, variants={"V1": _variant()})
    assert sp.gates("T2P", worse)["verdict"] == "기각 — ② 한계기여"
    flat = _res(marginal={"dic": _d(0.0043, 1.6, 0.0067, 2.0), "seed_pos": 5}, variants={"V1": _variant(turn_pair_t=-2.0)})
    assert sp.gates("T2P", flat)["verdict"] == "기각 — ③ 실제 포트"


def test_feature_trial_principle_counts_registered_signs() -> None:
    feats = {c: _d(0.02 * s, 5.0 * s, 0.02 * s, 4.0 * s) for c, s in sp.TK_SIGN.items()}
    res = _res(principle={"ic": _d(0.09, 17.0, 0.08, 13.0), "features": feats},
               variants={"V1": {k: v for k, v in _variant(turn=21.0, turn_current=20.0).items() if k != "vs_same"}})
    assert sp.gates("TK", res)["passed"] == ["V1"]
    flipped = {c: _d(-v["ann"], -v["t"], -v["ann"], -v["t"]) for c, v in feats.items()}
    res["principle"] = {"ic": _d(0.09, 17.0, 0.08, 13.0), "features": flipped}
    assert sp.gates("TK", res)["verdict"] == "기각 — ① 원리"


def test_gates_stop_on_nan() -> None:
    with pytest.raises(ValueError):
        sp.gates("BLT", _res(marginal={"dic": _d(float("nan"), 1.0, 0.0, 1.0), "seed_pos": 5}))


def test_confirm_margins_and_nan() -> None:
    m = {"sd_ann": 0.4, "sd_mdd": 0.03, "turn_q95": 1.1, "sd_dic": 0.017}
    dic = pd.Series([0.01] * 30)
    ok, v = sp.judge_confirm("BLT", dic, {"ann": 0.10, "mdd": -0.05, "turn": 5.0}, {"ann": 0.20, "mdd": -0.05, "turn": 20.0}, m)
    assert v.startswith("확인")
    _, v = sp.judge_confirm("BLT", dic, {"ann": -0.60, "mdd": -0.05, "turn": 5.0}, {"ann": 0.20, "mdd": -0.05, "turn": 20.0}, m)
    assert v.startswith("기각")                                       # −80%p < −1.645 × 40%p
    _, v = sp.judge_confirm("BLT", pd.Series([-0.04] * 30), {"ann": 0.1, "mdd": -0.05, "turn": 5.0},
                            {"ann": 0.1, "mdd": -0.05, "turn": 20.0}, m)
    assert v.startswith("기각")                                       # ΔIC −0.04 < −0.028
    with pytest.raises(ValueError):
        sp.judge_confirm("BLT", dic, {"ann": float("nan"), "mdd": 0, "turn": 1}, {"ann": 0, "mdd": 0, "turn": 1}, m)
    with pytest.raises(ValueError):
        sp.judge_confirm("BLT", dic, {"ann": 0, "mdd": 0, "turn": 1}, {"ann": 0, "mdd": 0, "turn": 1}, {"sd_ann": 0.1})


def test_block_margins_shape() -> None:
    rng = np.random.default_rng(0)
    idx = [date(2024, 1, 1) + timedelta(days=i) for i in range(200)]
    t = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    c = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    turn = pd.Series(np.where(np.arange(200) % 10 == 0, 0.5, 0.0), index=idx)
    m = sp.block_margins(t, c, turn * 0.5, turn, pd.Series(rng.normal(0, 0.05, 200), index=idx))
    assert m["blocks"] == (200 - 30) // 5 + 1
    assert m["sd_ann"] > 0 and m["sd_mdd"] > 0 and abs(m["turn_q95"] - 0.5) < 1e-9 and m["sd_dic"] > 0


# --------------------------------------------------------------------------- ⑤ 스타일 잔차


def test_style_residual_is_orthogonal_per_session() -> None:
    rng = np.random.default_rng(1)
    n = 400
    groups = np.repeat([1, 2], n)
    styles = rng.normal(size=(2 * n, 3))
    x = styles[:, 0] * 2.0 + rng.normal(size=2 * n)
    x[5] = np.nan
    styles[7, 1] = np.nan
    r = sp.style_residual(x, styles, groups)
    assert np.isnan(r[5]) and np.isnan(r[7])
    for g in (1, 2):
        m = (groups == g) & np.isfinite(r)
        z = (styles[m] - styles[m].mean(0)) / styles[m].std(0)
        assert np.allclose(z.T @ r[m], 0.0, atol=1e-8)
        assert abs(r[m].mean()) < 1e-9


# --------------------------------------------------------------------------- 포트(위상 평균)


def _ctx(days: int = 120, names: int = 60) -> el.Context:
    rng = np.random.default_rng(2)
    idx = [date(2024, 1, 2) + timedelta(days=i) for i in range(days)]
    cols = [f"KR:{i:06d}" for i in range(names)]
    close = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.01, (days, names)), axis=0), index=idx, columns=cols)
    ret = close.shift(-2) / close.shift(-1) - 1.0
    trad = {d: set(cols) for d in idx}
    z = pd.Series(0.0, index=idx)
    return el.Context(close=close, ret=ret, bench=z, k200_close=pd.Series(1.0, index=idx), trad=trad, uni=z, scale=z + 1)


def test_arm_book_phase_average_runs_and_low_turnover_is_lower() -> None:
    ctx = _ctx()
    rng = np.random.default_rng(3)
    wide = pd.DataFrame(rng.normal(size=ctx.close.shape), index=ctx.close.index, columns=ctx.close.columns)
    n0, t0 = sp.arm_book(wide, ctx, "B0")
    n2, t2 = sp.arm_book(wide, ctx, "B2p")
    assert len(n0) and len(n2) and np.isfinite(n0).all()
    assert t2.sum() < t0.sum()


def _preds(ctx: el.Context, signal: float, seed0: int) -> dict[int, pd.DataFrame]:
    """다음 수익을 조금 아는 점수(signal) + 잡음 — 시드 둘."""
    fwd = (ctx.close.shift(-6) / ctx.close.shift(-1) - 1.0).fillna(0.0)
    out = {}
    for s in (0, 1):
        rng = np.random.default_rng(seed0 + s)
        w = fwd * signal + rng.normal(0, 0.05, fwd.shape)
        long = w.stack().rename("pred").reset_index()
        long.columns = ["session", "entity_id", "pred"]
        out[s] = long[["entity_id", "session", "pred"]]
    return out


def test_model_blocks_run_end_to_end_on_synthetic_data() -> None:
    ctx = _ctx(days=160, names=60)
    treat, ctrl = _preds(ctx, 3.0, 10), _preds(ctx, 0.0, 20)
    pb = sp.principle_block(treat, ctrl, ctx)
    assert pb["marginal"]["dic"]["ann"] > 0 and pb["marginal"]["seed_pos"] == 2
    tb, cb = sp.seed_books(treat, ctx, "B2p"), sp.seed_books(ctrl, ctx, "B0")
    vb = sp.variant_block(tb, cb, sp.seed_books(ctrl, ctx, "B2p"), ctx)
    for k in ("vs_current", "vs_same", "vs_uni", "vs_bench", "mdd", "turn", "turn_current", "turn_pair_t"):
        assert k in vb
    m = sp.block_margins(tb["bundle"][0], cb["bundle"][0], tb["bundle"][1], cb["bundle"][1], pb["_dic_daily"])
    assert m["blocks"] > 0 and np.isfinite(m["sd_ann"])


def test_vault_ports_on_synthetic_window() -> None:
    ctx = _ctx(days=80, names=60)
    treat, ctrl = _preds(ctx, 3.0, 30), _preds(ctx, 0.0, 40)
    sessions = list(ctx.close.index[20:60])
    t, c = sp._vault_ports(treat, ctrl, ctx.ret, ctx.trad, sessions, "B2p", "B0")
    assert set(t) == {"ann", "mdd", "turn"} and all(np.isfinite(v) for v in (*t.values(), *c.values()))
