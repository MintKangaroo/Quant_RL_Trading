"""백필 계약 테스트.

여기서 지키는 것은 하나다 — **백필된 과거 데이터가 그 시점에 알 수 있었던
것과 정확히 일치해야 한다.** 틀리면 5년치 전부가 조용히 거짓이 되고, 그 위의
백테스트·IC·보상은 전부 무의미해진다.

네트워크를 타지 않는다. KRX 자리에 가짜 소스를 끼운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.collectors.backfill import (
    PRICES,
    UNIVERSE,
    Backfiller,
    ProgressLog,
    session_run_id,
)
from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.publication import (
    NotATradingDay,
    NotYetPublished,
    PublicationPolicy,
)
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

KST = ZoneInfo("Asia/Seoul")

#: 2024-03-04(월) ~ 03-07(목). 전부 거래일이다.
D1, D2, D3 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)

#: 15:30 마감 + 30분.
LAG = 1800.0

#: 003570 은 D3 명단에서 사라진다 — 상장폐지.
LISTED = {
    D1: [
        {"code": "005930", "name": "삼성전자"},
        {"code": "003570", "name": "사라질회사"},
        {"code": "123450", "name": "미래스팩1호"},
    ],
    D2: [
        {"code": "005930", "name": "삼성전자"},
        {"code": "003570", "name": "사라질회사"},
        {"code": "123450", "name": "미래스팩1호"},
    ],
    D3: [
        {"code": "005930", "name": "삼성전자"},
        {"code": "123450", "name": "미래스팩1호"},
    ],
}

BARS = {
    D1: [
        {"code": "005930", "open": 70000, "high": 71000, "low": 69500,
         "close": 70500, "volume": 12345678, "value": 8.7e11},
        {"code": "003570", "open": 1000, "high": 1050, "low": 990,
         "close": 1010, "volume": 5000, "value": 5.05e6},
        {"code": "123450", "open": 2000, "high": 2000, "low": 2000,
         "close": 2000, "volume": 100, "value": 2.0e5},
    ],
    D2: [
        {"code": "005930", "open": 70500, "high": 72000, "low": 70000,
         "close": 71800, "volume": 9000000, "value": 6.4e11},
        {"code": "003570", "open": 1010, "high": 1010, "low": 900,
         "close": 900, "volume": 90000, "value": 8.1e7},
        {"code": "123450", "open": 2000, "high": 2005, "low": 2000,
         "close": 2005, "volume": 50, "value": 1.0e5},
    ],
    D3: [
        {"code": "005930", "open": 71800, "high": 72500, "low": 71000,
         "close": 72000, "volume": 8000000, "value": 5.8e11},
        {"code": "123450", "open": 2005, "high": 2010, "low": 2005,
         "close": 2010, "volume": 40, "value": 8.0e4},
    ],
}


@dataclass
class FakeSource:
    """KRX 자리. 호출 횟수를 세고, 지정한 날에 터진다."""

    name: str = "krx-fake"
    fail_on: date | None = None
    calls: list[date] = field(default_factory=list)

    def listed_on(self, day: date) -> list[dict[str, Any]]:
        if day == self.fail_on:
            raise CollectorError(f"주입된 실패: {day}")
        self.calls.append(day)
        return [dict(item) for item in LISTED[day]]

    def ohlcv_on(self, day: date) -> list[dict[str, Any]]:
        if day == self.fail_on:
            raise CollectorError(f"주입된 실패: {day}")
        return [dict(item) for item in BARS[day]]


def published_at(day: date, lag: float = LAG) -> datetime:
    return (
        datetime(day.year, day.month, day.day, 15, 30, tzinfo=KST)
        + timedelta(seconds=lag)
    ).astimezone(UTC)


def make_backfiller(store: Any, tmp_path: Path, **kwargs: Any) -> Backfiller:
    # 백필을 도는 벽시계 시각은 구간보다 한참 뒤다. 실제 백필이 그렇다.
    clock = ReplayClock(datetime(2024, 6, 1, tzinfo=UTC))
    return Backfiller(
        store=store,
        source=kwargs.pop("source", FakeSource()),
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        market=Market.KR,
        **kwargs,
    )


# -- 관측시각 -----------------------------------------------------------------


def test_observed_at_is_publication_time_not_backfill_time(store, tmp_path) -> None:
    """M1 의 핵심. 마감 직전 조회에는 그날 봉이 없고, 공표 직후에는 있다.

    이게 깨지면 5년치가 하루씩 미래를 보거나(너무 이름), 과거 조회에서
    통째로 사라진다(너무 늦음).
    """
    backfiller = make_backfiller(store, tmp_path)
    assert backfiller.run_session(D1).ok

    just_before = datetime(2024, 3, 4, 15, 59, tzinfo=KST)
    just_after = datetime(2024, 3, 4, 16, 1, tzinfo=KST)

    assert store.get(PRICES, as_of=just_before).empty
    visible = store.get(PRICES, as_of=just_after)
    assert not visible.empty
    assert set(visible["entity_id"]) == {"KR:005930", "KR:003570", "KR:123450"}

    # 백필을 돌린 벽시계 시각(2024-06-01)은 어디에도 찍히지 않는다.
    assert visible["observed_at"].max().to_pydatetime() == published_at(D1)


def test_publication_policy_refuses_future_sessions() -> None:
    """아직 공표되지 않은 봉은 저장할 방법 자체가 없어야 한다."""
    clock = ReplayClock(datetime(2024, 3, 4, 6, 0, tzinfo=UTC))  # 15:00 KST, 장중
    policy = PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock)

    with pytest.raises(NotYetPublished):
        policy.for_session(D1)


def test_publication_policy_refuses_holidays() -> None:
    clock = ReplayClock(datetime(2024, 6, 1, tzinfo=UTC))
    policy = PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock)

    with pytest.raises(NotATradingDay):
        policy.for_session(date(2024, 3, 1))  # 삼일절


def test_publication_lag_shifts_visibility(store, tmp_path) -> None:
    """지연을 늘리면 그만큼 늦게 보인다 — 설정이 실제로 먹는지 확인."""
    clock = ReplayClock(datetime(2024, 6, 1, tzinfo=UTC))
    backfiller = Backfiller(
        store=store,
        source=FakeSource(),
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=7200.0, clock=clock),
        market=Market.KR,
    )
    assert backfiller.run_session(D1).ok

    assert store.get(PRICES, as_of=datetime(2024, 3, 4, 17, 0, tzinfo=KST)).empty
    assert not store.get(PRICES, as_of=datetime(2024, 3, 4, 17, 31, tzinfo=KST)).empty


# -- 생존편향 -----------------------------------------------------------------


def test_delisted_symbol_survives_in_past_universe(store, tmp_path) -> None:
    """상폐 종목이 상폐 이전 조회에 살아 있어야 한다.

    오늘 명단으로 과거를 그리면 003570 은 처음부터 없던 회사가 되고,
    그 종목에서 난 손실은 백테스트에서 영원히 사라진다.
    """
    backfiller = make_backfiller(store, tmp_path)
    for day in (D1, D2, D3):
        assert backfiller.run_session(day).ok

    before = store.get(UNIVERSE, as_of=published_at(D2) + timedelta(minutes=1))
    listed_then = before[before["is_listed"].astype(bool)]["entity_id"]
    assert "KR:003570" in set(listed_then)

    after = store.get(UNIVERSE, as_of=published_at(D3) + timedelta(minutes=1))
    latest = after.sort_values("valid_from").groupby("entity_id").tail(1)
    delisted = latest[~latest["is_listed"].astype(bool)]
    assert set(delisted["entity_id"]) == {"KR:003570"}
    assert delisted.iloc[0]["name"] == "사라질회사"
    assert delisted.iloc[0]["delisted_on"].to_pydatetime() == datetime(
        2024, 3, 6, tzinfo=UTC
    )

    # 상폐 종목의 과거 시세도 남아 있다. 명단만 남고 가격이 없으면 쓸모없다.
    prices = store.get(PRICES, as_of=published_at(D2) + timedelta(minutes=1))
    assert not prices[prices["entity_id"] == "KR:003570"].empty


def test_spac_is_listed_but_not_tradable(store, tmp_path) -> None:
    """데이터 유니버스와 매매 유니버스는 다르다."""
    backfiller = make_backfiller(store, tmp_path)
    assert backfiller.run_session(D1).ok

    frame = store.get(UNIVERSE, as_of=published_at(D1) + timedelta(minutes=1))
    spac = frame[frame["entity_id"] == "KR:123450"].iloc[0]
    assert bool(spac["is_listed"]) is True
    assert bool(spac["is_tradable"]) is False


# -- 재개 ---------------------------------------------------------------------


def test_resume_skips_completed_sessions(store, tmp_path) -> None:
    """중단 후 재실행이 이어받고, 결과가 무중단 실행과 같아야 한다."""
    broken = FakeSource(fail_on=D2)
    first = make_backfiller(store, tmp_path, source=broken)

    results = [first.run_session(day) for day in (D1, D2, D3)]
    assert results[0].ok and results[2].ok
    assert not results[1].ok and "주입된 실패" in (results[1].error or "")

    # 새 프로세스가 뜬 셈 치고 상태를 통째로 새로 만든다.
    second = make_backfiller(store, tmp_path, source=FakeSource())
    pending = second.pending([D1, D2, D3])
    assert pending == [D2]

    assert second.run_session(D2).ok
    assert second.run_session(D1).skipped

    prices = store.get(PRICES, as_of=datetime(2024, 6, 1, tzinfo=UTC))
    assert len(prices) == sum(len(BARS[day]) for day in (D1, D2, D3))


def test_rerun_does_not_duplicate(store, tmp_path) -> None:
    """append-only 창고에서 중복은 지울 수 없다. 애초에 넣지 않는다."""
    backfiller = make_backfiller(store, tmp_path)
    assert backfiller.run_session(D1).ok
    again = make_backfiller(store, tmp_path).run_session(D1)

    assert again.skipped
    assert len(store.get(PRICES, as_of=datetime(2024, 6, 1, tzinfo=UTC))) == len(BARS[D1])


def test_run_ids_are_deterministic() -> None:
    """재개가 성립하는 근거. run id 가 흔들리면 같은 날이 두 번 들어간다."""
    assert session_run_id(PRICES, Market.KR, D1) == "bf-prices-KR-20240304"
    assert session_run_id(UNIVERSE, Market.KR, D1) == "bf-universe-KR-20240304"


def test_progress_log_is_append_only(store, tmp_path) -> None:
    backfiller = make_backfiller(store, tmp_path)
    log = ProgressLog(root=tmp_path, plan_id="KR-test")

    for day in (D1, D2):
        log.record(backfiller.run_session(day), at=datetime(2024, 6, 1, tzinfo=UTC))

    assert len(log.path.read_text(encoding="utf-8").strip().splitlines()) == 2


# -- 정규화 -------------------------------------------------------------------


def test_only_codes_filters_trial_run(store, tmp_path) -> None:
    backfiller = make_backfiller(store, tmp_path, only_codes=frozenset({"005930"}))
    result = backfiller.run_session(D1)

    assert result.prices == 1
    frame = store.get(PRICES, as_of=published_at(D1) + timedelta(minutes=1))
    assert set(frame["entity_id"]) == {"KR:005930"}


def test_raw_payload_is_archived(store, tmp_path) -> None:
    """정규화에 버그가 있어도 원본이 있으면 다시 만들 수 있다."""
    backfiller = make_backfiller(store, tmp_path)
    backfiller.run_session(D1)

    saved = list((tmp_path / "raw").rglob("*.json"))
    assert saved, "원본이 저장되지 않았다"
    assert any("session-2024-03-04" in path.name for path in saved)


def test_prices_carry_no_adjusted_factor(store, tmp_path) -> None:
    """원주가만 저장한다. 수정주가에는 미래의 분할이 들어 있다."""
    backfiller = make_backfiller(store, tmp_path)
    backfiller.run_session(D1)

    frame = store.get(PRICES, as_of=published_at(D1) + timedelta(minutes=1))
    assert frame["adj_factor"].isna().all()
    samsung = frame[frame["entity_id"] == "KR:005930"].iloc[0]
    assert samsung["close"] == 70500.0
