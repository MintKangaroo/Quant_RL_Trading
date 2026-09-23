"""모의계좌 취소 확인 — CSPAQ13700 '취소확인' 행. 모의 전용, SC3 와 같은 검증, 주문을 정확히 닫을 때만."""

from __future__ import annotations

from datetime import date

import pytest

from quant_rl_trading.broker.order_confirmations import accept_inquiry_cancel

ROW = {"MrcTpNm": "취소확인", "OrdTime": "12:20:56", "OrgOrdNo": 12570, "OrdNo": 13085, "IsuNo": "A215200", "BnsTpCode": "2", "OrdQty": 62}


def test_실계좌_모드는_거부한다() -> None:
    with pytest.raises(ValueError, match="paper account only"):
        accept_inquiry_cancel(None, None, ROW, fingerprint="f", mode="real", day=date(2026, 9, 23))  # type: ignore[arg-type]


def test_취소확인이_아닌_행은_거부한다() -> None:
    with pytest.raises(ValueError, match="not a cancellation"):
        accept_inquiry_cancel(None, None, {**ROW, "MrcTpNm": "정정확인"}, fingerprint="f", mode="paper", day=date(2026, 9, 23))  # type: ignore[arg-type]


def test_시각이_이상하면_거부한다() -> None:
    with pytest.raises(ValueError, match="invalid inquiry time"):
        accept_inquiry_cancel(None, None, {**ROW, "OrdTime": "12:2"}, fingerprint="f", mode="paper", day=date(2026, 9, 23))  # type: ignore[arg-type]
