"""세션 종료코드 — **조용한 실패가 rc 로 나가는지.**

무인 실행에서 rc=0 은 "괜찮았다" 로 읽힌다. US 세션이 2026-08 내내 알파
Analyst 0종으로 후보를 못 만들면서 rc=0 을 냈고, 크론은 매일 정상으로
기록했다 (태스크 #12).
"""

from __future__ import annotations

from datetime import UTC, datetime

from quant_rl_trading.backtest import loop
from quant_rl_trading.selector import weights as weights_module
from tools import run_session


def _day(*, blocked_by: str = "", fault: str = "") -> loop.DayResult:
    return loop.DayResult(
        as_of=datetime(2026, 8, 21, 6, 40, tzinfo=UTC),
        nav=1_000_000.0,
        index_value=100.0,
        drawdown=0.0,
        twr_return=0.0,
        candidates=(),
        planned_orders=0,
        requested=0,
        filled=0,
        traded_value=0.0,
        blocked_by=blocked_by,
        fault=fault,
        notes=(),
    )


def test_후보가_0개여도_정상이면_0이다() -> None:
    """**"오늘 살 게 없다" 는 사고가 아니다.** 여기까지 rc 를 올리면 경보가
    무뎌지고, 무뎌진 경보는 진짜 고장도 못 잡는다.
    """
    assert run_session.exit_code(_day()) == 0


def test_차단은_2로_나간다() -> None:
    assert run_session.exit_code(_day(blocked_by="mdd_band")) == 2


def test_설비_고장은_차단과_다른_코드다() -> None:
    """둘을 같은 rc 로 묶으면 "안전장치가 일했다" 와 "선정이 못 돌았다" 가
    로그에서 다시 붙는다 — 이 태스크가 고치려던 것이 정확히 그 뭉뚱그림이다.
    """
    assert run_session.exit_code(_day(fault=weights_module.CONSTRAINT_ONLY)) == 3
    assert run_session.exit_code(_day(fault=weights_module.NONE_PASSED)) == 3
    assert run_session.exit_code(_day(fault=weights_module.NO_MEASUREMENT)) == 3


def test_둘이_겹치면_차단이_먼저다() -> None:
    assert run_session.exit_code(_day(blocked_by="kill_switch", fault="none_passed")) == 2
