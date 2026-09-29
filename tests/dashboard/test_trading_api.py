"""트레이딩 API — 실제 회계·주문 위에서.

목업이 있던 자리다. 여기서 고정하는 사실은 넷이다.

1. **NAV 는 회계에서만 온다** — 화면이 자기 계산을 하지 않는다
2. **as_of 를 지킨다** — 되감으면 그 시점 이후 체결이 안 보인다 (불변식 9)
3. **없는 것은 null 이다** — 체결 지연·미체결 종목을 0 으로 채우지 않는다
4. **RL 이 아닌 것을 RL 처럼 그리지 않는다** — `rl_active` 는 M4 전까지 false
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)  # 한국시간 15:40
YESTERDAY = NOW - timedelta(days=1)
ENTITY = "KR:000100"
OTHER = "KR:000200"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": moment,
        "observed_at": moment,
        "source": "test",
        **extra,
    }


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    """입금 1억 · 2종목 시세 · 체결 1건 · 주문 1건 · 스냅샷 2일."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [_row("FX:USDKRW", day, rate=1_350.0) for day in (YESTERDAY, NOW)],
        ingest_run_id="fx",
    )
    store.append(
        "capital_flows",
        [_row("FUND", YESTERDAY, currency="KRW", amount=100_000_000.0, kind="deposit")],
        ingest_run_id="flow",
    )

    prices = []
    universe = []
    for index, day in enumerate((YESTERDAY, NOW)):
        for offset, entity in enumerate((ENTITY, OTHER)):
            close = 10_000.0 + index * 200 + offset * 5_000
            prices.append(
                _row(
                    entity, day, market="KR", open=close, high=close, low=close,
                    close=close, volume=100_000.0, value=close * 100_000.0,
                    adj_factor=None,
                )
            )
            universe.append(
                _row(
                    entity, day, market="KR", name=f"종목{offset}", is_listed=True,
                    is_tradable=True, delisted_on=None,
                )
            )
    store.append("prices", prices, ingest_run_id="prices")
    store.append("universe", universe, ingest_run_id="universe")

    store.append(
        "analyst_weights",
        [_row("fundamental", YESTERDAY, market="KR", ic=0.077, weight=1.0)],
        ingest_run_id="weights",
    )
    store.append(
        "signals",
        [
            _row(
                entity, NOW, analyst="fundamental", analyst_version="fundamental-v0.1.0",
                score=score, confidence=1.0, horizon_days=5, features_hash="x",
                evidence_json="[]", latency_ms=1.0,
            )
            for entity, score in ((ENTITY, 0.9), (OTHER, 0.4))
        ],
        ingest_run_id="signals",
    )

    # 어제 낸 주문이 오늘 체결됐다. 주문과 체결이 한 표에서 맞춰져야 한다.
    store.append(
        "orders",
        [
            _row(
                ENTITY, YESTERDAY, market="KR", session_id="KR-2026-08-11",
                slice_seq=0, side="buy", quantity=100.0, limit_price=10_050.0,
                target_weight=0.15, status="planned", reason="",
            )
        ],
        ingest_run_id="orders",
    )
    # 오늘 아침 전송됐다 — 주문 표는 **as_of 당일(한국시간)에 움직인 행**만 싣는다.
    store.append(
        "orders",
        [
            _row(
                ENTITY, YESTERDAY, market="KR", session_id="KR-2026-08-11",
                slice_seq=0, side="buy", quantity=100.0, limit_price=10_050.0,
                target_weight=0.15, status="sent", reason="broker_order_no=1", revision=1,
            )
            | {"observed_at": NOW - timedelta(hours=6)}
        ],
        ingest_run_id="orders-sent",
    )
    store.append(
        "trades",
        [
            _row(
                ENTITY, NOW, market="KR", side="buy", quantity=100.0, price=10_200.0,
                currency="KRW", fee=150.0, tax=0.0, order_id="KR-2026-08-11|KR:000100|buy",
            )
        ],
        ingest_run_id="trades",
    )
    store.append(
        "realized_weights",
        [
            _row(
                ENTITY, NOW, market="KR", session_id="KR-2026-08-12",
                target_weight=0.15, realized_weight=0.0102,
            )
        ],
        ingest_run_id="realized",
    )
    return store


