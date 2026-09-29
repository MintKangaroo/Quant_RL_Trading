"""주문 체결율 — 정의의 경계 (사용자 요청 2026-09-29, dashboard.md §4).

분모는 **증권사에 실제로 나간 조각만**: 예약·계획·가드 차단·휴장일 거부는 빼고, 일반 거부는 넣는다.
체결 수량은 trades 에서 온다. 전송 0 이면 비율은 None(0% 아님).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from quant_rl_trading.dashboard.services import trading as service

TODAY = date(2026, 9, 29)
MORNING = datetime(2026, 9, 29, 0, 40, tzinfo=UTC)  # 한국시간 09:40
SESSION = "KR-2026-09-28"
SESSION_AT = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)


def order(entity: str, status: str, *, qty: float = 10.0, seq: int = 0, revision: int = 1,
          session: str = SESSION, when: datetime = MORNING, valid: datetime = SESSION_AT,
          reason: str = "") -> dict[str, Any]:
    return {
        "entity_id": entity, "session_id": session, "slice_seq": seq, "revision": revision,
        "status": status, "reason": reason, "quantity": qty, "observed_at": when, "valid_from": valid,
    }


def trade(entity: str, qty: float, order_id: str) -> dict[str, Any]:
    return {"entity_id": entity, "quantity": qty, "order_id": order_id}


def broker_fill(entity: str, qty: float, *, seq: int = 0, session: str = SESSION) -> dict[str, Any]:
    return trade(entity, qty, f"{session}|{entity}|{seq}#{int(qty)}")


def test_분모는_나간_조각만_일반_거부는_넣고_휴장_거부는_뺀다() -> None:
    orders = pd.DataFrame([
        order("KR:A", "planned", revision=0), order("KR:A", "filled", revision=3),  # 최신 revision 으로 한 번만
        order("KR:B", "reserved"),                                                # 안 나감
        order("KR:C", "risk_blocked"),                                            # 가드가 막음
        order("KR:D", "rejected", reason="거부 — rsp_cd=01410 모의투자 영업일이 아닙니다"),  # 휴장
        order("KR:E", "rejected", reason="거부 — rsp_cd=02714 주문가능금액 부족"),         # 일반 거부 — 분모
        order("KR:F", "cancelled"),                                               # 마감 취소
        order("KR:G", "sent"),                                                    # 대기
    ])
    trades = pd.DataFrame([broker_fill("KR:A", 10.0)])

    out = service.fill_rate_counts(orders, trades, day=TODAY)

    assert out["sent_count"] == 4  # A·E·F·G
    assert out["sent_quantity"] == pytest.approx(40.0)
    assert out["filled_quantity"] == pytest.approx(10.0)
    assert out["quantity_rate"] == pytest.approx(0.25)
    assert out["count_rate"] == pytest.approx(0.25)
    assert out["unfilled"] == {"cancelled": 1, "rejected": 1, "pending": 1}
    assert out["holiday_excluded"] == 1
    assert out["not_sent"] == 2  # reserved · risk_blocked


def test_부분_체결은_수량과_건수가_갈린다() -> None:
    orders = pd.DataFrame([
        order("KR:A", "cancelled", qty=100.0),  # 30 만 차고 마감 취소 — 취소가 아니라 부분 체결로 센다
        order("KR:B", "filled", qty=100.0),
    ])
    trades = pd.DataFrame([
        broker_fill("KR:A", 10.0), broker_fill("KR:A", 20.0),  # 한 조각의 부분 체결 두 행은 더한다
        broker_fill("KR:B", 100.0),
    ])

    out = service.fill_rate_counts(orders, trades, day=TODAY)

    assert out["quantity_rate"] == pytest.approx(130.0 / 200.0)
    assert out["count_rate"] == pytest.approx(1.0)
    assert out["partial_count"] == 1
    assert out["unfilled"] == {"cancelled": 0, "rejected": 0, "pending": 0}


def test_조각보다_많이_채운_체결은_조각_수량에서_자른다() -> None:
    orders = pd.DataFrame([order("KR:A", "filled", qty=10.0)])
    trades = pd.DataFrame([broker_fill("KR:A", 15.0)])
    assert service.fill_rate_counts(orders, trades, day=TODAY)["quantity_rate"] == pytest.approx(1.0)


def test_전량_체결로_닫힌_조각은_체결_행이_안_붙어도_전량이다() -> None:
    """8/31·9/4·9/7 실측 — 체결 조회가 빠져 대사 스냅샷(snapshot-recon-…)이 수량을 메웠다. "대기" 가 아니다."""
    orders = pd.DataFrame([order("KR:A", "filled", qty=10.0), order("KR:B", "sent", qty=10.0)])
    trades = pd.DataFrame([trade("KR:A", 10.0, "snapshot-recon-2026-09-01|KR:A")])

    out = service.fill_rate_counts(orders, trades, day=TODAY)

    assert out["quantity_rate"] == pytest.approx(0.5)
    assert out["unfilled"] == {"cancelled": 0, "rejected": 0, "pending": 1}


def test_당일_전송이_0이면_None이다() -> None:
    orders = pd.DataFrame([
        order("KR:A", "reserved"),
        order("KR:B", "rejected", reason="rsp_cd=01410"),
        order("KR:C", "filled", when=MORNING - timedelta(days=1)),  # 어제 것 — 오늘 표에 없다
    ])
    trades = pd.DataFrame([broker_fill("KR:C", 10.0)])

    out = service.fill_rate_counts(orders, trades, day=TODAY)

    assert out["quantity_rate"] is None and out["count_rate"] is None
    assert out["sent_count"] == 0
    assert service.fill_rate_counts(pd.DataFrame(), pd.DataFrame(), day=TODAY)["quantity_rate"] is None


def test_shadow_의_세션_한_건_체결은_조각_순서대로_나눠_채운다() -> None:
    """백테스트 체결(order_id = 세션|종목|방향)은 조각이 없다 — 앞 조각부터 채운다."""
    orders = pd.DataFrame([
        order("US:X", "simulated", qty=10.0, seq=0, session="US-2026-09-25"),
        order("US:X", "simulated", qty=10.0, seq=1, session="US-2026-09-25"),
        order("US:X", "paper", qty=10.0, seq=2, session="US-2026-09-25"),  # 아직 시뮬레이션 전 — 대기
    ])
    trades = pd.DataFrame([trade("US:X", 15.0, "US-2026-09-25|US:X|buy")])

    out = service.fill_rate_counts(orders, trades, day=TODAY, market="US")

    assert out["quantity_rate"] == pytest.approx(15.0 / 30.0)
    assert out["filled_count"] == 2 and out["partial_count"] == 1
    assert out["unfilled"]["pending"] == 1


def test_시장이_다른_조각은_세지_않는다() -> None:
    orders = pd.DataFrame([order("KR:A", "filled"), order("US:B", "sent")])
    trades = pd.DataFrame([broker_fill("KR:A", 10.0)])
    out = service.fill_rate_counts(orders, trades, day=TODAY, market="KR")
    assert out["sent_count"] == 1 and out["quantity_rate"] == pytest.approx(1.0)


def test_창은_전송이_있는_최근_N_세션만() -> None:
    rows = []
    fills = []
    for index in range(4):
        session = f"KR-2026-09-2{index}"
        valid = SESSION_AT - timedelta(days=4 - index)
        rows.append(order("KR:A", "filled", session=session, valid=valid, when=valid + timedelta(hours=18)))
        fills.append(broker_fill("KR:A", 10.0 if index >= 2 else 0.0, session=session))
    # 가장 최근 세션은 전부 휴장 거부 — 창을 먹지 않는다.
    rows.append(order("KR:A", "rejected", session="KR-2026-09-24", valid=SESSION_AT,
                      reason="rsp_cd=01410"))
    out = service.fill_rate_counts(pd.DataFrame(rows), pd.DataFrame(fills), sessions=2)

    assert out["sessions"] == 2
    assert out["sent_count"] == 2
    assert out["quantity_rate"] == pytest.approx(1.0)
    # 제외 건수도 창 안 세션 것만 — 전송이 없는 휴장 세션은 창 밖이다.
    assert out["holiday_excluded"] == 0
