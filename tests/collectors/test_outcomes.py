"""0행의 판정 — (가) 원본이 안 냄 · (나) 우리가 못 받음 · (다) 걸러져서 0건.

셋이 전부 "0행" 으로 끝나고 rc=0 으로 나가던 자리다. 그래서 (나)가 며칠씩
조용히 이어져도 아무도 몰랐다 — 2026-08-19 KRX 지수·시총, 2026-08-22 FRED.

여기서 고정하는 것은 둘이다. **판정이 창고에 남는가**(사람이 로그를 안 볼
때도 판정할 수 있어야 한다), 그리고 **(나)만 실패로 세는가**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.collectors.backfill import BackfillReport
from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.macro_source import FRED_INDICES, IndexCollector, MacroUnavailable
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.outcome import OUTCOMES_TABLE, Verdict
from quant_rl_trading.collectors.panels import OPENAPI_PANELS, PanelBackfiller
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

KST = timedelta(hours=9)
LAG = 1800.0

#: 2024-03-04(월). 거래일이다.
MON = date(2024, 3, 4)

#: 그 세션의 공표 추정시각 — 15:30 마감 + 30분.
PUBLISHED = (datetime(2024, 3, 4, 16, 0) - KST).replace(tzinfo=UTC)


@dataclass
class FakeOpenApi:
    """KRX Open API 자리. **이름이 설정 키가 된다.**"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    name: str = "krx_openapi"

    def trades_on(self, day: date) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        return list(self.rows)


def make(store, tmp_path, source: FakeOpenApi, *, now: datetime) -> PanelBackfiller:  # type: ignore[no-untyped-def]
    clock = ReplayClock(now)
    return PanelBackfiller(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        # 계기가 된 패널이다. run_key 가 없어 key 는 테이블 이름(market_stats)이다.
        panel=OPENAPI_PANELS["shares"],
        market=Market.KR,
    )


def outcomes(store, as_of: datetime):  # type: ignore[no-untyped-def]
    return store.get(OUTCOMES_TABLE, as_of=as_of, lookback=30)


# -- 빈 응답 -------------------------------------------------------------------


def test_empty_without_a_verified_schedule_is_our_failure(store, tmp_path) -> None:
    """**모르면 (나)다.** 원본이 언제 내는지 모르면 0건은 실패다.

    "휴장이겠거니" 하고 넘어가는 쪽이 조용한 실패를 만든다. 여기서는 소스
    이름이 설정에 없어 확인할 근거가 아예 없다.
    """
    store.seed_config_defaults()
    source = FakeOpenApi(name="krx-알수없음")
    now = PUBLISHED + timedelta(hours=20)
    result = make(store, tmp_path, source, now=now).run_session(MON)

    assert result.verdict is Verdict.EMPTY_UNCONFIRMED
    assert result.error is not None
    assert not result.deferred
    # 실패로 센다 → 호출부가 rc≠0 으로 나간다.
    report = BackfillReport(market=Market.KR)
    report.absorb(result)
    assert len(report.failures) == 1


def test_empty_before_the_source_publishes_is_not_a_failure(store, tmp_path) -> None:
    """22:40 의 0행은 실패가 아니다. **원본이 아직 안 낸 것이다.**

    실측 2026-08-19: 마감 +7.2시간에 0행이던 같은 호출이 아침(+14.5시간)에
    5,744행을 줬다. 이 사실이 설정에 있으므로 판정할 수 있다.
    """
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=7)  # 22:40 KST 언저리
    result = make(store, tmp_path, FakeOpenApi(), now=now).run_session(MON)

    assert result.verdict is Verdict.TOO_EARLY
    assert result.deferred  # "내일 다시" 다
    report = BackfillReport(market=Market.KR)
    report.absorb(result)
    assert report.failures == []
    assert report.deferred == 1


def test_empty_after_the_source_publishes_is_our_failure(store, tmp_path) -> None:
    """아침에도 0행이면 그건 우리 문제로 본다. 그 자리가 조용하던 자리다."""
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=15)  # 06:30 KST 언저리
    result = make(store, tmp_path, FakeOpenApi(), now=now).run_session(MON)

    assert result.verdict is Verdict.EMPTY_UNCONFIRMED
    assert result.error is not None


def test_verdict_lands_in_the_warehouse(store, tmp_path) -> None:
    """**로그만으로는 부족하다.** 사람이 안 볼 때도 판정할 수 있어야 한다."""
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=15)
    make(store, tmp_path, FakeOpenApi(), now=now).run_session(MON)

    frame = outcomes(store, now + timedelta(minutes=1))
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["entity_id"] == "krx_openapi:market_stats"
    assert row["verdict"] == str(Verdict.EMPTY_UNCONFIRMED)
    assert row["table_name"] == "market_stats"
    # valid_from 은 그 세션, observed_at 은 우리가 겪은 시각이다 (불변식 3).
    assert row["valid_from"].to_pydatetime() == datetime(2024, 3, 4, tzinfo=UTC)
    assert row["observed_at"].to_pydatetime() == now


