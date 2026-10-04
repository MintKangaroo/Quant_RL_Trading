"""Yahoo 수집기 셋은 실행하면 거부한다 — robots.txt 전체 금지 (data-contract §4-2)."""

from __future__ import annotations

import pytest

from tools import collect_fx_yahoo, collect_indices_us, collect_prices_us_top


@pytest.mark.parametrize("module", [collect_fx_yahoo, collect_indices_us, collect_prices_us_top])
def test_yahoo_수집기는_rc2_로_거부한다(module, capsys) -> None:
    assert module.main([]) == 2
    err = capsys.readouterr().err
    assert "robots.txt" in err and module.REPLACEMENT in err
    assert "httpx" not in module.__dict__  # 네트워크 경로 자체가 없다
