"""벤치마크 배선 — 진짜 창고 위에서.

여기서 고정하는 것은 세 가지다.

1. **벤치마크만 움직인 날 초과수익이 정확히 그만큼 나온다.** 이게 어긋나면
   보상 함수의 `r_port - r_bench` 가 통째로 거짓이 된다.
2. **지수 결측일은 null 로 남는다.** 앞 값으로 채우면 벤치마크 낙폭이
   지워지고 우리 낙폭만 깊어 보인다 (accounting.md §7.2).
3. **벤치마크 시각이 NAV 와 같다.** 다르면 그 차이가 가짜 초과수익이 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import KRW, snapshot
from quant_rl_trading.accounting import benchmark as benchmark_module
from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.replay.clock import ReplayClock

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)   # 한국시간 15:40
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)

KR_INDEX = "KR:IDX:KOSPI"
US_INDEX = "US:IDX:SP500"
FX = 1_350.0


def index_rows(entity: str, market: str, day: datetime, close: float) -> list[dict]:
    return [{
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": market, "board": None,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 0.0, "value": 0.0,
    }]


@pytest.fixture
def funded(store):  # type: ignore[no-untyped-def]
    """1,000만원 현금 + 고정 환율. 주식은 없다 — 우리 수익률을 0 으로 못 박아
    벤치마크만 움직이게 하려는 것이다."""
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
            "source": "test", "rate": FX,
        } for day in (DAY1, DAY2, DAY3)],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "currency": KRW, "amount": 10_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )
    return store


def seed_indices(store, day: datetime, *, kr: float, us: float, run: str) -> None:
    store.append(
        "indices",
        index_rows(KR_INDEX, "KR", day, kr) + index_rows(US_INDEX, "US", day, us),
        ingest_run_id=run,
    )


def roll(store, day: datetime) -> snapshot.Snapshot:
    """하루를 접어 적재한다. loop.py 의 3단계와 같은 호출이다."""
    clock = ReplayClock(day)
    taken = snapshot.take(store, clock, as_of=day)
    snapshot.write(store, clock, snapshot=taken)
    return taken


def stored(store, day: datetime) -> dict:
    frame = store.get("nav_daily", as_of=day, entity=ledger_module.ACCOUNT)
    return frame.sort_values(["valid_from", "observed_at"]).iloc[-1].to_dict()


def test_설정은_창고에_실제로_있는_지수를_가리킨다(funded) -> None:
    """없는 지수를 비슷한 것으로 대신 채우지 않는다. 이름이 실물과 다르면
    화면이 "코스피" 라고 말하면서 다른 것을 그린다."""
    spec = benchmark_module.BenchmarkSpec.from_store(funded, as_of=DAY1)

    assert spec.kr_index == KR_INDEX
    assert spec.us_index == US_INDEX
    # 총수익지수가 아니라는 사실이 설정에 남아 있어야 화면이 배지를 띄운다.
    assert spec.total_return is False


def test_벤치마크만_움직인_날_초과수익이_정확히_그만큼이다(funded) -> None:
    """우리는 현금만 들고 있어 수익률이 0 이다. 그러면 초과수익은 벤치마크
    수익률의 부호만 뒤집은 값이어야 한다."""
    seed_indices(funded, DAY1, kr= 1_000.0, us=5_000.0, run="idx-1")
    seed_indices(funded, DAY2, kr= 1_020.0, us=5_000.0, run="idx-2")

    roll(funded, DAY1)
    day2 = roll(funded, DAY2)

    first, second = stored(funded, DAY1), stored(funded, DAY2)
    assert first["benchmark_index"] == pytest.approx(100.0)

    # KR +2%, US 0%, 비중 5:5 → 혼합 +1%
    assert second["benchmark_index"] == pytest.approx(101.0)
    assert day2.twr_return == pytest.approx(0.0)

    benchmark_return = second["benchmark_index"] / first["benchmark_index"] - 1.0
    assert day2.twr_return - benchmark_return == pytest.approx(-0.01)


def test_미장은_원화환산_후에_들어간다(funded) -> None:
    """환율만 움직인 날 벤치마크의 미장분이 정확히 그만큼 움직인다. 달러
    기준으로 두면 환차익이 통째로 초과수익으로 잡힌다."""
    funded.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": DAY2, "observed_at": DAY2,
            "source": "test", "rate": FX * 1.10, "revision": 1,
        }],
        ingest_run_id="fx-jump",
    )
    seed_indices(funded, DAY1, kr=1_000.0, us=5_000.0, run="idx-1")
    seed_indices(funded, DAY2, kr=1_000.0, us=5_000.0, run="idx-2")

    roll(funded, DAY1)
    roll(funded, DAY2)

    # KR 0%, US 달러로는 0% 이지만 원화로는 +10% → 혼합 +5%
    assert stored(funded, DAY2)["benchmark_index"] == pytest.approx(105.0)


def test_지수_결측일은_null_로_남는다(funded) -> None:
    """앞 값으로 채우면 그날 벤치마크가 안 빠진 것이 되어 낙폭이 지워진다."""
    seed_indices(funded, DAY1, kr=1_000.0, us=5_000.0, run="idx-1")
    # DAY2 는 지수가 통째로 없다.
    seed_indices(funded, DAY3, kr=900.0, us=5_000.0, run="idx-3")

    roll(funded, DAY1)
    roll(funded, DAY2)
    roll(funded, DAY3)

    frame = funded.get("nav_daily", as_of=DAY3, entity=ledger_module.ACCOUNT)
    rows = frame.sort_values("valid_from").to_dict(orient="records")

    assert rows[0]["benchmark_index"] == pytest.approx(100.0)
    # 결측일 — 앞 값 100.0 이 아니라 null 이어야 한다.
    assert rows[1]["benchmark_index"] is None or rows[1]["benchmark_index"] != rows[1][
        "benchmark_index"
    ]
    # 왜 null 인지 화면이 말할 수 있어야 한다.
    assert KR_INDEX in str(rows[1]["benchmark_note"])


def test_결측_구간의_등락은_사라지지_않는다(funded) -> None:
    """값이 돌아온 날은 **마지막으로 값이 있던 날**에서 수익률을 잰다.
    하루치만 재면 결측 구간의 시장 등락이 벤치마크에서 증발한다."""
    seed_indices(funded, DAY1, kr=1_000.0, us=5_000.0, run="idx-1")
    seed_indices(funded, DAY3, kr=900.0, us=5_000.0, run="idx-3")

    roll(funded, DAY1)
    roll(funded, DAY2)
    roll(funded, DAY3)

    # KR -10%, US 0%, 비중 5:5 → 혼합 -5%. 결측일을 건너뛰어도 등락은 남는다.
    assert stored(funded, DAY3)["benchmark_index"] == pytest.approx(95.0)


def test_지수가_한_번도_없으면_전부_null_이다(funded) -> None:
    """벤치마크가 없다는 사실이 기준값 100 으로 둔갑하면, 첫날부터 우리가
    벤치마크와 나란히 출발한 것처럼 보인다."""
    roll(funded, DAY1)

    row = stored(funded, DAY1)
    assert row["benchmark_index"] != row["benchmark_index"]  # NaN
    assert row["benchmark_note"]


def test_오래된_종가는_휴장이_아니라_구멍이다(funded) -> None:
    """직전 종가가 max_staleness_days 를 넘으면 null 이다. 안 그러면 수집이
    끊긴 몇 달이 "벤치마크가 안 움직인 몇 달" 로 기록된다."""
    stale_day = DAY1 - timedelta(days=60)
    seed_indices(funded, stale_day, kr=1_000.0, us=5_000.0, run="idx-old")

    result = benchmark_module.level(funded, as_of=DAY1, fx_rate=FX)

    assert result.index_value is None
    assert "지수 종가 없음" in str(result.note)


def test_유령_거래일은_구멍으로_세지_않는다(funded) -> None:
    """휴장을 구멍으로 오판하면 없는 결함을 benchmark_note 에 적는다.

    2026-07-17(제헌절)은 KRX 휴장인데 ``exchange_calendars`` 의 XKRX 는
    거래일이라고 답한다 — ``market_hours`` 의 예외층이 그것을 덮는다.
    덮지 않으면 직전 거래일(7/16) 종가가 최신인데도 "하루치 종가가 빠졌다"
    로 읽혀 벤치마크가 통째로 null 이 된다. KR 의 허용 지연은 0 거래일이라
    유령 날 하나로 바로 넘어간다(KNOWABLE_LAG).
    """
    thursday = datetime(2026, 7, 16, 6, 40, tzinfo=UTC)  # 한국시간 15:40
    holiday = datetime(2026, 7, 17, 6, 40, tzinfo=UTC)   # 제헌절 — 휴장
    seed_indices(funded, thursday, kr=1_000.0, us=5_000.0, run="idx-thu")

    result = benchmark_module.level(funded, as_of=holiday, fx_rate=FX)

    assert result.index_value is not None


def test_벤치마크는_NAV_와_같은_시각_같은_환율로_잰다(funded) -> None:
    """스냅샷이 NAV 평가에 쓴 환율을 그대로 받는다. 여기서 다시 조회하면 그
    사이 들어온 정정본을 집어 두 값이 갈리고, 갈린 만큼이 가짜 초과수익이다."""
    seed_indices(funded, DAY1, kr=1_000.0, us=5_000.0, run="idx-1")
    seed_indices(funded, DAY2, kr=1_000.0, us=5_500.0, run="idx-2")

    roll(funded, DAY1)
    taken = snapshot.take(funded, ReplayClock(DAY2), as_of=DAY2)
    snapshot.write(funded, ReplayClock(DAY2), snapshot=taken)

    row = stored(funded, DAY2)
    # 저장된 회계 시각(valid_from)과 벤치마크가 본 시각이 같다.
    assert row["valid_from"].to_pydatetime() == DAY2
    assert row["fx_rate"] == pytest.approx(taken.valuation.fx_rate)

    # 같은 as_of·같은 환율로 다시 재면 저장된 값이 그대로 나온다.
    again = benchmark_module.level(
        funded, as_of=DAY2, fx_rate=taken.valuation.fx_rate
    )
    assert again.index_value == pytest.approx(float(row["benchmark_index"]))
    # US +10%, KR 0% → 혼합 +5%
    assert again.index_value == pytest.approx(105.0)


def test_마감_전_시각엔_오늘_종가를_요구하지_않는다(store) -> None:
    """미장 시각(05:20 KST)의 스냅샷이 국장에 아직 열지도 않은 날의 종가를 요구해
    벤치마크가 NaN 이 됐다. 마감 전이면 직전 거래일 종가가 최신이다."""
    from datetime import UTC, date, datetime
    from zoneinfo import ZoneInfo

    from quant_rl_trading.accounting import benchmark as bm
    from quant_rl_trading.collectors.market_hours import Market

    store.seed_config_defaults()
    seoul = ZoneInfo("Asia/Seoul")
    store.append("indices", [{
        "entity_id": "KR:IDX:KOSPI", "valid_from": datetime(2026, 9, 1, tzinfo=UTC),
        "observed_at": datetime(2026, 9, 1, 16, 0, tzinfo=seoul), "source": "t", "market": "KR",
        "board": "KOSPI", "open": 1.0, "high": 1.0, "low": 1.0, "close": 6835.8, "volume": 0.0, "value": None,
    }], ingest_run_id="idx-test", source="t")
    dawn = datetime(2026, 9, 2, 5, 20, tzinfo=seoul)
    assert bm._expected_close_day(Market.KR, dawn) == date(2026, 9, 1)
    assert bm._close(store, entity="KR:IDX:KOSPI", market=Market.KR, as_of=dawn, search_days=10) == 6835.8
    # 9/2 마감 뒤에는 9/2 종가가 있어야 한다 — 9/1 것은 구멍이다
    evening = datetime(2026, 9, 2, 16, 0, tzinfo=seoul)
    assert bm._expected_close_day(Market.KR, evening) == date(2026, 9, 2)
    assert bm._close(store, entity="KR:IDX:KOSPI", market=Market.KR, as_of=evening, search_days=10) is None
