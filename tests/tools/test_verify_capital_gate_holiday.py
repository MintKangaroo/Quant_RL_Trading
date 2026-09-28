"""자본 증액 게이트의 주문 실패율 — 휴장일 거부는 실패가 아니다 (2026-09-28).

9/24 추석 `01410 모의투자 영업일이 아닙니다` 70건은 주문 경로의 고장이 아니라 날짜였다. 대시보드 거부율
(`reject_counts`)·재전송(`holiday_retry_due`)과 **같은 판별 함수**(`pipeline.is_holiday_rejection`)로 뺀다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tools.verify_capital_gate import check_order_fail_rate

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)


def _order(entity: str, status: str, reason: str = "") -> dict:
    moment = NOW - timedelta(hours=5)
    return {
        "entity_id": entity, "valid_from": moment, "observed_at": moment, "source": "test",
        "market": "KR", "session_id": "KR-2026-08-11", "slice_seq": 0, "side": "buy",
        "quantity": 1.0, "limit_price": 1_000.0, "target_weight": 0.1,
        "status": status, "reason": reason,
    }


def test_휴장일_거부는_주문_실패율에서_빠진다(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    rows = [_order(f"KR:{i:06d}", "sent", "broker_order_no=1") for i in range(100)]
    rows += [
        _order(f"KR:{i:06d}", "rejected", "거부 — TR CSPAT00601 rsp_cd=01410 msg=모의투자 영업일이 아닙니다")
        for i in range(100, 170)
    ]
    store.append("orders", rows, ingest_run_id="orders")

    check = check_order_fail_rate(store, NOW, "KR")

    assert check.status == "PASS"
    assert any("실계좌 주문 100건 · 실패 0건" in line for line in check.evidence)
    assert any("휴장일 거부 70건" in line for line in check.evidence)

    store.append(
        "orders",
        [_order(f"KR:9{i:05d}", "rejected", "거부 — rsp_cd=02714 주문가능금액 부족") for i in range(20)],
        ingest_run_id="orders-real-reject",
    )
    assert check_order_fail_rate(store, NOW, "KR").status == "FAIL"  # 진짜 거부는 그대로 센다
