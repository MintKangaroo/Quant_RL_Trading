"""공매도 잔고: 공표 전 결제일은 오류가 아니다 — rc=0 이어야 한다 (2026-09-19).

거래량 루프는 9/12 에 고쳤는데 잔고 루프가 빠져, 결제일마다 공표 전 일주일 내내 rc=1
헛경보가 났다. 진짜 고장이 그 칸에 묻힌다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

from quant_rl_trading.collectors.finra_short import DailyResult

TOOL = Path(__file__).resolve().parents[2] / "tools" / "backfill_finra.py"


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("backfill_finra_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_공표_전_결제일은_rc_0(monkeypatch) -> None:
    tool = _load()

    class FakeBackfiller:
        def __init__(self, **_: object) -> None:
            pass

        def plan(self, start: date, end: date) -> list[date]:
            return [date(2026, 8, 31), date(2026, 9, 15)]

        def run_settlement(self, day: date) -> DailyResult:
            if day == date(2026, 9, 15):
                return DailyResult(day, 0, error="아직 공표 전", pending=True)
            return DailyResult(day, 0, skipped=True)

    monkeypatch.setattr(tool, "ShortInterestBackfiller", FakeBackfiller)
    monkeypatch.setattr(tool, "make_post", lambda client: None)
    assert tool.run_interest(object(), object(), date(2026, 8, 5), date(2026, 9, 19)) == 0


def test_진짜_오류는_여전히_rc_1(monkeypatch) -> None:
    tool = _load()

    class Broken:
        def __init__(self, **_: object) -> None:
            pass

        def plan(self, start: date, end: date) -> list[date]:
            return [date(2026, 8, 31)]

        def run_settlement(self, day: date) -> DailyResult:
            return DailyResult(day, 0, error="HTTP 500")

    monkeypatch.setattr(tool, "ShortInterestBackfiller", Broken)
    monkeypatch.setattr(tool, "make_post", lambda client: None)
    assert tool.run_interest(object(), object(), date(2026, 8, 5), date(2026, 9, 19)) == 1
