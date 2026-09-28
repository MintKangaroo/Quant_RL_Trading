"""시행 D1(결정 중심 학습) — 포트 층 미분·상한·학습/판정 같은 경로·누설 간격·백본 잔차·관문. 합성 자료다("이긴다" 는 안 잰다).

규칙은 **진짜 kit** 에서 온다(`tools.final_round_kit` — 블록·반열림 경계·내부 분할·규칙 포트·대조군 관문·판정).
바꿔 끼우는 것은 자료뿐이다(`trial_final_dfl.synthetic`).
"""

from __future__ import annotations

import argparse
import copy
import inspect
from dataclasses import replace
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from tools import final_round_kit as kit
from tools import trial_ranker_kit as rkit
from tools import trial_us_kit as ukit
from tools.trial_final_dfl import (
    ALPHA,
    CAPS,
    D1_FIRST_BLOCK,
    D1_GAP,
    ETA,
    HOLD,
    INDEX_EXIT,
    JUDGE_KEYS,
    LAMBDA,
    SMOKE_HYPER,
    DaySet,
    Hyper,
    K,
    MarketData,
    base_weights,
    build_market,
    cap_project,
    chains_of,
    d1_since,
    d1_train_end,
    effective_n,
    ema_select,
    feature_include,
    hold_returns,
    judge_variant,
    judged_keys,
    make_head,
    predict,
    prepare,
    rank_gauss_exact,
    rank_gauss_soft,
    require_index_coverage,
    require_registered,
    roll_chain,
    rule_target,
    run_book,
    selection_wide,
    shuffled,
    soft_topk,
    split_pool,
    synthetic,
    synthetic_controls,
    tilt_weights,
    train_head,
    training_pool,
    walk,
    with_backbone,
)

TINY = replace(SMOKE_HYPER, steps_per_epoch=3)


@pytest.fixture(scope="module")
def syn():  # type: ignore[no-untyped-def]
    return synthetic(0, n_sessions=420, n_entities=80)


@pytest.fixture(scope="module")
def prep(syn):  # type: ignore[no-untyped-def]
    return prepare(syn.panel, syn.feats, syn.groups, syn.sessions, syn.books, syn.index_raw)


@pytest.fixture(scope="module")
def controls(syn, prep):  # type: ignore[no-untyped-def]
    return synthetic_controls(syn, prep, (0, 1))


@pytest.fixture(scope="module")
def inp(prep, controls):  # type: ignore[no-untyped-def]
    return with_backbone(prep, controls["C1"][0])


def _block(prep, number: int = TINY.first_block) -> tuple[date, date]:  # type: ignore[no-untyped-def]
    first, last = prep.blocks[number]
    return d1_train_end(prep.axis, first), kit.block_span(prep.axis, first, last)[0]


def _fit_val(prep, inp, variant: str = "D1a", number: int = TINY.first_block):  # type: ignore[no-untyped-def]
    cut, judged_first = _block(prep, number)
    return split_pool(prep.data, training_pool(variant, prep.data, inp, cut, judged_first))


# --------------------------------------------------------------------------- 포트 층


def test_소프트_상위k_는_미분_가능하다_암묵미분이_유한차분과_같다() -> None:
    torch.manual_seed(0)
    z = torch.randn(15, dtype=torch.float64, requires_grad=True)
    assert torch.autograd.gradcheck(lambda v: soft_topk(v, 4, 0.5, iters=200), (z,), eps=1e-6, atol=1e-5)


@pytest.mark.parametrize("n", [300, 2000])
def test_소프트_상위k_는_합1_종목당_1_over_k_이하다(n: int) -> None:
    z = torch.randn(n, dtype=torch.float64, generator=torch.Generator().manual_seed(n))
    w = soft_topk(z, K, 0.1)
    assert abs(float(w.sum()) - 1.0) < 1e-9
    assert float(w.max()) <= 1.0 / K * (1 + 1e-6)


def test_온도가_낮으면_딱_상위k_동일가중이다() -> None:
    z = torch.randn(400, dtype=torch.float64, generator=torch.Generator().manual_seed(1))
    hard = torch.zeros_like(z)
    hard[torch.topk(z, K).indices] = 1.0 / K
    assert torch.allclose(soft_topk(z, K, 1e-4), hard, atol=1e-6)


