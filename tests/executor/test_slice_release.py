"""조각을 시간에 걸쳐 내보내는 규칙 (``executor/orders.due_slices``).

**왜 나눠 내나** — 예전에는 네 조각이 전부 08:40 에 나가 09:00 개장 단일가에서
한꺼번에 체결됐다(2026-09-01 실측: 82건 중 57건이 개장에 즉시 종결). 하루 중
스프레드가 가장 넓고 호가가 가장 얇은 순간이고, 우리 주문은 종목당 일거래대금의
0.9~2.4% 라 무시할 크기가 아니다.

``slice_interval_sec`` 는 설정에도 있고 문서에도 "이 간격으로 낸다" 고 적혀
있었는데 **아무도 안 썼다** — 그래서 이 파일이 그 계약을 지킨다.
"""
from __future__ import annotations

from quant_rl_trading.executor.orders import PlannedOrder, SliceParams, due_slices
from quant_rl_trading.schemas.order import Order, Side


def _planned(count: int) -> list[PlannedOrder]:
    return [
        PlannedOrder(
            order=Order(entity_id="KR:A", side=Side.BUY, quantity=10, limit_price=1_000.0),
            order_id=f"o{seq}",
            session_id="KR-2026-09-01",
            slice_seq=seq,
            target_weight=0.1,
        )
        for seq in range(count)
    ]


def _params(interval: int) -> SliceParams:
    return SliceParams(slice_count=4, slice_interval_sec=interval, max_slippage=0.005)


def test_세션_시각에는_첫_조각만_나간다() -> None:
    due = due_slices(_planned(4), params=_params(3600), elapsed_sec=0.0)
    assert [item.slice_seq for item in due] == [0]


def test_시간이_지나면_해당_조각까지_나간다() -> None:
    planned, params = _planned(4), _params(3600)
    assert [i.slice_seq for i in due_slices(planned, params=params, elapsed_sec=3600)] == [0, 1]
    assert [i.slice_seq for i in due_slices(planned, params=params, elapsed_sec=7200)] == [0, 1, 2]
    assert [i.slice_seq for i in due_slices(planned, params=params, elapsed_sec=999999)] == [0, 1, 2, 3]


def test_간격이_0이면_전부_지금_나간다() -> None:
    """**되돌릴 수 있어야 한다.** 설정 하나로 예전 동작으로 돌아간다 (불변식 10)."""
    due = due_slices(_planned(4), params=_params(0), elapsed_sec=0.0)
    assert [item.slice_seq for item in due] == [0, 1, 2, 3]


def test_음수_간격도_전부_지금_나간다() -> None:
    """설정이 잘못 들어와도 **주문을 잃지 않는다** — 안 내보내는 쪽으로 실패하면
    조각이 영영 안 나가고, 그건 조용히 포지션이 비는 사고다."""
    due = due_slices(_planned(4), params=_params(-1), elapsed_sec=0.0)
    assert len(due) == 4


def test_이미_지난_조각은_다시_고르지_않는다() -> None:
    """``due_slices`` 는 **지금 낼 수 있는 것 전부**를 준다. 중복 전송을 막는 것은
    ``submit_orders`` 의 ``submit-<order_id>`` 멱등 가드지 이 함수가 아니다 —
    두 곳이 같은 일을 하면 한쪽을 고칠 때 다른 쪽이 조용히 어긋난다."""
    planned, params = _planned(4), _params(60)
    first = due_slices(planned, params=params, elapsed_sec=60)
    second = due_slices(planned, params=params, elapsed_sec=120)
    assert [i.slice_seq for i in first] == [0, 1]
    assert [i.slice_seq for i in second] == [0, 1, 2]
