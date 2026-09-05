"""FINRA 공매도 잔고 — 결제일·공표시각·파싱·페이지네이션."""
from __future__ import annotations

from datetime import UTC, date, datetime

from quant_rl_trading.collectors import finra_short as fs
from quant_rl_trading.collectors.market_hours import Market, trading_days

CSV = (
    '"accountingYearMonthNumber","symbolCode","issueName","issuerServicesGroupExchangeCode",'
    '"marketClassCode","currentShortPositionQuantity","previousShortPositionQuantity",'
    '"stockSplitFlag","averageDailyVolumeQuantity","daysToCoverQuantity","revisionFlag",'
    '"changePercent","changePreviousNumber","settlementDate"\n'
    '"20260814","A","Agilent","A","NYSE","5170553","5749623",,"1369994","3.77",,"-10.07","-579070","2026-08-14"\n'
    '"20260814","ZZZ","Blank","A","NYSE",,,,,,,,,"2026-08-14"\n'
)


def test_settlement_dates_roll_back_to_prior_session() -> None:
    sessions = trading_days(Market.US, date(2026, 1, 1), date(2026, 9, 30))
    days = fs.settlement_dates(date(2026, 8, 1), date(2026, 8, 31), sessions=sessions)
    # 2026-08-15 는 토요일 → 08-14, 말일 08-31 은 월요일
    assert days == [date(2026, 8, 14), date(2026, 8, 31)]


def test_publish_moment_is_ten_business_days_after_settlement() -> None:
    sessions = trading_days(Market.US, date(2026, 8, 1), date(2026, 9, 30))
    moment = fs.interest_publish_moment(date(2026, 8, 14), sessions=sessions)
    # 08-14 뒤 10영업일 = 08-28 (주말 2회 건너뜀), 18:00 ET = 22:00 UTC
    assert moment == datetime(2026, 8, 28, 22, 0, tzinfo=UTC)


def test_parse_interest_reads_by_column_name_and_drops_blank_position() -> None:
    observed = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)
    rows = fs.parse_interest(CSV, observed_at=observed)
    assert len(rows) == 1
    row = rows[0]
    assert row["entity_id"] == "US:A"
    assert row["kind"] == "interest"
    assert row["valid_from"] == datetime(2026, 8, 14, tzinfo=UTC)
    assert row["observed_at"] == observed
    assert row["short_position"] == 5170553.0
    assert row["previous_short_position"] == 5749623.0
    assert row["days_to_cover"] == 3.77
    assert row["average_daily_volume"] == 1369994.0


class _Store:
    def __init__(self) -> None:
        self.appended: list[tuple[str, int, str]] = []
        self.recorded: set[str] = set()

    def ingest_run_recorded(self, table: str, run_id: str) -> bool:
        return run_id in self.recorded

    def append(self, table: str, rows, *, ingest_run_id: str, source: str) -> int:
        self.appended.append((table, len(rows), ingest_run_id))
        self.recorded.add(ingest_run_id)
        return len(rows)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _page(n: int, settled: str = "2026-08-14") -> str:
    head = CSV.splitlines()[0] + "\n"
    body = "".join(
        f'"20260814","S{i}","X","A","NYSE","{100 + i}","90",,"10","1.0",,"1","1","{settled}"\n'
        for i in range(n)
    )
    return head + body


def test_backfiller_paginates_and_refuses_before_publication() -> None:
    sessions = trading_days(Market.US, date(2026, 8, 1), date(2026, 9, 30))
    calls: list[int] = []

    def post(url: str, body: dict) -> str:
        calls.append(body["offset"])
        # 첫 장은 꽉 찼고 둘째 장은 3행 — 거기서 멈춘다
        return _page(fs.INTEREST_PAGE) if body["offset"] == 0 else _page(3)

    store = _Store()
    late = _Clock(datetime(2026, 9, 2, 12, 0, tzinfo=UTC))
    filler = fs.ShortInterestBackfiller(store=store, post=post, clock=late, sessions=sessions)
    result = filler.run_settlement(date(2026, 8, 14))
    assert result.rows == fs.INTEREST_PAGE + 3
    assert calls == [0, fs.INTEREST_PAGE]
    assert store.appended[0][2] == "finra-shortint-2026-08-14"
    # 같은 결제일은 두 번 안 받는다
    assert filler.run_settlement(date(2026, 8, 14)).skipped

    # 08-31 결제분은 09-02 에 아직 공표 전 — 요청 자체를 안 한다
    calls.clear()
    result = filler.run_settlement(date(2026, 8, 31))
    assert result.error == "아직 공표 전"
    assert calls == []
