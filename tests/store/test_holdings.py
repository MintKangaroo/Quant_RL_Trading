"""13F 를 종목 축으로 읽기 — **줄어드는 방식이 조용하다.**

매핑이 안 되면 행이 0 이 되는 게 아니라 그럴듯하게 줄어든다. 그래서
"몇 종목이 나오나" 가 아니라 **무엇을 버렸나**를 못 박는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from quant_rl_trading.collectors.security_ids import IDENTITY_EPOCH
from quant_rl_trading.store.holdings import (
    by_security,
    cusip_to_entity,
    mapping_coverage,
    read_institutional_holdings,
)

AS_OF = datetime(2026, 8, 19, tzinfo=UTC)
QUARTER = datetime(2026, 6, 30, 21, tzinfo=UTC)
OBSERVED = datetime(2026, 8, 14, 21, 30, tzinfo=UTC)


def holding(cusip: str, value: float, *, filer: str = "1067983", weight: float = 0.1) -> dict:
    return {
        "entity_id": f"CUSIP:{cusip}",
        "valid_from": QUARTER,
        "observed_at": OBSERVED,
        "source": "sec_edgar",
        "filer_cik": filer,
        "filer_name": "Some Fund",
        "issuer": "SOME CO",
        "cusip": cusip,
        "value_usd": value,
        "shares": value / 100.0,
        "weight": weight,
        "folded_rows": 1.0,
        "lag_days": 45.0,
    }


def identifier(cusip: str, ticker: str) -> dict:
    return {
        "entity_id": f"US:{ticker}",
        "valid_from": IDENTITY_EPOCH,
        "observed_at": IDENTITY_EPOCH,
        "source": "openfigi",
        "market": "US",
        "id_type": "CUSIP",
        "id_value": cusip,
        "figi": f"BBG{ticker}",
        "composite_figi": f"BBG{ticker}",
        "security_type": "Common Stock",
        "name": "SOME CO",
        "mapped_at": datetime(2026, 8, 19, 3, tzinfo=UTC),
    }


@pytest.fixture
def loaded(store):  # type: ignore[no-untyped-def]
    store.append(
        "filings_13f",
        [holding("037833100", 900.0), holding("00032Q104", 100.0)],
        ingest_run_id="13f-test",
    )
    store.append("security_ids", [identifier("037833100", "AAPL")], ingest_run_id="figi-test")
    return store


def test_종목_축으로_바뀐다(loaded) -> None:  # type: ignore[no-untyped-def]
    """``CUSIP:037833100`` 이 ``US:AAPL`` 이 되어야 prices 와 붙는다."""
    frame = read_institutional_holdings(loaded, as_of=AS_OF)
    assert list(frame["entity_id"]) == ["US:AAPL"]
    # 원본 CUSIP 은 남는다. 두 축을 다 들고 있어야 검증이 된다.
    assert list(frame["cusip"]) == ["037833100"]


def test_매핑이_없는_CUSIP_은_빠진다(loaded) -> None:  # type: ignore[no-untyped-def]
    """억지로 붙이지 않는다. 이름이 같아도 붙이지 않는다."""
    frame = read_institutional_holdings(loaded, as_of=AS_OF)
    assert "CUSIP:00032Q104" not in set(frame["entity_id"])
    assert "00032Q104" not in set(frame["cusip"])


def test_한_CUSIP_이_티커_둘에_붙으면_아예_뺀다(store) -> None:  # type: ignore[no-untyped-def]
    """아무거나 고르면 **남의 종목 수급이 이 종목 신호**가 된다."""
    store.append("filings_13f", [holding("037833100", 100.0)], ingest_run_id="13f-test")
    store.append(
        "security_ids",
        [identifier("037833100", "AAPL"), identifier("037833100", "AAPB")],
        ingest_run_id="figi-test",
    )
    assert cusip_to_entity(store, as_of=AS_OF).empty
    assert read_institutional_holdings(store, as_of=AS_OF).empty


def test_커버리지가_낮은_분기는_통째로_버린다(store) -> None:  # type: ignore[no-untyped-def]
    """매핑은 스냅샷이라 낡는다. 새 분기가 조용히 반쪽만 들어오는 것을 막는다.

    행이 0 이 되면 알아채지만 **그럴듯하게 줄어들면 아무도 못 알아챈다** —
    남은 반쪽으로 잰 횡단면 순위는 시장이 아니라 "매핑이 오래된 종목 집합"
    을 재는 것이 된다.
    """
    store.append(
        "filings_13f",
        [holding("037833100", 100.0), holding("00032Q104", 900.0)],
        ingest_run_id="13f-test",
    )
    store.append("security_ids", [identifier("037833100", "AAPL")], ingest_run_id="figi-test")
    # 금액 기준 커버리지 10% — 종목 수로 재면 50% 라 통과했을 것이다.
    assert read_institutional_holdings(store, as_of=AS_OF).empty
    assert not read_institutional_holdings(store, as_of=AS_OF, min_mapped_value=0.05).empty


def test_커버리지는_게이트_전의_사실을_보여준다(loaded) -> None:  # type: ignore[no-untyped-def]
    """"왜 종목이 줄었나" 의 답은 걸러진 뒤의 프레임에 남아 있지 않다."""
    coverage = mapping_coverage(loaded, as_of=AS_OF)
    assert list(coverage["rows"]) == [2]
    assert list(coverage["mapped_rows"]) == [1]
    assert coverage["mapped_value"].iloc[0] == pytest.approx(0.9)


def test_종목으로_접을_때_비중은_더하지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    """``weight`` 는 기관마다 분모가 다르다. 더하면 아무 뜻이 없는 숫자가 된다."""
    store.append(
        "filings_13f",
        [
            holding("037833100", 100.0, filer="1067983", weight=0.4),
            holding("037833100", 300.0, filer="1350694", weight=0.2),
        ],
        ingest_run_id="13f-test",
    )
    store.append("security_ids", [identifier("037833100", "AAPL")], ingest_run_id="figi-test")
    folded = by_security(read_institutional_holdings(store, as_of=AS_OF))
    assert len(folded) == 1
    assert folded["value_usd"].iloc[0] == pytest.approx(400.0)
    assert folded["filers"].iloc[0] == 2
    assert folded["max_weight"].iloc[0] == pytest.approx(0.4)


def test_매핑_조회는_창을_좁히지_않는다(loaded, ts: Callable[..., datetime]) -> None:  # type: ignore[no-untyped-def]
    """이 표의 valid_from 은 2015 기준시점이다. lookback 을 주면 통째로 프루닝된다.

    그러면 "매핑이 없다" 와 "매핑을 잘라냈다" 가 똑같이 빈 프레임으로 보인다.
    """
    assert not cusip_to_entity(loaded, as_of=AS_OF).empty
    assert loaded.get("security_ids", as_of=AS_OF, lookback=30).empty
