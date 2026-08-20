"""**학습 설계값은 오늘 것, 시장 값은 그때 것.**

2026-08-20 에 `n_max_candidates` 를 30 → 15 로 바꾸고 학습을 돌렸는데
**마지막 자리까지 똑같은 숫자**가 나왔다. 환경이 이 값을 학습 구간 첫날
기준으로 읽어서 그날 발효한 정정본이 그 시점에 없었던 것이다. 오류가 아니라
조용히 옛 값이었고, 비교할 앞 판이 없었으면 "별 차이 없네" 로 끝났을 것이다.

발효일을 앞당기는 길은 막혀 있다 — 그러면 과거 백테스트가 소급해 바뀐다.
그래서 시점을 둘로 갈랐다. 여기서 그 갈라짐을 못 박는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from quant_rl_trading.allocator.env import EnvParams

TRAIN_FIRST = datetime(2025, 1, 2, tzinfo=UTC)
LATER = datetime(2026, 8, 20, tzinfo=UTC)


def _revise(store, name: str, value, moment: datetime, revision: int) -> None:
    """설정 정정본 한 줄. 창고는 append-only 라 UPDATE 가 없다 (불변식 4)."""
    store.append(
        "config",
        [{
            "entity_id": name,
            "valid_from": moment,
            "observed_at": moment,
            "source": "test",
            "revision": revision,
            "value_json": json.dumps(value),
        }],
        ingest_run_id=f"test-{name}-{moment.date()}-r{revision}",
    )


def test_hyper_as_of_sees_todays_setting(store) -> None:  # type: ignore[no-untyped-def]
    """오늘 바꾼 슬롯 수를 학습이 본다."""
    store.seed_config_defaults()
    _revise(store, "allocator.n_max_candidates", 15, LATER, 1)

    # 학습 구간 첫날로 읽으면 옛 값이다 — 백테스트 재현이 이걸 원한다.
    old = EnvParams.from_store(store, as_of=TRAIN_FIRST)
    assert old.n_max == 30

    # 오늘로 읽으면 새 값이다 — 학습은 이걸 원한다.
    fresh = EnvParams.from_store(store, as_of=TRAIN_FIRST, hyper_as_of=LATER)
    assert fresh.n_max == 15


def test_world_values_stay_gated(store) -> None:  # type: ignore[no-untyped-def]
    """**시장 값은 hyper_as_of 를 따라가지 않는다.**

    결제주기가 T+2 에서 T+1 로 바뀌는 것은 우리가 고른 것이 아니라 시장이
    바뀐 것이다. 과거 구간은 그때 규칙으로 굴러야 한다 — 안 그러면 백테스트가
    없던 유동성으로 체결한다.
    """
    store.seed_config_defaults()
    _revise(store, "execution.settlement_days", 1, LATER, 1)

    old = EnvParams.from_store(store, as_of=TRAIN_FIRST)
    fresh = EnvParams.from_store(store, as_of=TRAIN_FIRST, hyper_as_of=LATER)
    # 설계값 시점을 옮겨도 결제주기는 그때 값 그대로다.
    assert old.settlement_days == fresh.settlement_days == 2

    # 시장 시점 자체를 옮기면 그때는 바뀐다.
    moved = EnvParams.from_store(store, as_of=LATER + timedelta(days=1))
    assert moved.settlement_days == 1


def test_default_keeps_old_behaviour(store) -> None:  # type: ignore[no-untyped-def]
    """안 주면 옛 동작 그대로 — 백테스트 호출부를 건드리지 않는다."""
    store.seed_config_defaults()
    _revise(store, "allocator.n_max_candidates", 15, LATER, 1)
    assert (
        EnvParams.from_store(store, as_of=TRAIN_FIRST).n_max
        == EnvParams.from_store(store, as_of=TRAIN_FIRST, hyper_as_of=None).n_max
    )
