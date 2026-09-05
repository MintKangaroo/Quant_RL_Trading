"""Executor 계약 테스트 — **실제 돈이 나가는 층이다.**

여기서 고정하는 것은 셋이다.

1. **멱등성** — 프로세스는 반드시 죽었다 살아난다. 그때 같은 주문이 두 번
   나가면 비중이 두 배가 된다
2. **순서** — 킬스위치가 다른 어떤 판단보다 앞선다
3. **되먹임** — 목표와 실현의 차이가 기록된다 (불변식 7)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.executor import (
    SizingParams,
    Target,
    action_reflection_rate,
    client_order_id,
    engage,
    guards,
    pipeline,
    size_orders,
    split,
)
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)   # 한국시간 10:00
OPEN = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)  # 한국시간 09:00

PARAMS = SizingParams(
    max_adv_ratio=0.03,
    max_liquidation_days=3,
    min_order_value=100_000.0,
    max_price_ratio=0.15,
)


# -- 멱등성 ------------------------------------------------------------------------


def test_주문_식별자는_입력만으로_결정된다() -> None:
    """무작위 UUID 나 시각을 넣으면 재시작한 프로세스가 자기 주문을 못 알아본다."""
    first = client_order_id(session="KR-2026-08-12", entity_id="KR:005930", slice_seq=0)
    second = client_order_id(session="KR-2026-08-12", entity_id="KR:005930", slice_seq=0)

    assert first == second
    assert first != client_order_id(
        session="KR-2026-08-12", entity_id="KR:005930", slice_seq=1
    )
    assert first != client_order_id(
        session="KR-2026-08-13", entity_id="KR:005930", slice_seq=0
    )


def test_분할_조각의_합은_원_수량이다() -> None:
    """나머지를 버리면 목표 비중에 영영 도달하지 못하고 차이가 매일 쌓인다."""
    for quantity in (1, 7, 10, 101, 1_000):
        assert sum(split(quantity, slices=4)) == quantity


# -- 수량 변환 ---------------------------------------------------------------------


def test_내림한다_넘치는_쪽으로_틀리지_않는다() -> None:
    """반올림하면 현금이 모자라 거부되고, 거부는 재시도를, 재시도는 슬리피지를 만든다."""
    targets = [Target("KR:A", weight=0.10, price=70_000.0, adv_value=1e10)]

    sized, _ = size_orders(targets=targets, holdings={}, equity=10_000_000.0, params=PARAMS)

    # 100만원 / 7만원 = 14.28주 → 14주. 반올림이면 15주가 되어 목표를 넘는다.
    assert sized[0].quantity == 14


def test_거래대금_상한을_넘지_않는다() -> None:
    targets = [Target("KR:A", weight=1.0, price=1_000.0, adv_value=10_000_000.0)]

    sized, _ = size_orders(targets=targets, holdings={}, equity=1e9, params=PARAMS)

    # ADV 1,000만 × 3% = 30만원 → 300주. 목표대로면 100만주다.
    assert sized[0].quantity == 300
    assert "상한" in sized[0].reason


def test_거래대금을_모르면_주문하지_않는다() -> None:
    """0 으로 치면 상한이 사라진다. 못 파는 종목을 사는 것이 가장 비싼 실수다."""
    targets = [Target("KR:A", weight=0.5, price=1_000.0, adv_value=0.0)]

    sized, skipped = size_orders(targets=targets, holdings={}, equity=1e8, params=PARAMS)

    assert sized == []
    assert "거래대금" in skipped[0].reason


def test_시세가_없을_때_보유_여부로_사유가_갈린다() -> None:
    """**"못 골랐다" 와 "못 판다" 는 다른 사건이다.**

    스킵 자체는 옳다 — 가격을 모르면 수량을 못 낸다. 문제는 사유가 한 줄로
    뭉뚱그려져 있던 것이다. 보유 중인데 시세가 없으면 그건 평범한 스킵이
    아니라 **못 빠져나오는 포지션**이고, 하락장에서 그게 낙폭을 만든다.
    2026-08 OOS 백테스트의 청산 불가가 오래 안 보인 이유가 정확히 이것이다.
    """
    target = [Target("KR:A", weight=0.0, price=0.0, adv_value=1e10)]

    _, held = size_orders(targets=target, holdings={"KR:A": 100}, equity=1e8, params=PARAMS)
    _, unheld = size_orders(targets=target, holdings={}, equity=1e8, params=PARAMS)

    assert "청산 불가" in held[0].reason and "100" in held[0].reason
    assert unheld[0].reason == "시세 없음"
    assert held[0].reason != unheld[0].reason


def test_최소금액_미달은_매수만_막는다() -> None:
    """소액이라 못 파는 규칙은 포지션을 영영 남긴다."""
    buy = [Target("KR:A", weight=0.05, price=1_000.0, adv_value=1e10)]
    sized, skipped = size_orders(targets=buy, holdings={}, equity=1_000_000.0, params=PARAMS)
    # 5만원어치는 최소 주문금액(10만원)에 못 미친다.
    assert sized == [] and "최소 주문금액" in skipped[0].reason

    # 같은 크기라도 매도는 나간다.
    sell = [Target("KR:A", weight=0.0, price=1_000.0, adv_value=1e10)]
    sized, _ = size_orders(
        targets=sell, holdings={"KR:A": 10}, equity=1_000_000.0, params=PARAMS
    )
    assert sized[0].side is Side.SELL and sized[0].quantity == 10


def test_차이만_주문한다() -> None:
    """전량 청산 후 재매수하면 왕복 비용이 두 배가 된다."""
    targets = [Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e10)]

    sized, _ = size_orders(
        targets=targets, holdings={"KR:A": 900}, equity=10_000_000.0, params=PARAMS
    )

    # 목표 1,000주, 보유 900주 → 100주만 산다. 전량 청산 후 재매수가 아니다.
    assert sized[0].side is Side.BUY
    assert sized[0].quantity == 100


# -- 창고를 낀 8단계 ----------------------------------------------------------------


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    rows = []
    for offset in range(5):
        day = NOW - timedelta(days=offset)
        rows.append({
            "entity_id": "KR:A", "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR",
            "open": 1_000.0, "high": 1_000.0, "low": 1_000.0, "close": 1_000.0,
            "volume": 1e6, "value": 1e9, "adj_factor": None,
        })
    store.append("prices", rows, ingest_run_id="p-seed")
    return store


def targets() -> list[Target]:
    return [Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9)]


def test_같은_세션을_두_번_돌려도_주문은_한_번만_기록된다(seeded) -> None:
    """**프로세스가 죽었다 살아나는 것은 반드시 일어난다.**"""
    clock = ReplayClock(NOW)
    first = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0,
    )
    assert first.planned

    written = pipeline.record_orders(
        seeded, clock, planned=list(first.planned), as_of=NOW, market="KR"
    )
    assert written == 0   # 이미 run 이 기록했다

    stored = seeded.get("orders", as_of=NOW)
    assert len(stored) == len(first.planned)


def test_킬스위치가_걸리면_신규매수가_막힌다(seeded) -> None:
    clock = ReplayClock(NOW)
    engage(
        seeded, as_of=NOW - timedelta(days=1), observed_at=NOW - timedelta(days=1),
        reason="낙폭 한계 초과", by="test",
    )

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0,
    )

    assert result.planned == ()
    assert any("킬스위치" in note for note in result.notes)
    assert any("신규매수 차단" in item.reason for item in result.skipped)


def test_킬스위치가_걸려도_청산은_나간다(seeded) -> None:
    """청산까지 막으면 빠져나올 수 없다. 그건 안전장치가 아니다."""
    clock = ReplayClock(NOW)
    engage(seeded, as_of=NOW - timedelta(days=1), observed_at=NOW - timedelta(days=1),
           reason="낙폭", by="test")

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR",
        targets=[Target("KR:A", weight=0.0, price=1_000.0, adv_value=1e9)],
        holdings={"KR:A": 1_000}, equity=10_000_000.0,
    )

    assert result.planned
    assert all(item.order.side is Side.SELL for item in result.planned)
    # 청산은 시장가다.
    assert all(item.order.limit_price is None for item in result.planned)


def test_킬스위치는_사람이_풀기_전까지_유지된다(seeded) -> None:
    engage(seeded, as_of=NOW - timedelta(days=2), observed_at=NOW - timedelta(days=2),
           reason="낙폭", by="test")
    state, _ = guards.killswitch_state(seeded, as_of=NOW)
    assert state is guards.KillswitchState.ENGAGED

    guards.release(seeded, as_of=NOW, observed_at=NOW, by="사람")
    state, _ = guards.killswitch_state(seeded, as_of=NOW + timedelta(minutes=1))
    assert state is guards.KillswitchState.RELEASED


def test_개장_직후에는_신규매수를_보류한다(seeded) -> None:
    clock = ReplayClock(OPEN + timedelta(minutes=5))
    result = pipeline.run(
        seeded, clock, as_of=OPEN + timedelta(minutes=5), market="KR",
        targets=targets(), holdings={}, equity=10_000_000.0, market_open=OPEN,
    )

    assert result.planned == ()
    assert any("보류" in note for note in result.notes)


def test_실현_비중이_반드시_기록된다(seeded) -> None:
    """**8번이 빠지면 RL 은 자기가 하지 않은 행동으로 벌을 받는다** (불변식 7).

    목표 5% 인데 라운딩으로 0주가 된 사실이 기록에 없으면, Allocator 는
    자기가 5% 를 샀다고 믿는다.
    """
    clock = ReplayClock(NOW)
    # 목표는 있지만 최소금액에 걸려 주문이 안 나가는 상황.
    tiny = [Target("KR:A", weight=0.05, price=1_000.0, adv_value=1e9)]

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=tiny,
        holdings={}, equity=1_000_000.0,
    )

    assert result.planned == ()
    stored = seeded.get("realized_weights", as_of=NOW)
    assert len(stored) == 1
    assert float(stored.iloc[0]["target_weight"]) == pytest.approx(0.05)
    assert float(stored.iloc[0]["realized_weight"]) == 0.0


def test_액션_반영률을_계산한다(seeded) -> None:
    """**30% 미만이면 RL 이 아니라 룰 시스템이다** (CLAUDE.md)."""
    clock = ReplayClock(NOW)
    pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0,
    )

    rate = action_reflection_rate(seeded, as_of=NOW)
    assert 0.0 <= rate <= 1.0


# -- 상장폐지 보유 데드락 ------------------------------------------------------------


def test_시세_없는_보유가_매수를_잠그지_않는다(seeded) -> None:
    """**이게 몇 달치 백테스트를 조용히 무의미하게 만들었다.**

    데이터 품질 게이트가 ``targets`` 전체를 세면, 상장폐지 보유 하나가 결측률을
    임계 위로 밀어 올려 신규매수가 전면 차단된다. 그런데 그 종목은 시세가 없어
    팔 수도 없으므로(sizing 의 price<=0 가드) 게이트가 풀릴 길이 없다.

    2026-08-17 보정가 OOS 백테스트 실측: KR:005390 이 2025-09-29 상장폐지된 뒤로
    매수가 한 건도 안 나갔고, 주식비중이 81% → 3.7% 로 단조 감소하다 그 한
    종목만 남았다. 후보 24종목은 매일 정상이었고 점수도 양수였고 현금도 1억이
    있었다. **못 파는 보유 하나가 전부를 잠갔다.**
    """
    clock = ReplayClock(NOW)
    # 살 종목(시세 있음) + 상장폐지 보유(시세 없음).
    mixed = [
        Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9),
        Target("KR:DEAD", weight=0.0, price=0.0, adv_value=0.0),
    ]

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=mixed,
        holdings={"KR:DEAD": 944}, equity=10_000_000.0,
    )

    buys = [item for item in result.planned if item.order.side is Side.BUY]
    assert buys, f"매수가 잠겼다 — notes={result.notes}"
    assert not any("신규매수 차단" in item.reason for item in result.skipped)


def test_못_파는_보유는_조용히_넘어가지_않는다(seeded) -> None:
    """게이트를 안 걸더라도 사실은 남겨야 한다. 안 그러면 "왜 이 종목이 계속
    있지" 를 아무도 못 묻는다."""
    clock = ReplayClock(NOW)
    mixed = [
        Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9),
        Target("KR:DEAD", weight=0.0, price=0.0, adv_value=0.0),
    ]

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=mixed,
        holdings={"KR:DEAD": 944}, equity=10_000_000.0,
    )

    assert any("KR:DEAD" in note for note in result.notes), result.notes
    assert any("팔 수 없다" in note for note in result.notes), result.notes


def test_후보_시세가_빠지면_여전히_막는다(seeded) -> None:
    """게이트를 무력화한 것이 아니다. **살 종목**의 결측은 그대로 걸린다 —
    가격을 모르는 종목을 사려 들면 어제 가격으로 주문이 나간다."""
    clock = ReplayClock(NOW)
    # 창고에 없는 종목을 사려 한다 = 후보 중 결측.
    unknown = [Target("KR:GHOST", weight=0.10, price=1_000.0, adv_value=1e9)]

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=unknown,
        holdings={}, equity=10_000_000.0,
    )

    assert result.planned == ()
    assert any("결측" in note or "시세" in note for note in result.notes), result.notes
