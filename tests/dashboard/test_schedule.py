"""뉴스·일정 탭 월별 일정 — 세 표를 합치고, 추정은 추정이라 말하며, 되감기가 된다."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant_rl_trading.dashboard.services import schedule

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)


def _seed(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    at = datetime(2026, 9, 4, 12, 30, tzinfo=UTC)  # 21:30 KST
    store.append("macro_releases", [{
        "entity_id": "US:EMPLOYMENT", "valid_from": at, "observed_at": NOW, "source": "fred",
        "market": "US", "indicator": "EMPLOYMENT", "release_name": "Employment Situation",
        "scheduled_at": at, "actual": None, "previous": 4.2, "unit": "%", "status": "scheduled",
    }, {
        "entity_id": "US:FED_FUNDS", "valid_from": at, "observed_at": NOW, "source": "fred",
        "market": "US", "indicator": "FED_FUNDS", "release_name": "H.15", "scheduled_at": at,
        "actual": None, "previous": None, "unit": "%", "status": "scheduled",
    }], ingest_run_id="m")
    store.append("macro_consensus", [{
        "entity_id": "US:EMPLOYMENT", "valid_from": at, "observed_at": NOW, "source": "forexfactory",
        "market": "US", "title": "Non-Farm Employment Change", "forecast": "150K", "previous": "73K",
        "actual": "", "impact": "High",
    }], ingest_run_id="c")
    store.append("earnings_calendar", [{
        "entity_id": "US:AVGO", "valid_from": datetime(2026, 9, 3, 20, 30, tzinfo=UTC),
        "observed_at": NOW, "source": "nasdaq", "market": "US", "name": "Broadcom Inc.",
        "timing": "post", "fiscal_quarter": "Jul/2026", "eps_forecast": "$2.83",
        "market_cap": 1.7e12, "status": "scheduled",
    }, {
        "entity_id": "KR:005930", "valid_from": datetime(2026, 10, 30, 0, 0, tzinfo=UTC),
        "observed_at": NOW, "source": "dart-estimate", "market": "KR", "name": "삼성전자",
        "timing": "estimate", "fiscal_quarter": "작년 2025-10-30 공시(Q4) 기준", "eps_forecast": "",
        "market_cap": 0.0, "status": "estimated",
    }], ingest_run_id="e")


def test_month_merges_three_tables_and_drops_daily_noise(store) -> None:
    _seed(store)
    data = schedule.month_schedule(store, as_of=NOW, month="2026-09")
    labels = [i["label"] for day in data["days"].values() for i in day]
    assert "Non-Farm Employment Change" in labels          # 예측치가 있는 쪽
    assert "Employment Situation" not in labels             # 같은 지표의 FRED 행은 접힌다
    assert "H.15" not in labels                             # 매일 나오는 금리는 일정이 아니다
    assert "Broadcom Inc. 실적" in labels
    nfp = next(i for day in data["days"].values() for i in day if i["label"].startswith("Non-Farm"))
    assert nfp["time"] == "21:30" and nfp["impact"] == "High"
    assert data["counts"] == {"macro": 1, "earnings": 1, "estimated": 0}
    assert data["prev"] == "2026-08" and data["next"] == "2026-10"


def test_estimates_are_labelled_and_time_is_blank(store) -> None:
    _seed(store)
    data = schedule.month_schedule(store, as_of=NOW, month="2026-10")
    items = [i for day in data["days"].values() for i in day]
    assert len(items) == 1
    assert items[0]["label"] == "삼성전자 실적 (예상)"
    assert items[0]["estimated"] is True and items[0]["time"] == ""


def test_as_of_hides_schedules_observed_later(store) -> None:
    _seed(store)
    data = schedule.month_schedule(store, as_of=NOW - timedelta(days=1), month="2026-09")
    assert data["days"] == {}
