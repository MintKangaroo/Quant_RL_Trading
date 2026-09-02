"""?ledger=shadow 가 두 번째 장부를 고른다 (미장 paper 슬리브, 2026-09-02)."""
from __future__ import annotations

from pathlib import Path

from quant_rl_trading.dashboard.app import create_app
from quant_rl_trading.store import Store


def test_shadow_ledger_is_selected_only_when_asked(tmp_path: Path) -> None:
    paper = tmp_path / "_paper"; shadow = tmp_path / "_shadow"
    paper.mkdir(); shadow.mkdir()
    app = create_app(store=Store(root=paper))
    assert app.config["QUANT_RL_STORE_SHADOW"] is not None
    from quant_rl_trading.dashboard.api.common import store
    with app.test_request_context("/api/trading?ledger=shadow"):
        assert Path(store().root) == shadow
    with app.test_request_context("/api/trading"):
        assert Path(store().root) == paper


def test_no_shadow_dir_means_no_second_ledger(tmp_path: Path) -> None:
    paper = tmp_path / "_paper"; paper.mkdir()
    app = create_app(store=Store(root=paper))
    assert app.config["QUANT_RL_STORE_SHADOW"] is None
    from quant_rl_trading.dashboard.api.common import store
    with app.test_request_context("/api/trading?ledger=shadow"):
        assert Path(store().root) == paper
