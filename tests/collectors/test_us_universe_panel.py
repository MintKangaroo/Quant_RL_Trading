"""미장 명단 유도 계약 테스트.

지키는 것은 셋이다.

1. 명단의 관측시각은 **그 봉의 관측시각**이다. 지어내면 명단이 시세보다
   먼저 관측된 것이 되고, 그 순간 미래를 보게 된다
2. **봉이 빠진 날은 상폐가 아니다.** 미장에는 거래소 명단 스냅샷이 없어서
   결측과 상폐가 같은 모습을 하고 있다
3. 비활성 추정은 그 추정 시각부터만 보인다. 거래 부재만으로 확정 상폐를 지어내지 않는다
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.us_universe_panel import (
    DEAD_SESSIONS,
    delisting_rows,
    session_rows,
)

INFERRED = datetime(2026, 4, 1, tzinfo=UTC)


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

    assert delisting_rows(last_seen, sessions, inferred_at=INFERRED) == []


def test_소식이_끊기면_현재_시점의_비활성_추정으로_찍는다() -> None:
    sessions = [date(2026, 3, day) for day in range(1, 30)]
    last_day = sessions[-(DEAD_SESSIONS + 1)]
    valid_from = datetime(2026, 3, last_day.day, tzinfo=UTC)
    observed_at = datetime(2026, 3, last_day.day, 5, tzinfo=UTC)
    last_seen = {"US:GONE": (last_day, valid_from, observed_at)}

    rows = delisting_rows(last_seen, sessions, inferred_at=INFERRED)

    assert len(rows) == 1
    row = rows[0]
    assert row["is_listed"] is True
    assert row["is_tradable"] is False
    # 과거 마지막 봉을 덮지 않고 현재 비활성 추정으로 남긴다.
    assert row["valid_from"] == INFERRED
    assert row["observed_at"] == INFERRED
    assert row["delisted_on"] is None


def test_패널이_짧으면_아무도_상폐가_아니다() -> None:
    """세션이 판정 기준보다 적으면 판정 자체를 하지 않는다.

    갓 시작한 창고에서 전 종목을 상폐로 찍는 사고를 막는다.
    """
    sessions = [date(2026, 3, day) for day in range(1, DEAD_SESSIONS + 1)]
    last_seen = {"US:NEW": (sessions[0], object(), object())}

    assert delisting_rows(last_seen, sessions, inferred_at=INFERRED) == []


def test_delisting_not_visible_before_inference(store):
    sessions = [date(2026, 3, day) for day in range(1, 30)]
    original = bar("GONE", sessions[0])
    store.append("universe", session_rows([original]), ingest_run_id="last-observation")
    rows = delisting_rows(
        {"US:GONE": (sessions[0], original["valid_from"], original["observed_at"])},
        sessions, inferred_at=INFERRED,
    )
    store.append("universe", rows, ingest_run_id="inference")
    before = store.get("universe", as_of=datetime(2026, 3, 2, tzinfo=UTC))
    assert len(before) == 1
    assert bool(before.iloc[0]["is_tradable"])
    after = store.get("universe", as_of=INFERRED).sort_values("valid_from")
    assert not bool(after.iloc[-1]["is_tradable"])
    assert bool(after.iloc[-1]["is_listed"])
    assert after.iloc[-1]["source"] == "ls_us_inactive_inferred_v2"


def test_inference_rejects_future_panel():
    with pytest.raises(ValueError, match="fully observed"):
        delisting_rows({}, [date(2026, 4, 2)], inferred_at=INFERRED)


def test_legacy_backdated_universe_cannot_support_new_research(store):
    from quant_rl_trading.store.quality import require_causal_universe

    original = bar("GONE", date(2026, 3, 1))
    row = session_rows([original])[0]
    row.update(is_listed=False, is_tradable=False, delisted_on=row["valid_from"])
    store.append("universe", [row], ingest_run_id="legacy-backdated")
    with pytest.raises(ValueError, match="Legacy US inferred delistings"):
        require_causal_universe(store, as_of=INFERRED, market="US")
    require_causal_universe(store, as_of=INFERRED, market="KR")


def test_legacy_rows_known_before_window_do_not_block_live_session(store):
    """구간이 '안 날' 뒤에서 시작하면 소급 행은 이미 알려진 사실이다 — 오늘 하루 shadow 를 막지 않는다."""
    from quant_rl_trading.store.quality import require_causal_universe

    row = session_rows([bar("GONE", date(2026, 3, 1))])[0]
    row.update(is_listed=False, is_tradable=False, delisted_on=row["valid_from"])
    store.append("universe", [row], ingest_run_id="US-universe-delisted-2026-03-20")
    later = datetime(2026, 3, 25, tzinfo=UTC)
    require_causal_universe(store, as_of=later, market="US", window_start=later)
    with pytest.raises(ValueError, match="Legacy US inferred delistings"):
        require_causal_universe(
            store, as_of=later, market="US", window_start=datetime(2026, 3, 10, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="Legacy US inferred delistings"):
        require_causal_universe(store, as_of=later, market="US")
