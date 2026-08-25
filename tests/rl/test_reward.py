"""보상 함수 계약 — `docs/design/reward-and-risk.md §2` 와 `rl-training.md §3`.

여기 있는 숫자는 **투자철학이지 하이퍼파라미터가 아니다**(§9). 12/22/30 과 w
값을 바꾸면 이 테스트가 깨져야 한다 — 조용히 통과하면 학습이 배우는 벽과
화면에 찍히는 벽이 갈라진 채로 운용된다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from quant_rl_trading.accounting import nav
from quant_rl_trading.allocator.reward import (
    DrawdownTracker,
    ReturnNormalizer,
    RewardEngine,
    RewardParams,
    penalty_weight,
)

PARAMS = RewardParams(
    drawdown_free=0.12,
    drawdown_warn=0.22,
    drawdown_hard=0.30,
    w_free=0.0,
    w_mid=1.5,
    w_hot=8.0,
    terminal_penalty=-10.0,
    normalize_returns="return_std",
)


def _drop_to(engine: RewardEngine, target_index: float, *, benchmark: float = 0.0):
    """누적지수를 정확히 ``target_index`` 로 옮기는 한 스텝."""
    current = engine.drawdown.index
    return engine.step(
        portfolio_return=target_index / current - 1.0, benchmark_return=benchmark
    )


def test_설정에서_읽는다_하드코딩이_아니다(store, ts: Callable[..., datetime]) -> None:
    """불변식 10. 계수가 코드에 박혀 있으면 config 를 바꿔도 학습이 안 바뀐다."""
    store.seed_config_defaults()

    params = RewardParams.from_store(store, as_of=ts(2026, 8, 18))

    assert params.drawdown_free == 0.12
    assert params.drawdown_warn == 0.22
    assert params.drawdown_hard == 0.30
    assert (params.w_free, params.w_mid, params.w_hot) == (0.0, 1.5, 8.0)
    assert params.terminal_penalty == -10.0
    assert params.normalize_returns == "return_std"


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        (0.0, 0.0),
        (0.1199, 0.0),
        (0.12, 1.5),  # 경계는 아래쪽 구간에 속하지 않는다
        (0.2199, 1.5),
        (0.22, 8.0),
        (0.35, 8.0),
    ],
)
def test_낙폭_구간별_가중치(depth: float, expected: float) -> None:
    assert penalty_weight(depth, params=PARAMS) == expected


def test_자유구간_안에서는_벌점이_0이다() -> None:
    """작은 낙폭은 비용이 아니라 정상 영업이다. 여기서 벌점을 주면 방어에
    집착하는 "덜 잃고 덜 버는" 정책으로 수렴한다."""
    engine = RewardEngine(params=PARAMS)

    breakdown = _drop_to(engine, 90.0)  # -10%

    assert breakdown.depth == pytest.approx(0.10)
    assert breakdown.delta_depth == pytest.approx(0.10)
    assert breakdown.drawdown_penalty == 0.0


def test_11_9에서_12_1로_넘어가면_벌점이_생긴다() -> None:
    """§3 의 단위 테스트. 자유구간을 넘는 **그 0.2%p 에만** 1.5 가 걸린다."""
    engine = RewardEngine(params=PARAMS)
    before = _drop_to(engine, 88.1)  # -11.9%
    assert before.drawdown_penalty == 0.0

    after = _drop_to(engine, 87.9)  # -12.1%

    assert after.depth == pytest.approx(0.121)
    assert after.delta_depth == pytest.approx(0.002)
    # w(12.1%) = 1.5. 넘어간 뒤의 깊이로 잰다.
    assert after.drawdown_penalty == pytest.approx(1.5 * 0.002)
    assert after.reward == pytest.approx(after.excess_return - 1.5 * 0.002)


def test_신저점을_안_찍은_날은_벌점이_0이다() -> None:
    """100 → 85 → 92 → 88. 88 은 85 보다 위다. **아직 신저점이 아니다.**

    직전 스텝 대비로 재면 이 날 벌점이 붙고, 오르내림마다 이중 과금이 되어
    한 에피소드의 벌점 합이 실제 MDD 를 훌쩍 넘는다.
    """
    engine = RewardEngine(params=PARAMS)
    _drop_to(engine, 85.0)

    rebound = _drop_to(engine, 92.0)
    partial = _drop_to(engine, 88.0)

    assert rebound.delta_depth == 0.0
    assert rebound.drawdown_penalty == 0.0
    assert partial.depth == pytest.approx(0.12)
    assert partial.delta_depth == 0.0
    assert partial.drawdown_penalty == 0.0


def test_신고점을_찍으면_다음_낙폭이_다시_아프다() -> None:
    """20% 를 한 번 겪은 뒤로 새 낙폭이 영원히 공짜가 되면 안 된다."""
    engine = RewardEngine(params=PARAMS)
    _drop_to(engine, 80.0)  # -20%
    _drop_to(engine, 110.0)  # 신고점

    fresh = _drop_to(engine, 96.8)  # 110 대비 -12%

    assert fresh.depth == pytest.approx(0.12)
    assert fresh.delta_depth == pytest.approx(0.12)
    assert fresh.drawdown_penalty == pytest.approx(1.5 * 0.12)


def test_30퍼센트를_넘으면_종료하고_크게_아프다() -> None:
    engine = RewardEngine(params=PARAMS)

    breakdown = _drop_to(engine, 69.9)  # -30.1%

    assert breakdown.terminated is True
    assert breakdown.depth >= PARAMS.drawdown_hard
    # 벌점 없이 자르기만 하면, 남은 스텝의 벌점을 피하려고 일부러 벽에
    # 부딪히는 것이 이득이 된다.
    assert breakdown.reward < PARAMS.terminal_penalty


def test_벽에_닿기_전까지는_종료하지_않는다() -> None:
    engine = RewardEngine(params=PARAMS)

    breakdown = _drop_to(engine, 70.1)  # -29.9%

    assert breakdown.terminated is False


def test_비용은_그대로_차감된다() -> None:
    """국장 왕복 0.2~0.35%. **실비이지 튜닝 대상이 아니다**(§5).

    비용 모델 자체는 시뮬레이터가 갖고, 보상은 그 값을 받아 뺄 뿐이다.
    보상이 비용을 따로 추정하면 시뮬레이터가 뺀 돈과 에이전트가 배우는 벌점이
    갈라진다.
    """
    for cost in (0.002, 0.0035):
        engine = RewardEngine(params=PARAMS)
        breakdown = engine.step(
            portfolio_return=0.01, benchmark_return=0.004, cost=cost
        )
        assert breakdown.excess_return == pytest.approx(0.006)
        assert breakdown.reward == pytest.approx(0.006 - cost)


def test_낙폭은_누적지수로_잰다_입금이_지우지_못한다(
    store, ts: Callable[..., datetime]
) -> None:
    """`accounting.md §6`. NAV 원금액으로 재면 증액이 낙폭을 지운다.

    r_port 는 `accounting.nav.twr_return` 이고, 입금일의 TWR 은 0 이다 —
    그래서 큰돈을 넣어도 낙폭 상태가 그대로 남는다.
    """
    engine = RewardEngine(params=PARAMS)
    _drop_to(engine, 75.0)  # -25%
    depth_before = engine.drawdown.depth

    deposit_return = nav.twr_return(nav=750_000.0, previous_nav=75_000.0, inflow=675_000.0)
    breakdown = engine.step(portfolio_return=deposit_return, benchmark_return=0.0)

    assert deposit_return == pytest.approx(0.0)
    assert breakdown.depth == pytest.approx(depth_before)
    assert breakdown.terminated is False


def test_누적지수는_accounting_과_같은_값을_낸다() -> None:
    """보상이 자기 회계를 따로 세지 않는다는 것을 실제 값으로 못 박는다."""
    returns = [0.01, -0.03, 0.02, -0.05, 0.004]
    tracker = DrawdownTracker()

    depths = [tracker.step(value)[0] for value in returns]

    expected_index = nav.compound(returns)
    expected_depth = [-value for value in nav.drawdown(expected_index)]
    assert tracker.index == pytest.approx(expected_index[-1])
    assert depths == pytest.approx(expected_depth)


def test_리턴_정규화가_스케일을_끌어올린다() -> None:
    """§3: 일간 초과수익은 0.001 규모다. **정규화 없이는 가치함수가 이 크기를
    학습하지 못한다** — explained_variance 0 의 1순위 처방.

    숫자로 보여준다: 같은 보상열이 정규화 뒤 몇 배가 되는가.
    """
    rewards = [0.001, -0.0008, 0.0012, -0.0005, 0.0009] * 40
    normalizer = ReturnNormalizer(gamma=0.997, num_envs=1)

    scaled = [normalizer([value], [False])[0] for value in rewards]

    raw_scale = max(abs(value) for value in rewards)
    normalized_scale = max(abs(value) for value in scaled[-10:])
    assert raw_scale < 0.002
    assert normalized_scale > 0.02  # 열 배 이상 커진다
    # 부호와 상대 비율은 유지된다 — 낙폭 페널티와의 비율이 깨지면 안 된다.
    # (통계가 갱신되는 동안은 나눗수가 매 스텝 조금씩 바뀌므로 고정한 뒤에 잰다.)
    normalizer.freeze()
    after = [normalizer([value], [False])[0] for value in rewards[:5]]
    assert after[0] / after[1] == pytest.approx(rewards[0] / rewards[1])


def test_평가에서는_통계를_갱신하지_않는다() -> None:
    """평가 중에 스케일이 움직이면 성적이 흔들리고 재현이 안 된다."""
    normalizer = ReturnNormalizer(gamma=0.997, num_envs=1)
    for value in [0.001] * 50:
        normalizer([value], [False])
    normalizer.freeze()
    frozen = normalizer.rms.var

    for value in [10.0] * 50:
        normalizer([value], [False])

    assert normalizer.rms.var == frozen


def test_에피소드_경계에서_할인리턴을_끊는다() -> None:
    """안 끊으면 에피소드를 넘어 누적이 이어져 스케일이 부풀고, 그만큼 보상이
    작아진다 — 원인이 보상 함수가 아니라 정규화에 있는 종류의 고장이다."""
    kept = ReturnNormalizer(gamma=0.997, num_envs=1)
    cut = ReturnNormalizer(gamma=0.997, num_envs=1)

    for index in range(60):
        kept([0.01], [False])
        cut([0.01], [index % 10 == 9])

    assert cut.rms.var < kept.rms.var


def test_선택과_노출의_합은_excess_와_기계정밀도로_같다() -> None:
    """§8 분해 — 총보상은 안 바뀐다. 한쪽을 빼서 만들기 때문에 정확히 같다."""
    engine = RewardEngine(params=PARAMS)
    out = engine.step(
        portfolio_return=0.013, benchmark_return=0.004, cost=0.001,
        candidate_mean_return=0.007, invested_share=0.9,
    )
    assert out.selection_return + out.exposure_return == out.excess_return
    # 노출 = invested·r̄ − r_bench = 0.9×0.007 − 0.004
    assert abs(out.exposure_return - (0.9 * 0.007 - 0.004)) < 1e-15


def test_후보수익이_없으면_기존과_동일하다() -> None:
    """r̄ 을 못 구한 날(가격 결측)은 selection=excess · exposure=0 — 회귀 없음."""
    engine = RewardEngine(params=PARAMS)
    out = engine.step(portfolio_return=0.01, benchmark_return=0.002, cost=0.0)
    assert out.selection_return == out.excess_return
    assert out.exposure_return == 0.0


def test_선택점수는_노출_결정에_무감각하다() -> None:
    """같은 종목 실력(후보 대비 +50bp)이면 주식을 30% 들든 90% 들든 선택
    점수가 같아야 한다 — 이것이 성적표를 가른 이유다."""
    tilt = 0.005          # 후보 평균 대비 내 포트폴리오의 우위 (종목 실력)
    r_bar = 0.01
    scores = []
    for invested in (0.3, 0.9):
        engine = RewardEngine(params=PARAMS)
        r_port = invested * (r_bar + tilt)          # 실력이 같고 노출만 다르다
        out = engine.step(
            portfolio_return=r_port, benchmark_return=0.0, cost=0.0,
            candidate_mean_return=r_bar, invested_share=invested,
        )
        scores.append(out.selection_return / invested)  # 단위 노출당 선택 점수
    assert abs(scores[0] - scores[1]) < 1e-12