def test_상한_투영은_상한을_지키고_AX_규칙과_같다() -> None:
    from tools.trial_selection_ranker import capped_cap_weights

    rng = np.random.default_rng(3)
    for cap in (0.10, 0.30):
        raw = pd.Series(np.exp(rng.normal(0, 2.0, 60)))
        mine = cap_project(torch.as_tensor(raw.to_numpy() / raw.sum()), cap).numpy()
        assert mine.max() <= cap + 1e-12 and abs(mine.sum() - 1.0) < 1e-12
        ref = capped_cap_weights(raw.copy(), cap).to_numpy()
        if ref.max() <= cap + 1e-12:
            assert np.allclose(mine, ref, atol=1e-10)


def test_부드러운_rank_gauss_는_하드와_거의_같고_미분_가능하다() -> None:
    v = torch.randn(200, dtype=torch.float64, generator=torch.Generator().manual_seed(2))
    soft = rank_gauss_soft(v).numpy()
    hard = rank_gauss_exact(v.numpy())
    assert np.corrcoef(soft, hard)[0, 1] > 0.99
    b = torch.rand(12, dtype=torch.float64) + 0.1
    s = torch.randn(12, dtype=torch.float64, requires_grad=True)
    assert torch.autograd.gradcheck(lambda x: tilt_weights(rank_gauss_soft(x, 0.5), b / b.sum(), LAMBDA, 0.30),
                                    (s,), eps=1e-6, atol=1e-5)


def test_기울이기는_상한을_지키고_람다0_이면_B0_와_같다(prep) -> None:  # type: ignore[no-untyped-def]
    for m, md in prep.data.items():
        st = next(st for st in md.sets.values() if st.idx_code is not None)
        z = torch.as_tensor(rank_gauss_exact(np.random.default_rng(4).normal(size=len(st.idx_code))))
        w = tilt_weights(z, torch.as_tensor(st.idx_b), LAMBDA, CAPS[m])
        assert float(w.max()) <= CAPS[m] + 1e-9 and abs(float(w.sum()) - 1.0) < 1e-9
        w0 = tilt_weights(z, torch.as_tensor(st.idx_b), 0.0, CAPS[m])
        assert np.allclose(w0.numpy(), base_weights(st)[1], atol=1e-12), "λ = 0 은 B0(규칙 9)"


# --------------------------------------------------------------------------- 백본 + 잔차 머리


def test_학습_전_점수는_정확히_백본이다(prep, inp) -> None:  # type: ignore[no-untyped-def]
    """마지막 층 0 초기화 — D1 − C1 이 정확히 DFL 의 몫이 되려면 출발점이 C1 그 자체여야 한다(§3.1 b)."""
    head = make_head(0, inp.X.shape[1])
    day = sorted(d for d in prep.data["KR"].days if np.isfinite(inp.bb[prep.data["KR"].sets[d].rows]).all())[5]
    out = predict(head, inp, prep.data, [("KR", day)])
    st = prep.data["KR"].sets[day]
    assert np.array_equal(out["pred"].to_numpy(), inp.bb[st.rows])


def test_머리는_작다() -> None:
    head = make_head(0, 77)
    assert sum(p.numel() for p in head.parameters()) < 2000, "잔차 머리는 외울 자유도가 작아야 한다(규율 1)"


# --------------------------------------------------------------------------- 학습 경로 = 판정 경로


def _pred_frame(prep, inp, market: str, days: list[date]) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    return predict(make_head(0, inp.X.shape[1]), inp, prep.data, [(market, d) for d in days])


