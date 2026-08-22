"""세션 피처 캐시 계약 시험 — **캐시 유무가 결과를 바꾸지 않는다.**

이 파일에서 제일 중요한 시험은 하나다: 같은 세션에 대해 캐시 경로와 창고
경로가 **같은 값**을 낸다. 캐시가 조용히 다른 값을 내면 그 위에서 나온 학습
곡선·어블레이션·승격 판단이 전부 무효인데, 그 사실은 어디에도 안 뜬다 —
빨라졌다는 것만 보인다.

나머지 둘은 그 하나를 지키기 위한 울타리다.

- **멱등** — 두 번 구워도 같은 파일이다. 중단·재개가 기본 동작인 도구라
  "이어 굽기" 가 값을 바꾸면 캐시 전체를 못 믿는다
- **표지 거부** — 다른 창고·다른 설정으로 구운 캐시를 읽으면 멈춘다.
  무시하고 창고로 되돌아가면 원인(몇 시간을 틀린 캐시로 학습)이 성능 문제로만
  보인다

창고는 `test_env.py` 의 것과 같은 합성 데이터다. 운영 창고를 읽으면 시험
결과가 그날의 수집 상태에 달리게 되고, 깨졌을 때 코드 문제인지 데이터 문제인지
구분할 수 없다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from quant_rl_trading.allocator import cache as cache_module
from quant_rl_trading.allocator.cache import (
    CachedSessionReader,
    CacheStampMismatch,
    SessionReader,
)
from quant_rl_trading.allocator.env import EnvParams, LatticeEnv
from quant_rl_trading.collectors.market_hours import Market, trading_days

SEOUL = ZoneInfo("Asia/Seoul")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FRAGMENT = REPO_ROOT / "config" / "rl_cache.yaml"

START = date(2026, 6, 1)
END = date(2026, 8, 7)
ENTITIES = ["KR:000100", "KR:000200", "KR:000300", "KR:000400"]
EPISODE = 4

#: 굽는 자본. 실제 값은 `config/rl_cache.yaml` 에서 오지만, 시험은 그 값이
#: 얼마인지가 아니라 **그 값 이상에서 캐시가 유효하다**는 규칙을 본다.
BAKE_EQUITY = 70_000_000.0


def _moment(day: date) -> datetime:
    return datetime.combine(day, time(15, 40), tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    """4종목 · 400세션 · 지수 · 환율 · IC 가중치. `test_env.py` 와 같은 창고.

    유니버스 필터(상장 180일·거래대금 하한)와 confidence(60거래일 롤링 IC)가
    실제로 통과할 만큼의 이력을 깐다. 모자라면 후보가 0건이 되는데, 그러면
    "캐시와 창고가 같다" 가 **빈 것끼리 같다**는 뜻이 되어 아무것도 증명하지
    못한다 — 아래 첫 시험이 후보 수를 먼저 확인하는 이유다.
    """
    store.seed_config_defaults()
    store.seed_config_defaults(path=CONFIG_FRAGMENT)
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
            for name, weight in (("fundamental", 1.0), ("risk", 0.5), ("chart", 0.0))
        ],
        ingest_run_id="w-seed",
    )
    return store


def _sessions() -> list[date]:
    return trading_days(Market.KR, START, END)


def _bake(store, root: Path, days: list[date], *, equity: float = BAKE_EQUITY) -> None:
    reader = SessionReader(store, "KR")
    for day in days:
        cache = cache_module.build_session(
            reader, as_of=_moment(day), session=day, equity=equity
        )
        cache_module.write(cache, cache_module.cache_path(root, "KR", day))


def _params(store) -> EnvParams:  # type: ignore[no-untyped-def]
    base = EnvParams.from_store(store, as_of=_moment(START))
    return EnvParams(**{**base.__dict__, "episode_days": EPISODE})


def _env(store, root: Path | None, *, use_cache: bool) -> LatticeEnv:  # type: ignore[no-untyped-def]
    return LatticeEnv(
        store,
        train_start=START,
        train_end=END,
        params=_params(store),
        use_cache=use_cache,
        cache_root=root,
    )


def _action(env: LatticeEnv, obs, *, rng: np.random.Generator):
    n_max = env.params.n_max
    weights = np.zeros(n_max + 1, dtype=np.float32)
    valid = np.flatnonzero(obs["mask"])
    if len(valid):
        raw = rng.random(len(valid))
        weights[valid] = 0.8 * raw / raw.sum()
    weights[n_max] = 1.0 - float(weights[:n_max].sum())
    return {
        "weights": weights,
        "delay": np.zeros(n_max, dtype=np.int64),
        "fx_alloc": np.array([0.0], dtype=np.float32),
    }


def _rollout(store, root: Path | None, *, use_cache: bool, seed: int) -> list[tuple]:  # type: ignore[no-untyped-def]
    """관측·보상·체결까지 전부 담은 궤적. **관측 텐서를 통째로 비교한다.**

    스칼라 몇 개만 비교하면 캐시가 종목 축 한 칸을 틀려도 통과한다 — 그 한 칸이
    정책 그래디언트에 실린다.
    """
    env = _env(store, root, use_cache=use_cache)
    obs, info = env.reset(seed=seed, options={"start": _sessions()[0]})
    rng = np.random.default_rng(seed)
    trace: list[tuple] = [(
        np.round(obs["assets"], 9).tobytes(),
        np.round(obs["portfolio"], 9).tobytes(),
        obs["mask"].tobytes(),
        info["candidates"],
    )]
    for _ in range(EPISODE - 1):
        action = _action(env, obs, rng=rng)
        obs, reward, terminated, truncated, info = env.step(action)
        trace.append((
            np.round(obs["assets"], 9).tobytes(),
            np.round(obs["portfolio"], 9).tobytes(),
            obs["mask"].tobytes(),
            info.get("candidates", ()),
            round(reward, 12),
            round(info["nav"], 6),
            round(info["cost"], 12),
            info["filled"],
        ))
        if terminated or truncated:
            break
    return trace


# -- 제일 중요한 것 --------------------------------------------------------------


def test_창고에_후보가_실제로_잡힌다(warehouse) -> None:
    """**빈 것끼리 같다**로 아래 시험이 통과하는 것을 막는 울타리.

    IC 이력이 없으면 후보가 0건이 되고, 그러면 캐시-창고 비교가 아무것도
    증명하지 않는다. 실제로 운영 창고는 지금 이 상태다(롤링 IC 백필 중).
    """
    reader = SessionReader(warehouse, "KR")
    selection = reader.selection(_moment(_sessions()[0]), equity=BAKE_EQUITY)
    assert selection.candidates, "후보가 0건이면 이 파일의 나머지 시험은 무의미하다"


def test_캐시_경로와_창고_경로가_같은_값을_낸다(warehouse, tmp_path) -> None:
    """이 파일의 존재 이유. 관측 텐서·보상·NAV·체결까지 전부 같아야 한다."""
    root = tmp_path / "rl_cache"
    _bake(warehouse, root, _sessions()[: EPISODE + 2])

    warehouse_trace = _rollout(warehouse, None, use_cache=False, seed=7)
    cached_trace = _rollout(warehouse, root, use_cache=True, seed=7)

    assert len(cached_trace) == len(warehouse_trace)
    for step, (left, right) in enumerate(zip(warehouse_trace, cached_trace, strict=True)):
        assert left == right, f"{step}번째 스텝에서 캐시 경로와 창고 경로가 갈라졌다"


def test_캐시가_실제로_쓰였다(warehouse, tmp_path) -> None:
    """위 시험이 **캐시를 한 번도 안 읽고** 통과하는 것을 막는다.

    파일이 없으면 리더는 조용히 창고 경로로 돌아간다 — 그건 옳은 동작이지만,
    그 상태로 "같다" 를 확인하면 아무것도 확인하지 않은 것이다.
    """
    root = tmp_path / "rl_cache"
    _bake(warehouse, root, _sessions()[: EPISODE + 2])
    env = _env(warehouse, root, use_cache=True)
    env.reset(seed=1, options={"start": _sessions()[0]})
    assert isinstance(env.reader, CachedSessionReader)
    assert env.reader.hits > 0
    assert env.reader.misses == 0


def test_캐시에_없는_종목은_창고에서_읽는다(warehouse, tmp_path) -> None:
    """보유 중인데 그날 후보가 아닌 종목. **덜 구우면 느릴 뿐 값은 같다.**"""
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    as_of = _moment(day)
    # 후보만 구운 캐시. ENTITIES 전부가 후보가 아닐 수 있다.
    _bake(warehouse, root, [day])
    cached = cache_module.read(cache_module.cache_path(root, "KR", day))
    outsider = next(entity for entity in ENTITIES if entity not in cached.covered)

    plain = SessionReader(warehouse, "KR")
    hybrid = CachedSessionReader(warehouse, "KR", root)
    entities = [*sorted(cached.covered), outsider]

    assert hybrid.stats(as_of, entities).prices == plain.stats(as_of, entities).prices
    assert hybrid.betas(as_of, entities) == plain.betas(as_of, entities)
    assert hybrid.signals(as_of, entities) == plain.signals(as_of, entities)
    assert hybrid.fill_states(as_of, entities) == plain.fill_states(as_of, entities)


# -- 멱등 ------------------------------------------------------------------------


def test_두_번_구워도_같다(warehouse, tmp_path) -> None:
    """중단·재개가 기본 동작이므로 이어 굽기가 값을 바꾸면 안 된다."""
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    first = cache_module.cache_path(root, "KR", day).read_bytes()
    _bake(warehouse, root, [day])
    second = cache_module.cache_path(root, "KR", day).read_bytes()
    assert first == second


def test_이미_구운_세션은_건너뛴다(warehouse, tmp_path) -> None:
    """`tools/build_rl_cache.already_built` 규약. 파일 존재 + 표지 일치."""
    from tools.build_rl_cache import already_built

    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    assert not already_built(warehouse, root, "KR", day, _moment(day))
    _bake(warehouse, root, [day])
    assert already_built(warehouse, root, "KR", day, _moment(day))


# -- 표지 -------------------------------------------------------------------------


def test_다른_창고의_캐시는_거부한다(warehouse, tmp_path) -> None:
    """가장 조용한 사고를 막는 자리. 값이 그럴듯해서 아무도 안 묻는다."""
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    cached = cache_module.read(cache_module.cache_path(root, "KR", day))

    from quant_rl_trading.store import Store

    other = Store(root=tmp_path / "다른창고")
    with pytest.raises(CacheStampMismatch, match="창고"):
        cache_module.verify(
            cached, store=other, market="KR", session=day, as_of=_moment(day)
        )


def test_설정이_바뀌면_거부한다(warehouse, tmp_path) -> None:
    """임계치가 바뀌면 후보도 점수도 달라진다. 옛 캐시는 그 사실을 모른다."""
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    cached = cache_module.read(cache_module.cache_path(root, "KR", day))

    warehouse.append(
        "config",
        [{
            "entity_id": "allocator.n_max_candidates",
            "valid_from": _moment(day) - timedelta(days=1),
            "observed_at": _moment(day) - timedelta(days=1),
            "source": "test", "revision": 99, "value_json": "7",
        }],
        ingest_run_id="config-bump",
    )
    with pytest.raises(CacheStampMismatch, match="설정 지문"):
        cache_module.verify(
            cached, store=warehouse, market="KR", session=day, as_of=_moment(day)
        )


def test_설정_변경은_이름과_값까지_알려준다(warehouse, tmp_path) -> None:
    """지문 두 개만 적으면 다음 행동이 안 나온다.

    후보 수를 정하는 `selector.n_candidates` 는 캐시 내용을 직접 바꾼다.
    거부하는 것만으로는 부족하고, **무엇이 얼마에서 얼마로** 바뀌었는지가
    메시지에 있어야 사람이 "그 변경이 맞다, 다시 굽자" 를 그 자리에서 정한다.
    """
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    cached = cache_module.read(cache_module.cache_path(root, "KR", day))

    _bump_config(warehouse, day, "selector.n_candidates", "3")
    with pytest.raises(CacheStampMismatch, match="selector.n_candidates"):
        cache_module.verify(
            cached, store=warehouse, market="KR", session=day, as_of=_moment(day)
        )


def test_환경이_안_읽는_설정이_바뀌어도_캐시는_유효하다(warehouse, tmp_path) -> None:
    """2026-08-22 에 실제로 겪은 일 — **브로커 계좌 두 줄이 캐시를 깼다.**

    `allocator/env.py` 도 `allocator/cache.py` 도 계좌 모드를 읽지 않는다.
    그런데 지문이 설정 전체 위에서 계산돼, 계좌 설정을 창고에 심은 순간 구워
    둔 캐시가 통째로 무효가 됐고 대조군 실행이 8초 만에 죽었다. 그러면 사람은
    설정을 안 고치거나 캐시를 안 굽는다.

    ⚠️ 이 시험만으로는 부족하다. 반대 방향(**읽는 키가 바뀌면 거부한다**)이
    위 두 시험이고, 목록 자체가 낡지 않는지는 `test_cache_config_scope.py` 가
    본다. 한 방향만 걸어 두면 좁히기가 과했는지 못 잡는다.
    """
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    cached = cache_module.read(cache_module.cache_path(root, "KR", day))

    for name, value in (
        ("execution.account_mode", '"paper"'),
        ("execution.live_account_fingerprint_paper", '"abc123def456"'),
        ("llm.monthly_budget_usd", "50"),
        ("reporting.rsi_period", "21"),
    ):
        _bump_config(warehouse, day, name, value)

    # 예외가 없다 = 캐시가 그대로 유효하다.
    cache_module.verify(
        cached, store=warehouse, market="KR", session=day, as_of=_moment(day)
    )


def _bump_config(warehouse, day: date, name: str, value_json: str) -> None:
    """설정 하나를 세션 전날 발효로 정정한다. 창고는 append-only 다(불변식 4)."""
    moment = _moment(day) - timedelta(days=1)
    warehouse.append(
        "config",
        [{
            "entity_id": name,
            "valid_from": moment,
            "observed_at": moment,
            "source": "test", "revision": 99, "value_json": value_json,
        }],
        ingest_run_id=f"config-bump-{name}",
    )


def test_다른_세션의_캐시는_거부한다(warehouse, tmp_path) -> None:
    root = tmp_path / "rl_cache"
    days = _sessions()[:2]
    _bake(warehouse, root, days[:1])
    cached = cache_module.read(cache_module.cache_path(root, "KR", days[0]))
    with pytest.raises(CacheStampMismatch, match="세션"):
        cache_module.verify(
            cached, store=warehouse, market="KR", session=days[1], as_of=_moment(days[1])
        )


# -- 자본 의존 --------------------------------------------------------------------


def test_자본이_굽던_때보다_작으면_후보는_창고에서_읽는다(warehouse, tmp_path) -> None:
    """1주 가격 상한(`selector/filters.PRICE_CAP_REASON`)이 다르게 걸릴 수 있다.

    캐시를 그대로 쓰면 후보 목록이 조용히 달라진다 — 값이 갈리느니 느린 쪽을
    고른다.
    """
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    as_of = _moment(day)
    _bake(warehouse, root, [day], equity=BAKE_EQUITY)
    hybrid = CachedSessionReader(warehouse, "KR", root)
    plain = SessionReader(warehouse, "KR")

    small = 30_000.0  # 1주 10,000원대라 상한(15%)에 실제로 걸린다
    assert hybrid.selection(as_of, equity=small) == plain.selection(as_of, equity=small)
    assert hybrid.selection(as_of, equity=BAKE_EQUITY) == plain.selection(
        as_of, equity=BAKE_EQUITY
    )


def test_굽는_자본에서_가격상한에_걸리면_굽지_않는다(warehouse) -> None:
    """`price_capped` 가 0 이 아니면 그 세션의 캐시는 자본에 의존한다."""
    as_of = _moment(_sessions()[0])
    assert cache_module.price_capped(
        warehouse, as_of=as_of, market="KR", equity=BAKE_EQUITY
    ) == 0
    assert cache_module.price_capped(
        warehouse, as_of=as_of, market="KR", equity=30_000.0
    ) > 0


# -- 경계 ------------------------------------------------------------------------


def test_캐시에는_포트폴리오_상태가_없다(warehouse, tmp_path) -> None:
    """**이 파일에서 두 번째로 중요한 시험.**

    누군가 "포트폴리오도 캐시하면 더 빠르겠네" 하고 넣는 순간 32개 환경이 남의
    장부로 보상받는다. 그 오류는 예외를 안 내고 학습 곡선에도 안 보인다 —
    그래서 구조로 막는다: 구운 파일의 컬럼·표지에 장부에서 온 값이 없어야 한다.
    """
    root = tmp_path / "rl_cache"
    day = _sessions()[0]
    _bake(warehouse, root, [day])
    import pyarrow.parquet as pq  # invariant-allow: data-access - 시험이 파일을 들여다본다

    table = pq.read_table(cache_module.cache_path(root, "KR", day))  # invariant-allow: data-access
    forbidden = {
        "realized_weight", "holding_days", "unrealized", "quantity", "avg_cost",
        "nav", "cash", "drawdown", "turnover", "reflection", "position",
    }
    for name in table.column_names:
        assert name not in forbidden, f"{name} 은 액션에 의존한다. 캐시에 있으면 안 된다"