@pytest.fixture
def client(desk):  # type: ignore[no-untyped-def]
    return create_app(store=desk, clock=ReplayClock(NOW)).test_client()


def test_벤치마크_낙폭은_창_이전_고점으로_잰다(desk) -> None:
    """언더워터 차트의 점선이 실선과 **같은 규칙으로** 재져야 한다.

    우리 낙폭은 전 기간 고점 기준으로 장부에 적혀 있다. 벤치마크 낙폭만
    창 안에서 재면 창 첫날이 고점이 되어 0 에서 시작하고, 같은 그림에서
    벤치마크가 덜 빠진 것처럼 보인다. 여기서는 고점(100)이 창 밖에 있다.
    """
    days = [NOW - timedelta(days=offset) for offset in range(4, -1, -1)]
    benchmarks = [100.0, 95.0, 90.0, 85.0, 81.0]
    desk.append(
        "nav_daily",
        [
            _row(
                "FUND", day, nav=1e8, inflow=0.0, twr_return=0.0,
                index_value=1.0, drawdown=0.0, cash_krw=1e8, cash_usd=0.0,
                equity_kr=0.0, equity_us=0.0, accrued_dividend=0.0, payable=0.0,
                fx_rate=1_350.0, tax_provision=0.0, nav_after_tax=1e8,
                benchmark_index=value,
            )
            for day, value in zip(days, benchmarks, strict=True)
        ],
        ingest_run_id="nav",
    )

    client = create_app(store=desk, clock=ReplayClock(NOW)).test_client()
    # 창을 이틀로 좁힌다 — 고점 100 은 이 창 밖에 있다.
    body = client.get("/api/trading?lookback=2").get_json()
    curve = body["data"]["equity"]

    assert curve["benchmark"] == [90.0, 85.0, 81.0]
    # 창만 봤다면 [0.0, -0.056, -0.10] 이 나왔을 자리다. 고점은 창 밖의 100 이다.
    assert curve["benchmark_drawdown"] == pytest.approx([-0.10, -0.15, -0.19])


def test_화면은_가격지수라는_사실을_말한다(client) -> None:
    """총수익지수가 아니라는 사실이 화면에 없으면, 보는 사람은 배당수익률만큼
    부풀려진 초과수익을 진짜로 읽는다 (accounting.md §7.1)."""
    label = client.get("/api/trading").get_json()["data"]["equity"]["benchmark_label"]

    assert label["price_return_only"] is True
    # 지수 이름은 config 에서 온다. 화면에 적어 두면 설정을 바꿔도 화면만
    # 옛 지수를 말한다 (불변식 10).
    assert label["kr_index"] == "KR:IDX:KOSPI"
    assert label["us_index"] == "US:IDX:SP500"


def test_벤치마크가_왜_비었는지를_화면이_말한다(desk) -> None:
    """null 을 앞 값으로 채우지 않는 대신, 왜 없는지는 말해야 한다. 안 그러면
    끊긴 점선이 "벤치마크가 안 빠졌다" 로 읽힌다."""
    desk.append(
        "nav_daily",
        [
            _row(
                "FUND", NOW, nav=1e8, inflow=0.0, twr_return=0.0,
                index_value=100.0, drawdown=0.0, cash_krw=1e8, cash_usd=0.0,
                equity_kr=0.0, equity_us=0.0, accrued_dividend=0.0, payable=0.0,
                fx_rate=1_350.0, tax_provision=0.0, nav_after_tax=1e8,
                benchmark_index=None, benchmark_note="지수 종가 없음: KR:IDX:KOSPI",
            )
        ],
        ingest_run_id="nav-gap",
    )
    client = create_app(store=desk, clock=ReplayClock(NOW)).test_client()
    curve = client.get("/api/trading").get_json()["data"]["equity"]

    assert curve["benchmark"] == [None]
    assert curve["benchmark_drawdown"] == [None]
    assert "KR:IDX:KOSPI" in curve["benchmark_note"]