@pytest.mark.parametrize("market", ["KR", "US"])
def test_학습의_선정_점수는_판정_규칙의_선정_점수와_같다(prep, inp, syn, market: str) -> None:  # type: ignore[no-untyped-def]
    """EMA5(국장) · M1 뒤 EMA5(미장) — 학습 쪽 하드 선정 점수가 kit 판정 경로(`us_m1_wide` · ewm)와 같아야 한다."""
    from tools.trial_final_dfl import day_values, scores

    md = prep.data[market]
    days = [d for d in md.days if np.isfinite(inp.bb[md.sets[d].rows]).all()][:12]   # EMA 창 안(잘림 없음)
    wide = selection_wide(_pred_frame(prep, inp, market, days), syn.books[market])
    head = make_head(0, inp.X.shape[1])
    vals, masks = [], []
    for d in days:
        st = md.sets[d]
        v = np.zeros(len(md.entities))
        v[st.code] = day_values(md, st, scores(head, inp, st), inp, soft=False).detach().numpy()
        mk = np.zeros(len(md.entities), bool)
        mk[st.code] = True
        vals.append(v)
        masks.append(mk)
    sel, have = ema_select(vals, masks)
    row = wide.loc[days[-1]].dropna()
    mine = pd.Series(sel.numpy()[have], index=np.asarray(md.entities, dtype=object)[have]).reindex(row.index)
    assert np.allclose(mine.to_numpy(), row.to_numpy(), atol=1e-9)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_이_파일의_규칙_장부는_kit_규칙_포트와_같은_일수익을_낸다(prep, inp, syn, market: str) -> None:  # type: ignore[no-untyped-def]
    """하드 규칙(상위 24 · 완충 72 · C10 · 드리프트) — 학습의 직전 보유와 기록용 장부가 판정(kit)과 같은 규칙이어야 한다."""
    md = prep.data[market]
    days = [d for d in md.days if np.isfinite(inp.bb[md.sets[d].rows]).all()][:80]
    pred = _pred_frame(prep, inp, market, days)
    book = syn.books[market]
    mine, _t, _h = run_book(md, days, rule_target(md, selection_wide(pred, book)))
    if market == "KR":
        ref, _x = rkit.portfolio(pred[["entity_id", "session", "pred"]], book.ret, book.trad, every=HOLD)
    else:
        ref, _x = ukit.book(kit.us_m1_wide(pred[["entity_id", "session", "pred"]], book.fund), book.ret, book.cost,
                            n=kit.N, exit_mult=kit.EXIT_MULT, every=HOLD)
    ref = ref.reindex(mine.index)
    assert np.allclose(mine.to_numpy(), ref.to_numpy(), atol=1e-12)


def test_보유_수익은_드리프트까지_w_곱하기_G_로_정확히_선형이다() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    R = np.random.default_rng(5).normal(0.001, 0.02, (30, 6))
    md = MarketData("KR", days, [f"E{i}" for i in range(6)], R, hold_returns(R), days, 0.0, pd.Series(0.0, index=days), None)
    for i, d in enumerate(days):
        md.sets[d] = DaySet(d, "KR", i, np.arange(6), np.arange(6), np.zeros(6, np.float32))
    w = np.array([0.3, 0.2, 0.1, 0.25, 0.15, 0.0])
    daily, _t, _h = run_book(md, days[:HOLD], lambda _st: (np.arange(6), w))
    assert np.isclose(np.prod(1 + daily.to_numpy()) - 1, float(w @ md.G[0]), atol=1e-12)
    md.cost = 0.004
    costly, _t, _h = run_book(md, days[:HOLD], lambda _st: (np.arange(6), w))
    diff = np.prod(1 + daily.to_numpy()) - np.prod(1 + costly.to_numpy())
    assert 0.004 * 0.95 < diff < 0.004 * 1.05, "비용은 재조정일에 한 번만(첫날 회전 1)"


def test_판정은_위상_0_만_쓴다() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    R = np.zeros((30, 6))
    md = MarketData("KR", days, [f"E{i}" for i in range(6)], R, hold_returns(R), days, 0.0, pd.Series(0.0, index=days), None)
    for i, d in enumerate(days):
        md.sets[d] = DaySet(d, "KR", i, np.arange(6), np.arange(6), np.zeros(6, np.float32))
    called: list[date] = []

    def target(st: DaySet) -> tuple[np.ndarray, np.ndarray]:
        called.append(st.day)
        return np.arange(6), np.full(6, 1 / 6)

    run_book(md, days, target)
    assert called == [days[0], days[10], days[20]]
    src = inspect.getsource(judge_variant)
    assert "kit.evaluate_all(preds[s].judge" in src, "D1a 판정은 kit 규칙 포트(위상 0)다"
    assert "phase=p" in src and "기준 아님" in src


# --------------------------------------------------------------------------- 사슬·그래디언트


