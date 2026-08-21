"""패널 백필 — 데이터셋마다 다른 공표 지연.

같은 세션의 사실이라도 일봉은 마감 직후에 나오고 **공매도는 T+1~2 에** 나온다.
하나의 지연으로 뭉뚱그리면 늦게 나오는 쪽이 통째로 미래를 본다. 그리고 그
사실은 화면 어디에도 드러나지 않는다 — flow_kr 의 IC 가 좋아 보일 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.panels import PANELS, PanelBackfiller
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

KST_OFFSET = timedelta(hours=9)
LAG = 1800.0

#: 2024-03-04(월) ~ 03-08(금). 전부 거래일.
MON, TUE, WED, THU, FRI = (date(2024, 3, day) for day in (4, 5, 6, 7, 8))

NOW = datetime(2024, 6, 1, tzinfo=UTC)


@dataclass
class FakePanelSource:
    name: str = "krx-fake"
    calls: list[tuple[str, date]] = field(default_factory=list)

    def flows_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("flows", day))
        return [
            {"code": "005930", "investor": "외국인", "net_value": 1.0e9, "net_volume": 1000},
            {"code": "005930", "investor": "개인", "net_value": -1.0e9, "net_volume": -1000},
            # 연기금은 그날 이 종목을 손대지 않았다 — 행 자체가 없다.
            {"code": "000660", "investor": "외국인", "net_value": 5.0e8, "net_volume": 500},
        ]

    def shorting_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("shorting", day))
        return [
            {"code": "005930", "board": "KOSPI", "short_volume": 100.0,
             "total_volume": 10000.0, "short_ratio": 1.0},
        ]

    def fundamentals_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("fundamentals", day))
        return [
            {"code": "005930", "BPS": 50000, "PER": 12.3, "PBR": 1.4,
             "EPS": 5700, "DIV": 2.1, "DPS": 1444,
             "market_cap": 4.2e14, "shares": 5.97e9},
        ]

    def indices_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("indices", day))
        return [
            {"code": "KOSPI:1001", "board": "KOSPI", "open": 2600.0, "high": 2620.0,
             "low": 2590.0, "close": 2610.0, "volume": 1.0e8, "value": 1.0e13},
        ]

    def listed_on(self, day: date) -> list[dict[str, Any]]:  # pragma: no cover
        return []

    def ohlcv_on(self, day: date) -> list[dict[str, Any]]:  # pragma: no cover
        return []


def make(store, tmp_path, panel_name: str, **kwargs: Any) -> PanelBackfiller:  # type: ignore[no-untyped-def]
    clock = ReplayClock(NOW)
    panel = PANELS[panel_name]
    if "lag_days" in kwargs:
        panel = replace(panel, lag_days=kwargs.pop("lag_days"))
    return PanelBackfiller(
        store=store,
        source=kwargs.pop("source", FakePanelSource()),
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        panel=panel,
        market=Market.KR,
        **kwargs,
    )


def published(day: date, lag_days: int = 0) -> datetime:
    """세션 마감 15:30 KST + 30분, 거래일 lag_days 만큼 뒤."""
    sessions = [MON, TUE, WED, THU, FRI]
    target = sessions[sessions.index(day) + lag_days]
    return datetime(target.year, target.month, target.day, 16, 0) - KST_OFFSET


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


# -- 공표 지연 ----------------------------------------------------------------


def test_shorting_is_observed_two_trading_days_later(store, tmp_path) -> None:
    """공매도는 T+2 다. 세션 당일에 보이면 flow_kr 이 미래를 본다."""
    store.seed_config_defaults()
    backfiller = make(store, tmp_path, "shorting", lag_days=2)

    assert backfiller.run_session(MON).ok

    # 월요일 세션인데 수요일 16:00 KST 전에는 안 보인다.
    assert store.get("shorting", as_of=_utc(published(MON, 0)) + timedelta(hours=1)).empty
    assert store.get("shorting", as_of=_utc(published(MON, 1)) + timedelta(hours=1)).empty

    visible = store.get("shorting", as_of=_utc(published(MON, 2)) + timedelta(minutes=1))
    assert len(visible) == 1
    assert visible.iloc[0]["entity_id"] == "KR:005930"
    # valid_from 은 여전히 월요일 세션이다. 사실이 유효한 날과 알 수 있게 된
    # 날은 다른 필드다.
    assert visible.iloc[0]["valid_from"].to_pydatetime() == datetime(2024, 3, 4, tzinfo=UTC)


def test_lag_counts_trading_days_not_calendar_days(store, tmp_path) -> None:
    """금요일 세션의 T+2 는 일요일이 아니라 화요일이다.

    달력일로 세면 주말에 이미 알고 있는 것이 되고, 월요일 아침 매매가
    아직 공표되지 않은 공매도를 본다.
    """
    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    policy = PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock)

    friday = policy.for_session(FRI, extra_lag_days=2)

    # 2024-03-08(금) + 거래일 2 = 2024-03-12(화)
    assert friday.astimezone(UTC).date() == date(2024, 3, 12)


def test_flows_have_no_day_lag(store, tmp_path) -> None:
    """수급은 그날 안에 나온다. 공매도의 T+1~2 지연을 물려 주면 안 된다.

    **하루 중 몇 시에 나오는지는 다른 문제이고, 우리는 그것을 모른다** —
    아래 두 테스트가 그 하한을 고정한다.
    """
    store.seed_config_defaults()
    backfiller = make(store, tmp_path, "flows")

    assert backfiller.run_session(MON).ok

    visible = store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1))
    assert len(visible) == 3


def test_flows_observed_at_is_not_earlier_than_collection(store, tmp_path) -> None:
    """**그날 것을 그날 받으면 받은 시각이 하한이다.**

    KRX 가 투자자별 순매수를 하루 중 언제 내는지 우리는 확인하지 못했다.
    일봉의 마감+30분(16:00 KST)을 그대로 물려 쓴 결과 창고의 flows 는
    131,200행 전부가 16:00 KST 정각으로 찍혔는데, 실제 수집은 22:40 에 돈다.
    Analyst 의 세션 as_of 가 정확히 16:00 이라 그 6시간 40분어치를 경계에서
    그대로 읽었다 — 불변식 3 이 막으려던 구멍이다.
    """
    store.seed_config_defaults()
    collected = _utc(published(MON)) + timedelta(hours=6, minutes=40)  # 22:40 KST
    clock = ReplayClock(collected)
    backfiller = PanelBackfiller(
        store=store,
        source=FakePanelSource(),
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        panel=PANELS["flows"],
        market=Market.KR,
    )

    assert backfiller.run_session(MON).ok

    # 16:00 시점에는 아직 안 보인다. 22:40 에야 보인다.
    assert store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1)).empty
    assert len(store.get("flows", as_of=collected)) == 3


def test_old_flow_sessions_keep_the_publication_estimate(store, tmp_path) -> None:
    """**5년치를 오늘 받았다고 5년치를 오늘 알았다고 적지 않는다.**

    하한을 무조건 걸면 과거 백필이 통째로 "오늘 관측" 이 되고, 게이트가
    정직하게 동작해서 리플레이가 빈 시장 위에서 돈다.
    """
    store.seed_config_defaults()
    make(store, tmp_path, "flows").run_session(MON)  # NOW 는 세션에서 석 달 뒤다

    frame = store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1))
    assert len(frame) == 3


# -- 정규화 -------------------------------------------------------------------


def test_is_final_is_unknown_not_true(store, tmp_path) -> None:
    """**모르는 것을 True 로 적지 않는다.** KRX 응답에 잠정/확정 구분이 없다.

    예전에는 여기서 True 를 박았고, 그 결과 창고의 flows 는 한 행도 빠짐없이
    "확정치" 가 됐다. flow_kr 은 그 칸으로 확정치를 우선하려 했는데 정렬 키가
    상수라 한 번도 발화하지 않았다. 확정치 대체는 revision 으로 성립한다.
    """
    store.seed_config_defaults()
    make(store, tmp_path, "flows").run_session(MON)

    frame = store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1))
    assert frame["is_final"].isna().all()



def test_investor_is_part_of_the_natural_key(store, tmp_path) -> None:
    """같은 종목·같은 날에 주체별로 행이 따로 남아야 한다.

    자연키에 주체가 없으면 마지막 주체 하나만 살아남고 나머지가 사라진다.
    """
    store.seed_config_defaults()
    make(store, tmp_path, "flows").run_session(MON)

    frame = store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1))
    samsung = frame[frame["entity_id"] == "KR:005930"]

    assert set(samsung["investor"]) == {"외국인", "개인"}


def test_absent_investor_rows_are_not_filled_with_zero(store, tmp_path) -> None:
    """'순매수 0' 과 '거래 없음' 은 다른 사실이다."""
    store.seed_config_defaults()
    make(store, tmp_path, "flows").run_session(MON)

    frame = store.get("flows", as_of=_utc(published(MON)) + timedelta(minutes=1))
    hynix = frame[frame["entity_id"] == "KR:000660"]

    assert set(hynix["investor"]) == {"외국인"}
    assert "개인" not in set(hynix["investor"])


def test_fundamentals_fan_out_to_long_format(store, tmp_path) -> None:
    """지표 하나가 행 하나. 지표가 늘어도 스키마를 안 고친다."""
    store.seed_config_defaults()
    make(store, tmp_path, "fundamentals").run_session(MON)

    frame = store.get("fundamentals", as_of=_utc(published(MON)) + timedelta(minutes=1))

    assert set(frame["metric"]) == {"BPS", "PER", "PBR", "EPS", "DIV", "DPS",
                                    "market_cap", "shares"}
    assert float(frame[frame["metric"] == "PER"].iloc[0]["value"]) == pytest.approx(12.3)
    assert set(frame["report_type"]) == {"krx_daily"}


def test_indices_do_not_land_in_prices(store, tmp_path) -> None:
    """지수가 종목 유니버스에 끼면 커버리지 통계와 횡단면 z 가 오염된다."""
    store.seed_config_defaults()
    make(store, tmp_path, "indices").run_session(MON)

    as_of = _utc(published(MON)) + timedelta(minutes=1)

    assert store.get("prices", as_of=as_of).empty
    assert len(store.get("indices", as_of=as_of)) == 1


# -- 재개 ---------------------------------------------------------------------


def test_panel_resume_skips_recorded_sessions(store, tmp_path) -> None:
    store.seed_config_defaults()
    first = make(store, tmp_path, "flows")
    assert first.run_session(MON).ok

    second = make(store, tmp_path, "flows")
    assert second.pending([MON, TUE]) == [TUE]
    assert second.run_session(MON).skipped


def test_only_codes_filters_panels(store, tmp_path) -> None:
    store.seed_config_defaults()
    backfiller = make(store, tmp_path, "flows", only_codes=frozenset({"005930"}))

    assert backfiller.run_session(MON).rows == 2
