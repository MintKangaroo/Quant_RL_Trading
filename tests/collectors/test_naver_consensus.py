import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quant_rl_trading.collectors import naver_consensus as nc

FIXTURES = Path(__file__).parent / "fixtures"


def _load(code: str) -> dict:
    return json.loads((FIXTURES / f"naver_integration_{code}.json").read_text(encoding="utf-8"))


def test_parse_integration_covered() -> None:
    # 2026-09-25 실제 응답(m.stock.naver.com/api/stock/005930/integration) 을 줄인 것
    p = nc.parse_integration(_load("005930"))
    assert p["rating"] == 4.0 and p["target_price"] == 493864.0
    assert p["eps_ttm"] == 22292.0 and p["per_ttm"] == 12.85
    assert p["eps_fwd"] == 47922.0 and p["per_fwd"] == 5.98
    assert p["pbr"] == 3.33 and p["dividend_yield"] == 0.58
    seen = datetime(2026, 9, 25, 12, tzinfo=UTC)
    row = nc.row_for("005930", day=date(2026, 9, 23), observed_at=seen, parsed=p)
    assert row is not None and row["entity_id"] == "KR:005930" and row["observed_at"].day == 25


def test_no_coverage_makes_no_row() -> None:
    # consensusInfo 없음, 추정 PER/EPS 는 "N/A"
    p = nc.parse_integration(_load("000040"))
    assert p["rating"] is None and p["eps_fwd"] is None and p["eps_ttm"] == 1718.0
    seen = datetime(2026, 9, 2, tzinfo=UTC)
    assert nc.row_for("000040", day=date(2026, 9, 2), observed_at=seen, parsed=p) is None


@pytest.mark.parametrize(
    "payload", [{"code": "StockConflict"}, "<html>없음</html>", None, {"totalInfos": None}]
)
def test_not_a_stock_payload_raises(payload: object) -> None:
    with pytest.raises(nc.ConsensusUnavailable):
        nc.parse_integration(payload)


def test_fail_verdict() -> None:
    assert nc.fail_verdict(135, 2803, max_fail_ratio=0.10) is None
    assert nc.fail_verdict(2799, 2799, max_fail_ratio=0.10) is not None
    assert nc.fail_verdict(0, 0, max_fail_ratio=0.10) is not None
