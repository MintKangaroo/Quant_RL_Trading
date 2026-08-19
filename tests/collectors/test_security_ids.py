"""CUSIP → 티커 매핑 — **틀린 매핑은 에러를 내지 않는다.**

남의 종목 수급이 이 종목 신호가 되고, 그 신호로 주문이 나갈 때까지 아무도
모른다. 그래서 "안 붙이고 남기는" 쪽 동작을 테스트로 못 박는다.
"""

from __future__ import annotations

import httpx
import pytest

from quant_rl_trading.collectors.security_ids import (
    CINS_TYPE,
    CUSIP_TYPE,
    IDENTITY_EPOCH,
    Mapping,
    Miss,
    OpenFigiClient,
    SecurityIdError,
    id_type_for,
    ingest_run_id,
    mapping_job,
    parse_result,
    to_rows,
)


def figi_row(ticker: str, name: str = "SOME CO", security_type: str = "Common Stock") -> dict:
    return {
        "figi": f"BBG{ticker}",
        "name": name,
        "ticker": ticker,
        "exchCode": "US",
        "compositeFIGI": f"BBG{ticker}",
        "securityType": security_type,
        "marketSector": "Equity",
    }


# -- 실측 1: 문자로 시작하면 CINS 다 --------------------------------------------


def test_문자로_시작하는_식별자는_CINS_로_묻는다() -> None:
    """``G1151C101``(ACCENTURE PLC)을 ``ID_CUSIP`` 으로 물으면 "없다" 가 온다.

    없는 게 아니라 **묻는 방식이 틀린 것**이다. 우리 CUSIP 4,150개 중
    361개가 여기 해당한다 — 모르면 8.7% 를 매핑 실패로 적고 넘어간다.
    """
    assert id_type_for("G1151C101") == CINS_TYPE
    assert id_type_for("H8817H100") == CINS_TYPE
    assert id_type_for("037833100") == CUSIP_TYPE
    assert mapping_job("G1151C101")["idType"] == CINS_TYPE


def test_요청은_미국_통합거래소로_좁힌다() -> None:
    """안 좁히면 한 CUSIP 에 상장 거래소별로 100줄 넘게 온다(실측 118줄).

    받아서 거르나 안 받나 결과는 같은데, 받으면 응답이 100배 커진다.
    """
    assert mapping_job("037833100")["exchCode"] == "US"


# -- 안 붙는 것은 안 붙은 채로 --------------------------------------------------


def test_여러_티커가_오면_붙이지_않는다() -> None:
    """아무거나 고르면 그게 **조용히 틀린 매핑**이다."""
    result = parse_result("12345678A", {"data": [figi_row("AAA"), figi_row("BBB")]})
    assert isinstance(result, Miss)
    assert result.reason == "ambiguous:AAA,BBB"


def test_없는_것과_에러를_구분한다() -> None:
    """채권·비상장은 "없다" 이고, 형식 오류는 우리 잘못이다. 같이 세면 안 된다."""
    missing = parse_result("037833100", {"warning": "No identifier found."})
    broken = parse_result("037833100", {"error": "Invalid idValue format."})
    assert isinstance(missing, Miss) and missing.reason == "not_found"
    assert isinstance(broken, Miss) and broken.reason.startswith("openfigi_error:")


def test_매핑되면_시장_접두어를_붙인다() -> None:
    result = parse_result("037833100", {"data": [figi_row("AAPL", "APPLE INC")]})
    assert isinstance(result, Mapping)
    assert result.entity_id == "US:AAPL"


# -- 짝 맞추기 ------------------------------------------------------------------


def test_응답_개수가_다르면_멈춘다() -> None:
    """순서로 짝을 맞춘다. 개수가 어긋난 채로 진행하면 **A 의 답이 B 의 매핑**이 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"data": [figi_row("AAPL")]}])

    client = OpenFigiClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        interval=0.0,
        sleep=lambda _: None,
    )
    with pytest.raises(SecurityIdError):
        client.map_batch(["037833100", "02079K305"])


def test_요청당_10건을_넘기지_않는다() -> None:
    """키 없이 100건을 보내면 ``413 Request may only contain 10 mapping jobs.`` 다."""
    client = OpenFigiClient(interval=0.0, sleep=lambda _: None)
    with pytest.raises(SecurityIdError):
        client.map_batch([f"{index:09d}" for index in range(11)])


# -- 이중시간 -------------------------------------------------------------------


def test_기준시점으로_적재하고_조회시각은_따로_남긴다() -> None:
    """조회한 날로 찍으면 **과거 as_of 조회가 이 표를 한 행도 못 본다.**

    식별자는 그 시점에도 공개된 사실이었으므로 기준시점으로 넣는다. 대신
    우리가 실제로 언제 물어봤는지는 ``mapped_at`` 에 남는다 — 그 열이 없으면
    "이 매핑은 언제 찍은 스냅샷인가" 를 창고에 물을 수 없다.
    """
    from datetime import UTC, datetime

    mapped_at = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
    rows = to_rows(
        [parse_result("037833100", {"data": [figi_row("AAPL", "APPLE INC")]})],  # type: ignore[list-item]
        mapped_at=mapped_at,
    )
    assert rows[0]["valid_from"] == IDENTITY_EPOCH
    assert rows[0]["observed_at"] == IDENTITY_EPOCH
    assert rows[0]["mapped_at"] == mapped_at
    assert rows[0]["id_type"] == "CUSIP"


def test_run_id_는_날짜_눈금이다() -> None:
    from datetime import UTC, datetime

    assert ingest_run_id(datetime(2026, 8, 19, 12, tzinfo=UTC)) == "figi-20260819"