def test_nav_은_회계에서_온다(client) -> None:
    body = client.get("/api/trading").get_json()
    kpis = body["data"]["kpis"]

    # 1억 입금 - 매수대금 1,020,000 - 수수료 150 + 평가액 1,020,000
    assert kpis["nav"] == pytest.approx(100_000_000.0 - 150.0)
    assert kpis["positions"] == 1
    assert body["as_of"] == NOW.isoformat()


def test_주문과_체결이_한_행에서_맞춰진다(client) -> None:
    rows = client.get("/api/trading").get_json()["data"]["orders"]

    assert len(rows) == 1
    assert rows[0]["status"] == "filled"
    assert rows[0]["fill_price"] == pytest.approx(10_200.0)
    # **체결 지연은 실거래에서만 잰다.** 0 으로 채우면 "빠르다" 로 읽힌다.
    assert rows[0]["latency_ms"] is None


def test_rl_이_아닌_것을_rl_처럼_그리지_않는다(client) -> None:
    decision = client.get("/api/trading").get_json()["data"]["decision"]

    assert decision["rl_active"] is False
    assert decision["engine_note"] == "RL(강화학습) 꺼짐 — 비중은 규칙이 정한다"
    assert decision["entity_id"] == ENTITY  # 보유 비중 1위(이 창고에선 합성 점수 1위이기도 하다)
    assert [item["analyst"] for item in decision["contributions"]] == ["fundamental"]
    # 목표와 실현이 벌어진 사실이 화면까지 온다 (불변식 7).
    assert decision["target_weight"] == pytest.approx(0.15)
    assert decision["realized_weight"] == pytest.approx(0.0102)


def test_되감으면_그_시점_이후_체결이_안_보인다(client) -> None:
    body = client.get(f"/api/trading?as_of={YESTERDAY.isoformat()}").get_json()

    # 어제 낸 주문은 어제도 보인다. **체결되지 않은 상태로** 보여야 한다 —
    # 오늘 체결을 어제 화면이 알고 있으면 그게 미래 훔쳐보기다.
    assert [row["status"] for row in body["data"]["orders"]] == ["planned"]
    assert body["data"]["orders"][0]["fill_price"] is None
    assert body["data"]["positions"] == []
    # 입금은 어제 있었으므로 자본은 그대로다.
    assert body["data"]["kpis"]["nav"] == pytest.approx(100_000_000.0)


def test_주문_표는_as_of_당일에_움직인_주문만_싣는다(desk) -> None:
    """2026-09-28: 휴장을 건너뛴 재조정이 같은 세션(전 거래일 16:00)이라, 세션 날짜로 자르던 표에 9/24 추석
    거부 70건이 오늘 주문처럼 섞였다. 기준은 행이 마지막으로 바뀐 시각(observed_at)의 한국시간 날짜다."""
    stale = YESTERDAY + timedelta(minutes=1)  # 어제 거부된 조각 — 세션 날짜로 자르면 오늘과 섞일 수 있다
    desk.append(
        "orders",
        [
            _row(
                OTHER, YESTERDAY, market="KR", session_id="KR-2026-08-11",
                slice_seq=0, side="buy", quantity=10.0, limit_price=15_000.0,
                target_weight=0.05, status="rejected", reason="거부 — rsp_cd=01410", revision=1,
            )
            | {"observed_at": stale}
        ],
        ingest_run_id="orders-stale-reject",
    )
    client = create_app(store=desk, clock=ReplayClock(NOW)).test_client()

    today = client.get(f"/api/trading?as_of={NOW.isoformat()}").get_json()["data"]["orders"]
    assert [row["entity_id"] for row in today] == [ENTITY]

    # 되감으면 그날 표가 나온다(불변식 9) — 어제 화면에선 그 거부가 그날의 주문이었다.
    back = client.get(f"/api/trading?as_of={(stale + timedelta(minutes=1)).isoformat()}")
    assert {row["entity_id"] for row in back.get_json()["data"]["orders"]} == {ENTITY, OTHER}


