"""watchlist 의 신호 이름표 — 덜 산 종목이 HOLD 로 보이던 것(2026-10-06 사용자 지적)."""
from quant_rl_trading.dashboard.services.trading import _signal_of


def test_목표_대비_모자라면_BUY_넘치면_TRIM_가까우면_HOLD() -> None:
    assert _signal_of(0.10, 0) == "BUY"
    assert _signal_of(0.10, 100, 0.06, 0.002) == "BUY"      # 4%p 모자람 → 잔여 채움이 사는 몫
    assert _signal_of(0.10, 100, 0.0995, 0.002) == "HOLD"   # 하한 안
    assert _signal_of(0.10, 100, 0.13, 0.002) == "TRIM"
    assert _signal_of(None, 100) == "SELL"
    assert _signal_of(0.0, 0) == "—"
    assert _signal_of(0.10, 100) == "HOLD"                   # 비중을 모르면 옛 이름표
