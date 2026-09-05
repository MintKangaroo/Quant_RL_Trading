"""정책을 실제 장부에 끼우는 배선(allocator/live.py) 시험.

증명하는 것은 셋이다.

1. `observe_live` 는 학습 관측과 같은 규격을 내고, 장부가 관측에 실린다
   (보유 비중·경과일·낙폭·직전 반영률).
2. `decide` 는 심플렉스 위의 목표 비중을 내고, 마스크 밖·현금 완충 규칙이
   학습 환경(`_decode`)과 같다. 지연 > 0 인 매수는 오늘 실현 비중에 머문다.
3. 세션(`session/daily.run`)은 **모의계좌 장부에서만** 정책을 부르고, 노출
   배수는 곱하지 않으며, 이벤트 로그에 driver `rl` 을 남긴다. 다른 장부는
   룰 그대로다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
import torch
from tests.allocator.test_env import ENTITIES, START, _moment, seed_warehouse

from quant_rl_trading.accounting.book import KRW, USD, Book, Position
from quant_rl_trading.allocator import live
from quant_rl_trading.allocator.env import (
    FEATURE_HOLDING_DAYS,
    FEATURE_REALIZED_WEIGHT,
    N_ASSET_FEATURES,
    N_PORTFOLIO_FEATURES,
    EnvParams,
)
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.session import daily
from quant_rl_trading.store import Store

SESSION = date(2026, 7, 20)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    return seed_warehouse(store)


def _checkpoint(tmp_path, store, *, seed: int = 0):  # type: ignore[no-untyped-def]
    params = EnvParams.from_store(store, as_of=_moment(START))
    torch.manual_seed(seed)
    policy = AllocatorPolicy(
        PolicyConfig(
            n_max=params.n_max,
            n_asset_features=N_ASSET_FEATURES,
            n_portfolio_features=N_PORTFOLIO_FEATURES,
            n_delay_choices=3,
        )
    )
    path = tmp_path / "policy.pt"
    torch.save({"policy": policy.state_dict(), "update": 7}, path)
    return path


def _book(nav: float = 100_000_000.0) -> Book:
    return Book(
        cash={KRW: nav * 0.7, USD: 0.0},
        positions={ENTITIES[0]: Position(quantity=1_000.0, avg_cost=10_000.0)},
    )


def test_관측은_학습_규격이고_장부가_실린다(warehouse) -> None:
    params = EnvParams.from_store(warehouse, as_of=_moment(START))
    env = live.build_env(warehouse, as_of=_moment(SESSION), market="KR", params=params)
    book = _book()
    obs, info = env.observe_live(
        session=SESSION,
        book=book,
        entered={ENTITIES[0]: SESSION - timedelta(days=7), ENTITIES[3]: date(2020, 1, 1)},
        nav=100_000_000.0,
        drawdown_depth=0.08,
        candidates=[(entity, 0.5) for entity in ENTITIES],
        last_reflection=0.6,
    )
    assert obs["portfolio"].shape == (N_PORTFOLIO_FEATURES,)
    assert obs["assets"].shape == (params.n_max, N_ASSET_FEATURES)
    assert obs["mask"].sum() == len(ENTITIES)
    assert info["candidates"][0] == ENTITIES[0]  # 보유가 먼저
    assert obs["portfolio"][0] == pytest.approx(0.08)
    assert obs["portfolio"][20] == pytest.approx(0.5, abs=0.01)  # 에피소드 한복판
    assert obs["portfolio"][22] == pytest.approx(0.6)
    held = obs["assets"][0]
    assert held[FEATURE_REALIZED_WEIGHT] > 0.0
    assert 0.0 < held[FEATURE_HOLDING_DAYS] <= 1.0


def test_decide_는_심플렉스_위의_비중을_내고_지연_매수는_미룬다(warehouse, tmp_path) -> None:
    checkpoint = _checkpoint(tmp_path, warehouse)
    params = live.LiveParams(checkpoint=str(checkpoint), modes=("paper",))
    decision = live.decide(
        warehouse,
        as_of=_moment(SESSION),
        market="KR",
        book=_book(),
        nav=100_000_000.0,
        drawdown=-0.03,
        candidates=[(entity, 0.5) for entity in ENTITIES],
        params=params,
    )
    env_params = EnvParams.from_store(warehouse, as_of=_moment(START))
    total = sum(decision.weights.values())
    assert 0.0 <= total <= 1.0 - env_params.cash_buffer + 1e-6
    assert all(0.0 <= w <= env_params.max_position_weight + 1e-6 for w in decision.weights.values())
    assert set(decision.weights) == set(ENTITIES)
    assert decision.cash_weight == pytest.approx(1.0 - total)
    assert decision.update == 7
    assert decision.checkpoint == str(checkpoint)
    for entity in decision.deferred:
        assert decision.delays[entity] > 0
        # 미룬 매수는 오늘 실현 비중(보유 없으면 0)에 머문다
        expected = 0.0 if entity != ENTITIES[0] else pytest.approx(decision.weights[entity])
        assert decision.weights[entity] == expected


def test_같은_장부에는_같은_결정이_나온다(warehouse, tmp_path) -> None:
    checkpoint = _checkpoint(tmp_path, warehouse)
    params = live.LiveParams(checkpoint=str(checkpoint), modes=("paper",))
    kwargs = dict(
        as_of=_moment(SESSION), market="KR", book=_book(), nav=100_000_000.0,
        drawdown=0.0, candidates=[(entity, 0.5) for entity in ENTITIES], params=params,
    )
    first = live.decide(warehouse, **kwargs)
    second = live.decide(warehouse, **kwargs)
    assert first.weights == second.weights
    assert first.delays == second.delays


def test_LiveParams_는_설정이_없는_시점에는_꺼져_있다(warehouse) -> None:
    params = live.LiveParams.from_store(warehouse, as_of=_moment(SESSION))
    assert params.checkpoint == ""
    assert not params.active_for("PAPER")
    assert live.LiveParams(checkpoint="x.pt", modes=("paper",)).active_for("PAPER")
    assert not live.LiveParams(checkpoint="x.pt", modes=("paper",)).active_for("SHADOW")


def _seed_paper(store: Store, *, checkpoint: str, moment) -> None:
    import json

    from quant_rl_trading.store.tables import CONFIG_TABLE

    store.append(
        CONFIG_TABLE,
        [
            {
                "entity_id": name, "valid_from": moment, "observed_at": moment,
                "source": "test", "value_json": json.dumps(value), "revision": 1,
            }
            for name, value in (
                ("allocator.rl.checkpoint", checkpoint),
                ("allocator.rl.modes", ["paper"]),
            )
        ],
        ingest_run_id="rl-on",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": _moment(START - timedelta(days=10)),
            "observed_at": _moment(START - timedelta(days=10)), "source": "test",
            "currency": "KRW", "amount": 100_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )


@pytest.mark.parametrize(
    ("suffix", "driver"), [("data_paper", "rl"), ("data_shadow", "score")]
)
def test_세션은_모의계좌_장부에서만_정책을_부른다(tmp_path, suffix, driver) -> None:
    store = seed_warehouse(Store(root=tmp_path / suffix))
    checkpoint = _checkpoint(tmp_path, store)
    as_of = _moment(SESSION)
    _seed_paper(store, checkpoint=str(checkpoint), moment=_moment(START - timedelta(days=5)))

    result = daily.run(
        store, ReplayClock(as_of), as_of=as_of, market="KR", run_id="traced",
        wall_clock=ReplayClock(as_of),
    )
    assert result.candidates
    events = store.get("events", as_of=as_of + timedelta(minutes=1), entity="traced")
    allocate = events[events["stage"] == "allocate"]
    assert not allocate.empty
    assert allocate.iloc[-1]["actor"] == driver
    exposure = events[events["stage"] == "exposure"]
    if driver == "rl":
        assert exposure.iloc[-1]["actor"] == "rl:policy_cash"
        assert sum(result.weights.values()) <= 1.0
    else:
        assert exposure.iloc[-1]["actor"] != "rl:policy_cash"