def test_거부율은_주문별_최신_revision_으로_세고_휴장일_거부를_뺀다() -> None:
    import pandas as pd

    from quant_rl_trading.dashboard.services import trading as service

    def order(entity: str, revision: int, status: str, reason: str = "") -> dict[str, Any]:
        return {
            "entity_id": entity, "session_id": "KR-2026-09-23", "slice_seq": 0,
            "revision": revision, "status": status, "reason": reason,
            "observed_at": datetime(2026, 9, 28, 0, revision, tzinfo=UTC),
        }

    frame = pd.DataFrame([
        # 정정 행 셋이 한 주문이다 — 거부 한 건으로만 센다.
        order("KR:A", 0, "planned"), order("KR:A", 1, "submitting"),
        order("KR:A", 2, "rejected", "거부 — rsp_cd=02714 주문가능금액 부족"),
        order("KR:B", 0, "reserved"), order("KR:B", 1, "sent", "broker_order_no=9"),
        order("KR:C", 0, "sent", "broker_order_no=8"),
        order("KR:D", 0, "reserved"), order("KR:D", 1, "rejected", "거부 — rsp_cd=01410 모의투자 영업일이 아닙니다"),
        order("KR:E", 0, "rejected", "rsp_cd=01410"),
    ])

    counts = service.reject_counts(frame)

    assert counts == {"total": 3, "rejected": 1, "holiday_rejected": 2, "rate": pytest.approx(1 / 3)}
    risk_state = {
        "killswitch": {"engaged": False, "order_fail_rate": 0.5},
        "band": "free", "band_message": "",
        "reject_rate": counts["rate"], "orders_holiday_rejected": counts["holiday_rejected"],
    }
    kpi = {"action_reflection": None, "action_reflection_floor": 0.3}
    alerts = service.alerts(kpi, risk_state)
    assert not any(a["level"] == "critical" for a in alerts)
    assert any("휴장일 거부 2건" in a["text"] for a in alerts)


def test_거부율은_당일_주문만_센다() -> None:
    """사용자 요청(2026-09-28): 9/24 거부 70건이 9/28 거부율로 뜨면 안 된다 — 주문표와 같은 당일 기준."""
    import pandas as pd

    from quant_rl_trading.dashboard.services import trading as service

    def order(entity: str, when: datetime, status: str, reason: str = "") -> dict[str, Any]:
        return {"entity_id": entity, "session_id": "KR-2026-09-23", "slice_seq": 0, "revision": 0,
                "status": status, "reason": reason, "observed_at": when}

    old = datetime(2026, 9, 24, 0, 5, tzinfo=UTC)      # 9/24 09:05 KST
    today = datetime(2026, 9, 28, 0, 5, tzinfo=UTC)    # 9/28 09:05 KST
    frame = pd.DataFrame([
        order("KR:A", old, "rejected", "거부 — rsp_cd=02714 주문가능금액 부족"),
        order("KR:B", old, "rejected", "rsp_cd=01410"),
        order("KR:C", today, "sent", "broker_order_no=1"),
        order("KR:D", today, "rejected", "거부 — rsp_cd=02714 주문가능금액 부족"),
    ])
    counts = service.reject_counts(frame, day=datetime(2026, 9, 28).date())
    assert counts == {"total": 2, "rejected": 1, "holiday_rejected": 0, "rate": pytest.approx(0.5)}
    assert service.reject_counts(frame, day=datetime(2026, 9, 27).date())["rate"] is None


def test_체결율은_as_of_로_되감긴다(client) -> None:
    """사용자 요청(2026-09-29). 되감으면 그 시점에 알 수 있던 체결만 — 체결 전이면 0%, 전송 전이면 — (None)."""
    now = client.get(f"/api/trading?as_of={NOW.isoformat()}").get_json()["data"]["fill_rate"]
    assert now["today"]["quantity_rate"] == pytest.approx(1.0)
    assert now["today"]["sent_count"] == 1
    assert now["window_sessions"] == 20  # 설정(dashboard.fill_rate_window_sessions)에서 온다
    assert now["window"]["quantity_rate"] == pytest.approx(1.0)

    before_fill = client.get(f"/api/trading?as_of={(NOW - timedelta(hours=1)).isoformat()}").get_json()
    early = before_fill["data"]["fill_rate"]["today"]
    assert early["quantity_rate"] == pytest.approx(0.0)
    assert early["unfilled"] == {"cancelled": 0, "rejected": 0, "pending": 1}

    back = client.get(f"/api/trading?as_of={YESTERDAY.isoformat()}").get_json()["data"]["fill_rate"]
    assert back["today"]["quantity_rate"] is None  # 어제는 계획만 — 나간 것이 없다
    assert back["window"]["quantity_rate"] is None


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    assert client.get("/api/trading?as_of=2026-08-12T15:40:00").status_code == 400


