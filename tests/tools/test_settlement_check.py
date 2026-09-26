"""정산 대조 — 합계 비교 규칙 (2026-09-07)."""
from __future__ import annotations

from datetime import date

from quant_rl_trading.collectors.market_hours import Market
from tools.settlement_check import Totals, compare, trade_session_for


def test_정산일은_두_거래일_전_세션과_짝이다() -> None:
    # 9/7(월) 정산 ← 9/3(목) 체결. 주말을 건넌다.
    assert trade_session_for(date(2026, 9, 7), Market.KR) == date(2026, 9, 3)


def test_금액은_허용치_수량은_0_차이만_통과() -> None:
    ledger = Totals(100, 1_000_000, 150, 1_500, 50, 500_000, 75)
    broker = Totals(100, 999_500, 150, 1_500, 50, 500_000, 75)
    _, ok = compare(ledger, broker, tolerance=0.001)
    assert ok
    _, ok = compare(Totals(101, 1_000_000, 150, 1_500, 50, 500_000, 75), broker, tolerance=0.001)
    assert not ok
    _, ok = compare(Totals(100, 1_002_000, 150, 1_500, 50, 500_000, 75), broker, tolerance=0.001)
    assert not ok


def test_휴장일엔_대조하지_않는다(capsys) -> None:  # type: ignore[no-untyped-def]
    """추석 9/24 는 NotSessionError 로 죽었고 9/25 는 브로커 0 ↔ 장부 4,928만원을 "불일치" 로 냈다(2026-09-26 점검).
    휴장일엔 정산이 없다 — 창고·계좌를 열기 전에 끝난다."""
    from tools.settlement_check import main

    assert main(["--settle", "2026-09-25"]) == 0
    assert "거래일이 아니다" in capsys.readouterr().out
