"""하루 사이클 계약 테스트 — 진짜 창고 위에서.

**여기서 증명하는 것은 결정론이다.** 같은 as_of 로 두 번 돌리면 같은 주문이
나와야 한다. 안 나오면 백테스트가 거짓말이 되고, 그 위의 IR·MDD·보상이 전부
무의미해진다 (불변식 5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side
from quant_rl_trading.session import daily

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)      # 한국시간 10:00
SESSIONS = [NOW - timedelta(days=offset) for offset in range(400, -1, -1)]
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]
#: 시세는 있는데 신호가 없어 후보에 못 드는 종목. 보유만 남은 상태를 만든다.
ORPHAN = "KR:000400"


@pytest.fixture
def fund(store):  # type: ignore[no-untyped-def]
    """1억 입금 + 3종목 400세션 + IC 가중치 + 오늘 신호."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "rate": 1_350.0,
        }],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": SESSIONS[0], "observed_at": SESSIONS[0],
            "source": "test", "currency": "KRW", "amount": 100_000_000.0,
            "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * 5 + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    store.append(
        "analyst_weights",
        [{
            "entity_id": "fundamental", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    store.append(
        "signals",
        [{
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "fundamental", "analyst_version": "fundamental-v0.1.0",
            "score": score, "confidence": 1.0, "horizon_days": 5,
            "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
        } for entity, score in zip(ENTITIES, [0.9, 0.5, 0.2], strict=True)],
        ingest_run_id="s-seed",
    )
    return store


def test_후보에서_주문까지_이어진다(fund) -> None:
    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR")

    assert result.equity == pytest.approx(100_000_000.0)
    assert result.candidates
    assert result.weights
    assert result.orders
    # 종목 상한 15% 를 넘지 않는다.
    assert max(result.weights.values()) <= 0.15 + 1e-9


def test_같은_as_of_는_같은_주문을_낸다(fund, tmp_path) -> None:
    """**결정론.** 안 지켜지면 백테스트가 거짓말이 된다.

    두 번째 실행은 창고 상태가 달라진다(첫 실행이 주문·실현비중을 적재했다).
    그래도 **주문은 같아야 한다** — 결정은 관측의 함수이지 우리가 남긴
    기록의 함수가 아니기 때문이다.
    """
    first = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="a")
    second = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="b")

    assert first.digest() == second.digest()
    assert [item.order_id for item in first.orders] == [
        item.order_id for item in second.orders
    ]


def test_보유_중인데_목표에서_빠진_종목은_매도_대상이_된다(fund) -> None:
    """안 넣으면 팔 기회가 영영 오지 않는다.

    ⚠️ **이 테스트만으로는 부족하다.** ``KR:999999`` 는 창고에 시세가 아예
    없는 종목이라, 시세를 안 가져와서 못 파는 것과 시세가 없어서 못 파는 것을
    구분하지 못한다 — 실제로 2026-08 OOS 백테스트에서 후보 밖 보유 3,109건
    (종목×일) 에 매도 주문이 0건 나가는 동안 이 테스트는 통과하고 있었다.
    구분은 아래 ``test_후보에서_빠진_보유종목도_매도_주문이_나간다`` 가 한다.
    """
    result = daily.run(
        fund, ReplayClock(NOW), as_of=NOW, market="KR",
        holdings={"KR:999999": 100},
    )

    # 가격이 없는 종목이라 주문은 안 나가지만, 대상에는 들어가 사유가 남는다.
    assert "KR:999999" not in result.weights


@pytest.fixture
def fund_with_orphan(fund):  # type: ignore[no-untyped-def]
    """``fund`` + **시세는 있는데 신호가 없는 종목** 하나.

    신호가 없으면 합성 점수가 안 나오고, 점수가 없으면 후보에 못 든다. 그런데
    보유는 하고 있다 — 실전에서 흔한 모습이다(어제까지 top-N 이었다가 오늘
    밀려난 종목). **이게 청산되어야 할 종목이다.**
    """
    rows_universe = []
    rows_price = []
    for index, day in enumerate(SESSIONS):
        rows_universe.append({
            "entity_id": ORPHAN, "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR", "name": ORPHAN,
            "is_listed": True, "is_tradable": True, "delisted_on": None,
        })
        close = 12_000.0 + index * 5
        rows_price.append({
            "entity_id": ORPHAN, "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR",
            "open": close, "high": close, "low": close, "close": close,
            "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None,
        })
    fund.append("universe", rows_universe, ingest_run_id="u-orphan")
    fund.append("prices", rows_price, ingest_run_id="p-orphan")
    return fund