def test_empty_session_stays_pending(store, tmp_path) -> None:
    """0행을 완료로 적지 않는다. 나중에 데이터가 생기면 다시 받아야 한다."""
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=7)
    backfiller = make(store, tmp_path, FakeOpenApi(), now=now)
    backfiller.run_session(MON)

    assert backfiller.pending([MON]) == [MON]


# -- 받았는데 0건 --------------------------------------------------------------


def test_filtered_to_nothing_is_normal(store, tmp_path) -> None:
    """(다) 원본도 냈고 우리도 받았는데 조건에 맞는 행이 없다. 정상이다.

    실패로 세면 매일 밤 헛경보가 나고, 헛경보는 다음 진짜 실패를 가린다.
    다만 **몇 행이 걸러졌는지는 남긴다** — 전량이 걸러지는 상태가 이어지면
    그건 정규화가 깨진 것이다(ISU_CD 를 ISIN 으로 읽어 전 행을 버린 적이 있다).
    """
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=15)
    # 코드가 없는 행은 normalize_shares 가 전부 버린다.
    source = FakeOpenApi(rows=[{"code": "", "shares": 100, "market_cap": 100}])
    result = make(store, tmp_path, source, now=now).run_session(MON)

    assert result.verdict is Verdict.FILTERED_EMPTY
    assert result.ok and not result.deferred

    row = outcomes(store, now + timedelta(minutes=1)).iloc[0]
    assert row["verdict"] == str(Verdict.FILTERED_EMPTY)
    assert "1행" in row["detail"]


# -- 못 받았다 -----------------------------------------------------------------


def test_fetch_failure_is_recorded_and_fails(store, tmp_path) -> None:
    """(나) 인증 실패·타임아웃·파싱 실패는 전부 여기로 온다."""
    store.seed_config_defaults()
    now = PUBLISHED + timedelta(hours=7)
    source = FakeOpenApi(error=CollectorError("인증 실패 (401)"))
    result = make(store, tmp_path, source, now=now).run_session(MON)

    assert result.verdict is Verdict.FETCH_FAILED
    assert result.error is not None and not result.deferred

    row = outcomes(store, now + timedelta(minutes=1)).iloc[0]
    assert row["verdict"] == str(Verdict.FETCH_FAILED)
    assert row["error_type"] == "CollectorError"


# -- FRED 지수 -----------------------------------------------------------------


@dataclass
class FakeFred:
    """FRED 자리. 시리즈 하나만 죽인다."""

    dead: str
    name: str = "fred"

    def latest_observations(self, series_id: str, *, limit: int = 2) -> list[dict[str, Any]]:
        if series_id == self.dead:
            raise MacroUnavailable("FRED 500")
        return [{"date": "2024-03-04", "value": "5100.0"}]


class NoArchive:
    def save(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_a_dead_fred_series_does_not_pass_as_success(store, tmp_path) -> None:
    """**행 수만 보면 성공처럼 보인다.**

    전에는 ``except CollectorError: continue`` 였다. 시리즈 하나가 죽어도
    나머지가 적재되므로 화면에는 "indices 적재: 140행" 이 찍히고, 죽은
    시리즈는 어디에도 안 남았다 — 2026-08-22 아침 브리핑이 그 자리였다.
    """
    store.seed_config_defaults()
    dead = sorted(FRED_INDICES)[0]
    now = datetime(2024, 3, 5, 12, tzinfo=UTC)
    collector = IndexCollector(
        store=store, source=FakeFred(dead=dead), clock=ReplayClock(now),
        archive=NoArchive(), days=2,
    )

    with pytest.raises(MacroUnavailable) as raised:
        collector.collect()
    assert dead in str(raised.value)

    # 받은 것은 버리지 않는다.
    assert len(store.get("indices", as_of=now + timedelta(days=1), lookback=30)) > 0
    # 못 받은 것은 창고에 남는다.
    frame = outcomes(store, now + timedelta(minutes=1))
    assert list(frame["entity_id"]) == [f"fred:{dead}"]
    assert frame.iloc[0]["verdict"] == str(Verdict.FETCH_FAILED)


def test_the_verdict_can_change_and_both_stay_readable(store, tmp_path) -> None:
    """저녁엔 "아직", 아침엔 "실패". **두 판정이 다 남는다.**

    이 표는 append-only 다(불변식 4). 아침의 판정이 저녁의 판정을 덮어쓰는
    것이 아니라 옆에 쌓이고, ``as_of`` 가 그때 유효했던 판정을 고른다 —
    정정본이 사고의 흔적을 지우는 것을 막으려고 만든 표다.
    """
    store.seed_config_defaults()
    evening = PUBLISHED + timedelta(hours=7)
    morning = PUBLISHED + timedelta(hours=15)
    for now in (evening, morning):
        make(store, tmp_path, FakeOpenApi(), now=now).run_session(MON)

    assert list(outcomes(store, evening + timedelta(minutes=1))["verdict"]) == [
        str(Verdict.TOO_EARLY)
    ]
    assert list(outcomes(store, morning + timedelta(minutes=1))["verdict"]) == [
        str(Verdict.EMPTY_UNCONFIRMED)
    ]