def test_차트는_종목을_요구한다(client) -> None:
    assert client.get("/api/trading/chart").status_code == 400

    body = client.get(f"/api/trading/chart?entity={ENTITY}").get_json()
    assert body["data"]["entity_id"] == ENTITY
    assert len(body["data"]["sessions"]) == 2
    # 우리 체결이 봉 위에 얹힌다.
    assert body["data"]["trades"][0]["side"] == "buy"


def test_리스크_임계치는_설정에서_온다(client) -> None:
    risk = client.get("/api/trading").get_json()["data"]["risk"]

    assert risk["bands"] == {"free": 0.12, "warn": 0.22, "hard": 0.30}
    assert risk["band"] == "free"
    assert risk["killswitch"]["engaged"] is False


def test_알_수_없는_시장은_거부한다(client) -> None:
    assert client.get("/api/trading?market=JP").status_code == 400


# -- EMERGENCY STOP ------------------------------------------------------------


def test_킬스위치는_이유를_요구한다(client) -> None:
    """이유 없는 발동·해제는 나중에 '왜 그랬나' 를 답할 수 없게 만든다."""
    assert client.post("/api/trading/killswitch", json={"action": "engage"}).status_code == 400
    assert client.post(
        "/api/trading/killswitch", json={"action": "engage", "reason": ""}
    ).status_code == 400
    assert client.post(
        "/api/trading/killswitch", json={"action": "무엇", "reason": "테스트"}
    ).status_code == 400


