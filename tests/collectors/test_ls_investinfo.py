"""LS t3518·t3521 파싱과 지수 소수점 배율 — 2026-10-04 실측 응답 모양 (네트워크 없음)."""

from __future__ import annotations

from datetime import date

import pytest

from quant_rl_trading.collectors.ls_investinfo import (
    decimal_scale,
    fetch_index_daily,
    parse_close,
    parse_daily,
)


def _bar(day: str, price: str, open_: str = "1.0") -> dict:
    return {"date": day, "time": day, "open": open_, "high": price, "low": price, "price": price}


DAILY = {"t3518OutBlock1": [
    _bar("20261002", "77.2272", "77.2624"),
    _bar("20261001", "76.6645", "76.6647"),
]}
CLOSE = {"t3521OutBlock": {"symbol": "SPI@SPX", "close": "7722.72", "date": "20261002"}}


def test_일봉은_날짜_오름차순_0_줄은_버린다() -> None:
    payload = {"t3518OutBlock1": [*DAILY["t3518OutBlock1"], _bar("20261003", "1345.03", "0.0000")]}
    bars = parse_daily(payload)
    assert [b.day for b in bars] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert bars[-1].close == pytest.approx(77.2272)


def test_모르는_심볼은_빈_블록이라_None() -> None:
    empty = {"t3521OutBlock": {"symbol": "", "close": "0", "date": ""}}
    assert parse_close(empty) is None
    assert parse_close(CLOSE) == (date(2026, 10, 2), 7722.72)


def test_지수_배율은_t3521_과_견줘_100() -> None:
    assert decimal_scale(parse_daily(DAILY), (date(2026, 10, 2), 7722.72)) == 100.0


def test_기준일_봉이_없거나_비율이_어긋나면_거부() -> None:
    bars = parse_daily(DAILY)
    with pytest.raises(ValueError):
        decimal_scale(bars, (date(2026, 9, 30), 7651.54))
    with pytest.raises(ValueError):
        decimal_scale(bars, (date(2026, 10, 2), 8000.0))  # 103.6배 — 배율이 아니라 다른 값


class _Client:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request_tr(self, path, tr_cd, body):
        self.calls.append(tr_cd)
        return DAILY if tr_cd == "t3518" else CLOSE


def test_지수_일봉은_배율까지_맞춰_돌려준다() -> None:
    client = _Client()
    bars = fetch_index_daily(client, "SPI@SPX", count=5)
    assert client.calls == ["t3518", "t3521"]
    assert bars[-1].close == pytest.approx(7722.72) and bars[-1].open == pytest.approx(7726.24)
