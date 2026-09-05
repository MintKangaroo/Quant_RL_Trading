"""LatticeEnv 계약 시험 — 진짜 창고(합성 데이터) 위에서.

목으로 통과하는 환경 시험은 환경을 검증하지 않는다. 여기서 쓰는 창고는
임시 디렉터리에 진짜 Parquet 을 깔고 진짜 `store.get()` 을 부른다. 다만
**실제 운영 창고는 읽지 않는다** — 그러면 시험 결과가 그날의 수집 상태에
달리게 되고, 깨졌을 때 코드 문제인지 데이터 문제인지 구분할 수 없다.

여기서 증명하는 것은 셋이다 (M4-kickoff 4-1).

1. `reset`/`step` 이 §1 의 규격대로 움직인다
2. **같은 시드로 두 번 돌리면 궤적이 같다** — 아니면 그 위의 학습 곡선·
   어블레이션·시드 중앙값이 전부 무의미해진다
3. 마스크된 슬롯에는 비중이 배정되지 않는다
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from quant_rl_trading.allocator import env as env_module
from quant_rl_trading.allocator.env import EnvParams, LatticeEnv
from quant_rl_trading.collectors.market_hours import Market, trading_days

SEOUL = ZoneInfo("Asia/Seoul")

#: 학습 구간. 짧게 잡되 **거래일만** 쓴다 — 휴장일을 섞으면 "그날 봉이 없다"
#: 와 구분이 안 된다.
START = date(2026, 6, 1)
END = date(2026, 8, 7)
ENTITIES = ["KR:000100", "KR:000200", "KR:000300", "KR:000400"]

#: 시험용 에피소드 길이. 실제는 250 거래일이고(`allocator.episode_days`),
#: 그 값이 설정에서 온다는 것은 아래 별도 시험이 확인한다. 궤적 시험까지
#: 250일로 돌리면 한 번에 250 × (선정 + 시세) 조회가 붙는다.
EPISODE = 4


def _moment(day: date) -> datetime:
    return datetime.combine(day, __import__("datetime").time(15, 40), tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    return seed_warehouse(store)


def seed_warehouse(store):  # type: ignore[no-untyped-def]
    """4종목 · 400세션 · 지수 · 환율 · IC 가중치.

    유니버스 필터(상장 180일·거래대금 하한)와 confidence(60거래일 롤링 IC)가
    실제로 통과할 만큼의 이력을 깐다. 모자라면 후보가 0건이 되는데, 그건
    환경의 고장이 아니라 **창고가 빈 것**이라 시험이 아무것도 증명하지 못한다.
    """
    store.seed_config_defaults()
    history = [START - timedelta(days=offset) for offset in range(400, 0, -1)]
    days = history + trading_days(Market.KR, START, END)

    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW", "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "rate": 1_350.0 + index * 0.1,
            }
            for index, day in enumerate(days)
        ],
        ingest_run_id="fx-seed",
    )
    store.append(
        "indices",
        [
            {
                "entity_id": "KR:IDX:KOSPI", "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "market": "KR",
                "board": "KOSPI", "open": 2_500.0, "high": 2_500.0, "low": 2_500.0,
                "close": 2_500.0 + index * 1.5, "volume": 1.0, "value": 1.0,
            }
            for index, day in enumerate(days)
        ],
        ingest_run_id="idx-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(days):
        moment = _moment(day)
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            # 종목마다 다른 기울기 — 점수가 갈려야 후보 선정이 의미를 갖는다.
            close = 10_000.0 + index * (3 + offset) + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR",
                "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    store.append(
        "signals",
        [
            {
                "entity_id": entity, "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test",
                "analyst": analyst, "analyst_version": f"{analyst}-v0.1.0",
                "score": 0.2 + 0.2 * offset - 0.1 * slot, "confidence": 1.0,
                "horizon_days": 5, "features_hash": "x", "evidence_json": "[]",
                "latency_ms": 1.0,
            }
            for day in days[-140:]
            for offset, entity in enumerate(ENTITIES)
            # risk 는 알파가 아니지만 **관측에는 들어간다** (태스크 #32).
            for slot, analyst in enumerate(("chart", "fundamental", "risk"))
        ],
        ingest_run_id="sig-seed",
    )
    measured = _moment(START - timedelta(days=30))
    store.append(
        "analyst_weights",
        [
            {
                "entity_id": name, "valid_from": measured, "observed_at": measured,
                "source": "test", "market": "KR", "ic": 0.077, "weight": weight,
            }
            # chart 는 관찰 모드(가중치 0)다. 알파에는 안 들어가고 관측에는
            # 들어간다 — 그 둘이 갈라지는 것이 이 저장소의 최근 설계 변경이다.
            for name, weight in (("fundamental", 1.0), ("risk", 0.5), ("chart", 0.0))
        ],
        ingest_run_id="w-seed",
    )
    return store


def _params(store) -> EnvParams:  # type: ignore[no-untyped-def]
    """시험용 짧은 에피소드. 나머지 임계치는 전부 창고에서 온다."""
    base = EnvParams.from_store(store, as_of=_moment(START))
    return EnvParams(**{**base.__dict__, "episode_days": EPISODE})


def _env(store, **kwargs) -> LatticeEnv:  # type: ignore[no-untyped-def]
    return LatticeEnv(
        store, train_start=START, train_end=END, params=_params(store), **kwargs
    )


def _action(env: LatticeEnv, obs, *, rng: np.random.Generator | None = None):
    """마스크를 보고 유효 슬롯에만 비중을 싣는 액션.

    정책(4-3)이 concentration 을 마스크로 눌러서 만들 값을 손으로 흉내낸 것이다.
    """
    n_max = env.params.n_max
    weights = np.zeros(n_max + 1, dtype=np.float32)
    valid = np.flatnonzero(obs["mask"])
    if len(valid):
        raw = (
            rng.random(len(valid)) if rng is not None else np.ones(len(valid))
        )
        weights[valid] = 0.8 * raw / raw.sum()
    weights[n_max] = 1.0 - float(weights[:n_max].sum())
    return {
        "weights": weights,
        "delay": np.zeros(n_max, dtype=np.int64),
        "fx_alloc": np.array([0.0], dtype=np.float32),
    }


def _rollout(store, *, seed: int, steps: int = EPISODE - 1) -> list[tuple]:  # type: ignore[no-untyped-def]
    env = _env(store)
    obs, _info = env.reset(seed=seed)
    rng = np.random.default_rng(seed)
    trace: list[tuple] = []
    for _ in range(steps):
        action = _action(env, obs, rng=rng)
        obs, reward, terminated, truncated, info = env.step(action)
        trace.append(
            (
                round(reward, 12),
                round(info["nav"], 6),
                round(info["cost"], 12),
                round(info["turnover"], 12),
                round(info["action_reflection_rate"], 12),
                tuple(np.round(info["realized_weights"], 10).tolist()),
                terminated,
                truncated,
            )
        )
        if terminated or truncated:
            break
    return trace


# -- 규격 ---------------------------------------------------------------------


def test_관측과_액션이_스펙대로다(warehouse) -> None:
    """§1 의 표 그대로. **모양을 줄이지 않는다** — 선행 프로젝트의
    "obs 42 vs 모델 128" 은 규격에서만 나는 배선 고장이었다."""
    env = _env(warehouse)
    obs, info = env.reset(seed=0)

    assert obs["portfolio"].shape == (24,)
    assert obs["portfolio"].dtype == np.float32
    assert obs["assets"].shape == (30, 28)
    assert obs["assets"].dtype == np.float32
    assert obs["mask"].shape == (30,)
    assert obs["mask"].dtype == np.bool_
    assert env.observation_space.contains(obs)

    assert env.action_space["weights"].shape == (31,)
    assert env.action_space["delay"].nvec.tolist() == [4] * 30
    assert env.action_space["fx_alloc"].shape == (1,)
    assert "candidates" in info


def test_에피소드_길이와_지연_상한은_설정에서_온다(warehouse) -> None:
    """하드코딩 금지(불변식 10). 250 과 4 가 코드에 박혀 있으면 화면·학습·
    설정이 각자 다른 벽을 보게 된다."""
    params = EnvParams.from_store(warehouse, as_of=_moment(START))

    assert params.episode_days == 250
    assert params.delay_choices == 4
    assert params.n_max == 30


def test_스텝이_보상과_info_여섯을_돌려준다(warehouse) -> None:
    env = _env(warehouse)
    obs, _ = env.reset(seed=1)
    obs, reward, terminated, _truncated, info = env.step(_action(env, obs))

    assert isinstance(reward, float)
    assert not terminated
    for key in (
        "realized_weights", "target_weights", "action_reflection_rate",
        "cost", "drawdown", "turnover",
    ):
        assert key in info, key
    assert info["target_weights"].shape == (31,)
    assert info["realized_weights"].shape == (30,)
    # 낙폭은 **양수 깊이**다 (reward.DrawdownTracker).
    assert info["drawdown"] >= 0.0
    assert 0.0 <= info["action_reflection_rate"] <= 1.0


def test_에피소드는_길이에_닿으면_truncated_로_끝난다(warehouse) -> None:
    """250일(여기서는 EPISODE)은 MDD 정의에 묶여 있다. 낙폭으로 끝난 것과
    시간이 다 된 것을 같은 플래그로 돌려주면 §10 지표가 섞인다."""
    trace = _rollout(warehouse, seed=2)

    assert len(trace) == EPISODE - 1
    assert trace[-1][-1] is True   # truncated
    assert trace[-1][-2] is False  # terminated


def test_시계는_거래일마다_앞으로만_간다(warehouse) -> None:
    """`datetime.now()` 가 아니라 ReplayClock 이다 (불변식 2)."""
    env = _env(warehouse)
    obs, _ = env.reset(seed=3)
    moments = [env.as_of]
    for _ in range(EPISODE - 1):
        obs, *_rest = env.step(_action(env, obs))
        moments.append(env.as_of)

    assert moments == sorted(moments)
    assert len(set(moments)) == len(moments)
    assert all(moment.date() in trading_days(Market.KR, START, END) for moment in moments)


# -- 재현성 --------------------------------------------------------------------


def test_같은_시드로_두_번_돌리면_궤적이_같다(warehouse) -> None:
    """**이 저장소의 핵심 규약이다.** 궤적이 갈리면 시드 중앙값도 어블레이션도
    전부 노이즈를 재는 일이 된다 (rl-training.md §11)."""
    first = _rollout(warehouse, seed=7)
    second = _rollout(warehouse, seed=7)

    assert first == second
    # 아무것도 안 하는 구현도 "두 번 돌려 같다" 는 통과한다. 실제로 굴렀는지
    # 함께 본다 — NAV 가 움직였거나 체결이 있었어야 한다.
    assert len({row[1] for row in first}) > 1


def test_다른_시드는_다른_구간을_뽑는다(warehouse) -> None:
    """시작일을 학습 구간에서 무작위로 뽑는다(§1). 늘 같은 날에서 시작하면
    겹치는 윈도우 증강(§7)이 아무 표본도 늘리지 못한다."""
    starts = set()
    for seed in range(12):
        env = _env(warehouse)
        env.reset(seed=seed)
        starts.add(env.as_of.date())

    assert len(starts) > 1


def test_시작일을_지정하면_그날에서_시작한다(warehouse) -> None:
    """평가는 같은 구간을 반복해서 봐야 한다. 무작위로 뽑으면 두 정책이
    아니라 두 구간을 비교하게 된다."""
    day = trading_days(Market.KR, START, END)[3]
    env = _env(warehouse)
    env.reset(seed=0, options={"start": day})

    assert env.as_of.date() == day


# -- 마스크 --------------------------------------------------------------------


def test_마스크된_슬롯에는_비중이_가지_않는다(warehouse) -> None:
    """정책이 패딩 슬롯에 비중을 실어도 그것으로는 아무것도 살 수 없다.
    그 비중을 나머지에 다시 나누면 정책이 의도하지 않은 레버리지가 생기므로,
    **현금으로 보낸다.**"""
    env = _env(warehouse)
    obs, _ = env.reset(seed=4)
    n_max = env.params.n_max
    valid = int(obs["mask"].sum())
    assert 0 < valid < n_max  # 후보가 30개 미만이라 패딩이 실제로 생긴다

    # 전부 패딩 슬롯에만 싣는 액션. 극단이지만 정책은 이런 값을 낼 수 있다.
    weights = np.zeros(n_max + 1, dtype=np.float32)
    weights[valid:n_max] = 1.0 / (n_max - valid)
    action = {
        "weights": weights,
        "delay": np.zeros(n_max, dtype=np.int64),
        "fx_alloc": np.array([0.0], dtype=np.float32),
    }
    _obs, _reward, _terminated, _truncated, info = env.step(action)

    assert float(info["target_weights"][:n_max].sum()) == 0.0
    assert info["target_weights"][n_max] == pytest.approx(1.0)
    assert float(np.abs(info["realized_weights"]).sum()) == 0.0


def test_후보가_모자라면_패딩하고_마스크로_가린다(warehouse) -> None:
    env = _env(warehouse)
    obs, info = env.reset(seed=5)

    assert obs["mask"].sum() == len(info["candidates"])
    padded = ~obs["mask"]
    # 가려진 줄은 통째로 0 이다. 쓰레기 값을 남기면 정책이 패딩에서 신호를 찾는다.
    assert not obs["assets"][padded].any()


def test_종목_상한을_넘겨_배분하지_않는다(warehouse) -> None:
    """`allocator.max_position_weight` 는 분산의 정의다. 깎인 몫은 현금으로
    간다 — 다른 종목에 옮기면 정책이 내리지 않은 결정을 대신 내는 것이다."""
    env = _env(warehouse)
    obs, _ = env.reset(seed=6)
    n_max = env.params.n_max
    weights = np.zeros(n_max + 1, dtype=np.float32)
    weights[int(np.flatnonzero(obs["mask"])[0])] = 1.0  # 한 종목에 전부
    action = {
        "weights": weights,
        "delay": np.zeros(n_max, dtype=np.int64),
        "fx_alloc": np.array([0.0], dtype=np.float32),
    }
    _obs, _reward, _t, _tr, info = env.step(action)

    assert info["target_weights"][:n_max].max() <= env.params.max_position_weight + 1e-6


# -- 되먹임 --------------------------------------------------------------------


def test_실현_비중이_다음_관측으로_되먹여진다(warehouse) -> None:
    """불변식 7. 빠지면 RL 은 자기가 하지 않은 행동으로 보상받는다."""
    env = _env(warehouse)
    obs, _ = env.reset(seed=8)
    rng = np.random.default_rng(8)
    for _ in range(EPISODE - 2):
        obs, _reward, _t, _tr, info = env.step(_action(env, obs, rng=rng))
        assert np.allclose(
            obs["assets"][:, env_module.FEATURE_REALIZED_WEIGHT],
            info["realized_weights"],
            atol=1e-6,
        )


def test_체결된_비중이_실제로_생긴다(warehouse) -> None:
    """마스크·상한 시험은 "0 이면 통과" 라 아무것도 안 사는 구현도 지나간다.
    한 번은 실제로 사야 한다 — 안 그러면 나머지 시험이 전부 공회전이다."""
    trace = _rollout(warehouse, seed=9)

    assert any(sum(row[5]) > 0 for row in trace), "한 번도 체결되지 않았다"


def test_잘못된_액션은_거부한다(warehouse) -> None:
    """공매도도, 심플렉스 밖의 값도 조용히 고쳐 주지 않는다. 고쳐서 받으면
    클리핑된 액션과 로그확률이 어긋나 정책 그래디언트가 편향된다 (§1)."""
    env = _env(warehouse)
    obs, _ = env.reset(seed=10)
    n_max = env.params.n_max

    bad = _action(env, obs)
    bad["weights"] = bad["weights"][:-1]
    with pytest.raises(ValueError, match="현금"):
        env.step(bad)

    negative = _action(env, obs)
    negative["weights"][0] = -0.5
    with pytest.raises(ValueError, match="음수"):
        env.step(negative)

    late = _action(env, obs)
    late["delay"] = np.full(n_max, env.params.delay_choices, dtype=np.int64)
    with pytest.raises(ValueError, match="지연"):
        env.step(late)


# -- 오라클 카나리 (§0) --------------------------------------------------------
#
# 여기서 보는 것은 **정답이 관측의 어느 칸에 어떤 값으로 들어가나** 뿐이다.
# 그것으로 학습이 되는지는 `tests/rl/test_real_env_canary.py` 가 실제 창고에서
# 200k 스텝을 돌려 본다 — 합성 데이터로 통과하는 시험은 현실을 말해 주지
# 않는다(constant-feature 사건).

#: 오라클은 5거래일 앞을 본다. 4일짜리 에피소드에서는 미래가 늘 구간 밖이라
#: 정답이 통째로 0 이 되고, 그 0 을 "주입이 안 됐다" 와 구분할 수 없다.
ORACLE_EPISODE = 12


def _oracle_env(store, **kwargs):  # type: ignore[no-untyped-def]
    base = EnvParams.from_store(store, as_of=_moment(START))
    params = EnvParams(**{**base.__dict__, "episode_days": ORACLE_EPISODE})
    return LatticeEnv(
        store, train_start=START, train_end=END, params=params, **kwargs
    )


def test_오라클은_기본이_꺼져_있다(warehouse) -> None:
    """실수로 켜지면 그 위의 성과가 통째로 가짜다. 섹터 칸(26)은 지금 비어
    있으므로, 꺼진 상태에서 그 칸은 0 이어야 한다."""
    env = _oracle_env(warehouse)
    obs, _ = env.reset(seed=0)

    assert env.oracle_leak is False
    assert not np.any(obs["assets"][:, env_module.FEATURE_ORACLE])


def test_오라클을_켜면_크게_경고한다(warehouse) -> None:
    with pytest.warns(RuntimeWarning, match="oracle_leak=True"):
        _oracle_env(warehouse, oracle_leak=True)


def test_오라클은_5일뒤_실제_초과수익이다(warehouse) -> None:
    """**값까지 확인한다.** "0 이 아니다" 로 통과시키면 잡음을 심어 놓고
    카나리가 도는 것을 배선 정상으로 읽는다.

    창고의 종가·지수가 결정론적이라 정답을 손으로 계산할 수 있다:
    5세션 뒤 종가 상승률에서 지수 5세션 상승률을 빼고 `ORACLE_SCALE` 로 나눈 값.
    """
    with pytest.warns(RuntimeWarning):
        env = _oracle_env(warehouse, oracle_leak=True)
    sessions = trading_days(Market.KR, START, END)
    obs, info = env.reset(options={"start": sessions[0]})

    # 창고 픽스처의 종가 공식. `days` 의 400번째가 첫 거래일이다(그 앞은 이력).
    def close(offset: int, session: int) -> float:
        return 10_000.0 + (400 + session) * (3 + offset) + offset * 500

    horizon = env_module.ORACLE_HORIZON
    index_now = 2_500.0 + 400 * 1.5
    index_then = 2_500.0 + (400 + horizon) * 1.5
    benchmark = index_then / index_now - 1.0

    assert info["candidates"], "후보가 0건이면 이 시험은 아무것도 증명하지 않는다"
    for slot, entity in enumerate(info["candidates"]):
        offset = ENTITIES.index(entity)
        expected = (
            close(offset, horizon) / close(offset, 0) - 1.0 - benchmark
        ) / env_module.ORACLE_SCALE
        assert obs["assets"][slot, env_module.FEATURE_ORACLE] == pytest.approx(
            expected, rel=1e-4
        ), entity


def test_오라클이_관측_규격을_바꾸지_않는다(warehouse) -> None:
    """칸을 늘리면 `policy.py` 의 `n_asset_features` 가 따라가고, 그 전에 학습한
    정책이 통째로 못 쓰게 된다. 그래서 **덮어쓴다**."""
    with pytest.warns(RuntimeWarning):
        env = _oracle_env(warehouse, oracle_leak=True)
    obs, _ = env.reset(seed=0)

    assert obs["assets"].shape == (30, 28)
    assert env.observation_space.contains(obs)


def test_에피소드_끝_5일은_정답이_0이다(warehouse) -> None:
    """미래가 에피소드 밖이라 알려줄 것이 없다. 지어내면 그 5일만 정답이
    거짓말이 되고, 표에는 안 보인다."""
    with pytest.warns(RuntimeWarning):
        env = _oracle_env(warehouse, oracle_leak=True)
    sessions = trading_days(Market.KR, START, END)
    obs, _ = env.reset(options={"start": sessions[0]})

    filled = []
    for _ in range(ORACLE_EPISODE - 1):
        obs, _r, _t, truncated, _info = env.step(_action(env, obs))
        if truncated:
            break
        filled.append(bool(np.any(obs["assets"][:, env_module.FEATURE_ORACLE])))

    # 커서가 (길이 - 1 - 5) 를 넘어가면 5세션 뒤가 구간 밖이다.
    assert filled[0] is True
    assert filled[-1] is False


def test_관측_칸은_전부_O1_스케일이다(warehouse) -> None:
    """§1: 환율 원값(1,478)·보유 일수(126)·log10 자본(8) 이 그대로 들어가면
    그 칸 하나가 가치 헤드 그래디언트를 1,000 대로 키운다(2026-08-27)."""
    env = _env(warehouse)
    obs, _ = env.reset(seed=0)
    for _ in range(5):
        obs, _, terminated, truncated, _ = env.step(_action(env, obs))
        if terminated or truncated:
            break
    assert float(np.abs(obs["portfolio"]).max()) < 10.0, obs["portfolio"]
    assert float(np.abs(obs["assets"]).max()) < 10.0


# -- 3회차 설계 (2026-08-29): 현금은 액션이 아니다 · 보유 상태 출발 ---------------


def test_cash_action_fixed_는_투자분을_다_쓴다(warehouse) -> None:
    """정책이 현금에 90% 를 줘도 fixed 면 투자 비중은 1 − cash_buffer 다."""
    from dataclasses import replace

    env = LatticeEnv(
        warehouse, train_start=START, train_end=END,
        params=replace(_params(warehouse), cash_action="fixed"),
    )
    obs, _ = env.reset(seed=0)
    n_max = env.params.n_max
    weights = np.zeros(n_max + 1, dtype=np.float32)
    valid = np.flatnonzero(obs["mask"])
    weights[valid] = 0.1 / len(valid)
    weights[n_max] = 0.9
    targets, _delays = env._decode({
        "weights": weights, "delay": np.zeros(n_max, dtype=np.int64),
        "fx_alloc": np.array([0.0], dtype=np.float32),
    })
    investable = 1.0 - env.params.cash_buffer
    expected = min(investable, env.params.max_position_weight * len(valid))
    assert float(targets[:n_max].sum()) == pytest.approx(expected, abs=1e-6)
    assert float(targets[n_max]) == pytest.approx(1.0 - expected, abs=1e-6)
    assert np.all(targets[:n_max] <= env.params.max_position_weight + 1e-6)


def test_warm_start_는_첫날_후보를_들고_시작한다(warehouse) -> None:
    from dataclasses import replace

    env = LatticeEnv(
        warehouse, train_start=START, train_end=END,
        params=replace(_params(warehouse), warm_start=True),
    )
    obs, info = env.reset(seed=0)
    held = [e for e, p in env._state.book.positions.items() if p.quantity > 0]
    assert held, "warm start 인데 보유가 없다"
    assert set(held) <= set(info["candidates"])
    # 실현 비중이 첫 관측에 실려 있다 — 현금 출발이면 전부 0 이다.
    assert float(obs["assets"][:, env_module.FEATURE_REALIZED_WEIGHT].sum()) > 0.3  # 후보 수 × 상한 15%
    # 수수료를 물었으므로 NAV 는 초기 자본보다 조금 작다.
    assert env._state.nav < env.params.initial_capital
    assert env._state.nav > env.params.initial_capital * 0.99