@pytest.mark.parametrize("variant", ["D1a", "D1b"])
def test_포트_손실의_그래디언트가_잔차_머리까지_간다(prep, inp, variant: str) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import Calib, port_loss

    fit, _val = _fit_val(prep, inp, variant)
    md = prep.data["KR"]
    chain = chains_of(md, [d for m, d in fit if m == "KR"], 4)[0]
    head = make_head(0, inp.X.shape[1])
    with torch.no_grad():
        head[0].weight.normal_()                       # 0 초기화면 첫 층 기울기가 0 이다 — 경로만 본다
        head[3].weight.fill_(0.1)
    ports, _preds = roll_chain(variant, head, inp, md, chain, Calib({"KR": 0.1, "US": 0.1}, 1.0, 1.0), TINY)
    assert len(ports) == len(chain) - 1, "첫 결정은 워밍업(보유만 세운다)"
    port_loss(ports, ETA).backward()
    grads = [p.grad for p in head.parameters()]
    assert all(g is not None for g in grads) and sum(float(g.abs().sum()) for g in grads if g is not None) > 0


def test_사슬은_같은_위상_10세션_간격이다(prep, inp) -> None:  # type: ignore[no-untyped-def]
    fit, _val = _fit_val(prep, inp)
    md = prep.data["US"]
    for chain in chains_of(md, [d for m, d in fit if m == "US"], 4)[:20]:
        pos = [md.pos_of(d) for d in chain]
        assert all(b - a == HOLD for a, b in pairwise(pos))


# --------------------------------------------------------------------------- 표본 규율·누설


def test_D1_간격은_16이고_kit_간격_10_이면_라벨이_판정_첫날을_본다(prep, inp) -> None:  # type: ignore[no-untyped-def]
    """§3.1 a — 이 테스트가 D1 간격의 존재 이유다: kit 간격이면 새고, D1 간격이면 안 샌다."""
    assert D1_GAP == 16
    first, last = prep.blocks[TINY.first_block]
    judged_first = kit.block_span(prep.axis, first, last)[0]
    for m, md in prep.data.items():
        kit_cut = kit.train_end(prep.axis, first)
        ends = [md.label_end(d) for d in md.days if d <= kit_cut and md.label_end(d) is not None]
        assert max(ends) >= judged_first, f"{m}: kit 간격으로도 안 샌다면 이 규칙의 근거가 틀렸다"
    pool = training_pool("D1a", prep.data, inp, d1_train_end(prep.axis, first), judged_first)
    assert pool
    assert all(prep.data[m].label_end(d) < judged_first for m, d in pool), "학습 라벨의 끝 < 판정 첫 세션"


def test_학습이_읽은_가격은_전부_판정_첫날_전이다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import calibrate

    fit, val = _fit_val(prep, inp)
    calib = calibrate("D1a", inp, prep.data, fit, TINY, 0)
    _head, log = train_head("D1a", inp, prep.data, syn.books, fit, val, 0, calib, hyper=TINY)
    _cut, judged_first = _block(prep)
    assert log.last_label_day is not None and log.last_label_day < judged_first
    assert all(d < judged_first for _m, d in log.seen | log.val_days)


def test_내부_검증은_적합_뒤에_있고_적합_라벨과_안_겹친다(prep, inp) -> None:  # type: ignore[no-untyped-def]
    fit, val = _fit_val(prep, inp)
    assert fit and val
    val_start = min(d for _m, d in val)
    assert all(prep.data[m].label_end(d) < val_start for m, d in fit)


def test_판정_구간_뒤_자료를_바꿔도_머리가_같다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    """미래 누설 — 판정 첫날 이후의 수익·피처·라벨을 통째로 바꿔도 학습된 머리가 한 비트도 안 바뀌어야 한다."""
    from tools.trial_final_dfl import Calib

    fit, val = _fit_val(prep, inp, "D1b")
    _cut, judged_first = _block(prep)
    calib = Calib({"KR": 0.1, "US": 0.1}, 1.0, 0.01)
    a, _ = train_head("D1b", inp, prep.data, syn.books, fit, val, 0, calib, hyper=TINY)
    rng = np.random.default_rng(9)
    data2 = copy.deepcopy(prep.data)
    X2 = inp.X.copy()
    for md in data2.values():
        after = np.array([d >= judged_first for d in md.days])
        md.R = md.R.copy()
        md.R[after] = rng.normal(0, 0.2, (int(after.sum()), md.R.shape[1]))
        md.G = hold_returns(md.R)
        for d, st in md.sets.items():
            if d >= judged_first:
                st.y = rng.normal(size=len(st.y)).astype(np.float32)
                X2[st.rows] = rng.normal(size=(len(st.rows), X2.shape[1])).astype(np.float16)
    b, _ = train_head("D1b", replace(inp, X=X2), data2, syn.books, fit, val, 0, calib, hyper=TINY)
    for (name, pa), (_n, pb) in zip(a.state_dict().items(), b.state_dict().items(), strict=True):
        assert torch.equal(pa, pb), f"{name}: 판정 구간 자료가 학습에 샜다"


