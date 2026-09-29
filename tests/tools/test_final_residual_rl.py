"""시행 BG(잔차 RL) — 행동 제약·결정론·미래 안 봄·보상 계약·관문. 합성 자료다(그래서 "이긴다"는 여기서 안 잰다).

이 파일이 지키는 것은 `docs/rl-postmortem.md` 의 실패 다섯이 **구조적으로 못 돌아오게** 하는 불변식들이다 —
외울 자유도(현금·종목·지연)가 없다 · 관측이 O(1) 이다 · 비용을 두 번 빼지 않는다 · 0 기울기는 정확히 동일가중이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch

from tools.trial_final_residual_rl import (
    EVERY,
    TILT_MAX,
    TOP_N,
    Bands,
    Prepared,
    Rollout,
    TiltPolicy,
    build_obs,
    collect,
    episode,
    evaluate_span,
    needed_steps,
    obs_dim,
    oracle_column,
    pilot_gate,
    project_tilt,
    saturated,
    step_reward,
    synthetic,
    train_one,
)

COST = 0.0041


def _run(prep: Prepared, net: TiltPolicy, span: range, *, seed: int = 0, randomize: bool = False,
         deterministic: bool = False, cost: float = COST) -> Rollout:
    out = Rollout()
    episode(net, prep, span.start, span.stop - span.start, rng=np.random.default_rng(seed),
            gen=torch.Generator().manual_seed(seed), bands=Bands.default(), cost=cost,
            randomize=randomize, deterministic=deterministic, out=out)
    return out


# --------------------------------------------------------------------------- 행동 제약


@pytest.mark.parametrize("n", [2, 5, 24])
def test_기울기는_합0_상한5pp_비중하한0_을_정확히_지킨다(n: int) -> None:
    """이 셋이 BG 설계의 전부다 — 하나라도 새면 정책이 '종목 선택'·'현금' 을 되찾아 외울 자리가 생긴다."""
    rng = np.random.default_rng(0)
    base = np.full(n, 1.0 / n)
    for _ in range(300):
        raw = np.tanh(rng.normal(0.0, 3.0, n))       # 일부러 포화시킨 입력
        d = project_tilt(raw, base)
        assert abs(float(d.sum())) < 1e-9, "합이 0 이 아니면 현금·레버리지가 생긴다"
        assert d.max() <= TILT_MAX + 1e-12
        assert (base + d).min() >= -1e-12, "비중 하한 0 위반 = 공매도"


def test_하한은_비중0에서_나온다() -> None:
    """1/24 = 4.17%p 라 −5%p 가 아니라 −4.17%p 에서 먼저 걸린다. 이걸 놓치면 음수 비중이 조용히 들어온다."""
    base = np.full(TOP_N, 1.0 / TOP_N)
    raw = np.zeros(TOP_N)
    raw[0] = -1.0                                        # "이 종목을 최대로 덜 담아라"
    d = project_tilt(raw, base)
    assert d[0] == pytest.approx(-1.0 / TOP_N, abs=1e-9), "−5%p 가 아니라 −4.17%p 에서 비중 0 이 먼저 걸린다"
    assert d[0] > -TILT_MAX
    assert base[0] + d[0] == pytest.approx(0.0, abs=1e-12)
    assert saturated(d, base) == 1, "한계에 붙은 것을 되밀어 올리면 이 계수가 거짓이 된다"


def test_한계붙음_계수는_실제로_붙은_것만_센다() -> None:
    base = np.full(4, 0.25)
    assert saturated(np.array([TILT_MAX, -TILT_MAX, 0.0, 0.0]), base) == 2
    assert saturated(np.zeros(4), base) == 0


# --------------------------------------------------------------------------- 보상 계약


def test_보상은_비용을_두_번_빼지_않는다() -> None:
    """순수익에 이미 든 비용을 또 빼면 정책이 배우는 첫 번째는 '덜 행동하기' 다(1회차 현금 도망의 모양)."""
    r = step_reward(0.010, 0.004, 0.0, 0.0, 0.0, 0.0, Bands.default())
    assert r == pytest.approx(0.010 - 0.004), "낙폭이 안 깊어졌으면 보상 = 순수익 차이 그대로"


def test_공통_낙폭_벌점은_상쇄된다() -> None:
    """같은 낙폭을 둘이 같이 겪으면 벌점은 0 — 행동과 무관한 큰 항이 advantage 를 덮는 것을 막는다."""
    b = Bands.default()
    r = step_reward(-0.02, -0.02, 0.10, 0.25, 0.10, 0.25, b)
    assert r == pytest.approx(0.0, abs=1e-12)
    깊게 = step_reward(-0.03, -0.02, 0.20, 0.25, 0.20, 0.22, b)
    assert 깊게 < -0.01, "정책이 **더** 깊게 파면 벌점이 남아야 한다"


def test_밴드는_allocator_reward_와_같은_계단이다() -> None:
    """임계치의 단일 소스는 config 다(불변식 10). 두 곳이 갈리면 학습과 화면이 다른 12/22/30 을 든다."""
    from quant_rl_trading.allocator.reward import RewardParams, penalty_weight

    bands = Bands.default()
    params = RewardParams(drawdown_free=bands.free, drawdown_warn=bands.warn, drawdown_hard=bands.hard,
                          w_free=bands.w_free, w_mid=bands.w_mid, w_hot=bands.w_hot,
                          terminal_penalty=-1.0, normalize_returns="none")
    for depth in (0.0, 0.05, 0.119, 0.12, 0.21, 0.22, 0.29, 0.31, 0.5):
        assert bands.weight(depth) == penalty_weight(depth, params=params)


def test_0_기울기면_정책이_대조와_완전히_같고_보상이_0_이다() -> None:
    """잔차 정책의 출발점 검증. 마지막 층 0 초기화가 깨지면 여기서 먼저 걸린다."""
    prep = synthetic(0, n_dec=30)
    net = TiltPolicy(obs_dim(prep.n_static))
    roll = _run(prep, net, range(0, 30), deterministic=True)
    assert len(roll.reward) == 30
    assert np.allclose(roll.reward, 0.0, atol=1e-12)
    assert np.allclose(np.array(roll.policy_net), np.array(roll.ctrl_net), atol=1e-12)
    assert np.allclose(np.array(roll.tilt_abs), 0.0, atol=1e-12)


def test_KL_벌점은_0_기울기에서_정확히_0_이다() -> None:
    net = TiltPolicy(obs_dim(3))
    mask = torch.ones(2, TOP_N, dtype=torch.bool)
    mu = torch.zeros(2, TOP_N)
    assert float(net.kl_to_flat(mu, mask).detach()) == pytest.approx(0.0, abs=1e-9)
    assert float(net.kl_to_flat(mu + 0.5, mask).detach()) > 0.0


# --------------------------------------------------------------------------- 결정론


def test_시드가_같으면_같은_행동과_같은_보상() -> None:
    """'어제 왜 그 종목을 더 담았나' 를 답할 수 없으면 실전에 못 넣는다."""
    prep = synthetic(1, n_dec=40)
    net = TiltPolicy(obs_dim(prep.n_static))
    torch.manual_seed(3)
    for p in net.parameters():
        p.data.add_(torch.randn_like(p) * 0.05)          # μ ≠ 0 인 상태로 비교한다
    a = _run(prep, net, range(0, 40), seed=7)
    b = _run(prep, net, range(0, 40), seed=7)
    assert np.allclose(np.stack(a.u), np.stack(b.u))
    assert np.allclose(a.reward, b.reward)


def test_학습은_시드마다_갈린다() -> None:
    """갈리지 않으면 난수가 안 들어간 것이고 시드 흩어짐을 잴 수 없다(seed-noise 교훈)."""
    prep = synthetic(2, n_dec=60, alpha=0.02)
    a = train_one(prep, range(0, 45), range(45, 60), seed=0, updates=4, bands=Bands.default(), cost=COST,
                  eval_every=2, verbose=False)
    b = train_one(prep, range(0, 45), range(45, 60), seed=1, updates=4, bands=Bands.default(), cost=COST,
                  eval_every=2, verbose=False)
    assert a.best_edge != b.best_edge


# --------------------------------------------------------------------------- 미래를 안 본다


def test_나중_수익을_바꿔도_앞의_결정이_안_바뀐다() -> None:
    """지연 규약 검증 — 결정 i 는 그 뒤 10세션으로만 채점되고, 그 밖의 미래는 관측에 닿지 않는다."""
    prep = synthetic(3, n_dec=40)
    net = TiltPolicy(obs_dim(prep.n_static))
    torch.manual_seed(5)
    for p in net.parameters():
        p.data.add_(torch.randn_like(p) * 0.05)
    before = _run(prep, net, range(0, 10), seed=0)
    rets = prep.rets.copy()
    rets[10:] = rets[10:] * 5.0 + 0.1            # 결정 10 이후를 크게 흔든다
    static = prep.static.copy()
    static[10:] = static[10:] * -3.0
    tainted = Prepared(prep.days, prep.slots, prep.mask, static, rets, prep.ctx, prep.feat_names, prep.entities)
    after = _run(tainted, net, range(0, 10), seed=0)
    assert np.allclose(np.stack(before.u), np.stack(after.u))
    assert np.allclose(before.reward, after.reward)


def test_관측은_그날_칸만_본다_전역_통계가_없다() -> None:
    """자료에서 추정한 평균·표준편차를 쓰면 누수가 생기고 실전에서 같은 값을 못 만든다(불변식 5)."""
    prep = synthetic(4, n_dec=30)
    mask = prep.mask[5]
    args = {"weights": np.full(TOP_N, 1.0 / TOP_N), "held_days": np.zeros(TOP_N), "depth": 0.0,
            "progress": 0.0, "mask": mask}
    a = build_obs(prep, 5, **args)
    static = prep.static.copy()
    static[6:] = static[6:] * 100.0
    other = Prepared(prep.days, prep.slots, prep.mask, static, prep.rets, prep.ctx, prep.feat_names, prep.entities)
    assert np.allclose(a, build_obs(other, 5, **args))


def test_관측_칸은_전부_O1_스케일이다() -> None:
    """2회차 r5 를 죽인 '환율 원값 1,478' 이 구조적으로 못 들어오는지 본다 — 고정 상수 스케일링뿐이다."""
    prep = synthetic(5, n_dec=20)
    static = prep.static * 50.0                  # 피처가 미쳐도 관측은 묶여 있어야 한다
    wild = Prepared(prep.days, prep.slots, prep.mask, static, prep.rets * 20.0,
                    prep.ctx * 30.0, prep.feat_names, prep.entities)
    obs = build_obs(wild, 3, weights=np.full(TOP_N, 1.0), held_days=np.full(TOP_N, 500.0),
                    depth=0.9, progress=1.0, mask=prep.mask[3])
    assert np.abs(obs).max() <= 5.0, "어떤 칸도 O(1) 을 벗어나지 못한다 — 마지막 잠금이 있다"
    assert np.isfinite(obs).all()


def test_가려진_슬롯은_관측이_0_이다() -> None:
    prep = synthetic(6, n_dec=10)
    mask = prep.mask[2].copy()
    mask[5:] = False
    obs = build_obs(prep, 2, weights=np.full(TOP_N, 1.0 / 5), held_days=np.zeros(TOP_N), depth=0.0,
                    progress=0.0, mask=mask)
    assert np.allclose(obs[5:], 0.0)


# --------------------------------------------------------------------------- 과적합 억제 장치


def test_도메인_무작위화가_실제로_달라진_경로를_만든다() -> None:
    prep = synthetic(7, n_dec=40)
    net = TiltPolicy(obs_dim(prep.n_static))
    plain = _run(prep, net, range(0, 40), seed=1, randomize=False)
    rand = _run(prep, net, range(0, 40), seed=1, randomize=True)
    assert not np.allclose(plain.ctrl_net, rand.ctrl_net), "잡음·부분표본이 대조 장부까지 흔들어야 한다"


def test_에피소드_시작점이_무작위다() -> None:
    """1회차가 같은 시작점을 714회 반복해 외운 실패를 막는 장치."""
    prep = synthetic(8, n_dec=200)
    roll = collect(TiltPolicy(obs_dim(prep.n_static)), prep, range(0, 200),
                   rng=np.random.default_rng(0), gen=torch.Generator().manual_seed(0),
                   bands=Bands.default(), cost=COST, episodes=6, randomize=True)
    assert len(roll.done) == 6 * min(25, 200)
    assert sum(roll.done) == 6, "에피소드마다 마지막 결정에만 done 이 선다"


# --------------------------------------------------------------------------- 파일럿 관문


def test_배울_것이_없으면_파일럿_관문이_막는다() -> None:
    """종목별 수익이 전부 같으면 기울기로 얻을 것이 0 이고 비용만 남는다 — 관문은 '> 0' 이라 반드시 막아야 한다."""
    prep = synthetic(9, n_dec=60)
    same = np.repeat(prep.rets.mean(axis=2, keepdims=True), TOP_N, axis=2)
    flat = Prepared(prep.days, prep.slots, prep.mask, prep.static, same, prep.ctx, prep.feat_names, prep.entities)
    ok, runs = pilot_gate(flat, range(0, 45), range(45, 60), bands=Bands.default(), cost=COST,
                          updates=3, seeds=(0,))
    assert not ok
    assert max(r.best_edge for r in runs) <= 0.0


def test_배관은_살아_있다_정답을_흘리면_우위가_커진다() -> None:
    """카나리의 축소판 — 여기서도 안 커지면 신호가 아니라 코드를 고칠 차례다(rl-training §0)."""
    prep = synthetic(10, n_dec=160, alpha=0.05)
    run = train_one(prep, range(0, 120), range(120, 160), seed=0, updates=40, bands=Bands.default(),
                    cost=COST, eval_every=10, verbose=False)
    _roll, stats = evaluate_span(run.net, prep, range(120, 160), bands=Bands.default(), cost=COST)
    assert stats["edge"] > 0.0
    assert run.best_edge > 0.0


def test_필요_스텝은_1_over_r제곱으로_는다() -> None:
    """'예산을 안 찍고 안 배운다를 말하지 않는다'(rl-postmortem §1)의 산수."""
    assert needed_steps(0.043) == pytest.approx(110_000, rel=1e-6)
    assert needed_steps(0.086) == pytest.approx(27_500, rel=1e-6)
    assert needed_steps(0.0) == float("inf")


def test_정답_칸은_슬롯마다_부호다() -> None:
    prep = synthetic(11, n_dec=20)
    ora = oracle_column(prep)
    assert ora.shape == (prep.n_dec, TOP_N)
    assert set(np.unique(ora)) <= {-1.0, 0.0, 1.0}


# --------------------------------------------------------------------------- 관문(사전등록)


@pytest.mark.parametrize("cmd", ["canary", "pilot", "judge"])
def test_사전등록_플래그_없이는_안_돈다(cmd: str) -> None:
    from tools.trial_final_residual_rl import main

    with pytest.raises(SystemExit):
        main([cmd])


def test_스모크는_아무_때나_돈다() -> None:
    from tools.trial_final_residual_rl import main

    assert main(["smoke", "--updates", "2", "--decisions", "40"]) == 0


def test_에피소드_길이는_250세션_이상이다() -> None:
    """reward-and-risk §2 — MDD 는 경로 통계라서 짧은 에피소드에선 30% 벽이 죽은 항이 된다."""
    from tools.trial_final_residual_rl import EPISODE_DECISIONS

    assert EPISODE_DECISIONS * EVERY >= 250


def test_가치_손실은_정책_파라미터를_건드리지_않는다() -> None:
    """2회차 r5 의 뿌리 — 가치 그래디언트가 인코더로 흘러 정책 군에 섞이면 분리 클리핑이 이름뿐이 된다."""
    net = TiltPolicy(obs_dim(3))
    obs = torch.randn(4, TOP_N, obs_dim(3))
    mask = torch.ones(4, TOP_N, dtype=torch.bool)
    _mu, _ls, value = net(obs, mask)
    value.pow(2).mean().backward()
    for name, p in net.named_parameters():
        if name.startswith("value."):
            assert p.grad is not None and float(p.grad.abs().sum()) > 0.0, f"{name} 은 배워야 한다"
        else:
            assert p.grad is None or float(p.grad.abs().sum()) == 0.0, f"{name} 에 가치 그래디언트가 새어 들어왔다"


def test_회전은_종목으로_잰다_슬롯_번호가_아니다() -> None:
    """슬롯 축에 비중을 남기면 |w − prev| 가 엉뚱한 두 종목을 견주고 회전이 거짓이 된다 —
    회전은 알파의 대부분을 먹는 항이라(교훈 5) 여기서 틀리면 판정 전체가 거짓이다."""
    from tools.trial_final_residual_rl import Book

    book = Book()
    names = np.array([10, 11, 12, -1])
    w = np.array([1 / 3, 1 / 3, 1 / 3, 0.0])
    book.rebalance(names, w, np.zeros((1, 4)), 0.0)
    assert book.turnover == pytest.approx(1.0), "첫 진입은 전량 매수"
    # 같은 종목인데 **슬롯 순서만** 뒤집으면 회전은 0 이어야 한다
    book.rebalance(np.array([12, 11, 10, -1]), w, np.zeros((1, 4)), 0.0)
    assert book.turnover == pytest.approx(0.0, abs=1e-12), "순서가 바뀐 것은 거래가 아니다"
    # 한 종목이 명단에서 빠지면 그 비중은 전량 매도로 센다
    book.rebalance(np.array([12, 11, 99, -1]), w, np.zeros((1, 4)), 0.0)
    assert book.turnover == pytest.approx(2 / 3, abs=1e-9)


def test_명단이_굴러도_0_기울기면_보상이_0_이다() -> None:
    """명단 교체·회전이 섞인 상태에서도 잔차가 정확히 0 이어야 한다(두 장부가 같은 비용을 낸다)."""
    prep = synthetic(12, n_dec=50)
    roll = _run(prep, TiltPolicy(obs_dim(prep.n_static)), range(0, 50), deterministic=True)
    assert np.allclose(roll.reward, 0.0, atol=1e-12)
    assert max(roll.turnover) == pytest.approx(0.0, abs=1e-12), "기울기가 0 이면 정책이 더 낸 회전도 0"


def test_일수익_시계열이_세션마다_한_칸이다() -> None:
    """판정 지표(샤프·MDD·β·국면)는 결정 단위가 아니라 **일수익**으로 잰다 — 대조군과 같은 함수를 쓰려면 필요하다."""
    from tools.trial_final_residual_rl import _series

    prep = synthetic(13, n_dec=20)
    roll = _run(prep, TiltPolicy(obs_dim(prep.n_static)), range(0, 20), deterministic=True)
    daily = _series(roll.daily)
    assert len(daily) == 20 * EVERY, "결정 20회 × 보유 10세션"
    assert daily.index.is_monotonic_increasing
    assert np.allclose(daily.to_numpy(), _series(roll.ctrl_dailyseries).to_numpy(), atol=1e-12), \
        "0 기울기면 두 장부의 일수익이 같다"


def test_판정은_두_시장_합동이_기본이다() -> None:
    """공통 틀 §자료 — 국장+미장 합동이다. 기본값이 KR 단독이면 미장이 조용히 사라진다."""
    from tools.trial_final_residual_rl import build_parser

    args = build_parser().parse_args(["judge", "--i-registered"])
    assert args.markets == "KR,US"
    assert args.synthetic is False, "판정은 합성 자료로 돌지 않는다"


def test_내_지표_경로가_judge_필수_키를_다_낸다() -> None:
    """리드 지시(2026-09-27) — kit.judge 가 필수 키가 빠지면 ValueError 로 멈춘다. 내 경로가 일곱을 다 내는지 본다.

    빠진 키는 `_mean` 에서 nan 이 되고 비교가 False 가 되어 **관문이 조용히 떨어진다**(교훈 6).
    일수익 구간이 **두 국면을 걸쳐야** `rally_ann` 이 nan 이 아니다 — 이 테스트를 처음 썼을 때 합성 자료가
    2024-04 에서 끝나 급등 국면이 비었고, `require_keys` 가 그걸 잡았다. 실자료 판정 창(2022-07~2026-06)은
    둘 다 있지만, 창을 좁혀 돌리는 사람이 조용히 떨어지지 않게 하는 것이 이 관문의 일이다.
    """
    from tools.trial_final_residual_rl import JUDGE_KEYS, _metrics, _series, require_keys
    from tools.trial_ranker_ensemble import BOX_END

    prep = synthetic(14, n_dec=60)
    roll = _run(prep, TiltPolicy(obs_dim(prep.n_static)), range(0, 60), deterministic=True)
    raw = _series(roll.daily)
    # BOX_END 를 가운데 두고 다시 붙인다 — 값이 아니라 **국면 두 개가 있는지**를 보는 테스트다.
    span = pd.bdate_range(pd.Timestamp(BOX_END) - pd.Timedelta(days=int(len(raw))), periods=len(raw))
    daily = pd.Series(raw.to_numpy(), index=[d.date() for d in span])
    rng = np.random.default_rng(0)
    bench = pd.Series(rng.normal(0.0004, 0.01, len(daily)), index=daily.index)   # 오르는 날·내리는 날 둘 다

    class _Book:                       # MarketBook 의 bench 만 쓴다
        pass

    _Book.bench = bench                # type: ignore[attr-defined]
    m = _metrics(daily, _Book(), turn=1.2, ic=0.05)
    assert set(JUDGE_KEYS) <= set(m), f"빠진 키 {set(JUDGE_KEYS) - set(m)}"
    require_keys("테스트", m)          # 유한성까지 — 여기서 안 터져야 한다
    assert m["ic"] == 0.05, "ic 는 C0 의 값을 그대로 들고 간다(ΔIC = 0)"
    assert m["turn"] == 1.2


def test_판정_키가_비면_조용히_지나가지_않는다() -> None:
    from tools.trial_final_residual_rl import require_keys

    with pytest.raises(SystemExit):
        require_keys("빈 지표", {"ann": 0.1})
    with pytest.raises(SystemExit):
        require_keys("nan 지표", dict.fromkeys(
            ("ann", "sharpe", "box_ann", "rally_ann", "mdd", "turn", "ic"), float("nan")))


class _FakeBook:
    """MarketBook 의 필요한 칸만 — prepare_market 은 ret·trad·bench·cost 만 본다."""

    def __init__(self, ret: pd.DataFrame) -> None:
        self.market, self.ret, self.trad, self.cost = "US", ret, None, 0.0025
        self.bench = pd.Series(0.0003, index=ret.index)
        self.fund = None


def test_미장_장부는_미장_세션을_다_덮는다() -> None:
    """리드 지시(2026-09-27) — kit 의 `sessions` 는 **국장 세션**이다. 그걸 미장 축으로 쓰면 국장 휴장일에만
    열린 미장 세션이 조용히 빠지고, 미장 슬리브의 보유일이 짧아져 비용·드리프트가 과소 계상된다.

    BG 는 시장마다 따로 굽고 축이 `wide.index ∩ book.ret.index`(둘 다 그 시장 달력)임을 여기서 고정한다.
    """
    from tools.trial_final_residual_rl import prepare_market

    days = [d.date() for d in pd.bdate_range("2024-01-01", periods=60)]
    kr_holidays = {days[7], days[19], days[33]}          # 국장만 쉬는 날 — 미장은 열렸다
    names = [f"U{i}" for i in range(30)]
    rng = np.random.default_rng(0)
    wide = pd.DataFrame(rng.normal(0, 1, (len(days), len(names))), index=days, columns=names)
    ret = pd.DataFrame(rng.normal(0.0004, 0.01, (len(days), len(names))), index=days, columns=names)
    panel = pd.DataFrame({"session": days * len(names),
                          "entity_id": [e for e in names for _ in days]}).assign(market="US")
    preps = prepare_market("US", wide, _FakeBook(ret), panel, {})

    covered = {d for p in preps for w in p.hold_days for d in w}
    assert kr_holidays <= covered, f"국장 휴장일의 미장 세션이 빠졌다: {sorted(kr_holidays - covered)}"
    # 창 끝 EVERY−1 세션은 보유 구간을 못 채우므로 결정이 안 선다. 그 밖은 다 덮어야 한다.
    assert len(covered) >= len(days) - EVERY, f"덮은 세션 {len(covered)} / 전체 {len(days)}"
    assert len(preps) == EVERY, "위상 10벌"


def test_예산_소진은_조기_종료가_아니다() -> None:
    """공통 틀 담당의 off-by-one 지적과 같은 자리 — 마지막 평가가 최고면 '조기 종료' 가 아니라 '예산 부족' 이다.
    반대로 읽으면 과적합 진단이 뒤집힌다."""
    from tools.trial_final_residual_rl import Trained

    net = TiltPolicy(obs_dim(3))
    assert Trained(net, 10, 0.1, [], None, 10).budget_exhausted is True
    assert Trained(net, 4, 0.1, [], None, 10).budget_exhausted is False
    assert Trained(net, 0, 0.0, [], None, 0).budget_exhausted is False, "안 돌린 것은 소진이 아니다"


def test_판정은_위상_0_만_쓴다() -> None:
    """열 벌 중 좋은 위상을 고르는 자유도가 남으면 이 회차에서 가장 비싼 과적합 경로다(공통 틀 담당 지적)."""
    import inspect

    from tools.trial_final_residual_rl import cmd_judge

    src = inspect.getsource(cmd_judge)
    assert "inp.preps[0]" in src, "판정 일수익은 위상 0(실제 장부)에서만 낸다"
    assert "phase_spread" in src, "위상 간 흩어짐은 기준이 아니라 기록으로 남긴다"


def _ctrl_frame(days: list, market: str = "KR") -> pd.DataFrame:
    return pd.DataFrame({"entity_id": ["A"] * len(days), "session": days,
                         "market": [market] * len(days), "pred": 0.5})


def test_대조군이_채점받지_않는_세션을_채점하지_않는다() -> None:
    """블록에 못 든 꼬리 구간까지 에피소드를 늘리면 **판정에 안 쓰는 구간으로 보상을 받는다** —
    "학습창 성과를 판정에 쓰지 않는다" 는 등록 규칙이 조용히 깨지는 자리(공통 틀 담당의 반열림 경계 지적)."""
    from tools.trial_final_residual_rl import require_same_span

    days = [d.date() for d in pd.bdate_range("2024-01-01", periods=40)]
    ctrls = {"C0": {0: _ctrl_frame(days)}, "C1": {0: _ctrl_frame(days)}}
    line = require_same_span(days[:35], ctrls, "KR")     # BG 가 더 적게 쓰는 것은 괜찮다(창 끝)
    assert "채점 구간" in line and "못 쓴 5" in line
    with pytest.raises(SystemExit):                      # BG 가 **더 많이** 쓰면 멈춘다
        require_same_span([*days, (pd.Timestamp(days[-1]) + pd.Timedelta(days=7)).date()], ctrls, "KR")


def test_워밍업_길이는_관측이_뒤돌아보는_만큼이다() -> None:
    """0 으로 채워진 창은 아무 경고도 내지 않는다 — rank-gauss 뒤 0 은 '순위 중앙' 으로 보인다."""
    from tools.trial_final_residual_rl import WARMUP_SESSIONS

    assert WARMUP_SESSIONS >= 20, "지수 문맥이 20세션을 뒤돌아본다"


def test_채점되는_결정은_전부_안_본_구간이다() -> None:
    """이 회차에서 가장 비싼 오염 — 앞 80%로 배우고 100%를 채점받으면 **자기 학습 구간을 채점받는다.**
    C0·C1 은 블록마다 앞만 보고 예측하는 워크포워드라, 그 구조를 안 맞추면 비교가 같은 것을 재지 않는다.
    """
    from tools.trial_final_residual_rl import GAP_DECISIONS, walk_folds

    for n in (40, 123, 200):
        folds = walk_folds(n)
        assert folds, f"결정 {n}회에서 폴드를 못 만들었다"
        for fit, judge in folds:
            assert fit.stop + GAP_DECISIONS <= judge.start, "적합 끝과 채점 시작 사이에 퍼지가 없다"
            assert set(range(fit.start, fit.stop)).isdisjoint(range(judge.start, judge.stop))
        spans = [j for _f, j in folds]
        assert all(a.stop == b.start for a, b in zip(spans, spans[1:], strict=False)), "채점 구간이 이어져야 한다"
        assert spans[-1].stop == n, "마지막 결정까지 채점한다"
        assert spans[0].start > 0, "앞쪽 일부는 적합에만 쓰고 채점하지 않는다"


def test_적합은_확장창이다() -> None:
    """뒤 폴드의 적합은 앞 폴드의 채점 구간을 포함한다 — 그때는 이미 지나간 자료다(미래를 보는 것이 아니다)."""
    from tools.trial_final_residual_rl import walk_folds

    folds = walk_folds(200, folds=3)
    fits = [f.stop for f, _j in folds]
    assert fits == sorted(fits) and len(set(fits)) == len(fits), "적합창이 커져야 한다"
    for i, (fit, _j) in enumerate(folds):
        if i:
            assert fit.stop > folds[i - 1][1].start, "앞 폴드의 채점 구간이 뒤 폴드의 적합에 들어온다"


def test_내부_검증은_적합창_안에_있고_채점과_안_겹친다() -> None:
    from tools.trial_final_residual_rl import GAP_DECISIONS, _inner, walk_folds

    for fit_all, judge in walk_folds(200):
        cut = fit_all.stop - _inner(fit_all)
        fit = range(fit_all.start, max(fit_all.start + 1, cut))
        valid = range(fit.stop + GAP_DECISIONS, fit_all.stop)
        assert valid.stop - valid.start >= 1, "내부 검증이 비면 조기 종료가 뜻이 없다"
        assert valid.stop <= judge.start, "내부 검증이 채점 구간을 넘보면 하이퍼파라미터를 판정 창에서 고르는 셈이다"
        assert fit.stop + GAP_DECISIONS <= valid.start, "적합과 내부 검증 사이에도 퍼지"


def test_학습창_지표는_격차에_쓰는_키만_요구한다() -> None:
    """2026-09-29: 학습창(박스장뿐)에 급등 국면이 없어 rally_ann=nan → 판정 키 전부를 요구하다 rc=1, 대기열이 처음부터 다시 돌렸다."""
    import math

    import pytest

    from tools.trial_final_residual_rl import require_keys

    train = {"ann": 0.05, "sharpe": 0.8, "ic": 0.01, "box_ann": 0.04, "rally_ann": math.nan, "mdd": -0.1, "turn": 10.0}
    assert require_keys("학습창", train, keys=("ann", "sharpe", "ic")) is train
    with pytest.raises(SystemExit):
        require_keys("판정창", train)   # 판정 쪽은 여전히 전부 요구한다
