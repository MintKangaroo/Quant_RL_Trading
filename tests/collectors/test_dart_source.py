"""DART 재무 — 회계기간과 접수일은 다른 시각이다.

여기서 고정하는 것은 하나다. **2024 1분기 실적은 2024-03-31 에 유효하지만
2024-05-16 에야 알 수 있었다.** 둘을 혼동하면 3월 31일에 5월 실적을 아는
백테스트가 되고, 그 백테스트의 재무 팩터는 전부 가짜 알파를 낸다.

간격이 한 달 반이라 눈에 잘 띄지도 않는다 — 하루 이틀이면 이상하다고 느끼지만
분기 실적은 "원래 늦게 나오는 것" 이라 넘어가기 쉽다.

샘플은 실호출 응답 모양 그대로다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from quant_rl_trading.collectors.dart_source import (
    ACCOUNTS,
    MAX_CORPS_PER_CALL,
    FilingPolicy,
    batched,
    normalize_financials,
    quarter_end,
    receipt_date,
)
from quant_rl_trading.collectors.publication import NotYetPublished
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, tzinfo=UTC)

#: 삼성전자 2024 1분기. rcept_no 앞 8자리가 접수일이다.
SAMPLE = [
    {
        "stock_code": "005930", "corp_code": "00126380", "rcept_no": "20240516001421",
        "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "BS", "sj_nm": "재무상태표",
        "account_nm": "자산총계", "thstrm_amount": "470,899,812,000,000",
        "bsns_year": "2024", "reprt_code": "11013",
    },
    {
        "stock_code": "005930", "corp_code": "00126380", "rcept_no": "20240516001421",
        "fs_div": "CFS", "sj_div": "IS", "account_nm": "매출액",
        "thstrm_amount": "71,919,169,000,000",
    },
    {
        "stock_code": "005930", "corp_code": "00126380", "rcept_no": "20240516001421",
        "fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익(손실)",
        "thstrm_amount": "(1,234,000,000)",     # DART 는 음수를 괄호로 준다
    },
    {
        # 같은 회사·같은 지표의 별도재무제표. 연결이 이겨야 한다.
        "stock_code": "005930", "corp_code": "00126380", "rcept_no": "20240516001421",
        "fs_div": "OFS", "sj_div": "BS", "account_nm": "자산총계",
        "thstrm_amount": "1,000,000",
    },
    {   # 우리가 안 쓰는 계정은 버린다
        "stock_code": "005930", "corp_code": "00126380", "rcept_no": "20240516001421",
        "fs_div": "CFS", "sj_div": "BS", "account_nm": "이연법인세자산",
        "thstrm_amount": "999",
    },
]


@pytest.fixture
def policy():  # type: ignore[no-untyped-def]
    return FilingPolicy(hour_kst=18, clock=ReplayClock(NOW))


def normalized(policy, rows: list[dict[str, Any]] | None = None):  # type: ignore[no-untyped-def]
    return normalize_financials(
        SAMPLE if rows is None else rows,
        market="KR", year=2024, quarter=1,
        observed_at_for=policy.for_filing,
    )


# -- 이중시간 --------------------------------------------------------------------


def test_fiscal_period_and_filing_date_are_different_times(policy) -> None:
    """이 테스트가 이 수집기의 존재 이유다."""
    row = next(item for item in normalized(policy) if item["metric"] == "revenue")

    # 그 사실이 유효해진 시점 = 회계기간 종료일
    assert row["valid_from"] == datetime(2024, 3, 31, tzinfo=UTC)
    # 우리가 알 수 있었던 시점 = 공시 접수일 18:00 KST = 09:00 UTC
    assert row["observed_at"] == datetime(2024, 5, 16, 9, 0, tzinfo=UTC)
    # 간격이 한 달 반이다. 이걸 혼동하면 재무 팩터가 통째로 가짜가 된다.
    assert (row["observed_at"] - row["valid_from"]).days == 46


def test_receipt_date_comes_from_rcept_no() -> None:
    """공시 목록 API 를 따로 부를 필요가 없다."""
    assert receipt_date("20240516001421") == date(2024, 5, 16)
    assert receipt_date("") is None
    assert receipt_date("abc") is None


def test_rows_without_a_receipt_date_are_dropped(policy) -> None:
    """언제 알 수 있었는지 모르는 사실은 저장하지 않는다 (불변식 3)."""
    broken = [{**SAMPLE[0], "rcept_no": ""}]

    assert normalized(policy, broken) == []


def test_filing_not_yet_received_is_refused() -> None:
    """미래에 접수될 공시를 지금 저장할 방법은 없어야 한다."""
    early = FilingPolicy(hour_kst=18, clock=ReplayClock(datetime(2024, 5, 1, tzinfo=UTC)))

    with pytest.raises(NotYetPublished):
        early.for_filing(date(2024, 5, 16))


def test_publication_hour_is_after_the_filing_deadline(policy) -> None:
    """자정으로 잡으면 그날 아침부터 실적을 알고 있던 것이 된다.

    DART 는 날짜만 준다. 시각을 모르면 늦게 잡는 것이 정직하다.
    """
    moment = policy.for_filing(date(2024, 5, 16))

    assert moment.astimezone(UTC).hour == 9   # 18:00 KST


def test_quarter_end_maps_to_period_end() -> None:
    assert quarter_end(2024, 1) == datetime(2024, 3, 31, tzinfo=UTC)
    assert quarter_end(2024, 4) == datetime(2024, 12, 31, tzinfo=UTC)


# -- 정규화 ----------------------------------------------------------------------


def test_consolidated_statements_win_over_separate(policy) -> None:
    """연결과 별도를 섞으면 같은 지표가 회사마다 다른 의미가 된다."""
    row = next(item for item in normalized(policy) if item["metric"] == "total_assets")

    assert row["value"] == pytest.approx(470_899_812_000_000.0)
    assert row["report_type"] == "dart_cfs"


def test_parenthesised_negatives_stay_negative(policy) -> None:
    """DART 는 음수를 괄호로 준다. 부호를 잃으면 적자가 흑자가 된다."""
    row = next(item for item in normalized(policy) if item["metric"] == "net_income")

    assert row["value"] == pytest.approx(-1_234_000_000.0)


def test_unknown_accounts_are_dropped(policy) -> None:
    metrics = {item["metric"] for item in normalized(policy)}

    assert metrics <= set(ACCOUNTS.values())
    assert "이연법인세자산" not in metrics


def test_long_format_one_row_per_metric(policy) -> None:
    """지표 하나가 행 하나. 계정이 늘어도 스키마를 안 고친다."""
    rows = normalized(policy)

    assert {item["metric"] for item in rows} == {"total_assets", "revenue", "net_income"}
    assert all(item["fiscal_period"] == "2024Q1" for item in rows)
    assert all(item["entity_id"] == "KR:005930" for item in rows)


# -- 배치 ------------------------------------------------------------------------


def test_batches_respect_the_measured_limit() -> None:
    """실측 상한이 100이다. 200이면 status 021 로 아무것도 안 온다."""
    codes = [f"{index:08d}" for index in range(250)]

    chunks = list(batched(codes))

    assert [len(chunk) for chunk in chunks] == [100, 100, 50]
    assert all(len(chunk) <= MAX_CORPS_PER_CALL for chunk in chunks)