def test_온도는_첫_학습창에서_유효_종목_24_30_으로_얼린다(prep, inp) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import EFF_BAND, TARGET_EFF, calibrate

    fit, _val = _fit_val(prep, inp)
    calib = calibrate("D1a", inp, prep.data, fit, TINY, 0)
    z = torch.randn(len(prep.data["KR"].entities), dtype=torch.float64, generator=torch.Generator().manual_seed(0))
    eff = effective_n(soft_topk(z, K, calib.temp["KR"]))
    assert EFF_BAND[0] - 3 <= eff <= EFF_BAND[1] + 3 and EFF_BAND[0] <= TARGET_EFF <= EFF_BAND[1]
    assert calib.l_pred > 0 and calib.l_port > 0
    assert "if calib is None:" in inspect.getsource(walk), "온도·척도는 시드마다 첫 재학습에서만 정한다(얼림)"


def test_채택_여백을_못_넘으면_머리를_버리고_백본으로_돌아간다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    """조기 종료는 검증 창의 잡음을 줍는다(shuffle 에서 4회 중 2회 "개선") — 여백을 넘은 이득만 머리를 쓴다."""
    from tools.trial_final_dfl import Calib

    fit, val = _fit_val(prep, inp)
    calib = Calib({"KR": 0.1, "US": 0.1}, 1.0, 0.01)
    _free, log0 = train_head("D1a", inp, prep.data, syn.books, fit, val, 0, calib, hyper=TINY, margin=0.0)
    assert log0.gain >= 0.0 and log0.accepted == (log0.best_epoch > 0 and log0.gain > 0.0)
    held, log1 = train_head("D1a", inp, prep.data, syn.books, fit, val, 0, calib, hyper=TINY, margin=1e9)
    assert not log1.accepted and log1.margin == 1e9
    day = next(d for m, d in val if m == "KR")
    out = predict(held, inp, prep.data, [("KR", day)])
    assert np.array_equal(out["pred"].to_numpy(), inp.bb[prep.data["KR"].sets[day].rows]), "여백 미달이면 h = 0"
    assert log1.val_scores == log0.val_scores, "여백은 학습을 바꾸지 않고 채택만 가른다"


FIXTURE = Path(__file__).parent / "fixtures" / "dfl-shuffle-margin-synthetic.json"


def test_여백은_변형마다_하나_합동_최댓값이고_판정은_읽기만_한다(tmp_path: Path) -> None:
    import json

    from tools.trial_final_dfl import CHECK_EXIT, cmd_judge, load_margins, save_margins

    fx = json.loads(FIXTURE.read_text())
    assert fx["margins"]["D1a"] == max(fx["gains"]["D1a"]), "여백 = 시드·재학습 전체 이득의 최댓값(합동)"
    assert load_margins(("D1a",), FIXTURE, protocol_hash="synthetic", backbone="C1", seeds=(0, 4)) == fx["margins"]
    path = tmp_path / "m.json"
    with pytest.raises(SystemExit) as info:
        load_margins(("D1a",), path, protocol_hash="h", backbone="C1")
    assert info.value.code == CHECK_EXIT == 7
    save_margins({"D1a": 0.03, "D1b": 0.01}, {"D1a": [0.0, 0.03], "D1b": [0.01]}, path,
                 protocol_hash="h", backbone="C1", seeds=(0, 1))
    assert load_margins(("D1a", "D1b"), path, protocol_hash="h", backbone="C1", seeds=(0, 1)) == {"D1a": 0.03, "D1b": 0.01}
    for bad in ({"protocol_hash": "다른 등록"}, {"backbone": "C2"}, {"seeds": (0, 1, 2)}):
        kw = {"protocol_hash": "h", "backbone": "C1", "seeds": (0, 1), **bad}
        with pytest.raises(SystemExit):
            load_margins(("D1a",), path, **kw)             # type: ignore[arg-type]
    src = inspect.getsource(cmd_judge)
    assert "margin=margins[variant]" in src and "save_margins" not in src, "판정은 여백을 만들지 않는다"


