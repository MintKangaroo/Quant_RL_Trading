"""Yahoo 미장 지수 — 응답을 indices 행으로 바꾸는 부분만 고정한다 (네트워크 없음)."""

from __future__ import annotations

from datetime import date

from tools.collect_indices_us import fetch


class _Client:
    def get(self, url, headers=None, timeout=None):
        class R:
            def json(self):
                return {"chart": {"result": [{
                    "timestamp": [1787751000, 1787837400],   # 2026-08-26 / 08-27 13:30 UTC
                    "indicators": {"quote": [{"close": [7675.7, 7730.99], "open": [7650.0, 7680.0],
                                               "high": [7690.0, 7740.0], "low": [7640.0, 7670.0], "volume": [1, 2]}]},
                }]}}
        return R()


def test_yahoo_응답을_날짜별_종가로() -> None:
    bars = fetch("^GSPC", _Client())
    assert [d for d, _ in bars] == [date(2026, 8, 26), date(2026, 8, 27)]
    assert bars[-1][1]["close"] == 7730.99 and bars[-1][1]["high"] == 7740.0
