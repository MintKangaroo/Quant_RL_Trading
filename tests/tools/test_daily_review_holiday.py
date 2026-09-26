"""tools/daily_review.py — 휴장일엔 LLM 을 부르지 않는다(2026-09-26 점검: 추석 9/24·9/25 에도 크론이 돌아 값을 치렀다)."""
from __future__ import annotations

from datetime import UTC, datetime

from quant_rl_trading.replay.clock import ReplayClock
from tools import daily_review as tool


def test_휴장일엔_리뷰하지_않는다(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tool, "load_env", lambda: None)
    monkeypatch.setattr(tool, "LiveClock", lambda: ReplayClock(datetime(2026, 9, 25, 7, 30, tzinfo=UTC)))  # 추석 16:30 KST

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("휴장일에 창고·LLM 을 열었다")

    monkeypatch.setattr(tool, "build_store", boom)
    monkeypatch.setattr(tool.DailyReviewer, "from_store", boom)
    assert tool.main(["--market", "KR"]) == 0
    assert "휴장" in capsys.readouterr().out