def test_여백_굽기는_판정_블록_예측을_하나도_안_만든다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    """실자료 라벨 섞기가 canary 와 같은 지위인 근거 — 판정 블록 수익을 볼 길 자체가 없다."""
    res = walk("D1a", inp, shuffled(prep.data, 0), syn.books, prep.axis, prep.blocks[: TINY.first_block + 1], 0,
               aug=prep.aug, hyper=TINY, val_data=prep.data, gains_only=True)
    assert res.judge.empty and res.train.empty and res.logs
    assert all(d < d1_since(prep, TINY) for log in res.logs for _m, d in log.val_days | log.seen)


def test_라벨을_섞어도_내부_검증은_진짜_수익으로_잰다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import Calib

    fit, val = _fit_val(prep, inp, "D1b")
    calib = Calib({"KR": 0.1, "US": 0.1}, 1.0, 0.01)
    hyper = replace(TINY, max_epochs=1)
    _a, real = train_head("D1b", inp, prep.data, syn.books, fit, val, 0, calib, hyper=hyper)
    _b, mixed = train_head("D1b", inp, shuffled(prep.data, 0), syn.books, fit, val, 0, calib, hyper=hyper,
                           val_data=prep.data)
    assert real.val_scores[0] == mixed.val_scores[0], "h = 0 의 검증 수익은 라벨을 섞어도 같아야 한다(진짜 수익으로 잰다)"


