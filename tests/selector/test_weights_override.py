"""샌드박스 가중치 고정(`selector.weights_override`) — BE2 shadow 만 알파를 바꿔 끼우고 운영 장부는 그대로.

지키는 것:
 ① 끔(체크인 기본값 `{}`)이면 측정표 그대로 — 운영 장부 동작이 안 바뀐다.
 ② 샌드박스 덮어쓰기 파일이 있으면 그 값 — 제약 Analyst(risk)는 알파에서 빠지고 census 도 같은 값을 본다.
 ③ 실전 창고에는 덮어쓰기 파일이 있을 수 없다(Store 가 거부) — 운영 장부로 새지 않는다.
 ④ 모양이 틀리면 크게 멈춘다 — 조용히 측정표로 물러서면 "BE2 장부" 가 현행 랭커로 돈다.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from quant_rl_trading.selector import weights as weights_module
from quant_rl_trading.store import OVERRIDES_FILE, Store, StoreError

NOW = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)
TEMPLATE = Path(__file__).resolve().parents[2] / "config" / "shadow" / "be2.config-overrides.yaml"


def _measured(store: Store) -> None:
    store.append("analyst_weights", [
        {"entity_id": name, "valid_from": NOW, "observed_at": NOW, "source": "test", "market": "KR",
         "ic": w, "weight": w}
        for name, w in (("ranker", 1.0), ("risk", 1.0), ("fundamental", 0.0))
    ], ingest_run_id="w-live")


@pytest.fixture
def live_like(tmp_path: Path) -> Store:
    store = Store(root=tmp_path / "data")
    store.seed_config_defaults()
    _measured(store)
    return store


def test_default_is_off_and_measured_weights_stand(live_like: Store) -> None:
    assert weights_module.weights_override(live_like, as_of=NOW, market="KR") is None
    assert weights_module.measured_weights(live_like, as_of=NOW, market="KR") == {"ranker": 1.0, "risk": 1.0}
    assert weights_module.analyst_weights(live_like, as_of=NOW, market="KR") == {"ranker": 1.0}


def test_sandbox_template_swaps_ranker_for_be2(live_like: Store) -> None:
    (Path(live_like.root) / OVERRIDES_FILE).write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    assert weights_module.measured_weights(live_like, as_of=NOW, market="KR") == {"be2": 1.0, "risk": 1.0}
    assert weights_module.analyst_weights(live_like, as_of=NOW, market="KR") == {"be2": 1.0}
    census = weights_module.weight_census(live_like, as_of=NOW, market="KR")
    assert census.alpha == ("be2",) and census.constrained == ("risk",) and census.fault == ""


def test_template_only_touches_weights() -> None:
    """나머지 규칙(상위 24·완충·C10·배분·노출)은 현행 그대로 — 덮어쓰기 키는 하나뿐이다."""
    values = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert values == {"selector.weights_override": {"be2": 1.0, "risk": 1.0}}


def test_live_warehouse_refuses_override_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from quant_rl_trading import store as store_module

    root = tmp_path / "data"
    store = Store(root=root)
    store.seed_config_defaults()
    monkeypatch.setattr(store_module, "DEFAULT_ROOT", root)
    (root / OVERRIDES_FILE).write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(StoreError):
        weights_module.measured_weights(store, as_of=NOW, market="KR")


def test_malformed_override_fails_loudly(live_like: Store) -> None:
    (Path(live_like.root) / OVERRIDES_FILE).write_text("selector.weights_override: be2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        weights_module.measured_weights(live_like, as_of=NOW, market="KR")