def test_후보에서_빠진_보유종목도_매도_주문이_나간다(fund_with_orphan) -> None:
    """**사고 재현.** 후보 밖으로 밀린 보유 종목이 팔리지 않으면 장부는 한
    방향 래칫이 된다 — top-N 에 들어야 살 수 있고, top-N 에 남아 있어야만
    팔 수 있다.

    2026-08 OOS 백테스트에서 실제로 그랬다. 매도 주문 191건이 전부 그날의
    후보였던 종목이고, 후보 밖 보유 3,109건(종목×일) 에는 한 건도 안 나갔다.
    2026-02-27~03-13 은 11세션 연속 주문 0건으로 -13% 급락을 통과했다.

    원인은 ``session/daily.py`` 가 시세를 **후보 종목만** 조회한 것이다. 후보
    밖 보유 종목은 ``price=0`` 이 되고, ``sizing`` 이 그것을 "시세 없음" 으로
    스킵했다. 스킵은 옳다 — 가격을 모르면 수량을 못 낸다. 틀린 것은 **알 수
    있는 가격을 안 가져온 쪽**이다.
    """
    result = daily.run(
        fund_with_orphan, ReplayClock(NOW), as_of=NOW, market="KR",
        holdings={ORPHAN: 100},
    )

    assert ORPHAN not in result.weights, "신호가 없으니 목표 비중은 0 이어야 한다"
    sells = [
        planned.order for planned in result.orders
        if planned.order.entity_id == ORPHAN and planned.order.side is Side.SELL
    ]
    assert sells, "후보에서 빠진 보유 종목의 매도 주문이 없다 — 영영 못 판다"
    assert sum(order.quantity for order in sells) == 100, "전량 청산이어야 한다"


def test_시세_결측일에도_청산은_나간다(fund_with_orphan) -> None:
    """**팔 수 없는 한 종목이 매수까지 잠그면 안 된다.**

    이 테스트는 두 번 뒤집혔다. 처음에는 ``data_quality.missing_warn`` 이
    0.01 이라 시세 없는 종목 하나로 결측률이 1% 를 넘어 세션이 통째로
    막혔고(``blocked_by``), 그 동작을 고정했다. 한 번 뒤집어 매도를 열었다 —
    *"청산까지 막는 안전장치는 빠져나올 길을 막는 것이라 안전장치가 아니다."*

    두 번째가 여기다. 매도만 열어 두는 것으로는 부족했다. 상장폐지 보유는
    **영원히 안 팔린다** — 시세가 없어 sizing 이 주문을 못 만든다. 그래서
    결측률이 매 세션 1% 를 넘고 매수가 영구히 잠겼다. 실측(2026-08-17)에서
    주식 비중이 81% → 3.7% 로 말라붙었고, 그 위에서 잰 MDD -0.92% 는
    전략의 성적이 아니라 **거래를 안 한 계좌의 성적**이었다.

    고친 자리는 ``executor/pipeline.py`` 다. 품질 게이트가 **살 대상만**
    센다 — 못 파는 보유는 게이트에서 빼고 화면에 사유만 남긴다. 못 파는
    것은 사실이지만, 그것이 나머지 전부를 멈출 이유는 아니다.
    """
    result = daily.run(
        fund_with_orphan, ReplayClock(NOW), as_of=NOW, market="KR",
        holdings={"KR:999999": 100, ORPHAN: 100},
    )

    assert not result.blocked_by, f"통째로 막히면 안 된다: {result.blocked_by}"

    # 못 파는 보유는 게이트에서 빠진다 — 세면 매수가 영구히 잠긴다.
    assert any(
        "데이터 품질 게이트에는 세지 않는다" in note for note in result.notes
    ), f"제외 사유가 화면에 없다: {result.notes}"
    assert not any(
        "청산만 허용" in note for note in result.notes
    ), f"못 파는 보유 하나로 세션이 청산 전용이 됐다: {result.notes}"

    assert [
        planned for planned in result.orders
        if planned.order.entity_id == ORPHAN and planned.order.side is Side.SELL
    ], "시세가 있는 보유 종목은 결측일에도 팔 수 있어야 한다"

    # 시세를 모르는 종목 자체는 여전히 못 판다 — sizing 의 price<=0 가드는 옳다.
    assert not [
        planned for planned in result.orders
        if planned.order.entity_id == "KR:999999"
    ]
    assert any("청산 불가" in note and "KR:999999" in note for note in result.notes)


def test_자본이_0이면_주문하지_않는다(store) -> None:
    """목표 비중을 금액으로 바꿀 수 없다. 0 으로 나누지 않고 멈춘다."""
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "rate": 1_350.0,
        }],
        ingest_run_id="fx-seed",
    )

    result = daily.run(store, ReplayClock(NOW), as_of=NOW, market="KR")

    assert result.orders == ()
    assert any("자본이 0" in note for note in result.notes)


def test_전_단계가_이벤트_로그에_남는다(fund) -> None:
    """**후보가 0개인 날 이유를 말할 수 없으면 그 시스템은 운영할 수 없다.**"""
    daily.run(
        fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="traced",
        wall_clock=ReplayClock(NOW),
    )

    events = fund.get("events", as_of=NOW + timedelta(minutes=1), entity="traced")
    stages = list(events.sort_values("seq")["stage"])

    assert stages == ["observe", "select", "allocate", "execute"]