def test_여백_조각은_이어_쓴다(prep, controls, syn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import bake_margin

    hyper = replace(TINY, max_epochs=1)
    blocks = prep.blocks[: TINY.first_block + 1]
    small = replace(prep, blocks=blocks)
    first = bake_margin("D1a", small, controls, syn.books, (0,), hyper=hyper, parts=tmp_path, tag="h-C1")
    assert (tmp_path / "h-C1-D1a-seed0.json").exists() and all(g >= 0 for g in first)
    assert bake_margin("D1a", small, controls, syn.books, (0,), hyper=hyper, parts=tmp_path, tag="h-C1") == first


def test_시드가_같으면_같은_예측(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    blocks = prep.blocks[: TINY.first_block + 1]
    a = walk("D1a", inp, prep.data, syn.books, prep.axis, blocks, 0, aug=prep.aug, hyper=TINY)
    b = walk("D1a", inp, prep.data, syn.books, prep.axis, blocks, 0, aug=prep.aug, hyper=TINY)
    assert a.judge["pred"].equals(b.judge["pred"])
    assert not torch.equal(make_head(0, 9)[0].weight, make_head(1, 9)[0].weight)


def test_앞_블록은_채점하지_않는다(prep, inp, syn) -> None:  # type: ignore[no-untyped-def]
    res = walk("D1a", inp, prep.data, syn.books, prep.axis, prep.blocks[: TINY.first_block + 1], 0,
               aug=prep.aug, hyper=TINY)
    assert min(res.judge["session"]) >= d1_since(prep, TINY)
    assert D1_FIRST_BLOCK == 10


def test_미장_휴장일_행도_판정한다(prep) -> None:  # type: ignore[no-untyped-def]
    keys = [k for first, last in prep.blocks for k in judged_keys(prep.data, prep.axis, first, last)]
    assert {d for m, d in keys if m == "US"} - set(prep.axis)


def test_라벨_섞기는_세트_안에서만_섞는다(prep) -> None:  # type: ignore[no-untyped-def]
    mixed = shuffled(prep.data, 0)
    for m, md in prep.data.items():
        for d in list(md.sets)[:30]:
            st, st2 = md.sets[d], mixed[m].sets[d]
            p = md.pos_of(d)
            assert np.allclose(np.sort(md.R[p, st.code]), np.sort(mixed[m].R[p, st.code]))
            assert np.allclose(np.sort(st.y), np.sort(st2.y))
            assert np.array_equal(st.rows, st2.rows), "피처는 섞지 않는다"


# --------------------------------------------------------------------------- 판정 경로


@pytest.fixture(scope="module")
def judged(syn, prep, controls):  # type: ignore[no-untyped-def]
    since = d1_since(prep, TINY)
    out = {}
    for variant in ("D1a", "D1b"):
        preds = {s: walk(variant, with_backbone(prep, controls["C1"][s]), prep.data, syn.books, prep.axis,
                         prep.blocks, s, aug=prep.aug, hyper=TINY) for s in (0, 1)}
        out[variant] = judge_variant(variant, preds, prep.data, controls, syn.books, prep.y, prep.inp.X,
                                     prep.index_of, since, hyper=TINY)
    return out


@pytest.mark.parametrize("variant", ["D1a", "D1b"])
def test_판정_경로가_필수_키를_내고_판정_줄은_마지막_하나다(judged, variant: str) -> None:  # type: ignore[no-untyped-def]
    v = judged[variant]
    for seed, m in v.results.items():
        assert set(JUDGE_KEYS) <= set(m), f"시드 {seed} 빠진 키 {set(JUDGE_KEYS) - set(m)}"
    assert [line for line in v.lines if line.startswith("판정:")] == [v.lines[-1]]
    assert any("성향" in line and "β" in line for line in v.lines)
    assert any(line.startswith("기록: D1 − C1") for line in v.lines)
    assert set(v.delta) == {0, 1}
    if variant == "D1b":
        assert any(line.startswith("v2: IR(지수)") for line in v.lines)
        assert any("B0(λ=0)" in line for line in v.lines)


def test_대조군은_D1_채점_구간으로_잘라_잰다() -> None:
    from tools.trial_final_dfl import require_same_span, restrict

    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]
    frame = pd.DataFrame({"entity_id": "A", "session": days, "market": "KR", "pred": 0.5})
    cut = restrict(frame, days[10])
    assert min(cut["session"]) == days[10]
    require_same_span(cut, {"C1": {0: cut}})
    with pytest.raises(SystemExit):
        require_same_span(frame, {"C1": {0: cut}})


def test_판정_키가_비면_조용히_지나가지_않는다() -> None:
    from tools.trial_final_dfl import require_keys

    with pytest.raises(SystemExit):
        require_keys("빈 지표", {"ann": 0.1})


# --------------------------------------------------------------------------- 관문


def test_사전등록_플래그_없이는_판정이_안_돈다() -> None:
    from tools.trial_final_dfl import main

    with pytest.raises(SystemExit):
        main(["judge"])


def test_초안이면_판정을_거부한다(tmp_path: Path) -> None:
    args = argparse.Namespace(i_registered=True)
    draft = tmp_path / "p.md"
    draft.write_text("# 사전등록 — D1\n\n> **초안 — 해시 미고정.**\n")
    with pytest.raises(SystemExit):
        require_registered(args, "판정", draft)
    fixed = tmp_path / "q.md"
    fixed.write_text("# 사전등록 — D1\n\n> 해시 고정 2026-10-04\n")
    assert len(require_registered(args, "판정", fixed)) == 16
    with pytest.raises(SystemExit):
        require_registered(argparse.Namespace(i_registered=False), "판정", fixed)


def test_실제_등록_문서는_지금_초안이다() -> None:
    from tools.trial_final_dfl import PROTOCOL

    assert any(line.startswith("> **초안") for line in PROTOCOL.read_text().splitlines()[:5])


def test_관문_순서() -> None:
    from tools.trial_final_dfl import cmd_judge

    src = inspect.getsource(cmd_judge)
    order = ["require_registered", "feature_include", "load_margins", "load_full_panel", "require_full_coverage",
             "require_controls", "require_full_window", "require_index_coverage", "walk("]
    where = [src.index(name) for name in order]
    assert where == sorted(where), f"관문 순서가 어긋났다: {dict(zip(order, where, strict=True))}"


def test_백본_캐시가_없으면_rc3(prep, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as info:
        kit.require_controls(prep.keys, cache_dir=tmp_path, arms=("C0", "C2"))
    assert info.value.code == kit.CONTROLS_EXIT == 3


def test_FA플러스_묶음이_kit_에_없으면_rc3_짝이_안_맞으면_거부() -> None:
    assert feature_include("FA", "C1") == tuple(kit.BLOCK_ORDER)
    with pytest.raises(SystemExit):
        feature_include("FA", "C2")
    with pytest.raises(SystemExit):
        feature_include("FA+", "C1")
    defined = set(kit.blocks_of((), include=None))
    if not {"G10", "G11", "G12", "G13"} <= defined:           # 2026-09-28: kit 이 아직 G10~G13 을 정의하지 않는다
        with pytest.raises(SystemExit) as info:
            feature_include("FA+", "C2")
        assert info.value.code == 3


def test_원피처_굽기가_없으면_rc4(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(kit.RAW_DIRS, "KR", tmp_path)
    with pytest.raises(SystemExit) as info:
        kit.require_full_coverage()
    assert info.value.code == kit.COVERAGE_EXIT == 4


def test_EMA_창이_채점_첫날_앞에_없으면_rc5(prep) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_final_dfl import EMA_WINDOW

    axis = sorted({d for md in prep.data.values() for d in md.days})
    with pytest.raises(SystemExit) as info:
        kit.require_full_window(axis, axis[EMA_WINDOW - 1], EMA_WINDOW)
    assert info.value.code == kit.WINDOW_EXIT == 5
    kit.require_full_window(axis, d1_since(prep), EMA_WINDOW)


def test_지수_구성이_모자라면_rc6(syn, prep) -> None:  # type: ignore[no-untyped-def]
    keys = [k for first, last in prep.blocks for k in judged_keys(prep.data, prep.axis, first, last)]
    assert require_index_coverage(prep.data, keys)
    thin = {m: dict(list(v.items())[: len(v) // 3]) for m, v in syn.index_raw.items()}
    data = {m: build_market(prep.keys, m, syn.books[m], thin[m]) for m in ("KR", "US")}
    with pytest.raises(SystemExit) as info:
        require_index_coverage(data, keys)
    assert info.value.code == INDEX_EXIT


def test_자기_점검_배선(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """카나리·라벨 섞기 — 배선만(작은 예산). 통과 여부는 등록 값으로 러너가 본다."""
    from tools.trial_final_dfl import self_check

    ok, lines, gains = self_check("shuffle", (0,), hyper=replace(TINY, first_block=6), n_sessions=420, n_entities=60)
    assert isinstance(ok, bool) and lines[-1].startswith("라벨 섞기(합성)") and all(g >= 0 for g in gains)
    ok, lines, _g = self_check("canary", (0,), hyper=replace(TINY, first_block=6), n_sessions=420, n_entities=60,
                               margin=0.05)
    assert lines[-1].startswith("카나리(심은 신호, 여백 5.00%)") and "IC" in lines[-1]


def test_실자료_여백_굽기와_카나리는_등록_뒤에만() -> None:
    from tools.trial_final_dfl import main

    for cmd in (["shuffle"], ["canary"]):
        with pytest.raises(SystemExit):
            main(cmd)


# --------------------------------------------------------------------------- 고정값


def test_고정값은_등록_문서의_값이고_명령행으로_못_바꾼다() -> None:
    from tools.trial_final_dfl import HYPER, RANK_TAU, TARGET_EFF, build_parser

    assert (K, HOLD, LAMBDA, ALPHA, ETA, D1_GAP, D1_FIRST_BLOCK, TARGET_EFF, RANK_TAU) == \
        (24, 10, 0.5, 0.5, 0.5, 16, 10, 27.0, 0.05)
    assert CAPS == {"KR": 0.30, "US": 0.10}
    assert Hyper() == HYPER
    judge = build_parser()._subparsers._group_actions[0].choices["judge"]  # type: ignore[union-attr]
    flags = {o for a in judge._actions for o in a.option_strings}
    assert not flags & {"--alpha", "--eta", "--temp", "--lam", "--k", "--epochs", "--first-block"}
    doc = Path("docs/protocols/decision-focused-2026-10.md").read_text()
    for token in ("k = 24", "λ = 0.5", "α = 0.5", "η = 0.5", "간격 16", "유효 종목 수 27"):
        assert token in doc, f"등록 문서에 고정값 {token!r} 이 없다"
