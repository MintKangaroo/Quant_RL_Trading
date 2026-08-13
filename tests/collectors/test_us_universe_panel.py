"""미장 명단 유도 계약 테스트.

지키는 것은 셋이다.

1. 명단의 관측시각은 **그 봉의 관측시각**이다. 지어내면 명단이 시세보다
   먼저 관측된 것이 되고, 그 순간 미래를 보게 된다
2. **봉이 빠진 날은 상폐가 아니다.** 미장에는 거래소 명단 스냅샷이 없어서
   결측과 상폐가 같은 모습을 하고 있다
3. 상폐 행의 시각은 **마지막으로 본 세션**의 것이다. 오늘로 찍으면 과거
   리플레이가 그날의 사실과 어긋난다
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.us_universe_panel import (
    DEAD_SESSIONS,
    delisting_rows,
    session_rows,
)


def bar(ticker: str, day: date, hour: int = 5) -> dict[str, object]:
    """미장 일봉 한 줄. 관측은 다음날 새벽이다 (공표 정책)."""
    return {
        "entity_id": f"US:{ticker}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
    }


def test_관측시각은_봉에서_그대로_온다() -> None:
    day = date(2026, 3, 4)
    rows = session_rows([bar("AAPL", day)])

    assert len(rows) == 1
    row = rows[0]
    assert row["observed_at"] == datetime(2026, 3, 4, 5, tzinfo=UTC)
    assert row["valid_from"] == datetime(2026, 3, 4, tzinfo=UTC)
    assert row["market"] == str(Market.US)
    assert row["is_listed"] is True
    assert row["is_tradable"] is True
    assert row["delisted_on"] is None
    # 이름은 티커다. SEC 는 오늘 이름만 주므로 과거 행에 찍으면 사명 변경이
    # 소급된다.
    assert row["name"] == "AAPL"


def test_같은_종목이_두_번_와도_한_행() -> None:
    day = date(2026, 3, 4)
    rows = session_rows([bar("AAPL", day), bar("AAPL", day)])
    assert len(rows) == 1


def test_봉이_빠진_날은_상폐가_아니다() -> None:
    """중간에 며칠 거래가 없어도, 다시 나타나면 상폐가 아니다.

    이걸 상폐로 찍으면 유동성 낮은 종목이 매일 상폐와 재상장을 반복하며
    횡단면을 흔든다.
    """
    sessions = [date(2026, 3, day) for day in range(1, 30)]
    # 마지막 봉이 패널 끝이다 — 중간에 아무리 빠져도 살아 있다.
    last_seen = {"US:THIN": (sessions[-1], object(), object())}

    assert delisting_rows(last_seen, sessions) == []


def test_소식이_끊기면_상폐로_찍는다() -> None:
    sessions = [date(2026, 3, day) for day in range(1, 30)]
    last_day = sessions[-(DEAD_SESSIONS + 1)]
    valid_from = datetime(2026, 3, last_day.day, tzinfo=UTC)
    observed_at = datetime(2026, 3, last_day.day, 5, tzinfo=UTC)
    last_seen = {"US:GONE": (last_day, valid_from, observed_at)}

    rows = delisting_rows(last_seen, sessions)

    assert len(rows) == 1
    row = rows[0]
    assert row["is_listed"] is False
    assert row["is_tradable"] is False
    # 시각은 오늘이 아니라 마지막으로 본 세션의 것이다.
    assert row["valid_from"] == valid_from
    assert row["observed_at"] == observed_at
    assert row["delisted_on"] == valid_from


def test_패널이_짧으면_아무도_상폐가_아니다() -> None:
    """세션이 판정 기준보다 적으면 판정 자체를 하지 않는다.

    갓 시작한 창고에서 전 종목을 상폐로 찍는 사고를 막는다.
    """
    sessions = [date(2026, 3, day) for day in range(1, DEAD_SESSIONS + 1)]
    last_seen = {"US:NEW": (sessions[0], object(), object())}

    assert delisting_rows(last_seen, sessions) == []
