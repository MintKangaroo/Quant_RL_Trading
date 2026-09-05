"""LS 수급(t1717) — 필드맵과 관측시각.

선행 프로젝트가 US 계좌 정규화에서 한 실수가 **미검증 필드맵 위에 짓는 것**
이었다 (postmortem-ls.md §6-6). 그래서 이 필드맵은 실호출로 검증한 뒤 여기에
고정한다. 아래 샘플은 2024-06-24 삼성전자 실제 응답이다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from quant_rl_trading.collectors.krx_source import KRXUnavailable
from quant_rl_trading.collectors.ls_flow import (
    INVESTOR_FIELDS,
    MAX_ROWS_PER_CALL,
    LSFlowBackfiller,
    date_windows,
    flow_run_id,
    normalize_flow_rows,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 11, tzinfo=UTC)

#: 실호출로 받은 응답. 값을 지어내지 않았다.
SAMPLE = [
    {
        "date": "20240624", "close": "80600", "volume": "13022600",
        "tjj0016_vol": "262698", "tjj0016_dan": "80264",
        "tjj0018_vol": "281038", "tjj0018_dan": "80215",
        "tjj0008_vol": "-729677", "tjj0008_dan": "80164",
        "tjj0006_vol": "-230785", "tjj0006_dan": "80200",
        "tjj0003_vol": "-44218", "tjj0003_dan": "80190",
        "tjj0001_vol": "12000", "tjj0001_dan": "80100",
        "tjj0002_vol": "5000", "tjj0002_dan": "80150",
        "tjj0000_vol": "-3000", "tjj0000_dan": "80120",
        "tjj0017_vol": "185941", "tjj0017_dan": "80180",
    },
    {"date": "", "close": "-"},  # 쓰레기 행은 버린다
]


@pytest.fixture
def policy():  # type: ignore[no-untyped-def]
    return PublicationPolicy(
        market=Market.KR, lag_seconds=1800.0, clock=ReplayClock(NOW)
    )


def normalized(policy):  # type: ignore[no-untyped-def]
    return normalize_flow_rows(
        SAMPLE, entity_id="KR:005930", market="KR",
        observed_at_for=policy.for_session,
        collected_at=NOW,
    )


# -- 필드맵 -------------------------------------------------------------------


def test_one_row_per_investor(policy) -> None:
    """주체 하나가 행 하나. 넓게 저장하면 주체가 늘 때마다 스키마를 고쳐야 한다."""
    rows = normalized(policy)

    assert len(rows) == len(INVESTOR_FIELDS)
    assert {row["investor"] for row in rows} == set(INVESTOR_FIELDS.values())


def test_net_value_is_volume_times_price(policy) -> None:
    """금액이 있어야 시총 대비 정규화가 된다.

    순매수 100만주는 대형주에선 무의미하고 소형주에선 폭등 신호다.
    """
    rows = {row["investor"]: row for row in normalized(policy)}
    foreign = rows["외인계"]

    assert foreign["net_volume"] == pytest.approx(262698.0)
    assert foreign["net_value"] == pytest.approx(262698.0 * 80264.0)


def test_sell_side_stays_negative(policy) -> None:
    """부호를 잃으면 순매수와 순매도가 같아진다."""
    rows = {row["investor"]: row for row in normalized(policy)}

    assert rows["개인"]["net_volume"] < 0
    assert rows["개인"]["net_value"] < 0


def test_investor_identity_holds(policy) -> None:
    """외인계 + 기관 + 개인 + 기타계 = 0. 매수와 매도는 짝이 맞는다.

    필드를 잘못 매핑하면 이 항등식이 먼저 깨진다.
    """
    rows = {row["investor"]: row for row in normalized(policy)}
    total = sum(rows[name]["net_volume"] for name in ("외인계", "기관", "개인", "기타계"))

    assert total == pytest.approx(0.0, abs=1.0)


def test_garbage_rows_are_dropped(policy) -> None:
    assert all(row["valid_from"].year == 2024 for row in normalized(policy))


# -- 관측시각 -----------------------------------------------------------------


def test_observed_at_is_publication_not_session_midnight(policy) -> None:
    """봉과 같은 규칙이다. 세션 자정에 그날 수급을 알 수 있었을 리 없다."""
    row = normalized(policy)[0]

    assert row["valid_from"] == datetime(2024, 6, 24, tzinfo=UTC)
    # 15:30 KST 마감 + 30분 = 16:00 KST = 07:00 UTC
    assert row["observed_at"] == datetime(2024, 6, 24, 7, 0, tzinfo=UTC)


def test_unpublished_sessions_are_dropped() -> None:
    """아직 공표되지 않은 세션은 지어내지 않고 버린다."""
    early = PublicationPolicy(
        market=Market.KR, lag_seconds=1800.0,
        clock=ReplayClock(datetime(2024, 6, 24, 1, tzinfo=UTC)),  # 장중
    )

    assert normalize_flow_rows(
        SAMPLE, entity_id="KR:005930", market="KR",
        observed_at_for=early.for_session, collected_at=NOW,
    ) == []


def test_observed_at_is_not_earlier_than_collection() -> None:
    """**그날 것을 그날 받으면 받은 시각이 하한이다.**

    t1717 이 하루 중 언제 값을 내는지 우리는 모른다. 봉의 공표 일정
    (마감+30분 = 16:00 KST)을 그대로 물려 쓰면, 22:40 에 받은 수급이
    "16:00 에 알 수 있었다" 로 적힌다. Analyst 의 세션 as_of 가 정확히
    16:00 이라 그 6시간 40분이 경계에서 그대로 읽힌다.
    """
    session = date(2024, 6, 24)
    collected = datetime(2024, 6, 24, 13, 40, tzinfo=UTC)  # 22:40 KST
    policy = PublicationPolicy(
        market=Market.KR, lag_seconds=1800.0, clock=ReplayClock(collected)
    )

    rows = normalize_flow_rows(
        SAMPLE, entity_id="KR:005930", market="KR",
        observed_at_for=policy.for_session, collected_at=collected,
    )

    assert rows, "행이 나와야 한다"
    assert all(row["observed_at"] == collected for row in rows)
    assert rows[0]["valid_from"] == datetime(session.year, session.month, session.day, tzinfo=UTC)


def test_old_sessions_keep_the_publication_estimate() -> None:
    """**5년치를 오늘 받았다고 5년치를 오늘 알았다고 적지 않는다.**

    하한을 무조건 걸면 과거 백필이 통째로 "오늘 관측" 이 되고, 게이트가
    정직하게 동작해서 리플레이가 빈 시장 위에서 돈다.
    """
    rows = normalize_flow_rows(
        SAMPLE, entity_id="KR:005930", market="KR",
        observed_at_for=PublicationPolicy(
            market=Market.KR, lag_seconds=1800.0, clock=ReplayClock(NOW)
        ).for_session,
        collected_at=NOW,  # 2026-08-11 — 세션은 2024-06-24
    )

    assert rows[0]["observed_at"] == datetime(2024, 6, 24, 7, 0, tzinfo=UTC)


def test_is_final_is_unknown_not_true() -> None:
    """**모르는 것을 True 로 적지 않는다.** t1717 응답에 잠정/확정 구분이 없다.

    예전에는 여기서 True 를 박았고, 그 결과 창고의 flows 는 한 행도 빠짐없이
    "확정치" 가 됐다. flow_kr 은 그 칸으로 확정치를 우선하려 했는데 정렬 키가
    상수라 한 번도 발화하지 않았다.
    """
    assert all(row["is_final"] is None for row in normalized(
        PublicationPolicy(market=Market.KR, lag_seconds=1800.0, clock=ReplayClock(NOW))
    ))


# -- 창 분할 / 재개 ------------------------------------------------------------


def test_windows_cover_the_range_without_gaps() -> None:
    """한 콜에 250행까지만 오므로 창을 옮겨 페이징한다."""
    windows = list(
        date_windows(date(2021, 8, 11), date(2026, 8, 11), sessions_per_call=MAX_ROWS_PER_CALL)
    )

    assert windows[0][0] == date(2021, 8, 11)
    assert windows[-1][1] == date(2026, 8, 11)
    for earlier, later in pairwise(windows):
        assert later[0] == earlier[1] + timedelta(days=1), "창 사이에 구멍이 있다"


def test_run_id_is_per_symbol() -> None:
    assert flow_run_id("KR", "005930") == "bf-flows-KR-005930"


class FakeFlowSource:
    name = "ls-fake"

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = SAMPLE if rows is None else rows
        self.calls: list[tuple[str, date, date]] = []

    def fetch(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        self.calls.append((symbol, start, end))
        return self.rows if start <= date(2024, 6, 24) <= end else []


def make(store, tmp_path, policy, source=None):  # type: ignore[no-untyped-def]
    return LSFlowBackfiller(
        store=store,
        source=source or FakeFlowSource(),
        clock=ReplayClock(NOW),
        archive=RawArchive(root=tmp_path / "raw"),
        observed_at_for=policy.for_session,
    )


def test_backfill_writes_and_resumes(store, tmp_path, policy) -> None:
    store.seed_config_defaults()
    backfiller = make(store, tmp_path, policy)

    first = backfiller.run_symbol("005930", date(2024, 6, 1), date(2024, 6, 30))
    assert first.rows == len(INVESTOR_FIELDS)

    again = make(store, tmp_path, policy).run_symbol("005930", date(2024, 6, 1), date(2024, 6, 30))
    assert again.skipped
    assert make(store, tmp_path, policy).pending(["005930", "000660"]) == ["000660"]


def test_failure_can_be_recorded_without_crashing(store, tmp_path, policy) -> None:
    """**실패를 기록하는 경로**에 테스트가 없어서 18시간짜리 실행이 죽었다.

    BackfillReport 가 실패 목록을 만들 때 날짜를 가정하고 있었는데, 종목 축
    결과에는 날짜가 없다. 84번째 종목에서 수집이 실패한 순간 그것을 기록하려다
    프로세스가 통째로 죽었다 — 실패 처리가 실패한 것이다.
    """
    from quant_rl_trading.collectors.backfill import BackfillReport, ProgressLog
    from quant_rl_trading.collectors.market_hours import Market

    store.seed_config_defaults()

    class Broken:
        name = "ls-broken"

        def fetch(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
            raise KRXUnavailable(f"주입된 실패: {symbol}")

    result = make(store, tmp_path, policy, source=Broken()).run_symbol(
        "005930", date(2024, 6, 1), date(2024, 6, 30)
    )
    assert not result.ok
    assert result.unit == "005930"

    report = BackfillReport(market=Market.KR)
    report.absorb(result)
    assert report.failures == [("005930", result.error)]

    # 진행 로그도 종목 축을 그대로 받아써야 한다.
    log = ProgressLog(root=tmp_path, plan_id="KR-flows-test")
    log.record(result, at=NOW)
    assert '"unit": "005930"' in log.path.read_text(encoding="utf-8")


def test_empty_symbol_is_not_recorded_as_done(store, tmp_path, policy) -> None:
    """빈 결과에 매니페스트를 남기면 나중에 데이터가 생겨도 영영 건너뛴다."""
    store.seed_config_defaults()
    backfiller = make(store, tmp_path, policy, source=FakeFlowSource(rows=[]))

    result = backfiller.run_symbol("999999", date(2024, 6, 1), date(2024, 6, 30))

    assert result.rows == 0 and not result.skipped
    assert backfiller.pending(["999999"]) == ["999999"]
