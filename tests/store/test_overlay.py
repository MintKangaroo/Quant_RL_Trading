"""오버레이 창고 — 읽기는 원본, 쓰기는 사본.

여기서 증명하는 것은 하나다. **백테스트가 쓴 것이 실제 창고에 닿지 않는다.**
닿으면 모의 체결이 실전 장부에 섞이고, append-only 창고에서 그건 되돌릴 수 없다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_rl_trading.store import Store, overlay
from quant_rl_trading.store.errors import StoreError

NOW = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
WRITABLE = {"trades", "nav_daily", "orders"}


@pytest.fixture
def origin(store):  # type: ignore[no-untyped-def]
    """실제 창고 역할. 시세와 체결이 하나씩 들어 있다."""
    store.append(
        "prices",
        [{
            "entity_id": "KR:000100", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "market": "KR", "open": 100.0, "high": 100.0,
            "low": 100.0, "close": 100.0, "volume": 10.0, "value": 1_000.0,
            "adj_factor": None,
        }],
        ingest_run_id="p-seed",
    )
    store.append(
        "trades",
        [{
            "entity_id": "KR:000100", "valid_from": NOW, "observed_at": NOW,
            "source": "live", "market": "KR", "side": "buy", "quantity": 10.0,
            "price": 100.0, "currency": "KRW", "fee": 1.0, "tax": 0.0,
            "order_id": "real-order",
        }],
        ingest_run_id="t-seed",
    )
    return store


def test_읽기는_원본을_보고_쓰기는_사본에_남는다(origin, tmp_path: Path) -> None:
    layer = overlay.build(
        root=tmp_path / "sandbox", source=origin.root, writable=WRITABLE
    )
    sandbox = Store(root=layer.root)

    # 읽기 전용 테이블은 원본이 그대로 보인다.
    assert len(sandbox.get("prices", as_of=NOW)) == 1
    # 쓰기 테이블은 비어서 시작한다 — 원본의 실전 체결을 물려받지 않는다.
    assert sandbox.get("trades", as_of=NOW).empty

    sandbox.append(
        "trades",
        [{
            "entity_id": "KR:000100", "valid_from": NOW, "observed_at": NOW,
            "source": "backtest", "market": "KR", "side": "sell", "quantity": 5.0,
            "price": 110.0, "currency": "KRW", "fee": 1.0, "tax": 0.1,
            "order_id": "sim-order",
        }],
        ingest_run_id="sim-1",
    )

    assert len(sandbox.get("trades", as_of=NOW)) == 1
    # **원본은 그대로다.** 실전 체결 한 건뿐이어야 한다.
    real = origin.get("trades", as_of=NOW)
    assert len(real) == 1
    assert set(real["order_id"]) == {"real-order"}


def test_적재_이력을_물려받지_않는다(origin, tmp_path: Path) -> None:
    """원본 매니페스트를 그대로 보면 첫 적재가 '이미 했음' 으로 조용히 건너뛰어진다."""
    layer = overlay.build(
        root=tmp_path / "sandbox", source=origin.root, writable=WRITABLE
    )
    sandbox = Store(root=layer.root)
    assert not sandbox.ingest_run_recorded("trades", "t-seed")


def test_비우기가_원본을_지우지_않는다(origin, tmp_path: Path) -> None:
    layer = overlay.build(
        root=tmp_path / "sandbox", source=origin.root, writable=WRITABLE
    )
    Store(root=layer.root).append(
        "trades",
        [{
            "entity_id": "KR:000100", "valid_from": NOW, "observed_at": NOW,
            "source": "backtest", "market": "KR", "side": "buy", "quantity": 1.0,
            "price": 100.0, "currency": "KRW", "fee": 0.0, "tax": 0.0,
            "order_id": "sim-2",
        }],
        ingest_run_id="sim-2",
    )

    layer.clear()

    assert Store(root=layer.root).get("trades", as_of=NOW).empty
    assert len(origin.get("trades", as_of=NOW)) == 1
    # 링크는 살아 있어야 한다. 죽으면 다음 백테스트가 시세를 못 본다.
    assert len(Store(root=layer.root).get("prices", as_of=NOW)) == 1


def test_창고에_없는_테이블은_거부한다(origin, tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        overlay.build(
            root=tmp_path / "sandbox", source=origin.root, writable={"없는테이블"}
        )
