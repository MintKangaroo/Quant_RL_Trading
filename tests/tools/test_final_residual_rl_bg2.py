"""시행 BG2(next-four ②) — BE2 기준선 · 잔차 회전 벌점 · 판정 ⑦, 그리고 **등록된 BG 경로가 한 바이트도 안 바뀌었는지**.

합성 자료다(그래서 "이긴다" 는 여기서 안 잰다). 지키는 것:
- κ = 0 이면 보상·학습·판정이 BG 와 **정확히** 같다(등록된 BG 재현성).
- κ 는 보상에 **잔차로** 들어간다 — δ = 0 이면 벌점도 정확히 0(행동과 무관한 큰 항 금지).
- 기준선이 C1 → BE2 로 실제로 바뀐다(BE1 이 순위를 움직인다), C1 경로는 `c1_average` 그대로다.
- ⑥ 이 BE2 를 대조로 쓰고, ⑦ 회전 ≤ BE2 × 1.2 가 **필수**다(①~⑥ 을 다 통과해도 ⑦ 에서 떨어지면 기각).
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import pytest
import torch

from tools.trial_final_residual_rl import (
    GATE_TURN_BG2,
    PROTOCOL,
    PROTOCOL_BG2,
    TURN_K_BG2,
    Bands,
    Rollout,
    TiltPolicy,
    baseline_scores,
    be2_average,
    build_parser,
    c1_average,
    episode,
    judge_bg2,
    obs_dim,
    protocol_of,
    require_registered_variant,
    seed_mean_pct,
    synthetic,
    train_one,
    trial_name,
    turnover_penalty,
)

COST = 0.0041


def _tilting_net(n_static: int, seed: int = 7) -> TiltPolicy:
    """0 이 아닌 기울기를 내는 망 — 마지막 층 0 초기화를 풀어야 회전 차이가 생긴다."""
    net = TiltPolicy(obs_dim(n_static))
    torch.manual_seed(seed)
    with torch.no_grad():
        net.mu.weight.normal_(0.0, 2.0)
        net.mu.bias.normal_(0.0, 0.5)
    return net


def _run(prep, net, span, *, seed=0, randomize=False, deterministic=True, cost=COST, **kw) -> Rollout:  # type: ignore[no-untyped-def]
    out = Rollout()
    episode(net, prep, span.start, span.stop - span.start, rng=np.random.default_rng(seed),
            gen=torch.Generator().manual_seed(seed), bands=Bands.default(), cost=cost,
            randomize=randomize, deterministic=deterministic, out=out, **kw)
    return out


# --------------------------------------------------------------------------- 회전 벌점


def test_회전_벌점은_k0이면_정확히_0이고_잔차다() -> None:
    assert turnover_penalty(1.3, 0.4, COST, 0.0) == 0.0
    assert turnover_penalty(1.3, 0.4, COST, 1.0) == pytest.approx(COST * 0.9)
    assert turnover_penalty(0.4, 1.3, COST, 1.0) == pytest.approx(-COST * 0.9), "대조보다 덜 돌리면 보상"
    assert turnover_penalty(0.7, 0.7, COST, 5.0) == 0.0, "같은 회전이면 벌점 없음(잔차)"
    assert turnover_penalty(1.0, 0.0, 2 * COST, 1.0) == pytest.approx(2 * turnover_penalty(1.0, 0.0, COST, 1.0)), \
        "κ 는 편도비용에 비례한다(도메인 무작위화 배수가 같이 흔든다)"


def test_k0_이면_보상이_BG_와_비트까지_같다() -> None:
    """등록된 BG 재현성 — 인자를 안 준 것과 k=0 을 준 것이 같은 롤아웃이어야 한다(무작위화 경로 포함)."""
    prep = synthetic(3, n_dec=40)
    net = _tilting_net(prep.n_static)
    for randomize in (False, True):
        a = _run(prep, net, range(0, 40), randomize=randomize, deterministic=not randomize)
        b = _run(prep, net, range(0, 40), randomize=randomize, deterministic=not randomize, turn_k=0.0)
        assert a.reward == b.reward
        assert a.turnover == b.turnover
        assert a.policy_net == b.policy_net


def test_k가_보상에_결정당_한번_잔차로_들어간다() -> None:
    prep = synthetic(4, n_dec=40)
    net = _tilting_net(prep.n_static)
    base = _run(prep, net, range(0, 40))
    pen = _run(prep, net, range(0, 40), turn_k=TURN_K_BG2)
    extra = np.array(base.turnover)                      # 정책이 **더** 낸 회전
    assert np.abs(extra).max() > 1e-4, "시험이 뜻을 가지려면 기울기가 회전을 바꿔야 한다"
    assert np.allclose(np.array(pen.reward), np.array(base.reward) - TURN_K_BG2 * COST * extra, atol=1e-15)
    # 판정 경로(일수익·회전)는 벌점과 무관하다 — 벌점은 **학습 신호**에만 들어간다
    assert pen.policy_net == base.policy_net and pen.daily == base.daily and pen.abs_turnover == base.abs_turnover


def test_0_기울기면_k가_있어도_보상이_0이다() -> None:
    """잔차 형태의 요점 — δ = 0 이면 정책·대조 회전이 같아 벌점도 0. 전체 |Δw| 에 걸면 여기서 깨진다."""
    prep = synthetic(5, n_dec=40)
    roll = _run(prep, TiltPolicy(obs_dim(prep.n_static)), range(0, 40), turn_k=TURN_K_BG2)
    assert np.allclose(roll.reward, 0.0, atol=1e-12)


def test_k0_학습은_기본_학습과_같다() -> None:
    """train_one(turn_k=0) 이 기본 경로와 같은 체크포인트를 고른다 — BG 판정 재현성."""
    prep = synthetic(6, n_dec=60)
    fit, valid = range(0, 48), range(48, 60)
    a = train_one(prep, fit, valid, seed=0, updates=4, bands=Bands.default(), cost=COST, eval_every=2, verbose=False)
    b = train_one(prep, fit, valid, seed=0, updates=4, bands=Bands.default(), cost=COST, eval_every=2, verbose=False,
                  turn_k=0.0)
    assert (a.best_step, a.best_edge) == (b.best_step, b.best_edge)
    for (ka, va), (kb, vb) in zip(a.net.state_dict().items(), b.net.state_dict().items(), strict=True):
        assert ka == kb and torch.equal(va, vb)
    assert [x["reward"] for x in a.log] == [x["reward"] for x in b.log]


def test_k가_있으면_학습_신호가_달라진다() -> None:
    prep = synthetic(6, n_dec=60)
    fit, valid = range(0, 48), range(48, 60)
    a = train_one(prep, fit, valid, seed=0, updates=3, bands=Bands.default(), cost=COST, eval_every=3, verbose=False)
    b = train_one(prep, fit, valid, seed=0, updates=3, bands=Bands.default(), cost=COST, eval_every=3, verbose=False,
                  turn_k=5.0)
    assert [x["reward"] for x in a.log] != [x["reward"] for x in b.log]


# --------------------------------------------------------------------------- 기준선


def _preds(seed: int, *, flip: bool = False, drop: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for market in ("KR", "US"):
        for d in pd.bdate_range("2024-01-02", periods=5):
            names = [f"{market}:{i:03d}" for i in range(30 - drop)]
            base = np.arange(len(names), dtype=float)
            val = (-base if flip else base) + rng.normal(0.0, 3.0, len(names))
            rows += [{"entity_id": e, "session": d.date(), "market": market, "pred": float(v) * (seed + 1)}
                     for e, v in zip(names, val, strict=True)]
    return pd.DataFrame(rows)


def test_시드_평균_백분위는_c1_average_와_같은_값이다() -> None:
    """흘려서 평균하는 판(BE1 메모리 절약)이 BG 의 `c1_average` 와 같은 뜻인지 — 한 시드에만 있는 행 포함."""
    frames = {0: _preds(0), 1: _preds(1, drop=3), 2: _preds(2)}
    keys = ["entity_id", "session", "market"]
    a = c1_average(frames).sort_values(keys).reset_index(drop=True)
    b = seed_mean_pct(frames.values()).sort_values(keys).reset_index(drop=True)
    assert len(a) == len(b)
    assert (a[keys].astype(str).to_numpy() == b[keys].astype(str).to_numpy()).all()
    assert np.allclose(a["pred"].to_numpy(), b["pred"].to_numpy(), atol=1e-12)


def test_기준선_C1은_BG_그대로_BE2는_BE1이_순위를_움직인다() -> None:
    c1 = {s: _preds(s) for s in range(3)}
    be1 = {s: _preds(10 + s, flip=True) for s in range(3)}     # BE1 은 C1 과 반대로 줄 세운다
    got_c1, label_c1 = baseline_scores("C1", c1)
    pd.testing.assert_frame_equal(got_c1, c1_average(c1))
    assert label_c1.startswith("C1 예측(시드 3개 순위평균)"), "BG 로그 문구가 바뀌면 안 된다"

    got_be2, label_be2 = baseline_scores("BE2", c1, (f for f in be1.values()))
    assert "BE2" in label_be2
    want = be2_average(be1.values(), c1.values())
    pd.testing.assert_frame_equal(got_be2, want)
    keys = ["entity_id", "session", "market"]
    merged = got_c1.merge(got_be2, on=keys, suffixes=("_c1", "_be2"))
    assert len(merged) == len(got_c1), "두 모델이 같은 행을 덮으면 BE2 는 행을 잃지 않는다"
    # 순위 평균이므로 BE2 는 C1 과 달라야 하고(반대 순서의 BE1 이 끌어당긴다), 세션·시장 안 값이 (0, 1] 이다
    assert not np.allclose(merged["pred_c1"], merged["pred_be2"])
    assert got_be2["pred"].between(0.0, 1.0).all()
    top_c1 = set(got_c1.sort_values("pred").groupby(["market", "session"]).tail(5)["entity_id"])
    top_be2 = set(got_be2.sort_values("pred").groupby(["market", "session"]).tail(5)["entity_id"])
    assert top_c1 != top_be2, "기준선이 바뀌었다면 상위 명단도 바뀌어야 한다"


def test_BE2_기준선은_BE1이_없으면_멈춘다() -> None:
    with pytest.raises(SystemExit):
        baseline_scores("BE2", {0: _preds(0)}, None)
    with pytest.raises(SystemExit):
        baseline_scores("C9", {0: _preds(0)})


# --------------------------------------------------------------------------- 판정 ⑥·⑦


def _m(ann: float, turn: float, *, box: float | None = None, rally: float | None = None) -> dict[str, float]:
    return {"ann": ann, "box_ann": ann if box is None else box, "rally_ann": ann if rally is None else rally,
            "mdd": -0.20, "turn": turn, "ic": 0.05, "sharpe": 1.0}


def test_판정_모두_통과면_채택_후보() -> None:
    from tools import final_round_kit as kit

    res = {s: _m(0.30, 15.0) for s in range(3)}
    c0 = {s: _m(0.20, 14.0) for s in range(5)}
    be2 = {0: _m(0.28, 14.0)}
    lines, verdict = judge_bg2(kit, res, c0, be2)
    assert verdict.startswith("채택 후보")
    assert any("⑥ 대 BE2" in ln for ln in lines) and not any("⑥ 대 C1" in ln for ln in lines)
    assert any(ln.startswith("⑦ 회전") for ln in lines)
    assert lines[-1] == f"판정: {verdict}" and sum(ln.startswith("판정:") for ln in lines) == 1


def test_회전이_BE2의_1_2배를_넘으면_나머지가_다_통과해도_기각() -> None:
    from tools import final_round_kit as kit

    res = {s: _m(0.30, 14.0 * GATE_TURN_BG2 + 0.1) for s in range(3)}
    c0 = {s: _m(0.20, 30.0) for s in range(5)}                 # ⑤(대 C0 회전)는 넉넉히 통과
    be2 = {0: _m(0.28, 14.0)}
    _lines, verdict = judge_bg2(kit, res, c0, be2)
    assert verdict == "기각"
    # 한도 안쪽이면 통과 — 경계 확인
    ok = {s: _m(0.30, 14.0 * GATE_TURN_BG2 - 0.1) for s in range(3)}
    assert judge_bg2(kit, ok, c0, be2)[1].startswith("채택 후보")


def test_대_BE2_1pp_미만이면_기각이지_C1_후보가_아니다() -> None:
    """kit 의 '①~⑤ 만 → C1 채택 후보' 갈래는 BG2 에 뜻이 없다(BE2 가 이미 후보)."""
    from tools import final_round_kit as kit

    res = {s: _m(0.30, 14.0) for s in range(3)}
    c0 = {s: _m(0.20, 14.0) for s in range(5)}
    be2 = {0: _m(0.295, 14.0)}
    _lines, verdict = judge_bg2(kit, res, c0, be2)
    assert verdict == "기각"


# --------------------------------------------------------------------------- CLI · 등록 관문


def test_CLI_기본값은_등록된_BG_그대로다() -> None:
    for cmd in ("smoke", "canary", "pilot", "judge"):
        args = build_parser().parse_args([cmd])
        assert args.baseline == "C1" and args.turnover_kappa == 0.0
        assert protocol_of(args) == PROTOCOL and trial_name(args) == "BG"
    args = build_parser().parse_args(["judge", "--baseline", "BE2", "--turnover-kappa", str(TURN_K_BG2)])
    assert protocol_of(args) == PROTOCOL_BG2 and trial_name(args) == "BG2"


@pytest.mark.parametrize(("baseline", "k", "ok"), [
    ("C1", 0.0, True), ("C1", 1.0, False), ("BE2", TURN_K_BG2, True), ("BE2", 0.0, False), ("BE2", 2.0, False),
])
def test_실자료는_등록된_조합만_돈다(baseline: str, k: float, ok: bool) -> None:
    """실자료에서 κ 를 바꿔 돌리는 것은 판정 창에서 κ 를 고르는 것과 같다 — 막는다. 합성은 무엇이든 돈다."""
    real = argparse.Namespace(synthetic=False, baseline=baseline, turnover_kappa=k)
    if ok:
        require_registered_variant(real)
    else:
        with pytest.raises(SystemExit):
            require_registered_variant(real)
    require_registered_variant(argparse.Namespace(synthetic=True, baseline=baseline, turnover_kappa=k))


def test_BG2_는_등록_플래그_없이_안_돈다() -> None:
    from tools.trial_final_residual_rl import main

    for cmd in ("canary", "pilot", "judge"):
        with pytest.raises(SystemExit):
            main([cmd, "--baseline", "BE2", "--turnover-kappa", str(TURN_K_BG2)])


def test_BG2_스모크는_합성으로_돈다() -> None:
    from tools.trial_final_residual_rl import main

    assert main(["smoke", "--updates", "2", "--decisions", "40", "--baseline", "BE2",
                 "--turnover-kappa", str(TURN_K_BG2)]) == 0
