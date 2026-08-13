"""DART 공시 수집 계약 테스트. 네트워크를 타지 않는다.

지키는 것은 셋이다.

1. **접수일과 관측시각을 섞지 않는다.** DART 는 날짜만 주고 시각은 안 준다
2. **종목을 가릴 수 없는 행은 버린다.** 비상장 계열사 공시가 섞여 온다
3. **분류는 한 곳에서만 한다.** Analyst 가 제목 문자열을 뒤지기 시작하면
   규칙이 흩어지고 두 곳이 서로 다른 판단을 하게 된다
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from lattice.collectors.dart_filings import (
    OTHER,
    classify,
    filings_run_id,
    normalize_filings,
    parse_receipt_date,
)


def observed(day: date) -> datetime:
    """접수일 그날 장 마감 이후. 실제 정책과 같은 모양."""
    return datetime(day.year, day.month, day.day, 9, tzinfo=UTC)


def filing(**overrides: Any) -> dict[str, Any]:
    row = {
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "report_nm": "분기보고서 (2024.03)",
        "rcept_no": "20240516001421",
        "rcept_dt": "20240516",
        "flr_nm": "삼성전자",
    }
    row.update(overrides)
    return row


def test_접수일과_관측시각이_다르게_찍힌다() -> None:
    rows = normalize_filings([filing()], market="KR", observed_at_for=observed)

    assert len(rows) == 1
    row = rows[0]
    assert row["valid_from"] == datetime(2024, 5, 16, tzinfo=UTC)
    assert row["observed_at"] == datetime(2024, 5, 16, 9, tzinfo=UTC)
    assert row["observed_at"] > row["valid_from"]
    assert row["entity_id"] == "KR:005930"
    assert row["doc_id"] == "20240516001421"


def test_종목코드가_없으면_버린다() -> None:
    """비상장 계열사 공시. 어느 종목의 사건인지 말할 수 없다."""
    rows = normalize_filings(
        [filing(stock_code=""), filing(stock_code="   ")],
        market="KR",
        observed_at_for=observed,
    )
    assert rows == []


def test_접수번호가_같으면_한_행() -> None:
    rows = normalize_filings(
        [filing(), filing()], market="KR", observed_at_for=observed
    )
    assert len(rows) == 1


def test_접수일이_깨진_행은_버린다() -> None:
    rows = normalize_filings(
        [filing(rcept_dt="2024-05-16"), filing(rcept_dt=""), filing(rcept_dt="20241332")],
        market="KR",
        observed_at_for=observed,
    )
    assert rows == []
    assert parse_receipt_date("20240516") == date(2024, 5, 16)
    assert parse_receipt_date("20241332") is None


def test_정정공시도_같은_분류로_남는다() -> None:
    """정정 자체가 이벤트다. 그리고 접수번호가 달라 원본을 덮지 않는다."""
    rows = normalize_filings(
        [
            filing(report_nm="유상증자결정", rcept_no="20240516001421"),
            filing(report_nm="[기재정정]유상증자결정", rcept_no="20240517000002"),
        ],
        market="KR",
        observed_at_for=observed,
    )
    assert len(rows) == 2
    assert {row["doc_type"] for row in rows} == {"dilution"}


def test_분류_규칙() -> None:
    assert classify("분기보고서 (2024.03)") == "earnings"
    assert classify("연결재무제표기준영업(잠정)실적(공정공시)") == "earnings"
    assert classify("자기주식취득 신탁계약 체결 결정") == "buyback"
    assert classify("현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정") == "dividend"
    assert classify("유상증자결정") == "dilution"
    assert classify("무상증자결정") == "split"
    assert classify("불성실공시법인지정") == "distress"
    assert classify("단일판매ㆍ공급계약체결") == "contract"
    # 못 걸러도 버리지 않는다. 공시가 몰리는 것 자체가 정보다.
    assert classify("기타경영사항(자율공시)") == OTHER


def test_재개_단위는_날짜와_시장구분() -> None:
    assert filings_run_id("KR", date(2024, 5, 16), "Y") == (
        "bf-dart-filings-KR-2024-05-16-Y"
    )
    assert filings_run_id("KR", date(2024, 5, 16), "K") != filings_run_id(
        "KR", date(2024, 5, 16), "Y"
    )