def test_킬스위치를_걸면_화면과_executor_가_같은_상태를_본다(client, desk) -> None:
    from quant_rl_trading.executor import guards

    response = client.post(
        "/api/trading/killswitch", json={"action": "engage", "reason": "손으로 정지"}
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["state"] == "engaged"

    # **화면이 자기 상태를 따로 들지 않는다.** Executor 가 보는 것과 같아야 한다.
    assert not guards.check_killswitch(desk, as_of=NOW)
    body = client.get("/api/trading").get_json()
    assert body["data"]["risk"]["killswitch"]["engaged"] is True
    assert any(item["level"] == "critical" for item in body["data"]["alerts"])


def test_되감은_채로는_킬스위치를_못_건다(client) -> None:
    """과거 시점으로 발동하면 기록의 valid_from 이 과거가 된다."""
    response = client.post(
        f"/api/trading/killswitch?as_of={NOW.isoformat()}",
        json={"action": "engage", "reason": "테스트"},
    )
    assert response.status_code == 400


def test_모드가_창고에서_유도된다(client) -> None:
    """shadow 창고를 실전으로 착각하는 것이 가장 비싼 오해다."""
    system = client.get("/api/trading").get_json()["data"]["system"]
    assert system["mode"] in {"LIVE", "SHADOW", "BACKTEST", "PAPER"}
    assert system["store_root"]


def test_장이_닫혀_있으면_실시간_값이_종가라고_말한다(client) -> None:
    """**마감 후의 마지막 체결가는 참고값이 아니라 오늘 종가다.**

    일봉 수집이 장 끝난 뒤에 돌기 때문에, 그 사이 창고 기준 `nav`·`today_pnl`
    은 아직 어제를 가리킨다. 화면이 그 구간에서 실시간 값을 버리면 "오늘
    수익률 0.00%" 가 되는데, 안 움직인 것이 아니라 아직 모르는 것이다
    (2026-08-19 실측 -1.13%).

    시각 판단을 화면이 하면 장 마감 시각이 두 곳에 생기므로 서버가 말한다.
    """
    kpis = client.get("/api/trading").get_json()["data"]["kpis"]

    assert "live_is_close" in kpis
    # 이 픽스처의 NOW 는 정규장 밖이다 — 실시간 값이 있으면 그것이 종가다.
    if kpis["live_session_open"] is False and kpis["live_nav"] is not None:
        assert kpis["live_is_close"] is True
    else:
        assert kpis["live_is_close"] is False


# -- 성과 넷 --------------------------------------------------------------------


@pytest.fixture
def priced(desk):  # type: ignore[no-untyped-def]
    """어제 회계 스냅샷 한 행. 없으면 "비교할 어제" 자체가 없어 증감이 안 잰다."""
    desk.append(
        "nav_daily",
        [
            _row(
                "FUND", YESTERDAY, nav=100_000_000.0, inflow=100_000_000.0,
                twr_return=0.0, index_value=100.0, drawdown=0.0,
                cash_krw=100_000_000.0, cash_usd=0.0, equity_kr=0.0, equity_us=0.0,
                accrued_dividend=0.0, payable=0.0, fx_rate=1_350.0,
                tax_provision=0.0, nav_after_tax=100_000_000.0,
                benchmark_index=None, benchmark_note=None,
            )
        ],
        ingest_run_id="nav",
    )
    return desk


@pytest.fixture
def desk_client(priced):  # type: ignore[no-untyped-def]
    return create_app(store=priced, clock=ReplayClock(NOW)).test_client()


def test_성과는_회계가_접어_준다(desk_client) -> None:
    """화면이 자기 NAV·수익률을 접지 않는다. 메일도 같은 함수를 읽는다."""
    payload = desk_client.get("/api/trading").get_json()["data"]
    perf = payload["performance"]
    assert perf["nav"] == payload["kpis"]["nav"]
    assert perf["daily_return"] == payload["kpis"]["daily_return"]
    assert perf["cumulative_return"] == payload["kpis"]["cumulative_return"]
    assert perf["mode"] == payload["system"]["mode"]


def test_자산_증감_옆에_입출금이_실린다(desk_client) -> None:
    """증감과 수익률은 다른 사실이다. 입출금을 안 실으면 둘이 서로를
    거짓말쟁이로 만든다 (accounting.md §6)."""
    perf = desk_client.get("/api/trading").get_json()["data"]["performance"]
    assert "nav_change" in perf and "inflow" in perf and "pnl" in perf
    # 손익 = 증감 − 입출금. 화면이 이 뺄셈을 다시 하지 않게 서버가 낸다.
    assert perf["pnl"] == pytest.approx(perf["nav_change"] - perf["inflow"])


def test_원금은_통화를_환산해_더한다(desk_client) -> None:
    """달러 입금을 1원으로 세면 총 수익금이 조용히 부푼다."""
    perf = desk_client.get("/api/trading").get_json()["data"]["performance"]
    assert perf["principal"] == pytest.approx(100_000_000.0)


def test_체결_건수는_목록_길이가_아니다(desk_client) -> None:
    perf = desk_client.get("/api/trading").get_json()["data"]["performance"]
    assert perf["fill_count"] == perf["buy_count"] + perf["sell_count"]
    assert perf["buy_count"] == 1 and perf["sell_count"] == 0
    # 매수뿐이면 실현손익은 0 이 아니라 null 이다 — 0 은 "본전" 으로 읽힌다.
    assert perf["realized_pnl"] is None



def test_휴장일이나_오늘_스냅샷_뒤엔_실시간을_종가로_안_쓴다() -> None:
    """마지막 체결가는 거래소 종가가 아니다(시간외·NXT). 2026-09-25 추석에 9/23 저녁 체결 차이가 '오늘 수익금 +960,590' 으로 떴다."""
    from types import SimpleNamespace

    import pandas as pd

    from quant_rl_trading.dashboard.services.trading import _close_pending

    def ctx(ts: str) -> SimpleNamespace:
        return SimpleNamespace(market="KR", as_of=pd.Timestamp(ts))

    curve = pd.DataFrame({"valid_from": [pd.Timestamp("2026-09-23 16:00", tz="Asia/Seoul")]})
    assert _close_pending(ctx("2026-09-25 15:34+09:00"), curve) is False     # 추석 휴장
    assert _close_pending(ctx("2026-09-23 17:00+09:00"), curve) is False     # 오늘 스냅샷이 이미 있다
    assert _close_pending(ctx("2026-09-28 15:45+09:00"), curve) is True      # 거래일, 오늘 스냅샷 전
