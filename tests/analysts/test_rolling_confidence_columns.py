"""``rolling_confidence`` 의 컬럼 프루닝은 **값이 같아야** 의미가 있다.

이 수정은 "빨라졌다" 로 검증되지 않는다. `signals` 를 전 컬럼으로 퍼오던 것을
넷으로 좁힌 것인데(2026-02-20 세션이 신호 단계 0초 → 96초, RSS 1.6GB → 6.2GB 로
튄 자리다), 좁힌 뒤 값이 달라지면 그건 최적화가 아니라 **조용한 오답**이다.
컬럼이 빠지면 보통 KeyError 로 죽지만, 여기서는 `frame["analyst"] == analyst`
필터가 빈 프레임을 만들어 `NO_EVIDENCE_CONFIDENCE` 로 떨어지는 길이 있다 —
죽지 않고 "잴 표본이 없다" 로 위장한다.

그래서 같은 창고에 대고 **좁힌 읽기와 안 좁힌 읽기를 나란히 돌려 값을 맞춘다.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from quant_rl_trading.analysts import ic

pytestmark = pytest.mark.invariant

ENTITIES = [f"KR:{index:06d}" for index in range(12)]
AS_OF = datetime(2026, 3, 16, 7, 0, tzinfo=UTC)
SESSIONS = 90


class _WidenedStore:
    """``columns`` 를 무시하고 **전 컬럼**을 읽는 창고.

    수정 전 동작이다. 나머지 인자는 그대로 넘긴다 — 창(as_of·lookback·market)이
    달라지면 비교가 성립하지 않기 때문이다.
    """

    def __init__(self, store) -> None:  # type: ignore[no-untyped-def]
        self._store = store
        self.widened = 0

    def get(self, table: str, **kwargs):  # type: ignore[no-untyped-def]
        if table == "signals" and kwargs.pop("columns", None) is not None:
            self.widened += 1
        return self._store.get(table, **kwargs)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._store, name)


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """오르는 종목에 높은 점수를 준 신호 이력. IC 가 양수로 서야 한다.

    점수에 잡음을 섞는다. 완벽히 단조롭게 두면 IC 가 정확히 1.0 이 나오는데,
    그 값은 ``NO_EVIDENCE_CONFIDENCE`` 와 같아서 **잰 값과 못 잰 값이
    구별되지 않는다.** 실제로 첫 판에서 그 함정에 걸렸다.
    """
    rng = np.random.default_rng(20260815)
    start = AS_OF - timedelta(days=SESSIONS)
    universe_rows = []
    price_rows = []
    signal_rows = []
    for index in range(SESSIONS):
        moment = start + timedelta(days=index)
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
                "open": close, "high": close, "low": close, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
            for analyst in ("chart", "risk"):
                signal_rows.append({
                    "entity_id": entity, "valid_from": moment, "observed_at": moment,
                    "source": "test", "analyst": analyst,
                    "analyst_version": f"{analyst}-v0.1.0",
                    # chart 는 기울기를 맞히고 risk 는 뒤집는다 — 둘의
                    # confidence 가 갈려야 비교가 무의미해지지 않는다.
                    "score": float(
                        (0.3 * offset if analyst == "chart" else -0.3 * offset)
                        + rng.normal(0.0, 1.2)
                    ),
                    "confidence": 1.0, "horizon_days": 5,
                    "features_hash": "x",
                    # 프루닝이 덜어내는 바로 그 무거운 컬럼. 비워 두면 이
                    # 테스트가 검증하려는 차이가 사라진다.
                    "evidence_json": "[" + ",".join(['{"k":"v"}'] * 20) + "]",
                    "latency_ms": 1.0,
                })

    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")
    store.append("signals", signal_rows, ingest_run_id="sig-seed")
    return store


@pytest.mark.parametrize("analyst", ["chart", "risk"])
def test_pruned_read_gives_the_same_confidence(seeded, analyst: str) -> None:
    """좁혀 읽으나 안 좁혀 읽으나 같은 값이다."""
    widened = _WidenedStore(seeded)

    narrow = ic.rolling_confidence(seeded, analyst=analyst, as_of=AS_OF, market="KR")
    wide = ic.rolling_confidence(widened, analyst=analyst, as_of=AS_OF, market="KR")

    assert widened.widened == 1, "signals 를 컬럼 프루닝으로 읽지 않았다 — 수정이 되돌려졌다"
    assert narrow == pytest.approx(wide, abs=1e-12)


def test_the_comparison_is_not_two_fallbacks(seeded) -> None:
    """값이 같다는 것만으로는 부족하다 — 둘 다 '못 쟀다' 여도 같기 때문이다.

    표본이 실제로 잡혀 IC 가 측정됐는지 못 박는다. 이게 없으면 위 테스트는
    창고가 비어도 통과한다.

    ``NO_EVIDENCE_CONFIDENCE`` 가 1.0 이라 **"안 쟀다" 와 "완벽히 맞혔다" 가
    같은 값**이다. 그래서 0 과 1 사이에 있는지를 본다 — 잡음을 섞은 이유다.
    """
    measured = ic.rolling_confidence(seeded, analyst="chart", as_of=AS_OF, market="KR")

    assert 0.0 < measured < ic.NO_EVIDENCE_CONFIDENCE, (
        f"IC 가 {measured} 다 — 잰 값이 아니라 기본값으로 떨어졌을 수 있다"
    )


def test_pruned_columns_are_enough_for_the_analyst_filter(seeded) -> None:
    """좁힌 프레임에 ``analyst`` 가 남아 있는가.

    이 컬럼이 빠지면 KeyError 가 아니라 **빈 필터 → NO_EVIDENCE** 로 새어
    나간다. 조용히 0.5 를 반환하는 실패가 가장 비싸다.
    """
    frame = seeded.get(
        "signals", as_of=AS_OF, lookback=114, market=None,
        columns=["entity_id", "analyst", "score"],
    )

    assert not frame.empty
    for needed in ("entity_id", "analyst", "score", "valid_from"):
        assert needed in frame.columns, f"{needed} 가 없으면 IC 를 못 잰다"
    assert set(frame["analyst"]) == {"chart", "risk"}
