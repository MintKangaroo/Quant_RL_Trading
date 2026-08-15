"""Data Quality API 계약 테스트.

지키는 것은 하나다 — **화면이 보여주는 숫자가 그 시점에 알 수 있었던 것과
일치해야 한다.** 대시보드가 게이트를 우회하거나 as_of 를 무시하면, 사람이 보고
판단하는 화면 자체가 미래를 보게 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services import data_quality as dq
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2024, 3, 20, tzinfo=UTC)

#: 3/4 ~ 3/8 다섯 세션. 관측시각은 각 세션 16:00 KST = 07:00 UTC.
SESSIONS = [datetime(2024, 3, day, tzinfo=UTC) for day in (4, 5, 6, 7, 8)]

API_PATHS = ["summary", "coverage", "missing", "latency", "universe", "failures"]


def observed(session: datetime) -> datetime:
    return session + timedelta(hours=7)


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """다섯 세션 × 세 종목. 마지막 세션에서 한 종목이 상폐된다."""
    store.seed_config_defaults()
    codes = ["KR:000100", "KR:000200", "KR:000300"]

    for index, session in enumerate(SESSIONS):
        seen = observed(session)
        live = codes if index < len(SESSIONS) - 1 else codes[:2]

        store.append(
            "prices",
            [
                {
                    "entity_id": code, "valid_from": session, "observed_at": seen,
                    "source": "krx", "market": "KR",
                    "open": 1000.0, "high": 1100.0, "low": 990.0,
                    # 마지막 세션 한 종목만 close 결측 — 결측률이 0이 아니게 만든다
                    "close": None if (index == 4 and code == "KR:000100") else 1050.0,
                    "volume": 10000.0, "value": None, "adj_factor": None,
                }
                for code in live
            ],
            ingest_run_id=f"t-prices-{session:%Y%m%d}",
        )
        store.append(
            "universe",
            [
                {
                    "entity_id": code, "valid_from": session, "observed_at": seen,
                    "source": "krx", "market": "KR", "name": code,
                    "is_listed": True, "is_tradable": True, "delisted_on": None,
                }
                for code in live
            ]
            + (
                [
                    {
                        "entity_id": codes[2], "valid_from": session, "observed_at": seen,
                        "source": "krx", "market": "KR", "name": codes[2],
                        "is_listed": False, "is_tradable": False, "delisted_on": session,
                    }
                ]
                if index == len(SESSIONS) - 1
                else []
            ),
            ingest_run_id=f"t-universe-{session:%Y%m%d}",
        )

    # 자연키가 (entity_id, valid_from, stage) 라, 같은 단계는 시작 시각으로
    # 구분된다. 실제 파이프라인도 단계마다 시작 시각이 다르다.
    base = observed(SESSIONS[-1])
    store.append(
        "ingest_latency",
        [
            {
                "entity_id": "krx",
                "valid_from": base + timedelta(seconds=offset),
                "observed_at": base + timedelta(seconds=offset, milliseconds=elapsed),
                "source": "krx",
                "stage": stage, "elapsed_ms": elapsed, "ok": ok, "detail": detail,
            }
            for offset, stage, elapsed, ok, detail in [
                (0, "fetch", 900.0, True, ""),
                (2, "normalize", 40.0, True, ""),
                (4, "append", 120.0, True, ""),
                (6, "fetch", 5000.0, False, "타임아웃"),
            ]
        ],
        ingest_run_id="t-latency",
    )
    return store


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    app = create_app(store=seeded, clock=ReplayClock(NOW))
    app.config.update(TESTING=True)
    return app.test_client()


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


#: 필수 파라미터가 있는 라우트. **명단을 여기 두는 것이 요점이다** — 새
#: 엔드포인트가 파라미터를 요구하면 여기 등록해야 하고, 등록하지 않으면 아래
#: 테스트가 실패한다. 400 을 이유로 규약 검사에서 빠져나가는 길을 막는다.
REQUIRED_PARAMS = {"/api/trading/chart": "&entity=KR:000100"}


# -- 불변식 9 -----------------------------------------------------------------


def test_every_api_route_accepts_as_of(client, seeded) -> None:
    """**모든** /api/ 라우트가 as_of 를 받는다. 예외 없음 (불변식 9).

    화면이 늘어나도 이 테스트가 규약을 계속 강제한다. 하나라도 빠지면
    타임머신이 깨지고, 그 사실은 몇 달 뒤 이상한 백테스트로 드러난다.
    """
    app = create_app(store=seeded, clock=ReplayClock(NOW))
    # GET 라우트만 본다. 쓰기 라우트(POST)는 as_of 를 **거부**하는 것이 규약이라
    # (되감은 채로 상태를 바꾸면 기록의 valid_from 이 과거가 된다) 여기서 같은
    # 잣대로 잴 수 없다. 그쪽은 test_trading_api 가 따로 고정한다.
    rules = [
        rule
        for rule in app.url_map.iter_rules()
        if str(rule.rule).startswith("/api/") and "GET" in rule.methods
    ]
    assert rules, "검사할 API 라우트가 없다"

    past = observed(SESSIONS[1]).isoformat()
    for rule in rules:
        extra = REQUIRED_PARAMS.get(str(rule.rule), "")
        response = client.get(f"{rule.rule}?as_of={past}{extra}")
        assert response.status_code == 200, f"{rule.rule} → {response.status_code}"
        assert body(response)["as_of"] == past, f"{rule.rule} 가 as_of 를 되돌려주지 않는다"


def test_time_machine_hides_later_sessions(client) -> None:
    """과거 as_of 로 조회하면 그 시점 이후 세션이 보이지 않는다."""
    early = client.get(f"/api/data-quality/coverage?as_of={observed(SESSIONS[1]).isoformat()}")
    late = client.get(f"/api/data-quality/coverage?as_of={observed(SESSIONS[4]).isoformat()}")

    early_days = [point["session"] for point in body(early)["data"]["points"]]
    late_days = [point["session"] for point in body(late)["data"]["points"]]

    assert early_days == ["2024-03-04", "2024-03-05"]
    assert len(late_days) == 5


def test_as_of_just_before_publication_hides_the_session(client) -> None:
    """공표 1분 전에는 그날 세션이 화면에도 없어야 한다."""
    just_before = (observed(SESSIONS[2]) - timedelta(minutes=1)).isoformat()
    response = client.get(f"/api/data-quality/coverage?as_of={just_before}")
    days = [point["session"] for point in body(response)["data"]["points"]]

    assert "2024-03-06" not in days


def test_live_request_marks_itself(client) -> None:
    response = client.get("/api/data-quality/summary")
    payload = body(response)

    assert payload["live"] is True
    assert payload["as_of"] == NOW.isoformat()


# -- 입력 검증 ----------------------------------------------------------------


def test_naive_as_of_is_rejected(client) -> None:
    """타임존 없는 as_of 는 어느 쪽 자정인지 알 수 없다. 추측하지 않고 거부한다."""
    response = client.get("/api/data-quality/summary?as_of=2024-03-05T15:30:00")

    assert response.status_code == 400
    assert "타임존" in body(response)["error"]


def test_unparseable_as_of_is_rejected(client) -> None:
    assert client.get("/api/data-quality/summary?as_of=어제").status_code == 400


def test_lookback_ceiling_is_enforced(client, seeded) -> None:
    """화면 하나가 창고를 통째로 메모리에 올리지 못하게 한다."""
    ceiling = int(seeded.config("data_quality.max_lookback_days", as_of=NOW))
    response = client.get(f"/api/data-quality/summary?lookback={ceiling + 1}")

    assert response.status_code == 400
    assert str(ceiling) in body(response)["error"]


# -- 임계치 출처 --------------------------------------------------------------


def test_thresholds_come_from_store_config(client, seeded) -> None:
    """화면이 자기 숫자를 들면 학습 설정과 어긋난다 (불변식 10)."""
    payload = body(client.get("/api/data-quality/summary"))

    assert payload["thresholds"]["coverage_warn"] == seeded.config(
        "data_quality.coverage_warn", as_of=NOW
    )
    assert payload["lookback_days"] == int(
        seeded.config("data_quality.default_lookback_days", as_of=NOW)
    )


def test_changing_config_changes_the_warning(client, seeded) -> None:
    """설정을 바꾸면 경고 판정이 따라 바뀐다 — 하드코딩이 아니라는 증거."""
    # 심어 둔 결측은 1/14 = 7.1% 라 기본 임계치(1%)에서 경고가 난다.
    before = body(client.get("/api/data-quality/summary"))["data"]
    assert any("결측" in text for text in before["warnings"])

    # 임계치를 50% 로 올리면 같은 데이터에서 경고가 사라져야 한다.
    seeded.append(
        "config",
        [
            {
                "entity_id": "data_quality.missing_warn",
                "valid_from": datetime(2024, 1, 1, tzinfo=UTC),
                "observed_at": datetime(2024, 1, 1, tzinfo=UTC),
                "source": "test", "revision": 1, "value_json": "0.5",
            }
        ],
        ingest_run_id="t-config-loosen",
    )

    after = body(client.get("/api/data-quality/summary"))["data"]
    assert not any("결측" in text for text in after["warnings"])


# -- 내용 ---------------------------------------------------------------------


def test_summary_matches_the_seeded_data(client) -> None:
    data = body(client.get("/api/data-quality/summary"))["data"]

    assert data["covered_sessions"] == 5
    assert data["entities_total"] == 3
    assert data["listed_now"] == 2
    assert data["delisted_total"] == 1
    # 14행 중 1행의 close 가 결측이다.
    assert data["missing_close_rate"] == pytest.approx(1 / 14)
    assert data["failure_count"] == 1


def test_latency_reports_percentiles_per_stage(client) -> None:
    data = body(client.get("/api/data-quality/latency"))["data"]
    stages = {item["stage"]: item for item in data["stages"]}

    assert set(stages) == {"fetch", "normalize", "append"}
    assert stages["fetch"]["p90"] > stages["normalize"]["p90"]
    # 실패한 호출의 지연도 분포에 들어간다 — 빼면 p90 이 낙관적으로 왜곡된다.
    assert stages["fetch"]["ok_rate"] == pytest.approx(0.5)
    assert data["overall"]["p50"] is not None


def test_failures_are_listed(client) -> None:
    data = body(client.get("/api/data-quality/failures"))["data"]

    assert len(data) == 1
    assert data[0]["stage"] == "fetch"
    assert data[0]["detail"] == "타임아웃"


def test_delisted_symbol_is_visible_before_delisting(client) -> None:
    """상폐 이전 시점에는 세 종목이 전부 상장 상태다 (생존편향 방지의 화면 쪽)."""
    early = body(
        client.get(f"/api/data-quality/universe?as_of={observed(SESSIONS[1]).isoformat()}")
    )["data"]
    late = body(client.get("/api/data-quality/universe"))["data"]

    assert early["listed_now"] == 3
    assert early["delisted_total"] == 0
    assert late["listed_now"] == 2
    assert late["delisted_total"] == 1


def test_page_renders(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert b"Data Quality" in response.data


# -----------------------------------------------------------------------------
# 커버리지 분모·분자 — 유령 거래일과 시장 섞임
# -----------------------------------------------------------------------------

#: 2026-07-17 은 제헌절(휴장)인데 exchange_calendars 의 XKRX 는 거래일이라 한다.
JULY = [datetime(2026, 7, day, tzinfo=UTC) for day in (16, 20, 21)]


def price_row(code: str, market: str, session: datetime) -> dict[str, Any]:
    return {
        "entity_id": code, "valid_from": session, "observed_at": observed(session),
        "source": "test", "market": market,
        "open": 1000.0, "high": 1100.0, "low": 990.0, "close": 1050.0,
        "volume": 10000.0, "value": None, "adj_factor": None,
    }


@pytest.fixture
def july(store):  # type: ignore[no-untyped-def]
    """KR 은 7/16·20·21 세 세션. 미장은 **제헌절에도** 장이 선다."""
    store.seed_config_defaults()
    for session in JULY:
        store.append(
            "prices", [price_row("KR:000100", "KR", session)],
            ingest_run_id=f"kr-{session:%Y%m%d}",
        )
    holiday = datetime(2026, 7, 17, tzinfo=UTC)
    store.append(
        "prices", [price_row("US:AAPL", "US", holiday)], ingest_run_id="us-holiday",
    )
    return store


def test_유령_거래일은_결측으로_보고되지_않는다(july) -> None:
    """휴장을 구멍으로 세면 화면이 없는 결함을 경고한다.

    달력이 7/17 을 거래일이라 하면, KR 행이 없는 그 날이 결측 세션으로 잡힌다.
    """
    series = dq.coverage_series(
        july, as_of=datetime(2026, 8, 1, tzinfo=UTC), lookback=60, market=Market.KR
    )

    assert "2026-07-17" not in series["missing_sessions"]
    assert series["missing_sessions"] == []


def test_커버리지는_다른_시장의_세션을_세지_않는다(july) -> None:
    """분모가 KR 거래일이면 분자도 KR 이어야 한다.

    미장 행은 한국 휴장일에도 들어온다. 시장을 안 좁히면 그 날이 "커버된
    세션" 이 되어 비율이 100% 를 넘고, 넘은 비율은 구멍을 가린다.
    """
    series = dq.coverage_series(
        july, as_of=datetime(2026, 8, 1, tzinfo=UTC), lookback=60, market=Market.KR
    )

    assert series["covered_sessions"] == len(JULY)
    assert series["expected_sessions"] == len(JULY)
    assert series["ratio"] == 1.0
