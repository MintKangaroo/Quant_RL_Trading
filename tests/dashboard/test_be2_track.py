"""학습 탭 병행 트랙 — BE2 shadow 한 줄(첫 NAV 가 생긴 날부터, 모의계좌와 같은 창 수익).

- 장부가 아직 없으면 줄이 안 나간다(시작 전 트랙을 보여 주지 않는다).
- 수익은 회계가 적은 TWR 지수(index_value)끼리 — 화면이 NAV 를 다시 계산하지 않는다.
- as_of 뒤의 NAV 는 안 보인다(불변식 9 — 되감기).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.dashboard.services import model_story as story
from quant_rl_trading.store import Store


def _nav(root: Path, days: list[date], index: list[float], tag: str) -> None:
    store = Store(root=root)
    store.append("nav_daily", [{
        "entity_id": ledger_module.ACCOUNT, "valid_from": datetime(d.year, d.month, d.day, 6, 40, tzinfo=UTC),
        "observed_at": datetime(d.year, d.month, d.day, 6, 40, tzinfo=UTC), "source": "test",
        "nav": 5.03e8 * v, "index_value": v,
    } for d, v in zip(days, index, strict=True)], ingest_run_id=tag)


def test_be2_track_appears_with_returns_only_after_first_nav(store, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    root = tmp_path / "research"
    days = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    early = datetime(2026, 10, 2, 7, tzinfo=UTC)
    assert "BE2" not in {t["name"] for t in story.live_models(store, as_of=early, models_root=root)["tracks"]}

    _nav(root / "_be2_shadow", days, [1.00, 1.02, 1.03], "be2")
    _nav(root / "_paper", [date(2026, 10, 2), *days], [0.99, 1.00, 1.01, 0.99], "paper")
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    track = next(t for t in story.live_models(store, as_of=now, models_root=root)["tracks"] if t["name"] == "BE2")
    assert track["started"] == "2026-10-05"
    returns = track["returns"]
    assert returns["sessions"] == 3 and returns["compare_name"] == "모의계좌"
    assert abs(returns["book"] - 0.03) < 1e-12
    assert abs(returns["compare"] - (0.99 / 1.00 - 1.0)) < 1e-12      # 같은 창(10/5~) — 10/2 는 빠진다

    rewound = datetime(2026, 10, 6, 12, tzinfo=UTC)
    track = next(t for t in story.live_models(store, as_of=rewound, models_root=root)["tracks"] if t["name"] == "BE2")
    assert track["returns"]["sessions"] == 2 and abs(track["returns"]["book"] - 0.02) < 1e-12
